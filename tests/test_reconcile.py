"""Tests for matching bank deposits to reservations. Run: python3 -m unittest"""

import unittest
from datetime import date

from payouts import load_csv
from reconcile import expected_date, reconcile

RESERVATIONS = load_csv("data/reservations.csv")
DEPOSITS = load_csv("data/bank_deposits.csv")
AGREEMENTS = load_csv("data/owner_agreements.csv")
RES = {r["reservation_id"]: r for r in RESERVATIONS}


def row_for(results, reservations=None, transactions=None):
    for row in results:
        if reservations is not None and row["reservations"] == reservations:
            return row
        if transactions is not None and row["transactions"] == transactions:
            return row
    return None


class RealDataTests(unittest.TestCase):
    """Run the full reconciliation once on the September files."""

    @classmethod
    def setUpClass(cls):
        cls.results = reconcile(RESERVATIONS, DEPOSITS, AGREEMENTS)

    def test_exactly_these_four_need_attention(self):
        problems = {(r["reservations"], r["transactions"])
                    for r in self.results if r["status"] == "PROBLEM"}
        self.assertEqual(problems, {
            ("", "TXN50170"),        # duplicate deposit
            ("R1018", "TXN50238"),   # off by 2 cents
            ("", "TXN50391"),        # unidentified Zelle payment
            ("R1016", ""),           # Vrbo payout never arrived
        })

    def test_every_deposit_is_accounted_for_exactly_once(self):
        used = []
        for row in self.results:
            if row["transactions"]:
                used.extend(row["transactions"].split(" + "))
        self.assertEqual(sorted(used), sorted(d["transaction_id"] for d in DEPOSITS))

    def test_every_reservation_is_accounted_for_exactly_once(self):
        used = []
        for row in self.results:
            if row["reservations"]:
                used.extend(row["reservations"].split(" + "))
        self.assertEqual(sorted(used), sorted(RES))

    def test_combined_airbnb_payout(self):
        row = row_for(self.results, transactions="TXN50034")
        self.assertEqual(row["reservations"], "R1004 + R1005")

    def test_same_amount_on_back_to_back_days_matches_by_date(self):
        # R1023 and R1024 both expect 712.95; check-ins are Sep 25 and Sep 26.
        self.assertEqual(row_for(self.results, reservations="R1023")["transactions"], "TXN50306")
        self.assertEqual(row_for(self.results, reservations="R1024")["transactions"], "TXN50323")

    def test_refund_after_payout_reconciles_as_a_pair(self):
        row = row_for(self.results, reservations="R1014")
        self.assertEqual(row["transactions"], "TXN50187 + TXN50374")
        self.assertEqual(row["difference"], 0)

    def test_guestypay_batch_covers_the_whole_week(self):
        self.assertEqual(row_for(self.results, transactions="TXN50136")["reservations"],
                         "R1008 + R1011")

    def test_listing_without_owner_is_flagged_even_though_money_matched(self):
        row = row_for(self.results, reservations="R1007")
        self.assertIn("no owner agreement", row["reason"])


class TimingTests(unittest.TestCase):

    def test_direct_booking_settles_the_following_monday(self):
        self.assertEqual(expected_date(RES["R1003"]), date(2026, 9, 7))   # Wed check-in
        self.assertEqual(expected_date(RES["R1011"]), date(2026, 9, 14))  # Sat check-in

    def test_airbnb_pays_the_day_after_check_in(self):
        self.assertEqual(expected_date(RES["R1006"]), date(2026, 9, 6))

    def test_vrbo_pays_the_day_after_check_out(self):
        self.assertEqual(expected_date(RES["R1002"]), date(2026, 9, 6))


class SmallCaseTests(unittest.TestCase):
    """Hand-made inputs, so each rule is tested on its own."""

    BOOKING = {
        "reservation_id": "T1", "listing_id": "L001", "listing_name": "Test", "channel": "airbnb",
        "channel_confirmation": "HMTEST", "guest_name": "A. Test", "check_in": "2026-09-10",
        "check_out": "2026-09-12", "nights": "2", "nightly_rate": "100.00",
        "accommodation_total": "200.00", "cleaning_fee": "50.00", "taxes_collected": "35.00",
        "taxes_remitted_by": "channel", "platform_fee": "7.50", "status": "confirmed",
        "refund_amount": "0.00",
    }

    def test_missing_deposit_is_a_problem(self):
        results = reconcile([self.BOOKING], [])
        self.assertEqual(results[0]["status"], "PROBLEM")
        self.assertIn("none is in the bank feed", results[0]["reason"])

    def test_deposit_outside_the_date_window_does_not_match(self):
        late = {"transaction_id": "X1", "date": "2026-09-20", "amount": "242.50",
                "description": "AIRBNB PAYMENTS G-TEST"}
        statuses = {r["status"] for r in reconcile([self.BOOKING], [late])}
        self.assertEqual(statuses, {"PROBLEM"})   # both unmatched

    def test_deposit_on_time_and_exact_matches(self):
        on_time = {"transaction_id": "X1", "date": "2026-09-11", "amount": "242.50",
                   "description": "AIRBNB PAYMENTS G-TEST"}
        results = reconcile([self.BOOKING], [on_time])
        self.assertEqual([(r["status"], r["transactions"]) for r in results], [("OK", "X1")])


if __name__ == "__main__":
    unittest.main()
