"""
A stand-in for Supabase, for tests.

The page now holds no data, so a browser test has to be given a database or it has
nothing to look at. This intercepts the two hosts the app talks to and answers them
from local files: GoTrue for sign-in, PostgREST for the dataset and the checklists.

It is deliberately literal about the parts that matter to the app: an address that is
not on the allowlist gets an empty array rather than an error, because that is what
Row Level Security actually returns, and getting that wrong would hide the bug where
the app reports "no data" as if it were "not allowed".
"""
import datetime as dt
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TOKEN = {
    "access_token": "fake-access-token",
    "refresh_token": "fake-refresh-token",
    "expires_in": 3600,
    "user": {"email": "tester@example.com"},
}


class FakeBackend:
    def __init__(self, viability=None, checklists=None, allowed=True, paused=False,
                 published=True, row_timestamp=True, new_events=2):
        if viability is None:
            viability = json.loads((ROOT / "data" / "viability.json").read_text())
        # A checkout's dataset predates first_seen, and a badge for events added in
        # the latest run cannot be tested against a dataset where nothing was. Two
        # listed events are stamped as added in this run; the rest are left alone,
        # which is also how the real data will look for its first few days.
        viability = json.loads(json.dumps(viability))
        stamped = 0
        today = viability.get("generated") or ""
        # Upcoming against the real clock, not the payload's date. A checkout's
        # dataset ages, and stamping a show that has since happened marks something
        # the page hides by default, which proves nothing about the badge.
        now = dt.date.today().isoformat()
        for event in viability.get("events", []):
            upcoming = (event.get("end") or event.get("start") or "") >= now
            if stamped < new_events and event.get("listed", True) and upcoming:
                event["first_seen"] = today
                stamped += 1
            else:
                event["first_seen"] = "2026-08-17"
        # published=False is the state of a fresh database: the schema is there, the
        # checklists are seeded, and no run has written a dataset yet.
        self.published = published
        self.viability = viability
        # row_timestamp=False is a database whose row carries no updated_at, which is
        # how the page proves it can read the time out of the payload alone.
        self.updated_at = (dt.datetime.now(dt.timezone.utc).isoformat()
                           if row_timestamp else None)
        # Older payloads have no generated_at; the fixture keeps whatever the local
        # dataset has, so both paths get exercised as the real data catches up.
        self.checklists = checklists if checklists is not None else [sample_checklist()]
        self.allowed = allowed
        self.paused = paused
        self.calls = []
        self.writes = []

    # -------------------------------------------------------------- routing
    def handle(self, route):
        request = route.request
        url = request.url
        self.calls.append(url)
        if self.paused:
            return route.fulfill(status=540, content_type="application/json",
                                 body=json.dumps({"message": "project paused"}))
        if "/auth/v1/token" in url or "/auth/v1/signup" in url:
            body = json.loads(request.post_data or "{}")
            if body.get("password") == "wrong":
                return route.fulfill(status=400, content_type="application/json",
                                     body=json.dumps({
                                         "error_description": "Invalid login credentials"}))
            token = dict(TOKEN)
            token["user"] = {"email": body.get("email", "tester@example.com")}
            return route.fulfill(status=200, content_type="application/json",
                                 body=json.dumps(token))
        if "/auth/v1/user" in url:
            return route.fulfill(status=200, content_type="application/json",
                                 body=json.dumps({"email": "tester@example.com"}))
        if "/auth/v1/logout" in url:
            return route.fulfill(status=204, body="")
        if "/rest/v1/allowed_emails" in url:
            rows = [{"email": "tester@example.com"}] if self.allowed else []
            return route.fulfill(status=200, content_type="application/json",
                                 body=json.dumps(rows))
        if "/rest/v1/datasets" in url:
            # updated_at is when the run wrote the row, and the page reports it in
            # local time, so the fixture has to carry a real timestamp.
            rows = [] if not (self.allowed and self.published) else [
                {"payload": self.viability,
                 "generated": self.viability.get("generated"),
                 "updated_at": self.updated_at}]
            return route.fulfill(status=200, content_type="application/json",
                                 body=json.dumps(rows))
        if "/rest/v1/checklists" in url:
            if request.method == "POST":
                self.writes.append(json.loads(request.post_data or "[]"))
                return route.fulfill(status=201, body="")
            rows = [] if not self.allowed else [
                {"id": c["id"], "doc": c, "updated_at": "2026-01-01T00:00:00Z"}
                for c in self.checklists]
            return route.fulfill(status=200, content_type="application/json",
                                 body=json.dumps(rows))
        return route.fulfill(status=404, content_type="application/json",
                             body=json.dumps({"message": "no such route"}))

    def install(self, page):
        page.route("**/*.supabase.co/**", self.handle)

    # -------------------------------------------------------------- helpers
    def sign_in(self, page, email="tester@example.com", password="hunter2hunter2"):
        page.click("#gate-in")
        page.wait_for_selector("#acct-email", timeout=5000)
        page.fill("#acct-email", email)
        page.fill("#acct-pass", password)
        page.click("#acct-form button[type=submit]")
        page.wait_for_timeout(700)


def sample_checklist():
    """Shaped like the real thing, with nothing real in it."""
    return {
        "id": "test-show",
        "title": "Test Show",
        "subtitle": "A fixture, not a real event",
        "show_date": None,
        "setup": [{"label": "Venue", "value": "", "note": "where it happens"}],
        "tasks": [
            {"n": 1, "workstream": "Contract", "task": "Sign the agreement",
             "owner": "Someone", "status": "Not started", "d_minus": 60,
             "blocking": True, "why": "no show without it"},
            {"n": 2, "workstream": "Marketing", "task": "Book the posters",
             "owner": "Someone", "status": "Done", "d_minus": 30, "blocking": False,
             "why": ""},
        ],
    }
