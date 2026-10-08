"""
Reconciliation: match bank deposits to reservations and explain anything
that doesn't line up.

What each channel deposits, and when:
    Airbnb  rent + cleaning - fee             about 1 day after check-in
            (Airbnb pays the taxes itself, and may combine payouts)
    Vrbo    rent + cleaning + taxes - fee     about 1 day after check-out
            (the bank description includes the Vrbo confirmation code)
    Direct  rent + cleaning + taxes - fee     in a weekly batch, the Monday
            after check-in
Refunds issued after a payout show up as negative deposits.

Matching runs in steps, from most certain to least certain, and every
deposit or reservation is used at most once.
"""

from datetime import timedelta
from decimal import Decimal

from payouts import channel_fee, money, to_date

# How far a deposit can land from its expected date and still count.
DATE_WINDOW_DAYS = 2
# Differences at or below this are treated as rounding, matched, and flagged.
SMALL_DIFFERENCE = Decimal("1.00")


# ---------- what we expect to see in the bank ----------

def expected_deposit(res):
    """The amount that should reach the bank for this reservation, net of refunds."""
    amount = (money(res["accommodation_total"]) - money(res["refund_amount"])
              + money(res["cleaning_fee"]))
    if res["taxes_remitted_by"] == "company":   # Vrbo and direct pass taxes to us
        amount += money(res["taxes_collected"])
    return amount - channel_fee(res)


def deposit_before_refund(res):
    """What the first payout should have been, before any refund came back out."""
    return expected_deposit(res) + money(res["refund_amount"])


def expected_date(res):
    check_in = to_date(res["check_in"])
    if res["channel"] == "airbnb":
        return check_in + timedelta(days=1)
    if res["channel"] == "vrbo":
        return to_date(res["check_out"]) + timedelta(days=1)
    # direct: the next Monday after check-in (Monday is weekday 0)
    return check_in + timedelta(days=7 - check_in.weekday())


def close_in_time(deposit, day):
    return abs((to_date(deposit["date"]) - day).days) <= DATE_WINDOW_DAYS


# ---------- reading the bank feed ----------

def deposit_kind(deposit):
    text = deposit["description"].upper()
    if text.startswith("AIRBNB PAYMENTS ADJ"):
        return "airbnb_adjustment"
    if text.startswith("AIRBNB"):
        return "airbnb"
    if text.startswith("HOMEAWAY VRBO"):
        return "vrbo"
    if text.startswith("GUESTYPAY SETTLEMENT"):
        return "direct"
    return "unknown"


# ---------- the matching ----------

def reconcile(reservations, deposits, agreements=()):
    """Return a list of result rows, one per match or problem.

    agreements (optional): owner agreements, so matched money for a listing
    with no owner on file can be flagged.
    """
    results = []
    used_deposits = set()        # transaction IDs already explained
    matched_res = set()          # reservation IDs already matched

    def record(status, res_ids, txn_ids, expected, actual, reason):
        results.append({
            "status": status,
            "reservations": " + ".join(res_ids),
            "transactions": " + ".join(txn_ids),
            "expected": expected,
            "actual": actual,
            "difference": None if expected is None or actual is None else actual - expected,
            "reason": reason,
        })
        used_deposits.update(txn_ids)
        matched_res.update(res_ids)

    by_id = {r["reservation_id"]: r for r in reservations}
    by_vrbo_code = {r["channel_confirmation"]: r for r in reservations if r["channel"] == "vrbo"}

    # Step 1: duplicates. Same date, amount, and description as an earlier line.
    seen = {}
    for d in deposits:
        key = (d["date"], d["amount"], d["description"])
        if key in seen:
            record("PROBLEM", [], [d["transaction_id"]], None, money(d["amount"]),
                   f"Possible duplicate of {seen[key]}: same date, amount, and description. "
                   "Counted once. Check the bank statement: if the money really arrived "
                   "twice, the sender overpaid and will likely take it back.")
        else:
            seen[key] = d["transaction_id"]

    def open_deposits(kind):
        return [d for d in deposits
                if deposit_kind(d) == kind and d["transaction_id"] not in used_deposits]

    def open_reservations(channel):
        return [r for r in reservations
                if r["channel"] == channel and r["reservation_id"] not in matched_res]

    # Step 2: cancelled and fully refunded bookings expect no money at all.
    for res in reservations:
        if res["status"] == "cancelled" and expected_deposit(res) == 0:
            record("OK", [res["reservation_id"]], [], Decimal("0.00"), Decimal("0.00"),
                   "Cancelled and fully refunded; no deposit expected.")

    # Step 3: Vrbo deposits carry the confirmation code, so match on it directly.
    for d in open_deposits("vrbo"):
        code = d["description"].split()[-1]
        res = by_vrbo_code.get(code)
        if res is None or res["reservation_id"] in matched_res:
            continue
        compare(record, res, d)

    # Step 4: GuestyPay pays all direct bookings for a week in one Monday batch.
    batches = {}
    for res in open_reservations("direct"):
        batches.setdefault(expected_date(res), []).append(res)
    for d in open_deposits("direct"):
        group = batches.get(to_date(d["date"]), [])
        if not group:
            continue
        ids = [r["reservation_id"] for r in group]
        expected = sum((expected_deposit(r) for r in group), Decimal("0"))
        actual = money(d["amount"])
        if actual == expected:
            reason = "Weekly GuestyPay batch." if len(group) == 1 else \
                f"Weekly GuestyPay batch covering {len(group)} direct bookings."
            record("OK", ids, [d["transaction_id"]], expected, actual, reason)
        else:
            record("PROBLEM", ids, [d["transaction_id"]], expected, actual,
                   "GuestyPay batch total doesn't match the direct bookings for that week.")

    # Step 5: Airbnb refunds after payout. The original payout and the negative
    # adjustment should add up to what the booking is worth after the refund.
    for res in open_reservations("airbnb"):
        if money(res["refund_amount"]) == 0:
            continue
        for adj in open_deposits("airbnb_adjustment"):
            for d in open_deposits("airbnb"):
                if not close_in_time(d, expected_date(res)):
                    continue
                total = money(d["amount"]) + money(adj["amount"])
                if total == expected_deposit(res):
                    fee_returned = money(adj["amount"]) + money(res["refund_amount"])
                    record("OK (note)", [res["reservation_id"]],
                           [d["transaction_id"], adj["transaction_id"]],
                           expected_deposit(res), total,
                           f"Refund of {res['refund_amount']} issued after payout. "
                           f"The original payout ({d['amount']}) was {fee_returned} below "
                           f"the pre-refund amount ({deposit_before_refund(res)}), and the "
                           f"adjustment ({adj['amount']}) was {fee_returned} smaller than the "
                           "refund: Airbnb charged its fee on the full stay, then returned part "
                           "of it. The two together equal the booking's value after the refund.")
                    break
            if res["reservation_id"] in matched_res:
                break

    # Step 6: Airbnb, one deposit for one booking, exact amount.
    # When two bookings would fit (same amount), pick the one whose expected
    # date is closest to the deposit date.
    for d in sorted(open_deposits("airbnb"), key=lambda x: x["date"]):
        candidates = [r for r in open_reservations("airbnb")
                      if expected_deposit(r) == money(d["amount"])
                      and close_in_time(d, expected_date(r))]
        if candidates:
            best = min(candidates, key=lambda r: abs((to_date(d["date"]) - expected_date(r)).days))
            compare(record, best, d)

    # Step 7: Airbnb combined payouts. Two bookings paid out in one deposit.
    for d in open_deposits("airbnb"):
        nearby = [r for r in open_reservations("airbnb") if close_in_time(d, expected_date(r))]
        for i, a in enumerate(nearby):
            for b in nearby[i + 1:]:
                if expected_deposit(a) + expected_deposit(b) == money(d["amount"]):
                    record("OK (note)", [a["reservation_id"], b["reservation_id"]],
                           [d["transaction_id"]],
                           expected_deposit(a) + expected_deposit(b), money(d["amount"]),
                           "Airbnb combined two payouts into one deposit.")
                    break
            if d["transaction_id"] in used_deposits:
                break

    # Step 8: Airbnb near-misses: right booking and date, amount off by a little.
    for d in open_deposits("airbnb"):
        for res in open_reservations("airbnb"):
            gap = abs(money(d["amount"]) - expected_deposit(res))
            if gap <= SMALL_DIFFERENCE and close_in_time(d, expected_date(res)):
                compare(record, res, d)
                break

    # Step 9: whatever is left over is a problem.
    for d in deposits:
        if d["transaction_id"] in used_deposits:
            continue
        reason = "No reservation matches this deposit."
        hint = guest_hint(d, reservations)
        if hint:
            reason += " " + hint
        reason += " Not owner money until someone identifies it."
        record("PROBLEM", [], [d["transaction_id"]], None, money(d["amount"]), reason)

    for res in reservations:
        if res["reservation_id"] in matched_res:
            continue
        record("PROBLEM", [res["reservation_id"]], [], expected_deposit(res), None,
               f"Expected a {res['channel']} deposit of {expected_deposit(res)} "
               f"around {expected_date(res)}, but none is in the bank feed. "
               "Check the channel's payout status before paying the owner.")

    add_notes(results, by_id, {d["transaction_id"]: d for d in deposits}, agreements)
    return results


def add_notes(results, by_id, deposits_by_id, agreements):
    """Explain matches that are correct but still worth a human's attention."""
    listings_with_owner = {a["listing_id"] for a in agreements}
    for row in results:
        if row["status"] == "PROBLEM" or not row["reservations"] or not row["transactions"]:
            continue
        notes = []
        for res_id in row["reservations"].split(" + "):
            res = by_id[res_id]
            if agreements and res["listing_id"] not in listings_with_owner:
                notes.append(f"Money received for {res_id}, but listing {res['listing_id']} "
                             f"({res['listing_name']}) has no owner agreement, so it isn't on any "
                             "owner statement. Add the agreement, then pay it out.")
            first_txn = row["transactions"].split(" + ")[0]
            paid_month = deposits_by_id[first_txn]["date"][:7]
            stay_ends = res["check_out"][:7]
            if paid_month != stay_ends:
                notes.append(f"Money for {res_id} arrived in {paid_month}, but the stay ends in "
                             f"{stay_ends}, so it is paid to the owner on the {stay_ends} statement.")
        if notes:
            row["status"] = "OK (note)"
            row["reason"] = " ".join([row["reason"]] + notes).strip()


def compare(record, res, deposit):
    """Record a one-to-one match, flagging any difference in amount."""
    expected = expected_deposit(res)
    actual = money(deposit["amount"])
    if actual == expected:
        record("OK", [res["reservation_id"]], [deposit["transaction_id"]], expected, actual, "")
    else:
        record("PROBLEM", [res["reservation_id"]], [deposit["transaction_id"]], expected, actual,
               f"Matched by channel and date, but the amount is off by {actual - expected}. "
               "Small enough to be rounding, but the data can't prove that. Check the "
               "channel's payout report before closing it.")


def guest_hint(deposit, reservations):
    """If a guest's last name appears in the description, point at their booking."""
    words = deposit["description"].upper().split()
    for res in reservations:
        last_name = res["guest_name"].split()[-1].upper()
        if last_name not in words:
            continue
        position = words.index(last_name)
        bank_initial = words[position - 1][0] if position > 0 else "?"
        guest_initial = res["guest_name"][0].upper()
        if bank_initial == guest_initial:
            initials = "The first initial matches too."
        else:
            initials = (f"The first initial differs (bank shows {bank_initial}, "
                        f"booking shows {guest_initial}), so it may be a relative or someone else.")
        return (f"The description mentions {last_name.title()}: guest {res['guest_name']} "
                f"stayed in {res['reservation_id']} ({res['listing_name']}). {initials} "
                "No rule covers payments outside the booking channels.")
    return ""
