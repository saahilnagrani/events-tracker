"""
Scrape Platinumlist comedy and desi listings for Dubai and Abu Dhabi -> data/events.json

Findings from the scraper spike that this implementation depends on:

  * Listings are server-rendered. No browser needed, no JSON API exists.
  * A Queue-it waiting room (`protectallsite`) 302s the first request of every session
    through queue.platinumlist.net and sets a cookie. Follow redirects and keep one
    Session for the whole run; every request after the first is direct.
  * Prices default to USD. The `user_currency=AED` cookie is what makes them match
    `price_from_aed`.
  * Pagination has no reliable terminator in the markup: `<link rel="next">` is emitted
    unconditionally and points at pages that do not exist. An out-of-range `?page=N`
    302s back to the bare listing URL, and that redirect is the terminator.
  * The listing card carries title, date and price but never the venue, so each event
    needs a second pass over its detail page.
  * Detail pages carry no JSON-LD. The only machine-readable date with a *year* is the
    `Event Date Timestamp` inside a `data-webengage-click` analytics payload, and there
    are two such payloads per page, only one of which has it.
  * There is no language field anywhere on the page. Language is inferred from the
    description prose (see `infer_language`), which is why it can be wrong.

Run:  python src/scrape.py
"""
import argparse
import json
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from selectolax.parser import HTMLParser

ROOT = Path(__file__).resolve().parent.parent

# (city, category, listing url). abu-dhabi.platinumlist.net/event is deliberately absent:
# it is the city homepage built from carousels, not a filtered listing, and it yields
# ballet and opera rather than comedy.
LISTINGS = [
    ("Dubai", "Comedy", "https://dubai.platinumlist.net/comedy"),
    ("Dubai", "Comedy", "https://dubai.platinumlist.net/shows/comedy-shows"),
    ("Dubai", "Desi", "https://dubai.platinumlist.net/desi"),
    ("Abu Dhabi", "Comedy", "https://abu-dhabi.platinumlist.net/comedy"),
    ("Abu Dhabi", "Desi", "https://abu-dhabi.platinumlist.net/desi"),
]

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

MAX_PAGES = 12          # guard against a pagination bug turning into an unbounded crawl
RETRIES = 4
BACKOFF = [2, 4, 8, 16]

# The cache stores parsed output, not raw HTML, so a change to parse_detail has to
# invalidate it. Bump this whenever parse_detail's output shape or meaning changes.
PARSER_VERSION = 4

MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}

# Applied in order; first hit wins. Deliberately conservative: anything unmatched stays
# "Not stated" rather than being guessed at.
LANGUAGE_RULES = [
    (r"\bhinglish\b", "Hinglish"),
    (r"\bhindi\b.{0,40}\benglish\b|\benglish\b.{0,40}\bhindi\b", "Hindi & English"),
    (r"\bmostly in hindi\b|\bmainly in hindi\b", "Mostly Hindi"),
    (r"\bhindi\b", "Hindi"),
    (r"\burdu\b", "Urdu"),
    (r"\bpunjabi\b", "Punjabi"),
    (r"\bgujarati\b", "Gujarati"),
    (r"\bmarathi\b", "Marathi"),
    (r"\btamil\b", "Tamil"),
    (r"\btelugu\b", "Telugu"),
    (r"\bmalayalam\b", "Malayalam"),
    (r"\bcarnatic\b|\bindian classical\b", "Carnatic / Indian classical"),
    (r"\barabic\b", "Arabic"),
    (r"\brussian\b", "Russian"),
    (r"\bfrench\b", "French"),
    (r"\bin english\b|\benglish[- ]language\b|\bperformed in english\b", "English"),
]

# The analytics payload is HTML-attribute-escaped with hex entities.
UNESCAPE = [("&#x7B;", "{"), ("&#x7D;", "}"), ("&quot;", '"'), ("&#x3A;", ":"),
            ("&#x20;", " "), ("&#x2C;", ","), ("&#x5C;", "\\"), ("&#x2F;", "/"),
            ("&#x27;", "'"), ("&amp;", "&")]


# ---------------------------------------------------------------- http

def make_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Upgrade-Insecure-Requests": "1",
    })
    # Without this every price comes back in USD.
    s.cookies.set("user_currency", "AED", domain=".platinumlist.net")
    return s


def fetch(session, url, delay):
    """GET with retry on transport errors and 5xx. Returns the response."""
    last = None
    for attempt in range(RETRIES):
        try:
            r = session.get(url, timeout=45, allow_redirects=True)
            if r.status_code < 500:
                time.sleep(delay)
                return r
            last = f"HTTP {r.status_code}"
        except requests.RequestException as exc:
            last = type(exc).__name__
        if attempt < RETRIES - 1:
            time.sleep(BACKOFF[attempt])
    raise RuntimeError(f"giving up on {url} after {RETRIES} attempts ({last})")


# ---------------------------------------------------------------- listings

def parse_cards(html):
    """Listing cards only. Cross-sell carousels use different markup and are excluded."""
    out = []
    for node in HTMLParser(html).css(".event-grid-item"):
        a = node.css_first("a.event-grid-item__title")
        if a is None:
            continue
        href = (a.attributes.get("href") or "").split("?")[0].rstrip("/")
        if not href:
            continue
        price = node.css_first(".price")
        when = node.css_first(".date")
        out.append({
            "url": href,
            "title": " ".join(a.text().split()),
            "card_price": " ".join(price.text().split()) if price else None,
            "card_date": " ".join(when.text().split()) if when else None,
        })
    return out


# Words that only appear on a page that is refusing to serve the listing.
CHALLENGE = ("queue-it", "queue.platinumlist", "just a moment", "attention required",
             "access denied", "are you a robot", "captcha", "verify you are human")


def why_empty(r):
    """Say why a listing came back with no cards.

    A block and a redesign both read as "0 cards", and the log could not tell them
    apart, which cost a morning. So when a listing is empty this reports what actually
    arrived: the status, where it landed, how big it was, its title, and how much of
    the grid markup survived. A challenge page is small and oddly titled; a redesign is
    a full-size listing page with the cards renamed underneath it.
    """
    tree = HTMLParser(r.text)
    node = tree.css_first("title")
    title = " ".join(node.text().split())[:90] if node else "(no title)"
    items = len(tree.css(".event-grid-item"))
    gridish = len(tree.css("[class*=event-grid]"))
    low = f"{title} {r.url}".lower()
    if r.status_code != 200:
        verdict = "not a 200"
    elif any(word in low for word in CHALLENGE):
        verdict = "a challenge or waiting-room page, not the listing"
    elif items:
        verdict = f"{items} cards, but none carry a.event-grid-item__title any more"
    elif gridish:
        verdict = "grid markup is there but .event-grid-item is gone: renamed"
    elif len(r.text) < 50_000:
        verdict = "too small to be a listing: probably blocked"
    else:
        verdict = "a full page with no event grid in it at all"
    return (f"    ^ {verdict}\n"
            f"      HTTP {r.status_code}, {len(r.text) // 1024} KB, "
            f"{items} event-grid-item, {gridish} event-grid-ish, landed on {r.url}\n"
            f"      title: {title}")


def crawl_listings(session, delay, log=print):
    """Walk every listing URL to exhaustion. Returns {url: {...card, city, categories}}."""
    found = {}
    for city, category, base in LISTINGS:
        total, empty = 0, None
        for page in range(1, MAX_PAGES + 1):
            url = base if page == 1 else f"{base}?page={page}"
            r = fetch(session, url, delay)
            # An out-of-range page 302s back to the bare listing, dropping the query.
            # That redirect is the only trustworthy end-of-pagination signal this site
            # emits: <link rel="next"> is written unconditionally and points at pages
            # that do not exist.
            if page > 1 and f"page={page}" not in r.url:
                break
            cards = parse_cards(r.text)
            if not cards:
                # Only worth explaining on the first page; later pages run out normally.
                if page == 1:
                    empty = r
                break
            for c in cards:
                rec = found.setdefault(c["url"], {**c, "city": city, "categories": set()})
                rec["categories"].add(category)
                # A card seen on several listings: keep the first non-empty date/price.
                for k in ("card_date", "card_price"):
                    if rec.get(k) is None:
                        rec[k] = c[k]
            total += len(cards)
        log(f"  {city:9s} {base.split('.net')[1]:22s} {total:3d} cards")
        if empty is not None:
            log(why_empty(empty))
    return found


# ---------------------------------------------------------------- detail pages

def webengage_iso(html):
    """The only date on the page that carries a year. Two payloads exist; scan both."""
    for m in re.finditer(r"data-webengage-click='([^']*)'", html):
        raw = m.group(1)
        for a, b in UNESCAPE:
            raw = raw.replace(a, b)
        try:
            blocks = json.loads(raw)
        except ValueError:
            continue
        for block in (blocks if isinstance(blocks, list) else [blocks]):
            stamp = (block.get("eventData") or {}).get("Event Date Timestamp")
            if stamp:
                return stamp
    return None


def parse_detail(html):
    t = HTMLParser(html)
    out = {}

    node = t.css_first("[data-event-item]")
    if node:
        try:
            out["id"] = json.loads(node.attributes["data-event-item"]).get("idEvent")
        except (ValueError, KeyError):
            pass

    h1 = t.css_first("h1")
    out["title"] = " ".join(h1.text().split()) if h1 else None

    # Works for both markup variants: venues with their own Platinumlist page and
    # venues that only render a geo-anchor.
    venue = t.css_first(".event-item__venue-name")
    out["venue"] = " ".join(venue.text().split()) if venue else None

    # Only "Start:" is actually the start time. Plenty of pages publish "Doors:" alone,
    # or neither, and treating either of those as the start silently shifts an event
    # earlier by 30 to 120 minutes. Record which one we got so the caller can say so.
    labels = [" ".join(x.text().split()) for x in t.css(".buy-block__time-text")]
    values = [" ".join(x.text().split()) for x in t.css(".buy-block__time-value")]
    times = dict(zip(labels, values))
    if times.get("Start:"):
        out["time"], out["time_source"] = times["Start:"], "start"
    elif times.get("Doors:"):
        out["time"], out["time_source"] = times["Doors:"], "doors"
    else:
        out["time"], out["time_source"] = None, None

    price = t.css_first(".buy-block__price")
    if price:
        m = re.search(r"([\d.,]+)\s*AED", " ".join(price.text().split()))
        if m:
            value = float(m.group(1).replace(",", ""))
            out["price_from_aed"] = int(value) if value == int(value) else value

    # When a show sells out the whole on-sale block is replaced, taking the price and
    # the door/start times with it. That is real state, not a parse failure, and it has
    # to be distinguished from one: see the time carry-forward in build().
    # There are several .buy-block__section nodes; the date sits in the first one and
    # the sold-out marker in a later one, so every section has to be checked.
    sections = t.css(".buy-block__section")
    out["sold_out"] = bool(
        t.css_first(".buy-block__section--on-sale") is None
        and any("sold out" in " ".join(s.text().split()).lower() for s in sections))

    stamp = webengage_iso(html)
    if stamp:
        out["iso_start"] = stamp[:10]
        if out.get("time") is None and len(stamp) >= 16:
            # Last resort. The stamp's meaning is inconsistent: it is the door time on
            # some pages and an unrelated scheduling anchor on others, so it is flagged.
            out["time"], out["time_source"] = stamp[11:16], "anchor"

    desc = t.css_first(".event-item__description-section")
    meta = t.css_first('meta[name="description"]')
    out["description"] = " ".join(filter(None, [
        " ".join(desc.text().split()) if desc else "",
        (meta.attributes.get("content") or "") if meta else "",
    ]))
    return out


# ---------------------------------------------------------------- interpretation

def parse_card_dates(label, year_hint):
    """'Fri 25 Sep - Sun 27 Sep' -> ('2026-09-25', '2026-09-27'). Single dates -> (d, None)."""
    if not label:
        return None, None
    parts = [p.strip() for p in label.split("-")]
    parsed = []
    for part in parts:
        m = re.search(r"(\d{1,2})\s+([A-Za-z]{3})", part)
        if not m:
            continue
        day, mon = int(m.group(1)), MONTHS.get(m.group(2).title())
        if mon:
            parsed.append((mon, day))
    if not parsed:
        return None, None
    year = year_hint or date.today().year
    start = date(year, parsed[0][0], parsed[0][1])
    if len(parsed) < 2:
        return start.isoformat(), None
    end_year = year + 1 if parsed[1][0] < parsed[0][0] else year
    return start.isoformat(), date(end_year, parsed[1][0], parsed[1][1]).isoformat()


def infer_language(text):
    """No language field exists on the page, so this reads the description prose."""
    low = (text or "").lower()
    for pattern, label in LANGUAGE_RULES:
        if re.search(pattern, low):
            return label
    return "Not stated"


def match_artist(title, artists):
    hay = (title or "").lower()
    for name in sorted(artists["indian_standup_artists"], key=len, reverse=True):
        if name.lower() in hay:
            return name
    return None


def looks_desi(text, artists):
    low = (text or "").lower()
    if any(k in low for k in artists.get("exclude_keywords", [])):
        return False
    return any(k in low for k in artists["desi_keywords"])


def is_recurring(url):
    """Recurring series use a slug with no numeric id and advertise only the next date."""
    parts = url.rstrip("/").split("/")
    return not (len(parts) >= 2 and parts[-2].isdigit())


# ---------------------------------------------------------------- build

def build(session, cards, artists, cache, args, log=print):
    previous = {}
    events_path = ROOT / "data" / "events.json"
    if events_path.exists():
        for e in json.loads(events_path.read_text()):
            previous[e["url"].rstrip("/")] = e

    events, review, fetched, reused = [], [], 0, 0
    for url in sorted(cards):
        card = cards[url]
        entry = cache.get(url)
        stale = (entry is None
                 or entry.get("parser") != PARSER_VERSION
                 or entry.get("card_price") != card["card_price"]
                 or entry.get("card_date") != card["card_date"]
                 or entry.get("fetched", "") < (date.today() - timedelta(days=args.cache_days)).isoformat())
        if stale:
            detail = parse_detail(fetch(session, url, args.delay).text)
            detail["parser"] = PARSER_VERSION
            detail["fetched"] = date.today().isoformat()
            detail["card_price"] = card["card_price"]
            detail["card_date"] = card["card_date"]
            cache[url] = detail
            fetched += 1
        else:
            detail = entry
            reused += 1

        title = detail.get("title") or card["title"]
        iso = detail.get("iso_start")
        year_hint = int(iso[:4]) if iso else None
        start, end = parse_card_dates(card["card_date"], year_hint)
        start = iso or start
        if not start:
            log(f"  ! no date, skipping: {title[:60]}")
            continue

        old = previous.get(url, {})
        artist = match_artist(title, artists) or old.get("artist") or ""
        category = ("Comedy + Desi" if len(card["categories"]) > 1
                    else next(iter(card["categories"])))

        # A sold-out page stops publishing the time, but the show still happens then, so
        # the last known time is carried forward and labelled. This is deliberately
        # limited to the sold-out case: if the selectors ever break site-wide, times go
        # null and the breakage stays visible instead of being papered over with
        # yesterday's data.
        event_time = detail.get("time")
        if not event_time and detail.get("sold_out") and old.get("time"):
            event_time = old["time"]

        notes = old.get("notes", "")
        markers = []
        if detail.get("sold_out"):
            markers.append("Sold out on Platinumlist" + (
                "; time carried over from when it was on sale"
                if not detail.get("time") and event_time else ""))
        if is_recurring(url):
            markers.append("Recurring series; Platinumlist lists only the next occurrence")
        source = detail.get("time_source")
        if source == "doors":
            markers.append(f"Listing publishes the door time only ({detail['time']}); "
                           "the show starts later")
        elif source == "anchor":
            markers.append(f"Start time not published; {detail['time']} is the page's "
                           "scheduling anchor and may be wrong")
        for marker in markers:
            if marker not in notes:
                notes = f"{notes}; {marker}" if notes else marker

        events.append({
            "listed": True,
            # The day this listing first appeared. Carried across from the previous
            # run when there is one, because it can only be recorded as it happens:
            # nothing about a listing says when we first saw it.
            "first_seen": old.get("first_seen") or date.today().isoformat(),
            "last_seen": date.today().isoformat(),
            "city": card["city"],
            "category": category,
            "event": title,
            "artist": artist,
            "start": start,
            "end": end,
            "time": event_time,
            "venue": detail.get("venue") or "",
            "price_from_aed": detail.get("price_from_aed"),
            "language": infer_language(f"{title} {detail.get('description', '')}"),
            "notes": notes,
            "url": url,
        })

        if not match_artist(title, artists) and looks_desi(
                f"{title} {detail.get('description', '')}", artists):
            review.append({"event": title, "url": url, "city": card["city"],
                           "start": start, "why": "matches desi keywords but no known artist"})

    # Keep everything ever seen. A show that has come off Platinumlist is still a fact
    # about the market: it ran, or it was cancelled, and either way dropping it silently
    # would rewrite history every time a listing expired. Delisted events keep their
    # last known fields and the date they were last on sale.
    live = {e["url"].rstrip("/") for e in events}
    archived = 0
    for url, old_event in previous.items():
        if url in live:
            continue
        kept = dict(old_event)
        kept["listed"] = False
        kept.setdefault("last_seen", kept.get("start"))
        kept.setdefault("first_seen", kept.get("last_seen") or kept.get("start"))
        events.append(kept)
        archived += 1

    events.sort(key=lambda e: (e["start"], e["city"], e["event"]))
    return events, review, fetched, reused, archived


def check(events, cfg, log=print):
    """Fail loudly. A quietly empty calendar is worse than a stale one.

    Counts only what is currently listed. Once the dataset retains delisted events the
    totals climb forever, and a threshold measured against them would stop detecting the
    thing it exists to detect: a scrape that came back empty.
    """
    limits = cfg.get("scrape", {})
    floor = limits.get("min_events", 60)
    current = [e for e in events if e.get("listed", True)]
    problems = []
    if len(current) < floor:
        problems.append(f"only {len(current)} events on sale, floor is {floor}")
    for city in limits.get("required_cities", ["Dubai", "Abu Dhabi"]):
        n = sum(1 for e in current if e["city"] == city)
        if n == 0:
            problems.append(f"zero events on sale for {city}")
    undated = [e for e in events if not e["start"]]
    if undated:
        problems.append(f"{len(undated)} events with no start date")
    for p in problems:
        log(f"  FAIL: {p}")
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between requests")
    ap.add_argument("--cache", default=str(ROOT / ".cache" / "details.json"))
    ap.add_argument("--cache-days", type=int, default=7,
                    help="refetch a detail page older than this many days")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "data" / "events.json"))
    ap.add_argument("--review-out", default=str(ROOT / "data" / "review_queue.json"),
                    help="where to persist the review queue for src/changes.py to diff")
    ap.add_argument("--dry-run", action="store_true", help="do not write events.json")
    args = ap.parse_args()

    cfg = json.loads((ROOT / "data" / "config.json").read_text())
    artists = json.loads((ROOT / "data" / "artists.json").read_text())

    cache_path = Path(args.cache)
    cache = {}
    if cache_path.exists() and not args.no_cache:
        try:
            cache = json.loads(cache_path.read_text())
        except ValueError:
            print("cache unreadable, starting cold")

    session = make_session()
    print("crawling listings")
    cards = crawl_listings(session, args.delay)
    print(f"  {len(cards)} unique events across {len(LISTINGS)} listings")

    print("fetching detail pages")
    events, review, fetched, reused, archived = build(session, cards, artists, cache, args)
    print(f"  {fetched} fetched, {reused} from cache, {archived} retained after delisting")

    print("checks")
    problems = check(events, cfg)
    if problems:
        print(f"\nrefusing to write {args.out}; previous data left in place")
        return 1

    current = [e for e in events if e.get("listed", True)]
    by_city = {}
    for e in current:
        by_city[e["city"]] = by_city.get(e["city"], 0) + 1
    print(f"  {len(current)} on sale {by_city}, {len(events) - len(current)} delisted, "
          f"{len(events)} total")

    if review:
        print(f"\nreview queue ({len(review)}): desi by keyword, no artist in artists.json")
        for r in review[:15]:
            print(f"  {r['start']}  {r['event'][:64]}")

    # The cache is written even on a dry run: it is not user-facing output, and
    # discarding it would make the next run refetch every detail page for nothing.
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, indent=1, ensure_ascii=False))

    if args.dry_run:
        print(f"\ndry run, {args.out} left alone")
        return 0
    Path(args.out).write_text(json.dumps(events, indent=1, ensure_ascii=False) + "\n")
    # Persisted because it is derived from the detail-page description text, which
    # events.json does not carry: src/changes.py cannot recompute it from the dataset
    # alone, and diffing this file against its committed version is what separates a
    # newly surfaced listing from the standing backlog.
    Path(args.review_out).write_text(json.dumps(review, indent=1, ensure_ascii=False) + "\n")
    fresh = [e for e in events if e.get("first_seen") == date.today().isoformat()]
    if fresh:
        print(f"\nfirst seen today: {len(fresh)}")
        for e in fresh[:10]:
            print(f"  {e['start']}  {e['event'][:60]}")
    print(f"\nwrote {args.out} and {args.review_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
