"""
Which year a listing card's bare day-and-month means.

The card writes "Sun 31 Jan" and no year. Reading that as the current year is right
for ten months of the calendar and silently wrong for the other two: seen in
September it stored five 2027 shows as 2026, where the calendar scored them on the
wrong dates and the list tagged them PAST.

The weekday in front of the date is what settles it. 31 Jan is a Sunday in 2027 and a
Saturday in 2026, so the card can only mean one of them, and every case below is a
real label from Platinumlist or an edge the parser has to survive.

Run: python tests/test_scrape_dates.py
"""
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import scrape  # noqa: E402

# A Tuesday, and the day the wrong years were noticed.
TODAY = date(2026, 9, 8)

CASES = [
    # The five that were actually wrong, with the labels the site serves.
    ("Sun 31 Jan", None, ("2027-01-31", None), "next January, not the one just gone"),
    ("Sat 9 Jan", None, ("2027-01-09", None), "9 Jan is a Saturday in 2027"),
    ("Sat 23 Jan", None, ("2027-01-23", None), "and 23 Jan too"),
    ("Sat 6 Feb", None, ("2027-02-06", None), "February the same way"),
    # The ordinary case, which must not move.
    ("Fri 25 Sep", None, ("2026-09-25", None), "later this year stays this year"),
    ("Tue 8 Sep", None, ("2026-09-08", None), "a show today has not passed"),
    ("Fri 25 Sep - Sun 27 Sep", None, ("2026-09-25", "2026-09-27"), "a range"),
    ("Sat 26 Dec - Sat 2 Jan", None, ("2026-12-26", "2027-01-02"),
     "a range that crosses the new year"),
    # Degraded input.
    ("31 Jan", None, ("2027-01-31", None), "no weekday: the first year not yet over"),
    ("Wed 31 Jan", None, ("2027-01-31", None),
     "a weekday matching no plausible year is ignored, not chased into 2029"),
    ("Sun 29 Feb", None, ("2028-02-29", None), "29 Feb only exists in a leap year"),
    ("", None, (None, None), "no label"),
    ("Sold out", None, (None, None), "a label with no date in it"),
    # The detail page's own timestamp carries a year and outranks all of this.
    ("Sun 31 Jan", 2027, ("2027-01-31", None), "a year hint wins"),
    ("Sun 31 Jan", 2028, ("2028-01-31", None), "even when it disagrees with the weekday"),
]

checks, failures = 0, []


def check(name, ok, detail=""):
    global checks
    checks += 1
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def main():
    print("card dates")
    for label, hint, want, why in CASES:
        got = scrape.parse_card_dates(label, hint, today=TODAY, log=lambda *a: None)
        check(f"{label!r}: {why}", got == want,
              "" if got == want else f"got {got}, want {want}")

    print("\nthe rule itself")
    # Stated separately from the cases, because this is the property that was missing
    # rather than any one date: nothing a ticket site is selling has already happened.
    ahead = all(
        scrape.parse_card_dates(f"{d} {m}", None, today=TODAY, log=lambda *a: None)[0]
        >= TODAY.isoformat()
        for m in ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
        for d in (1, 8, 15, 28))
    check("no bare day-and-month is ever read as a date in the past", ahead)

    # The weekday is the evidence, so where the site states one that can be true of a
    # date still to come, the answer has to be that date and not merely the nearest
    # one. Only future days are asked: a label like "Thu 15 Jan", which was a Thursday
    # in 2026 and is no weekday in reach now, is impossible rather than ambiguous, and
    # falling back is the right answer there.
    wrong = []
    for y in (2027, 2028):
        for m in range(1, 13):
            want = date(y, m, 15)
            label = f"{want.strftime('%a')} 15 {want.strftime('%b')}"
            got = scrape.parse_card_dates(label, None, today=TODAY,
                                          log=lambda *a: None)[0]
            if got != want.isoformat():
                wrong.append(f"{label} -> {got}, want {want.isoformat()}")
    check("a weekday that names a real future date is always honoured",
          not wrong, "; ".join(wrong[:3]))

    print(f"\n{checks - len(failures)}/{checks} checks passed")
    for f in failures:
        print(f"  FAILED: {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
