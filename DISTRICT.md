# Adding District as a second source

Platinumlist is not the whole market. Some shows sell only through District, some only
through Platinumlist, and some through both. A tracker that watches one of them reports
a market that does not exist: it misses the exclusives, and it cannot tell you that a
date is contested when the competing show is on the other platform.

This is the plan for fixing that. It is written before anyone has looked at District's
markup, because the hard part is not the scraping.

## The hard part is identity, not scraping

A second scraper is a day's work. The question that decides whether this is worth doing
is what happens when the same show appears twice.

Right now an event **is** its Platinumlist URL. That is the primary key of the `events`
table, it is what the upsert merges on, it is what `first_seen` hangs off, and it is
what `src/changes.py` diffs. Every one of those breaks the moment a show can arrive from
two places, and they break quietly:

- **The calendar double-counts.** `src/viability.py` scores a date by how much is on
  that night. One show listed on both platforms would arrive as two events, and a date
  with one comedy night would be scored as a date with two. The tracker would start
  advising against dates that are actually free. This is the failure that matters:
  it is silent, it looks like data, and the whole point of the calendar is that number.
- **"New today" fires twice.** The same show would light up the NEW badge again on the
  day it appears on the second platform.
- **The event list shows duplicates**, and the artist facet counts the act twice.
- **Delisting becomes ambiguous.** A show that comes off Platinumlist but is still
  selling on District is not gone. Today `listed = false` means "gone".

So the work is: decide what an event is, then make everything downstream agree.

## Phase 0 — reconnaissance

Nothing below can be specified until someone has read District's pages. This cannot be
done from the Claude sandbox: the environment's network policy answers 403 to
`district.ae`, and only Platinumlist is allowlisted. A GitHub Actions runner has open
internet, so the scraper itself will be fine in CI; it is the exploring that has to
happen elsewhere — a local checkout, or by adding the host to the environment.

What to find out, in the order it matters:

1. **Is there a JSON API?** Open the site with the network tab recording. Most modern
   ticketing sites are a React or Next front end talking to an API, and if District is
   one of those, the API is a far better target than the HTML: no selectors to break, a
   real event id, and usually the price and the date already parsed. Check for
   `__NEXT_DATA__` in the page source too, which is the whole page's data as JSON.
2. **Is the listing server-rendered or client-rendered?** If the events only appear
   after JavaScript runs, `requests` plus `selectolax` will not see them and the scraper
   needs Playwright, which is already a dependency but makes every run slower.
3. **What identifies an event?** A numeric id in the URL is ideal. A slug is workable.
4. **Does a detail page carry the year?** Platinumlist's listing cards do not, which
   cost us five wrong dates (see `pick_year` in `src/scrape.py`). Assume District has
   its own version of this problem until proven otherwise.
5. **The equivalents of what we already read**: venue, start time and whether it is
   doors or curtain, the "from" price and whether it includes fees, and any artists
   block. Platinumlist publishes one and reading it took the artist filter from 22
   names to 62.
6. **robots.txt and terms.** Platinumlist is scraped politely: one session, a delay
   between requests, a detail-page cache so a run fetches only what changed. District
   gets the same treatment, and if robots.txt asks us not to, that is a decision for
   you rather than something to route around.

Write the answers into this file before writing any code. A scraper built on a guess
about which of those is true will be rewritten.

## Phase 1 — make the scraper take a source

`src/scrape.py` is one file that assumes Platinumlist throughout: the listing URLs, the
Queue-it waiting room, the currency cookie, the card parser, the WebEngage date. Split
it so the shape is shared and the site-specific parts are not.

    src/sources/__init__.py      the interface, and the registry
    src/sources/platinumlist.py  everything already in scrape.py
    src/sources/district.py      phase 2
    src/scrape.py                runs every source, merges, checks, writes

A source supplies: a name, a session factory, a list of listings to crawl, a card
parser, a detail parser, and its own quirks. It returns events in the shape the rest of
the pipeline already speaks, plus a `source` field.

Do this refactor **first and on its own**, with the tests passing and no behaviour
change. It is the step most likely to break something quietly, and it is much easier to
see that having not also changed what is being scraped in the same commit.

## Phase 2 — the District scraper

Straightforward once phase 0 is answered. It ends when `python src/scrape.py
--source district --dry-run` prints a plausible event count for both cities and the
existing checks pass on it.

Its own guards, mirroring the Platinumlist ones: a floor on the event count so an empty
scrape refuses to write, and the "still on sale but dated in the past" note that catches
a wrong year.

## Phase 3 — identity and merging

The design decision. Proposal, to be argued with:

**An event is an act, on a date, in a city.** The key is
`slug(artist or title) + start_date + city`. Two listings with the same key are one
event with two places to buy it.

Merging rules, which are the part worth getting right:

| Field | Rule | Why |
|---|---|---|
| `price_from_aed` | lowest across sources | it is the cheapest way in, which is what the number claims to mean |
| `listed` | true if **any** source still lists it | a show selling on District is not gone because Platinumlist dropped it |
| `first_seen` | earliest across sources | when it entered the market, not when the second platform got it |
| `last_seen` | latest across sources | same reasoning |
| `venue`, `time` | prefer the source that publishes a real start time | `time_source` already records `start` vs `doors` vs `anchor` |
| `artist` | union, joined with `"; "` | the facet already splits on this |
| `notes` | keep both, attributed | they disagree, and which one is wrong is worth seeing |

Fuzzy matching is the trap here. Titles differ between platforms ("Gurleen Pannu Live at
Sheikh Rashid Auditorium" against "Gurleen Pannu"), so exact title matching will miss
pairs and aggressive fuzzy matching will merge two genuinely different shows by the same
act on the same night, which does happen with early and late sittings. Start strict:
same date, same city, and one title's act name contained in the other's. Send everything
that nearly matched but did not to the review queue, which already exists for exactly
this kind of "a human should look at this" case. Read that queue for a week before
loosening anything.

## Phase 4 — the schema

The migration, which needs one SQL statement run in Supabase and cannot be undone
casually, so it goes in `supabase/schema.sql` and gets rehearsed on a copy of the data
first.

    create table public.event_sources (
      event_key   text references public.events(key) on delete cascade,
      source      text not null,          -- 'platinumlist' | 'district'
      url         text not null,
      price_from_aed numeric(10,2),
      listed      boolean not null default true,
      first_seen  date not null default current_date,
      last_seen   date,
      primary key (event_key, source)
    );

`events` gains `key` as its primary key and keeps the merged view of each field. `url`
stays on `events` as the preferred link so nothing on the page has to change at once.

Backfill: every existing row is a Platinumlist source row, and its `key` is computed
from what it already holds. `first_seen` must survive this exactly — it cannot be
recovered if it is lost, which is why `src/publish.py` already clamps it to the earlier
of stored and incoming.

## Phase 5 — the page

- **Say where a show is selling.** Two small platform tags on the row, each a link. A
  show on both is the interesting case: it is the one where you can compare prices.
- **A source facet**, alongside month, artist, category and language, so "what is
  District-exclusive" is one click. That is the question this whole exercise exists to
  answer.
- **The calendar needs no change** if phase 3 is right, which is the test of phase 3.

## Phase 6 — what has to be true before this ships

- `tests/test_scrape.py` covers District's date and artist parsing the way it covers
  Platinumlist's.
- A merge test with real pairs: a known dual-listed show merges, two sittings by the
  same act on the same night do **not**, and an exclusive on either side survives alone.
- A check that no date's viability score changed because of a duplicate. Score the
  calendar before and after the merge lands and diff it. Any date that moved is either a
  bug or a genuinely contested date that we were blind to, and both need looking at.
- The daily run reports per-source counts, so a source silently returning nothing is
  visible rather than looking like a quiet week.

## What this is worth

The exclusives are the reason to do it. A tracker that says a Saturday is clear when
there is a 1,500-seat comedy night selling on District is worse than no tracker, because
it is confidently wrong on the only question it is asked. Everything else here —
deduplication, price comparison, a source facet — follows from wanting that one answer
to be right.

## Status

Nothing built. Phase 0 not started, and blocked on network access to district.ae from
wherever the recon happens.
