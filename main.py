"""
Run the September 2026 owner statements and bank reconciliation.

Usage:
    python3 main.py                  # both reports, printed and saved to output/
    python3 main.py statements       # owner statements only
    python3 main.py reconcile        # reconciliation only

Options:
    --data DIR       folder with the three CSVs (default: data)
    --month YYYY-MM  statement month (default: 2026-09)
"""

import argparse
import csv
import os

from payouts import build_statements, check_data, load_csv
from reconcile import reconcile


def main():
    parser = argparse.ArgumentParser(description="Owner payouts and bank reconciliation")
    parser.add_argument("report", nargs="?", default="all",
                        choices=["all", "statements", "reconcile"])
    parser.add_argument("--data", default="data")
    parser.add_argument("--month", default="2026-09")
    args = parser.parse_args()

    year, month = (int(x) for x in args.month.split("-"))
    reservations = load_csv(os.path.join(args.data, "reservations.csv"))
    deposits = load_csv(os.path.join(args.data, "bank_deposits.csv"))
    agreements = load_csv(os.path.join(args.data, "owner_agreements.csv"))

    warnings = check_data(reservations)
    print("\nDATA CHECKS: " + ("all reservations passed" if not warnings else ""))
    for w in warnings:
        print("  WARNING " + w)

    # Reconcile first: a booking whose money never arrived is held on its statement.
    results = reconcile(reservations, deposits, agreements)
    missing = {row["reservations"] for row in results
               if row["status"] == "PROBLEM" and row["actual"] is None}

    os.makedirs("output", exist_ok=True)

    if args.report in ("all", "statements"):
        statements, no_agreement = build_statements(
            reservations, agreements, year, month, missing_deposits=missing)
        print_statements(statements, no_agreement, args.month)
        save_statements(statements, "output/owner_statements.csv")

    if args.report in ("all", "reconcile"):
        print_reconciliation(results)
        save_reconciliation(results, "output/reconciliation.csv")

    print("\nSaved to output/owner_statements.csv and output/reconciliation.csv")


# ---------- printing ----------

def print_statements(statements, no_agreement, month_label):
    print(f"\nOWNER STATEMENTS, {month_label} (stays that checked out this month)")
    print("=" * 78)
    for owner, st in sorted(statements.items()):
        print(f"\n{owner} <{st['owner_email']}>")
        print(f"  {'Reservation':<11} {'Listing':<22} {'Gross':>9} {'Ch. fee':>8} "
              f"{'Comm.':>8} {'Clean':>7} {'Payout':>9}")
        for l in sorted(st["lines"], key=lambda x: (x["listing_name"], x["check_in"])):
            print(f"  {l['reservation_id']:<11} {l['listing_name']:<22} {l['gross_rental']:>9} "
                  f"{l['channel_fee']:>8} {l['commission']:>8} {l['cleaning_to_owner']:>7} "
                  f"{l['payout']:>9}" + (f"  <- {l['note']}" if l["note"] else ""))
        print(f"  {'Total earned':>66} {st['total_earned']:>9}")
        if st["total_held"]:
            print(f"  {'Held until funds arrive':>66} {-st['total_held']:>9}")
        print(f"  {'Pay now':>66} {st['pay_now']:>9}")

    if no_agreement:
        print("\nNOT PAID: no owner agreement on file for these listings")
        for res in no_agreement:
            print(f"  {res['reservation_id']}  {res['listing_id']} {res['listing_name']}")


def print_reconciliation(results):
    print("\nBANK RECONCILIATION")
    print("=" * 78)
    order = {"PROBLEM": 0, "OK (note)": 1, "OK": 2}
    for row in sorted(results, key=lambda r: order[r["status"]]):
        exp = "" if row["expected"] is None else row["expected"]
        act = "" if row["actual"] is None else row["actual"]
        print(f"[{row['status']}] res: {row['reservations'] or '-'}  "
              f"txn: {row['transactions'] or '-'}  expected: {exp}  actual: {act}")
        if row["reason"]:
            print(f"    {row['reason']}")
    problems = sum(1 for r in results if r["status"] == "PROBLEM")
    print(f"\n{problems} item(s) need attention.")


# ---------- saving ----------

def save_statements(statements, path):
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["owner", "reservation_id", "listing_name", "channel", "check_in",
                         "check_out", "gross_rental", "channel_fee", "commission",
                         "cleaning_to_owner", "payout", "held", "note"])
        for owner, st in sorted(statements.items()):
            for l in st["lines"]:
                writer.writerow([owner, l["reservation_id"], l["listing_name"], l["channel"],
                                 l["check_in"], l["check_out"], l["gross_rental"],
                                 l["channel_fee"], l["commission"], l["cleaning_to_owner"],
                                 l["payout"], l["held"], l["note"]])


def save_reconciliation(results, path):
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["status", "reservations", "transactions", "expected", "actual",
                         "difference", "reason"])
        for r in results:
            writer.writerow([r["status"], r["reservations"], r["transactions"], r["expected"],
                             r["actual"], r["difference"], r["reason"]])


if __name__ == "__main__":
    main()
