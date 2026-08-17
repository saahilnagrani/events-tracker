"""
Diff a fresh scrape against the committed dataset -> docs/changes.json

Run this after src/scrape.py has written data/events.json but before that file is
committed, so "old" is the last committed state and "new" is what just came off the
site.

The field that matters is `dates_lost`. A new show is mildly interesting; a new show
that just took a date scored prime yesterday is the thing worth a notification. Working
that out needs the viability model run twice, over the old dataset and the new one, so
this module imports viability.build rather than reimplementing any scoring.

A note on what "non-empty" means, since a scheduled task keys its alerts off this file.
`review_queue` is standing state: it holds every listing that looks desi but matches no
known artist, and those entries persist until someone extends artists.json. If a
notification fired whenever that list was populated it would fire every morning. So the
top-level `has_changes` flag is the alerting signal, and it ignores review-queue entries
that were already there yesterday.

Run:  python src/changes.py
"""
import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import viability  # noqa: E402  (path set above so a fresh clone works)

# Fields worth reporting a change on. `notes` and `artist` are deliberately absent:
# the scraper writes its own markers into notes and carries curated artist prose
# across, so diffing them reports our own bookkeeping as if it were news.
TRACKED = ["event", "city", "category", "start", "end", "time", "venue",
           "price_from_aed", "language"]
# `listed` is deliberately absent: a flip to false is already reported as a removal,
# and tracking it here would report the same fact twice.

# blocked is not merely the bottom of the scale, it is a different statement: the date
# is unusable rather than merely poor. It ranks below poor so any slide into it counts.
TIER_RANK = {"blocked": 0, "poor": 1, "weak": 2, "good": 3, "prime": 4}


def git_show(rev, path, log=print):
    """Committed version of a file, or None if it is absent or git is unavailable."""
    try:
        out = subprocess.run(["git", "show", f"{rev}:{path}"], cwd=ROOT,
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        log(f"  git unavailable ({type(exc).__name__}), treating {path} as new")
        return None
    if out.returncode != 0:
        log(f"  no committed {path} at {rev}, treating it as new")
        return None
    return out.stdout


def load_json(text, default):
    if not text:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


def by_url(events):
    return {e["url"].rstrip("/"): e for e in events}


def summarise(event):
    """The subset of an event worth carrying into an alert."""
    return {k: event.get(k) for k in
            ("event", "city", "category", "start", "end", "time", "venue",
             "price_from_aed", "url")}


def listed(event):
    return event.get("listed", True)


def diff_events(old, new):
    old_by, new_by = by_url(old), by_url(new)
    # The dataset retains delisted events now, so "removed" cannot mean "no longer in
    # the file". It means it stopped being on sale: either it vanished outright, or its
    # listed flag went true to false.
    added = [summarise(new_by[u]) for u in new_by
             if u not in old_by and listed(new_by[u])]
    removed = [summarise(old_by[u]) for u in old_by
               if u not in new_by
               or (listed(old_by[u]) and not listed(new_by[u]))]

    changed = []
    for url in sorted(set(old_by) & set(new_by)):
        before, after = old_by[url], new_by[url]
        fields = {f: {"from": before.get(f), "to": after.get(f)}
                  for f in TRACKED if before.get(f) != after.get(f)}
        if fields:
            changed.append({"event": after["event"], "url": url,
                            "start": after.get("start"), "fields": fields})

    added.sort(key=lambda e: (e.get("start") or "", e["event"]))
    removed.sort(key=lambda e: (e.get("start") or "", e["event"]))
    return added, removed, changed


def day_map(events, cfg, artists):
    return {d["date"]: d for d in viability.build(events, cfg, artists)}


def diff_days(old_days, new_days):
    """Dates whose tier moved. Losses are what matter; gains are the counterpart."""
    lost, gained = [], []
    for day, after in new_days.items():
        before = old_days.get(day)
        if before is None:
            continue
        was, now = before["tier"], after["tier"]
        if was == now:
            continue
        old_events = set(before["events"])
        entry = {"date": day, "dow": after["dow"], "was": was, "now": now,
                 "score_was": before["score"], "score_now": after["score"]}
        if TIER_RANK[now] < TIER_RANK[was]:
            entry["taken_by"] = sorted(set(after["events"]) - old_events)
            entry["reasons"] = after["reasons"]
            lost.append(entry)
        else:
            entry["freed_by"] = sorted(old_events - set(after["events"]))
            gained.append(entry)

    # Worst landing tier first, then biggest fall, so a prime date going blocked leads.
    lost.sort(key=lambda e: (TIER_RANK[e["now"]], -(TIER_RANK[e["was"]] - TIER_RANK[e["now"]]),
                             e["date"]))
    gained.sort(key=lambda e: (-TIER_RANK[e["now"]], e["date"]))
    return lost, gained


def diff_review(old_queue, new_queue):
    """Full current queue, with entries absent yesterday marked new."""
    seen = {r["url"].rstrip("/") for r in old_queue}
    out = []
    for r in new_queue:
        out.append({**r, "new": r["url"].rstrip("/") not in seen})
    out.sort(key=lambda r: (not r["new"], r.get("start") or ""))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rev", default="HEAD", help="revision holding the previous dataset")
    ap.add_argument("--old", help="compare against this events.json instead of git")
    ap.add_argument("--old-review", help="compare against this review_queue.json")
    ap.add_argument("--new", default=str(ROOT / "data" / "events.json"))
    ap.add_argument("--new-review", default=str(ROOT / "data" / "review_queue.json"))
    ap.add_argument("--out", default=str(ROOT / "docs" / "changes.json"))
    args = ap.parse_args()

    cfg = json.loads((ROOT / "data" / "config.json").read_text())
    artists = json.loads((ROOT / "data" / "artists.json").read_text())

    new_events = json.loads(Path(args.new).read_text())
    old_events = load_json(
        Path(args.old).read_text() if args.old else git_show(args.rev, "data/events.json"),
        [])

    new_review = load_json(Path(args.new_review).read_text()
                           if Path(args.new_review).exists() else None, [])
    old_review = load_json(
        Path(args.old_review).read_text() if args.old_review
        else git_show(args.rev, "data/review_queue.json"),
        [])

    print(f"comparing {len(old_events)} committed events against {len(new_events)} scraped")
    added, removed, changed = diff_events(old_events, new_events)

    # Both sides are scored with today's config, so a tier move is attributable to the
    # dataset rather than to a weight someone edited in the same commit.
    lost, gained = diff_days(day_map(old_events, cfg, artists),
                             day_map(new_events, cfg, artists))
    review = diff_review(old_review, new_review)
    new_review_count = sum(1 for r in review if r["new"])

    has_changes = bool(added or removed or changed or lost or gained or new_review_count)
    payload = {
        "generated": date.today().isoformat(),
        "has_changes": has_changes,
        "summary": {"added": len(added), "removed": len(removed), "changed": len(changed),
                    "dates_lost": len(lost), "dates_gained": len(gained),
                    "review_queue": len(review), "review_queue_new": new_review_count},
        "added": added,
        "removed": removed,
        "changed": changed,
        "dates_lost": lost,
        "dates_gained": gained,
        "review_queue": review,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")

    print(f"  added {len(added)}, removed {len(removed)}, changed {len(changed)}")
    print(f"  dates lost {len(lost)}, dates gained {len(gained)}")
    print(f"  review queue {len(review)} ({new_review_count} new)")
    for e in lost[:8]:
        who = ", ".join(e["taken_by"]) or "scoring change"
        print(f"    LOST {e['date']} {e['dow']}  {e['was']} -> {e['now']}  {who[:60]}")
    print(f"\nwrote {out}  (has_changes={has_changes})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
