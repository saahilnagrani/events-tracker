"""
Move the data between the working files and Supabase.

The repository holds code and configuration. It does not hold the dataset any more,
and it never holds the checklists: those carry fees and counterparty names, and this
repository is public. The database is the store of record; the files under data/ are
a working copy that exists for the length of one run and is gitignored.

  python src/publish.py --pull                  # database -> data/*.json
  python src/publish.py --push                  # data/*.json -> database
  python src/publish.py --seed-checklists FILE  # one-off, from import_checklist.py
  python src/publish.py --dump-checklists FILE  # backup what the app has written

--pull writes data/events.prev.json alongside data/events.json, because src/changes.py
diffs against the previous run and there is no committed copy to compare with.

Seeding is deliberately a separate command. The daily run must never write the
checklists table: it holds edits made in the app, and a nightly overwrite from a file
would erase them.
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import backend  # noqa: E402  (path set above so a fresh clone works)

ROOT = backend.ROOT


def pull(log=print):
    events = backend.get_events()
    if events:
        backend.EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        backend.EVENTS_FILE.write_text(
            json.dumps(events, indent=1, ensure_ascii=False) + "\n")
        prev = backend.EVENTS_FILE.with_name("events.prev.json")
        prev.write_text(backend.EVENTS_FILE.read_text())
        listed = sum(1 for e in events if e.get("listed", True))
        log(f"  pulled {len(events)} events ({listed} still listed) -> events.json")
    else:
        log("  events: nothing stored yet")

    for name, path in backend.DATASETS.items():
        payload, generated = backend.get_dataset(name)
        if payload is None:
            log(f"  {name}: nothing stored yet")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
        log(f"  pulled {name} ({generated or 'no date'}) -> {path.name}")
        # The scraper overwrites its inputs in place, so the diff needs its own copy
        # taken before that happens.
        if name == "review_queue":
            prev = path.with_name(path.stem + ".prev.json")
            prev.write_text(path.read_text())
    return 0


def push(force=False, log=print):
    pushed = 0
    if backend.EVENTS_FILE.exists():
        events = json.loads(backend.EVENTS_FILE.read_text())
        # An upsert cannot delete, so a run that lost its history no longer erases
        # anything; it just fails to mention the rest. Saying so is still worth it,
        # because that run's viability payload will be missing them.
        stored = backend.get_events()
        if len(events) < len(stored):
            missing = len(stored) - len(events)
            log(f"  NOTE: this run carries {len(events)} events against {len(stored)} "
                f"stored; {missing} will keep their existing rows.")
            if not force:
                log("  Refusing, because the calendar built from this run is missing "
                    "them too. Re-run with --push --force if that is intended.")
                return 1
        backend.put_events(events, log)
        pushed += 1
    else:
        log("  events: no events.json to push")

    for name, path in backend.DATASETS.items():
        if not path.exists():
            log(f"  {name}: no {path.name} to push")
            continue
        payload = json.loads(path.read_text())
        generated = None
        if isinstance(payload, dict):
            generated = payload.get("generated")
        backend.put_dataset(name, payload, generated or date.today().isoformat(), log)
        pushed += 1
    if not pushed:
        log("  nothing to push")
        return 1
    return 0


def seed_checklists(path, log=print):
    raw = json.loads(Path(path).read_text())
    docs = raw.get("checklists", raw if isinstance(raw, list) else [])
    if not docs:
        log(f"  {path} holds no checklists")
        return 1
    existing = {c["id"] for c in backend.get_checklists()}
    fresh = [d for d in docs if d["id"] not in existing]
    already = [d["id"] for d in docs if d["id"] in existing]
    for cid in already:
        # Overwriting would throw away whatever has been ticked off since.
        log(f"  {cid}: already in the database, left alone")
    if fresh:
        backend.put_checklists(fresh, log)
    return 0


def dump_checklists(path, log=print):
    rows = backend.get_checklists()
    out = {"checklists": [r["doc"] for r in rows]}
    Path(path).write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    log(f"  wrote {len(rows)} checklists to {path}")
    log("  this file carries fees and names: keep it out of the repository")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pull", action="store_true")
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="push even if the event list has shrunk")
    ap.add_argument("--seed-checklists", metavar="FILE")
    ap.add_argument("--dump-checklists", metavar="FILE")
    args = ap.parse_args()

    if not any([args.pull, args.push, args.seed_checklists, args.dump_checklists]):
        ap.print_help()
        return 2

    try:
        url, _ = backend.config()
        print(f"backend {url}")
        if args.pull:
            return pull()
        if args.push:
            return push(force=args.force)
        if args.seed_checklists:
            return seed_checklists(args.seed_checklists)
        if args.dump_checklists:
            return dump_checklists(args.dump_checklists)
    except backend.BackendError as exc:
        return backend.fail(str(exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
