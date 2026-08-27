"""
The scraper's shape against the table's columns, both ways.

Three fields are renamed on the way in, because start, end and time are reserved or
type names in SQL, and a silent mismatch here would write nulls into a column nobody
looks at until a date goes missing from the calendar.

Run: python tests/test_events_mapping.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import backend  # noqa: E402

FULL = {
    "url": "https://dubai.platinumlist.net/event-tickets/12345/example",
    "event": "Example Night", "artist": "Someone", "city": "Dubai",
    "category": "Comedy + Desi", "language": "Hindi & English",
    "venue": "Emirates Theatre", "start": "2026-11-14", "end": "2026-11-16",
    "time": "20:30", "time_source": "doors", "price_from_aed": 146.25,
    "notes": "flash sale", "listed": False,
    "first_seen": "2026-08-17", "last_seen": "2026-09-02",
}

checks, failures = 0, []


def check(name, ok, detail=""):
    global checks
    checks += 1
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def main():
    row = backend.to_row(FULL)
    check("the three renamed fields land in their columns",
          row["start_date"] == "2026-11-14" and row["end_date"] == "2026-11-16"
          and row["start_time"] == "20:30")
    check("no scraper field is dropped on the way in",
          set(FULL) - set(backend.EVENT_FIELDS) == set(),
          str(sorted(set(FULL) - set(backend.EVENT_FIELDS))))
    check("nothing is invented on the way in",
          set(row) - set(backend.ROW_FIELDS) == set())

    back = backend.from_row(row)
    check("a round trip returns exactly what went in", back == FULL,
          json.dumps({k: (FULL[k], back.get(k)) for k in FULL
                      if back.get(k) != FULL[k]})[:120])

    # PostgREST hands numeric back as a string, and the page prints it as it stands.
    priced = backend.from_row({**row, "price_from_aed": "85.00"})
    check("a whole price does not come back as 85.0",
          priced["price_from_aed"] == 85 and isinstance(priced["price_from_aed"], int),
          repr(priced["price_from_aed"]))
    fraction = backend.from_row({**row, "price_from_aed": "146.25"})
    check("a fractional price keeps its fraction",
          fraction["price_from_aed"] == 146.25)

    check("columns the table adds are ignored on the way back",
          "updated_at" not in backend.from_row({**row, "updated_at": "2026-01-01"}))

    # This is the one that bit: PostgREST refuses a bulk insert whose objects have
    # different keys, and a one-night show has no end date while a run has no
    # time_source, so rows built only from what was present differed row to row.
    sparse = backend.to_row({"url": "u", "event": "E"})
    check("a partial event still fills every column",
          set(sparse) == set(backend.ROW_FIELDS),
          str(sorted(set(backend.ROW_FIELDS) - set(sparse))))
    check("the columns that cannot be null are given a value",
          sparse["listed"] is True and sparse["first_seen"],
          repr(sparse["first_seen"]))
    check("and the rest are null rather than invented",
          sparse["venue"] is None and sparse["start_date"] is None)

    local_events = ROOT / "data" / "events.json"
    if local_events.exists():
        rows = [backend.to_row(e) for e in json.loads(local_events.read_text())]
        shapes = {tuple(sorted(r)) for r in rows}
        check("every row of a real batch carries the same keys", len(shapes) == 1,
              f"{len(shapes)} different shapes")

    # The real dataset, if this checkout has one, is the best fixture there is.
    local = ROOT / "data" / "events.json"
    if local.exists():
        events = json.loads(local.read_text())
        trips = [backend.from_row(backend.to_row(e)) for e in events]
        # Anything the trip adds must be either null or one of the two the table
        # will not take a null for, which to_row fills in deliberately.
        defaulted = {"listed", "first_seen"}
        same = [all(t.get(k) == v for k, v in e.items() if k in backend.EVENT_FIELDS)
                and all(t[k] is None or k in defaulted for k in set(t) - set(e))
                for t, e in zip(trips, events)]
        check(f"every one of {len(events)} real events survives a round trip",
              all(same), f"{same.count(False)} differ")

    print(f"\n{checks - len(failures)}/{checks} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
