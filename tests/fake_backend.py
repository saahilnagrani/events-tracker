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
    def __init__(self, viability=None, checklists=None, allowed=True, paused=False):
        if viability is None:
            viability = json.loads((ROOT / "data" / "viability.json").read_text())
        self.viability = viability
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
        if "/rest/v1/datasets" in url:
            rows = [] if not self.allowed else [
                {"payload": self.viability,
                 "generated": self.viability.get("generated")}]
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
