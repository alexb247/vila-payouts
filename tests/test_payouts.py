"""Tests for the owner payout math. Run from the project folder: python3 -m unittest"""

import unittest
from decimal import Decimal

from payouts import build_statements, channel_fee, load_csv, owner_payout

RESERVATIONS = load_csv("data/reservations.csv")
AGREEMENTS = load_csv("data/owner_agreements.csv")
RES = {r["reservation_id"]: r for r in RESERVATIONS}
AGREEMENT = {a["listing_id"]: a for a in AGREEMENTS}


def payout_for(res_id):
    res = RES[res_id]
    return owner_payout(res, AGREEMENT[res["listing_id"]])


class OwnerPayoutTests(unittest.TestCase):

    def test_airbnb_cleaning_kept_by_company(self):
        # Worked by hand: 820 - 29.10 = 790.90; 20% = 158.18; 790.90 - 158.18 = 632.72
        line = payout_for("R1006")
        self.assertEqual(line["commission"], Decimal("158.18"))
        self.assertEqual(line["cleaning_to_owner"], Decimal("0"))
        self.assertEqual(line["payout"], Decimal("632.72"))

    def test_vrbo_cleaning_kept_by_owner(self):
        # 1360 - 124.80 = 1235.20; 25% = 308.80; 926.40 + 200 cleaning = 1126.40
        self.assertEqual(payout_for("R1002")["payout"], Decimal("1126.40"))

    def test_direct_fee_is_2_9_percent_plus_30_cents_of_total_charged(self):
        # Total charged 290 + 95 + 53.90 = 438.90; 2.9% = 12.7281; + 0.30 = 13.0281 -> 13.03
        self.assertEqual(channel_fee(RES["R1003"]), Decimal("13.03"))

    def test_commission_rounds_half_cent_up(self):
        # R1008: 25% x (1320 - 50.55) = 317.3625 -> 317.36
        self.assertEqual(payout_for("R1008")["commission"], Decimal("317.36"))

    def test_refund_comes_off_gross_rental(self):
        # R1014: 620 - 155 refund = 465 gross; the owner only earns on what was kept
        line = payout_for("R1014")
        self.assertEqual(line["gross_rental"], Decimal("465.00"))
        self.assertEqual(line["payout"], Decimal("367.52"))

    def test_cancelled_and_fully_refunded_pays_nothing(self):
        self.assertEqual(payout_for("R1010")["payout"], Decimal("0"))

    def test_taxes_never_reach_the_owner(self):
        # Same booking with taxes zeroed out must pay the owner the same,
        # except for direct bookings, where taxes raise the card fee.
        res = dict(RES["R1006"], taxes_collected="0.00")
        self.assertEqual(owner_payout(res, AGREEMENT["L001"])["payout"],
                         payout_for("R1006")["payout"])


class StatementTests(unittest.TestCase):

    def setUp(self):
        self.statements, self.no_agreement = build_statements(
            RESERVATIONS, AGREEMENTS, 2026, 9, missing_deposits={"R1016"})

    def test_stay_belongs_to_the_month_it_checks_out(self):
        becker_ids = {l["reservation_id"] for l in self.statements["Tom & Lisa Becker"]["lines"]}
        self.assertNotIn("R1022", becker_ids)   # checks out Oct 4
        dana_ids = {l["reservation_id"] for l in self.statements["Dana Whitfield"]["lines"]}
        self.assertIn("R1001", dana_ids)        # checked in Aug 28, out Sep 3

    def test_owner_with_two_listings_gets_one_statement(self):
        listings = {l["listing_name"] for l in self.statements["Dana Whitfield"]["lines"]}
        self.assertEqual(listings, {"Silver Lake Bungalow", "Los Feliz Craftsman"})

    def test_listing_without_owner_agreement_is_not_paid(self):
        self.assertEqual({r["reservation_id"] for r in self.no_agreement}, {"R1007", "R1017"})

    def test_held_line_is_shown_but_not_paid_now(self):
        becker = self.statements["Tom & Lisa Becker"]
        self.assertEqual(becker["total_earned"], Decimal("1582.72"))
        self.assertEqual(becker["total_held"], Decimal("657.89"))
        self.assertEqual(becker["pay_now"], Decimal("924.83"))

    def test_owner_totals(self):
        totals = {owner: st["total_earned"] for owner, st in self.statements.items()}
        self.assertEqual(totals, {
            "Dana Whitfield": Decimal("4819.71"),
            "Marcus Oyelaran": Decimal("4691.94"),
            "Priya Raman": Decimal("1811.30"),
            "Tom & Lisa Becker": Decimal("1582.72"),
        })


if __name__ == "__main__":
    unittest.main()
