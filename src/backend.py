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
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
TIMEOUT = 30

# Keys in the `datasets` table. One row each, rewritten whole, because the app reads
# every one of them in full and never queries inside them.
DATASETS = {
    "events":       ROOT / "data" / "events.json",
    "review_queue": ROOT / "data" / "review_queue.json",
    "viability":    ROOT / "data" / "viability.json",
    "changes":      ROOT / "data" / "changes.json",
}


class BackendError(RuntimeError):
    pass


def config(need_service_key=True, log=print):
    """(url, key). The URL may come from data/backend.json; the key never does.

    SUPABASE_URL wins when it is set, which is what makes a one-off against another
    project possible. It is also how you end up pulling from one project and pushing
    to another without noticing, so a disagreement is said out loud.
    """
    env_url = os.environ.get("SUPABASE_URL", "").strip()
    file_url = ""
    path = ROOT / "data" / "backend.json"
    if path.exists():
        file_url = (json.loads(path.read_text()).get("supabase_url") or "").strip()
    url = env_url or file_url
    if env_url and file_url and env_url.rstrip("/") != file_url.rstrip("/"):
        log(f"  NOTE: SUPABASE_URL ({env_url}) overrides data/backend.json "
            f"({file_url}), which is the project the published app reads.")
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
