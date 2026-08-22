# Events Tracker

Every comedy and desi event on sale in Dubai and Abu Dhabi, which dates are viable for
staging a show, and checklists for the shows you are running.

Live at <https://saahilnagrani.github.io/events-tracker/>.

## How it runs

`.github/workflows/daily.yml` fires at 03:00 UTC, which is 07:00 in Dubai. It scrapes
Platinumlist, diffs the result against the committed dataset, rescores every date,
rebuilds `docs/`, runs the browser smoke test, and commits **only if the dataset
actually moved**. A failing smoke test stops the publish on purpose: a page whose
filters are dead is worse than yesterday's page.

Locally, the same four steps:

```
python src/scrape.py        # -> data/events.json, data/review_queue.json
python src/changes.py       # -> docs/changes.json   (run before committing the data)
python src/viability.py     # -> docs/viability.json
python src/build_site.py    # -> docs/
python tests/test_site.py   # 152 browser checks
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

## Accounts and sync

Optional, and off until `data/backend.json` is filled in. See [SUPABASE.md](SUPABASE.md).
