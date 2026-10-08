# Homeowner Payout Reconciliation

Takes September 2026's reservations, bank deposits, and owner agreements and produces:

1. **Owner statements**: what each homeowner is owed, line by line per reservation.
2. **A reconciliation**: every bank deposit matched to the reservation(s) it pays for, and a list of everything that doesn't line up, each with a reason.

Plain Python 3 (standard library only), nothing to install.

## How to run

```bash
python3 main.py                 # both reports
python3 main.py statements      # owner statements only
python3 main.py reconcile       # reconciliation only
python3 -m unittest -v          # run the tests
```

Reports print to the terminal and are saved as CSVs in `output/`. Input files live in `data/`; use `--data` to point at another folder and `--month 2026-10` to run a different month.

## How it works

**Owner payout** (`payouts.py`), per reservation, following the brief:

```
gross rental = accommodation_total - refund_amount
channel fee  = platform_fee (Airbnb, Vrbo)
               2.9% + $0.30 of the total charged (direct / GuestyPay)
commission   = commission_pct x (gross rental - channel fee)
payout       = gross rental - channel fee - commission
               + cleaning fee, only if cleaning_fee_to = owner
```

**Reconciliation** (`reconcile.py`). First I work out what *should* have reached the bank for each reservation and when, then look for it:

| Channel | Expected deposit | Expected date |
|---|---|---|
| Airbnb | rent - refund + cleaning - fee (Airbnb remits taxes) | day after check-in |
| Vrbo | rent - refund + cleaning + taxes - fee | day after check-out |
| Direct | rent - refund + cleaning + taxes - processing fee | Monday after check-in, in a weekly batch |

Airbnb deposits don't name the reservation, so they are matched on three clues together: channel, date, and amount. Matching runs from most certain to least certain, and each deposit and reservation is used once:

1. Flag exact duplicate bank lines.
2. Cancelled, fully refunded bookings expect no money.
3. Vrbo: match on the confirmation code in the bank description.
4. GuestyPay: compare each Monday batch to the sum of that week's direct bookings.
5. Airbnb refunds: original payout + negative adjustment must equal the booking's value after the refund.
6. Airbnb one-to-one, exact amount; if two bookings fit, the closer expected date wins.
7. Airbnb combined payouts: two bookings paid in one deposit.
8. Airbnb near-misses: right channel and date, amount within $1.00, matched but flagged.
9. Anything left is a problem: deposits nobody can explain, bookings whose money never came.

## September results

**Owner totals**

| Owner | Earned | Held | Pay now |
|---|---|---|---|
| Dana Whitfield (Silver Lake Bungalow, Los Feliz Craftsman) | 4,819.71 | | 4,819.71 |
| Marcus Oyelaran (Venice Canal Loft) | 4,691.94 | | 4,691.94 |
| Priya Raman (Echo Park Studio) | 1,811.30 | | 1,811.30 |
| Tom & Lisa Becker (Mar Vista Cottage) | 1,582.72 | 657.89 | 924.83 |

**Needs attention**

| Item | What's wrong | Suggested action |
|---|---|---|
| TXN50170, 1,552.00 | Same date, amount, and payout code as TXN50153 (R1013). | Counted once. Check the bank statement to see whether the money really arrived twice; if so, expect Airbnb to take it back. |
| R1016, Vrbo, Mar Vista | Expected 932.80 around Sep 23; not in the bank feed. | Check the Vrbo payout. Becker's line is held until it arrives. |
| TXN50391, 450.00 Zelle "K OKAFOR" | No reservation. Guest B. Okafor stayed in R1006, but the initial differs. | Not owner money until identified. Ask the guest-relations team. |
| R1018, Airbnb, Silver Lake | Deposit 785.72 vs. expected 785.70. | Matched. Check Airbnb's payout report for the 2 cents. |
| L006 Highland Park Casita (R1007, R1017) | Money arrived for both, but there's no owner agreement for this listing. | Not paid. Add the agreement, then pay it out. |

**Matched, with a note**: R1004 + R1005 in one Airbnb deposit; R1014's refund came back as a separate negative deposit (the pair reconciles exactly); R1001's money arrived in August and R1022's arrived in September for a stay ending in October (see assumption 1).

## Assumptions

Where the brief didn't cover a case, I made these calls:

1. **A reservation belongs to the month it checks out.** Owners are paid for completed stays, so a booking can't be paid twice across months and a stay that's still going can still be cancelled or refunded. R1001 (Aug 28 to Sep 3) is on September's statement; R1022 (Sep 27 to Oct 4) will be on October's, even though its money arrived in September.
2. **Owners are paid only for money that has actually arrived.** R1016's Vrbo payout is missing, so the line is shown but held and left out of "pay now."
3. **No owner agreement, no payout.** L006 isn't in owner_agreements.csv, so I don't guess a commission. Its reservations are listed as unpaid.
4. **The owner pays the full channel fee in every channel**, as the brief says. That's consistent: Airbnb's and Vrbo's fees are charged on rent plus cleaning, and the owner pays them even when the company keeps the cleaning fee. The one difference for direct bookings is that the card fee (2.9% + $0.30) is also charged on taxes, which were never the owner's money. Across September's five direct bookings that's about $14.94. I followed the brief, but it's worth confirming that's intended.
5. **Rounding**: money uses `Decimal`, never float. Channel fee and commission are rounded to the cent (half up) per reservation, and totals are sums of rounded lines.
6. **Refunds** reduce gross rental and the expected deposit. A cancelled booking refunded in full shows as a $0.00 line.
7. **Timing tolerance**: a deposit can land up to 2 days either side of its expected date. Direct bookings settle on the Monday after check-in (a Monday check-in goes to the next Monday; none in this data).
8. **Amount tolerance**: an Airbnb deposit within $1.00 of the expected amount, on the right date, is matched but flagged.
9. **Duplicates**: two bank lines with the same date, amount, and description are one deposit counted twice. Safe here because Airbnb's description carries a unique payout code.
10. **Money from outside the channels** (the Zelle payment) is never treated as owner income until someone identifies it.

The program also checks the export itself: for every reservation, check-out minus check-in must equal `nights`, and `nightly_rate x nights` must equal `accommodation_total`. All 24 pass.

## What I'd do with more time

- **Read from the source systems instead of CSV exports**: Guesty's reservations API and the bank feed, on a schedule.
- **Use Airbnb's own payout report**, which lists the reservations in each payout, instead of inferring from date and amount. That removes the guesswork in steps 6 to 8.
- **Send the problems list to the people who fix them**, as a daily email or Slack message to accounting.
- **Carry open items forward**, so R1016 clears itself when the Vrbo money lands in October and the duplicate is closed once the bank confirms.
- **Per-owner rules** in owner_agreements (who pays the card fee, minimum fees) and nights-based proration across months if that's how the business prefers to allocate.
- **Handle refunds after payout for Vrbo and direct**, not only Airbnb (none in this data).

## Files

```
main.py           command line: runs the reports, prints them, saves CSVs
payouts.py        owner payout rules and statements
reconcile.py      expected deposits, matching, and reasons
tests/            26 unit tests (python3 -m unittest -v)
data/             the three input CSVs
AI_use.txt        how I used AI on this project
```
