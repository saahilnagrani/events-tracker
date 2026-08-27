"""
Talking to Supabase from the command line.

The published page is the other side of this: it uses the anon key, which is public
and bound by Row Level Security. This module is for the workflow, which writes with
the service_role key. That key bypasses every policy, so it is read from the
environment only, never from a file in the repository, and never printed.

Nothing here is imported by the page or by the build; only by src/publish.py.
"""
import json
import os
import sys
from datetime import date
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
TIMEOUT = 30

# Keys in the `datasets` table. One row each, rewritten whole, because they are
# derived, thrown away every morning, and read in full. The events are not among them:
# they accumulate, so they live in a table of their own.
DATASETS = {
    "review_queue": ROOT / "data" / "review_queue.json",
    "viability":    ROOT / "data" / "viability.json",
    "changes":      ROOT / "data" / "changes.json",
}

EVENTS_FILE = ROOT / "data" / "events.json"

# The scraper's field names on the left, the table's on the right. Three had to
# change: start, end and time are all reserved or type names in SQL.
EVENT_FIELDS = {
    "url": "url", "event": "event", "artist": "artist", "city": "city",
    "category": "category", "language": "language", "venue": "venue",
    "notes": "notes", "price_from_aed": "price_from_aed", "listed": "listed",
    "first_seen": "first_seen", "last_seen": "last_seen",
    "time_source": "time_source",
    "start": "start_date", "end": "end_date", "time": "start_time",
}
ROW_FIELDS = {v: k for k, v in EVENT_FIELDS.items()}
# The scraper always writes these two, even when they are empty, so keeping them null
# rather than dropping them is what makes a round trip give back what went in.
KEEP_NULL = {"end", "price_from_aed"}
# PostgREST will only take this many rows in one request comfortably; a year of this
# circuit is a few hundred, so it is a formality rather than a constraint.
CHUNK = 200


def to_row(event):
    """Every column, every time.

    PostgREST rejects a bulk insert whose objects do not all carry the same keys
    ("All object keys must match"), and events genuinely differ: a one-night show has
    no end date, most have no time_source. So a row is always the full set, with null
    where the event says nothing. The two columns that cannot take a null get the
    default the table would have given them.
    """
    row = {column: event.get(key) for key, column in EVENT_FIELDS.items()}
    if row.get("listed") is None:
        row["listed"] = True
    if not row.get("first_seen"):
        row["first_seen"] = date.today().isoformat()
    return row


def from_row(row):
    """Back to the shape src/scrape.py and src/viability.py already speak.

    Columns the event never had come back as null; they are dropped rather than
    carried, so a round trip does not quietly grow "end": null on every one-night show.
    """
    event = {}
    for column, key in ROW_FIELDS.items():
        if column in row and not (row[column] is None and key not in KEEP_NULL):
            event[key] = row[column]
    # numeric comes back as a string or a float depending on the driver, and the page
    # renders it straight, so 85.00 would read as a price nobody quoted.
    price = event.get("price_from_aed")
    if price is not None:
        price = float(price)
        event["price_from_aed"] = int(price) if price == int(price) else price
    return event


class BackendError(RuntimeError):
    pass


def config(need_service_key=True, log=print):
    """(url, key). The URL comes from data/backend.json; the key never does.

    data/backend.json wins over SUPABASE_URL deliberately. That file is the project
    the published app reads, so it is the only project worth writing to, and an
    environment variable left over from an older one is otherwise indistinguishable
    from a deliberate override until you notice the data went somewhere else. The
    variable is still honoured when the file has no URL at all.
    """
    env_url = os.environ.get("SUPABASE_URL", "").strip()
    file_url = ""
    path = ROOT / "data" / "backend.json"
    if path.exists():
        file_url = (json.loads(path.read_text()).get("supabase_url") or "").strip()
    url = file_url or env_url
    if env_url and file_url and env_url.rstrip("/") != file_url.rstrip("/"):
        log(f"  NOTE: ignoring SUPABASE_URL ({env_url}); using the project the app "
            f"reads, from data/backend.json ({file_url}).")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not url:
        raise BackendError("no SUPABASE_URL, and data/backend.json has no supabase_url")
    if need_service_key and not key:
        raise BackendError(
            "no SUPABASE_SERVICE_KEY in the environment. In Actions it comes from the "
            "repository secret of that name; locally, export it for the one command "
            "that needs it. It must never be committed.")
    return url.rstrip("/"), key


def call(method, path, key, url=None, body=None, headers=None):
    url, service = (url, key) if url else (config()[0], key)
    head = {"apikey": service, "Authorization": f"Bearer {service}",
            "Content-Type": "application/json"}
    head.update(headers or {})
    try:
        r = requests.request(method, url + path, headers=head, json=body,
                             timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise BackendError(f"{method} {path}: {type(exc).__name__}") from exc
    if r.status_code in (503, 522, 540):
        raise BackendError(
            f"{method} {path}: the project is paused ({r.status_code}). Restore it "
            f"from the Supabase dashboard; a request cannot wake it.")
    if r.status_code >= 400:
        # The body can carry the key back in an error echo, so only the message is kept.
        detail = ""
        try:
            detail = (r.json() or {}).get("message", "")
        except ValueError:
            pass
        raise BackendError(f"{method} {path}: HTTP {r.status_code} {detail}".strip())
    return r


def put_dataset(name, payload, generated=None, log=print):
    url, key = config()
    body = [{"key": name, "payload": payload, "generated": generated}]
    call("POST", "/rest/v1/datasets", key, url=url, body=body,
         headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
    size = len(json.dumps(payload, separators=(",", ":")))
    log(f"  pushed {name}: {size // 1024} KB")


def get_dataset(name):
    url, key = config()
    r = call("GET", f"/rest/v1/datasets?select=payload,generated&key=eq.{name}", key,
             url=url)
    rows = r.json()
    return (rows[0]["payload"], rows[0].get("generated")) if rows else (None, None)


def get_events():
    url, key = config()
    out, offset = [], 0
    while True:
        r = call("GET", f"/rest/v1/events?select=*&order=start_date.asc&"
                        f"limit={CHUNK}&offset={offset}", key, url=url)
        rows = r.json()
        out.extend(from_row(row) for row in rows)
        if len(rows) < CHUNK:
            return out
        offset += CHUNK


def put_events(events, log=print):
    """Upsert by url. Nothing is ever deleted here, which is the point of the table."""
    url, key = config()
    rows = [to_row(e) for e in events]
    for i in range(0, len(rows), CHUNK):
        call("POST", "/rest/v1/events?on_conflict=url", key, url=url,
             body=rows[i:i + CHUNK],
             headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
    log(f"  pushed {len(rows)} events "
        f"({sum(1 for e in events if e.get('listed', True))} still listed)")


def get_checklists():
    url, key = config()
    r = call("GET", "/rest/v1/checklists?select=id,doc,updated_at", key, url=url)
    return r.json()


def put_checklists(docs, log=print):
    """Upsert whole checklist documents. Seeding only: the app owns this table."""
    url, key = config()
    body = [{"id": d["id"], "doc": d} for d in docs]
    call("POST", "/rest/v1/checklists", key, url=url, body=body,
         headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
    for d in docs:
        log(f"  seeded {d['id']}: {len(d.get('tasks', []))} tasks")


def fail(message):
    print(f"backend: {message}", file=sys.stderr)
    return 1
