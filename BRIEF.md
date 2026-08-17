# UAE comedy and desi events tracker: build brief

Handoff for Claude Code. Everything in this package is tested and working. The job is to turn
a set of one-off scripts into a repo that updates itself daily and publishes to GitHub Pages.

---

## What this is for

Saahil organises Indian (English/Hindi) stand-up shows in the UAE. He needs two things kept
current without manual work:

1. **A list** of every comedy and desi event on sale in Dubai and Abu Dhabi, with venue, date,
   time, price, language and a ticket link.
2. **A calendar** that scores each date for whether he could stage his own show on it, based on
   what is already booked, the season, the holidays and the Ramadan pause.

The calendar shows **all** comedy and desi events. The **colour** answers a narrower question:
"could I stage an Indian stand-up show on this date". Keep that distinction visible in the UI,
because a busy night for Arabic comedy is not the same as a blocked night for him.

---

## Decisions already made

| Question | Decision |
|---|---|
| Hosting | GitHub Pages, public, now. Migrate to Cloudflare Pages later for privacy. |
| Migration cost | Near zero. Cloudflare Pages builds from the same GitHub repo; point it at the repo and move DNS. No code changes. |
| Cloudflare free limits | 500 builds/month, 1 concurrent, 20,000 files, 25 MiB/file, 100 custom domains. A daily build uses ~6% of the build quota. |
| Schedule | Daily, 03:00 UTC (07:00 Dubai). |
| Alerts | The workflow writes `docs/changes.json`. A separate scheduled task in the Claude app reads it each morning and only notifies when it is non-empty. Do NOT put SMTP credentials in the repo. |
| Scope | All comedy and desi events, both cities, in both the list and the calendar. |
| Mobile | Mobile-first. Agenda list is the default on small screens; the month grid is secondary. |

---

## Repo shape

```
.
├── data/
│   ├── events.json        # current dataset, 73 verified events (schema below)
│   ├── artists.json       # curated allowlist for classifying Indian stand-up
│   └── config.json        # model weights, blackout windows, holidays
├── src/
│   ├── scrape.py          # TO BUILD: fetch Platinumlist -> data/events.json
│   ├── viability.py       # DONE, tested: events.json -> docs/viability.json
│   ├── build_site.py      # TO BUILD: viability.json -> docs/index.html
│   └── build_xlsx.py      # TO BUILD: viability.json -> docs/events.xlsx
├── docs/                  # GitHub Pages serves this folder
│   ├── index.html
│   ├── viability.json
│   ├── changes.json
│   └── events.xlsx
└── .github/workflows/daily.yml
```

Set Pages to serve from `/docs` on `main`. Give the workflow `permissions: contents: write` so it
can commit its own output.

---

## Task order

Do these in sequence. Do not start task 2 before task 1 is proven.

### 1. Scraper spike (do this first, it is the only real unknown)

Everything in `data/events.json` was gathered through a tool that converts pages to markdown and
has a model read them. **Nobody has looked at Platinumlist's raw HTML.** Before writing the
scraper, find out:

- Is the listing server-rendered, or hydrated by JavaScript? Fetch `https://dubai.platinumlist.net/comedy`
  and look for event titles in the HTML source.
- Is there a JSON endpoint behind the pagination? Strong hint that there is: `?page=2` returns
  new results but `?page=3` silently returns page 1 again, which is what an infinite-scroll API
  looks like when the HTML fallback stops. Check the network tab or search the HTML for an API path.
- Check `robots.txt` and the terms of service. A single daily pass over about eight listing URLs
  is low volume, but confirm it is allowed before scheduling it.

If it is server-rendered, `requests` plus `selectolax` or BeautifulSoup is enough. If it needs
a browser, Playwright works fine on Actions, just budget a slower job and pin the browser version.

**Listing URLs that are known to work:**
```
https://dubai.platinumlist.net/comedy            (and ?page=2)
https://dubai.platinumlist.net/shows/comedy-shows (and ?page=2)
https://dubai.platinumlist.net/desi              (and ?page=2)
https://abu-dhabi.platinumlist.net/comedy
https://abu-dhabi.platinumlist.net/desi
https://abu-dhabi.platinumlist.net/event
```

Note the Abu Dhabi subdomain is `abu-dhabi`, with a hyphen. `abudhabi.platinumlist.net` exists but
302s to an unrelated city based on geo-IP, which will waste an hour if you do not know.

`/comedy` and `/shows/comedy-shows` overlap heavily but each carries exclusives, so union them.

**Detail pages matter.** The listing cards omit the venue for most events. Event pages look like
`https://dubai.platinumlist.net/event-tickets/<numeric-id>/<slug>`, though recurring series use a
slug with no numeric id and those are legitimate. Venue, exact time, language and price all come
from the detail page, so the scraper needs a second pass over each event URL. Cache by event id so
a daily run only fetches pages it has not seen.

### 2. Wire up the model

`src/viability.py` runs as-is: `python src/viability.py`. It reads the three data files and writes
`docs/viability.json`. On the current dataset it produces 231 days scored, 28 prime, 44 good,
48 blocked and 18 direct-clash nights. If your scraper feeds it and those numbers move wildly,
the scraper is wrong, not the model.

### 3. Change detection

After a scrape, diff the new `events.json` against the committed one and write `docs/changes.json`:

```json
{"generated": "2026-08-14",
 "added": [...], "removed": [...], "changed": [...],
 "dates_lost": [{"date": "2026-11-14", "was": "prime", "taken_by": "..."}],
 "review_queue": [...]}
```

`dates_lost` is the field that actually matters. A new show is mildly interesting; a new show that
just took a date scored prime yesterday is the thing worth a notification.

`review_queue` holds listings that look Indian or desi by keyword but match no artist in
`artists.json`. Never silently include or drop those. Surfacing them is how the allowlist grows.

### 4. Site build

`prototype/index.html` is a working desktop-first reference. Read it for the visual language and
the tier colours, then rebuild mobile-first rather than patching it. What to keep:

- Tier colours: prime `#0ca30c`, good `#2a78d6`, blocked `#d03b3b`, low is muted grey.
- **Never colour-only.** Green and red fail colour-blind separation (ΔE 4.1 deutan), so every cell
  carries an icon and a text label, and blocked cells also carry a diagonal hatch. This is not
  decoration, do not strip it.
- Light and dark are both hand-picked, not an auto-invert. Both sets are in the prototype CSS.
- The `data-theme` toggle must beat the OS setting in both directions.

What to change for mobile:

- Agenda list is the default under about 700px: upcoming dates as cards, viability badge, what is on.
- Month grid becomes one month per screen with swipe, not eight side by side.
- Add a web manifest and icons so it installs to the home screen.
- Filters (all / Fri+Sat / prime only) stay, as a sticky row.

**Bug already found and fixed, do not reintroduce it:** the filter buttons had ids like `f-all`.
A hyphenated id is not a valid bare JavaScript identifier, so referencing `f_all` threw a
ReferenceError that killed every handler below it. Use `getElementById` and add a smoke test that
clicks each filter and asserts the dimmed count changes.

### 5. Workflow

```yaml
on:
  schedule: [{cron: "0 3 * * *"}]
  workflow_dispatch:
```

Fail loudly. If the scrape returns fewer than about 60 events, or zero for either city, exit
non-zero and leave the previous `docs/` in place. A quietly empty calendar is worse than a stale
one. Commit only when something changed, so the history stays readable as a record of what
went on sale when.

---

## Data schema

`data/events.json` is a flat list of:

```json
{"city": "Dubai",
 "category": "Comedy" | "Desi" | "Comedy + Desi",
 "event": "Kunal Kamra Live in Dubai",
 "artist": "Kunal Kamra",
 "start": "2026-10-31",
 "end": null,
 "time": "18:00",
 "venue": "Emirates Theatre, Emirates International School, Jumeirah",
 "price_from_aed": 155,
 "language": "Mostly Hindi",
 "notes": "",
 "url": "https://dubai.platinumlist.net/event-tickets/106948/kunal-kamra-live-in-dubai"}
```

`end` is null for single-day events. Multi-day events occupy every date in the range.

---

## How the scoring works

Each date starts from its day of the week, then gains and loses points. All of it is in
`data/config.json`; change the JSON, not the Python.

Base: Sat 5.0, Fri 4.5, Thu 3.0, Sun 3.0, Wed 2.0, Mon/Tue 1.5. Saturday leads because that is
where this circuit already books.

Penalties: major desi concert same night -2.5, inside the Dubai Comedy Festival window -2.5,
another Indian act the night before or after -1.0, late August -1.0, other comedy same night -0.8,
New Year's Eve or Day -2.5.

Boosts: Eid Al Fitr window +1.5, public holiday +1.0, December to mid-January peak +0.5 to +0.7.

Blocked outright: a direct clash with an Indian stand-up act, or any date inside Ramadan.

Tiers: prime >= 4.0, good >= 2.5, weak >= 1.0, poor below that.

---

## Known limits, keep these visible in the UI

- **Venue availability is not modelled.** Emirates Theatre, Sheikh Rashid Auditorium at the Indian
  High School, and Live@Play in Al Quoz carry most of this circuit and book out early. A prime date
  is only prime if the room is free.
- **Ramadan and Eid are forecasts.** Expected 8 Feb and 10 Mar 2027, both subject to moon sighting.
  Anything from February 2027 onward is provisional.
- **One source.** Platinumlist only. Shows sold elsewhere are invisible to this.
- **Three source-data quirks** carried through from the listings, currently in the `notes` field:
  Radhika Das prints 06:30 with no AM/PM; Bhakti 2.0 and URJA are both titled "Oud Mehta Theater"
  but list Zabeel Ladies Club as the venue.

---

## Definition of done

- `python src/scrape.py && python src/viability.py && python src/build_site.py` runs clean from a
  fresh clone.
- The workflow has run green on schedule at least once without a human touching it.
- The page is usable one-handed on a phone, installs to the home screen, and works offline from cache.
- A deliberately broken scrape (point it at a 404) fails the job and leaves the last good site up.
- Clicking every filter on the built page changes the visible day count. Test it, do not eyeball it.
