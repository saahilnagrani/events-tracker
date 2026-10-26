"""
Two things the scraper reads off a page, both of which were quietly wrong.

Which year a listing card's bare day-and-month means, and who is on.

The card writes "Sun 31 Jan" and no year. Reading that as the current year is right
for ten months of the calendar and silently wrong for the other two: seen in
September it stored five 2027 shows as 2026, where the calendar scored them on the
wrong dates and the list tagged them PAST.

The weekday in front of the date is what settles it. 31 Jan is a Sunday in 2027 and a
Saturday in 2026, so the card can only mean one of them, and every case below is a
real label from Platinumlist or an edge the parser has to survive.

Run: python tests/test_scrape.py
"""
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from selectolax.lexbor import LexborHTMLParser as HTMLParser  # noqa: E402

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

    print("\nwho is on")
    # The names come from the block the detail page publishes. Before this, the artist
    # field held whatever matched a curated list of desi stand-up acts, so Trevor Noah
    # at Dubai Opera was nameless and could not be picked out of the artist filter,
    # along with seven in ten of the rest.
    #
    # The markup is the site's own, reduced to the parts that matter: the block is
    # emitted twice per page, once per breakpoint, which is where the repeats come from.
    page = """
      <div class="artist-block"><div class="artist-block__list">
        <div class="artist-block__item"><div class="artist-block__item-inner">
          Trevor   Noah </div></div>
        <div class="artist-block__item"><div class="artist-block__item-inner">
          Alexander Merkul / \u0410\u043b\u0435\u043a\u0441\u0430\u043d\u0434\u0440 </div></div>
      </div></div>
      <div class="artist-block artist-block--mobile"><div class="artist-block__list">
        <div class="artist-block__item"><div class="artist-block__item-inner">
          Trevor Noah </div></div>
        <div class="artist-block__item"><div class="artist-block__item-inner">
          Alexander Merkul / \u0410\u043b\u0435\u043a\u0441\u0430\u043d\u0434\u0440 </div></div>
      </div></div>"""
    got = scrape.parse_artists(HTMLParser(page))
    check("both acts are read off the page", got == ["Trevor Noah", "Alexander Merkul"],
          str(got))
    check("the block being rendered twice does not double the bill", len(got) == 2)
    check("whitespace inside a name is collapsed", "Trevor Noah" in got)
    check("a name given in two scripts keeps the first", "Alexander Merkul" in got)
    check("a page with no block names nobody, rather than guessing",
          scrape.parse_artists(HTMLParser("<div><h1>The Laughter Factory</h1></div>")) == [])
    check("an empty block is not an empty name",
          scrape.parse_artists(HTMLParser(
              '<div class="artist-block__item-inner">  </div>')) == [])

    print("\none row per show")
    # Platinumlist is moving listings from /event-tickets/<id>/<long-slug> to
    # /event-tickets/<short-slug>. The crawl finds the new URL and loses the old one,
    # which is retained as delisted, so the same show appeared twice: once wearing NEW
    # and once wearing "no longer listed". Fourteen had built up before this existed.
    OLD = "https://dubai.platinumlist.net/event-tickets/108618/sitar-for-mental-health-x"
    NEW = "https://dubai.platinumlist.net/event-tickets/sitar-for-mental-health"
    def rows():
        return [
            {"event": "Sitar", "start": "2027-01-30", "url": OLD, "listed": False,
             "first_seen": "2026-10-08", "last_seen": "2026-10-08"},
            {"event": "Sitar", "start": "2027-01-30", "url": NEW, "listed": True,
             "first_seen": "2026-10-09", "last_seen": "2026-10-09"},
        ]
    kept, moved = scrape.merge_moved(rows(), {NEW: 108618}, log=lambda *a: None)
    check("a listing that moved URL is one row, not two", len(kept) == 1, str(len(kept)))
    check("and the row kept is the one still on sale",
          kept and kept[0]["url"] == NEW and kept[0]["listed"] is True)
    # first_seen cannot be recovered once lost, and the show entered the market on the
    # earlier date whatever the URL says.
    check("the survivor inherits the earlier first_seen",
          kept and kept[0]["first_seen"] == "2026-10-08", kept[0]["first_seen"])
    check("and the later last_seen",
          kept and kept[0]["last_seen"] == "2026-10-09", kept[0]["last_seen"])
    check("the old URL is reported so it can be deleted",
          len(moved) == 1 and moved[0]["url"] == OLD and moved[0]["moved_to"] == NEW)

    # The retained row is never refetched, so its id comes from its own URL. The live
    # row's id has to come from its page, because the new URL shape has none in it.
    # Without that page id there is nothing to match on, and nothing is merged: a
    # title that looks similar is not evidence that two listings are one show.
    kept2, moved2 = scrape.merge_moved(rows(), {}, log=lambda *a: None)
    check("with no page id there is nothing to match on, and nothing is merged",
          len(kept2) == 2 and not moved2, f"{len(kept2)} rows")
    # Which is the case a real run is always in: the live listing was just fetched.
    kept3, moved3 = scrape.merge_moved(rows(), {NEW: 108618}, log=lambda *a: None)
    check("and a real run always has it, because the live listing was just fetched",
          len(kept3) == 1 and len(moved3) == 1)

    two = [{"event": "A", "start": "2027-01-30", "url": OLD, "listed": True},
           {"event": "B", "start": "2027-01-30",
            "url": "https://dubai.platinumlist.net/event-tickets/108999/b", "listed": True}]
    check("two different shows are left alone",
          len(scrape.merge_moved(two, {}, log=lambda *a: None)[0]) == 2)
    none = [{"event": "A", "start": "2027-01-30", "listed": True,
             "url": "https://dubai.platinumlist.net/event-tickets/slug-one"},
            {"event": "B", "start": "2027-01-30", "listed": True,
             "url": "https://dubai.platinumlist.net/event-tickets/slug-two"}]
    check("rows with no id anywhere are never merged on a guess",
          len(scrape.merge_moved(none, {}, log=lambda *a: None)[0]) == 2)

    # Both rows delisted is the case the id cannot reach: neither is fetched any more,
    # so the slug URL never yields a page id. Matched on the full fingerprint instead.
    def gone_pair(**over):
        a = {"event": "Soul Cruise", "start": "2026-10-10", "end": None,
             "venue": "QE2", "time": "19:00", "listed": False,
             "first_seen": "2026-09-09", "last_seen": "2026-09-13",
             "url": "https://dubai.platinumlist.net/event-tickets/108245/soul-cruise-qe2"}
        b = dict(a, url="https://dubai.platinumlist.net/event-tickets/the-soul-cruise",
                 first_seen="2026-09-14", last_seen="2026-09-23")
        b.update(over)
        return [a, b]

    kept4, moved4 = scrape.merge_moved(gone_pair(), {}, log=lambda *a: None)
    check("two delisted rows for one show collapse on the full fingerprint",
          len(kept4) == 1 and len(moved4) == 1, f"{len(kept4)} rows")
    check("the slug URL survives, carrying the earliest first_seen",
          kept4 and kept4[0]["url"].endswith("the-soul-cruise")
          and kept4[0]["first_seen"] == "2026-09-09", str(kept4[0].get("first_seen")))
    # Two sittings of the same show on one night differ by the time, and a promoter
    # does list those separately. Merging them would delete a real event.
    check("a different start time is a different show, and is kept",
          len(scrape.merge_moved(gone_pair(time="22:00"), {},
                                 log=lambda *a: None)[0]) == 2)
    check("a different venue likewise",
          len(scrape.merge_moved(gone_pair(venue="Other Hall"), {},
                                 log=lambda *a: None)[0]) == 2)
    # Two slug URLs, or two id URLs, is not the shape a move leaves behind.
    both_slugs = gone_pair()
    both_slugs[0]["url"] = "https://dubai.platinumlist.net/event-tickets/soul-cruise-a"
    check("two slug URLs are not treated as a move",
          len(scrape.merge_moved(both_slugs, {}, log=lambda *a: None)[0]) == 2)
    # And a live row is never reached by this pass; it has a page id to match on.
    check("a pair with one still on sale is left to the id, not the fingerprint",
          len(scrape.merge_moved(gone_pair(listed=True), {},
                                 log=lambda *a: None)[0]) == 2)

    print("\nwhether a listing repeats")
    # The note is carried forward in the stored row and markers are only ever added,
    # so one written under the old rule would outlive the rule itself.
    N = scrape.RECUR_NOTE
    check("a stale note is taken back out before it is reconsidered",
          scrape.drop_note("Flash sale; " + N + "; Sold out", N)
          == "Flash sale; Sold out")
    check("and removing the only note leaves nothing, not a stray semicolon",
          scrape.drop_note(N, N) == "")
    check("a note that was never there is left alone",
          scrape.drop_note("Flash sale", N) == "Flash sale")

    # Inferred from the URL until Platinumlist started giving every listing a slug,
    # which put "Recurring series" on a one-night arena show.
    for text, want, why in [
        ("Join us every Wednesday at the club", True, "every Wednesday"),
        ("A weekly comedy night", True, "weekly"),
        ("Back each Thursday", True, "each Thursday"),
        ("One night only at Coca-Cola Arena", False, "a one-off says nothing"),
        ("IIFA Awards 2027, the biggest night in Indian cinema", False, "nor does this"),
        ("", False, "no description at all"),
    ]:
        check(f"{why}", scrape.is_recurring(text) is want, repr(text[:40]))

    print(f"\n{checks - len(failures)}/{checks} checks passed")
    for f in failures:
        print(f"  FAILED: {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
