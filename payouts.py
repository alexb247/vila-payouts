"""
Owner payouts: how much each homeowner is owed for a month.

For every reservation:
    gross rental   = accommodation_total - refund_amount
    channel fee    = platform_fee (Airbnb, Vrbo) or 2.9% + $0.30 of the total charged (direct)
    commission     = commission_pct x (gross rental - channel fee)
    owner payout   = gross rental - channel fee - commission
                     + cleaning fee, only if the owner keeps it
Taxes are never the owner's money.

Money is handled with Decimal, not float, because float can't store
amounts like 0.10 exactly and the cents would drift.
"""

import csv
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")
DIRECT_FEE_RATE = Decimal("0.029")
DIRECT_FEE_FIXED = Decimal("0.30")


# ---------- small helpers ----------

def money(text):
    """Turn a CSV cell like '42.30' (or an empty cell) into a Decimal."""
    return Decimal(text) if text and text.strip() else Decimal("0")


def round_cents(amount):
    """Round to the nearest cent, with halves rounding up (0.005 -> 0.01)."""
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def to_date(text):
    return date.fromisoformat(text)


def load_csv(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def check_data(reservations):
    """Sanity checks on the export. Returns a list of warnings (empty if all good)."""
    warnings = []
    for res in reservations:
        nights = (to_date(res["check_out"]) - to_date(res["check_in"])).days
        if nights != int(res["nights"]):
            warnings.append(f"{res['reservation_id']}: dates give {nights} nights, "
                            f"nights column says {res['nights']}")
        if money(res["nightly_rate"]) * int(res["nights"]) != money(res["accommodation_total"]):
            warnings.append(f"{res['reservation_id']}: nightly_rate x nights doesn't equal "
                            "accommodation_total")
    return warnings


# ---------- the business rules ----------

def total_charged(res):
    """Everything the guest paid: rent + cleaning + taxes."""
    return (money(res["accommodation_total"])
            + money(res["cleaning_fee"])
            + money(res["taxes_collected"]))


def channel_fee(res):
    """The booking channel's cut.

    Airbnb and Vrbo: the platform_fee column.
    Direct (GuestyPay): 2.9% + $0.30 of the total charged, because the
    platform_fee column is blank for direct bookings.
    """
    if res["channel"] == "direct":
        return round_cents(total_charged(res) * DIRECT_FEE_RATE + DIRECT_FEE_FIXED)
    return money(res["platform_fee"])


def owner_payout(res, agreement):
    """Return the line-by-line payout breakdown for one reservation."""
    gross = money(res["accommodation_total"]) - money(res["refund_amount"])
    fee = channel_fee(res)
    commission = round_cents(Decimal(agreement["commission_pct"]) * (gross - fee))

    if agreement["cleaning_fee_to"] == "owner":
        cleaning = money(res["cleaning_fee"])
    else:
        cleaning = Decimal("0.00")

    return {
        "gross_rental": gross,
        "channel_fee": fee,
        "commission": commission,
        "cleaning_to_owner": cleaning,
        "payout": gross - fee - commission + cleaning,
    }


def in_statement_month(res, year, month):
    """A reservation belongs to the month its stay ENDS (check-out).

    Owners are paid for completed stays. A stay that checks out next month
    waits for next month's statement, in case it is cancelled or refunded.
    """
    check_out = to_date(res["check_out"])
    return check_out.year == year and check_out.month == month


# ---------- building the statements ----------

def build_statements(reservations, agreements, year, month, missing_deposits=()):
    """Group payouts by owner for one month.

    missing_deposits: reservation IDs whose money hasn't reached the bank.
    Those lines are shown but held, and left out of "pay now".

    Returns (statements, no_agreement), where no_agreement lists
    reservations that can't be paid because their listing has no owner on file.
    """
    agreements_by_listing = {a["listing_id"]: a for a in agreements}
    statements = {}
    no_agreement = []

    for res in reservations:
        if not in_statement_month(res, year, month):
            continue

        agreement = agreements_by_listing.get(res["listing_id"])
        if agreement is None:
            no_agreement.append(res)
            continue

        line = owner_payout(res, agreement)
        line["reservation_id"] = res["reservation_id"]
        line["listing_name"] = res["listing_name"]
        line["channel"] = res["channel"]
        line["check_in"] = res["check_in"]
        line["check_out"] = res["check_out"]
        line["note"] = ""
        line["held"] = False
        if res["status"] == "cancelled":
            line["note"] = "Cancelled and refunded"
        if res["reservation_id"] in missing_deposits:
            line["held"] = True
            line["note"] = "Held: guest payment not yet received in bank"

        owner = agreement["owner_name"]
        if owner not in statements:
            statements[owner] = {"owner_email": agreement["owner_email"], "lines": []}
        statements[owner]["lines"].append(line)

    for statement in statements.values():
        lines = statement["lines"]
        statement["total_earned"] = sum((l["payout"] for l in lines), Decimal("0"))
        statement["total_held"] = sum((l["payout"] for l in lines if l["held"]), Decimal("0"))
        statement["pay_now"] = statement["total_earned"] - statement["total_held"]

    return statements, no_agreement
