# Events Tracker

Every comedy and desi event on sale in Dubai and Abu Dhabi, which dates are viable for
staging a show, and checklists for the shows you are running.

Live at <https://saahilnagrani.github.io/events-tracker/>.

## Where the data is

Not here. The repository holds code; the events, the scored calendar and the checklists
live in Supabase, and the published page is a shell that fetches them once somebody has
signed in. That is what makes the sign-in real rather than decorative: a static page
cannot withhold what it has already handed over.

The files under `data/` are working copies for the length of one run, and gitignored.

## How it runs

`.github/workflows/daily.yml` fires at 03:00 UTC, which is 07:00 in Dubai. It pulls
yesterday's dataset out of Supabase, scrapes Platinumlist, diffs the two, rescores every
date, rebuilds the shell, runs the browser smoke test, and only then writes the new
dataset back. A failing smoke test stops the publish on purpose.

Locally, the same steps:

```
export SUPABASE_SERVICE_KEY=...   # never committed; see SUPABASE.md
python src/publish.py --pull      # database -> data/*.json
python src/scrape.py              # -> data/events.json, data/review_queue.json
python src/changes.py --old data/events.prev.json --out data/changes.json
python src/viability.py           # -> data/viability.json
python src/build_site.py          # -> docs/ (a shell: no data in it)
python tests/test_events_mapping.py  # the table mapping, both ways
python tests/test_site.py            # 186 browser checks, against a stand-in database
python src/publish.py --push      # data/*.json -> database
```

## The Refresh button

The Events tab shows when the listings were last checked and offers **Refresh now**.
The page is static, so the button cannot run anything itself: it asks GitHub to start
the same workflow, then watches the run and tells you when to reload.

GitHub will not take that request from an anonymous page, so the first click asks for a
fine-grained personal access token, scoped to this repository with **Actions: read and
write** and nothing else. It is stored in that browser's `localStorage`, on that device
only, and is sent to `api.github.com` and nowhere else. Repeat on each device, or use
the **Run it on GitHub instead** link, which needs no token.

Anyone with the unlocked device can use a stored token, so on a shared machine use the
link rather than saving one.

## The demo page

`docs/demo/` is a second build for handing to someone who is evaluating this. It opens
with no sign-in, carries the real listings and the real scored calendar, and an invented
checklist in place of the real ones. It is rebuilt by the daily run, so it stays current.

<https://saahilnagrani.github.io/events-tracker/demo/>

It is the only output that inlines any data, and what it inlines is scraped public
listings plus fiction. The build refuses to produce it if the checklist file holds
anything other than the sample, and the smoke test greps the built page for the real
checklist's fee and counterparties.

## Accounts

Everything is behind one. Signing in is what fetches the data, and an address has to be
on the allowlist before it returns anything. See [SUPABASE.md](SUPABASE.md) for the
setup, including the one secret this needs and where it goes.
