"""
Build the published site: docs/viability.json -> docs/index.html (+ manifest, icons, sw)

Mobile-first rebuild of prototype/index.html. The prototype is desktop-first and is kept
only as the reference for the visual language; this does not patch it.

Carried over from the prototype deliberately:
  * Tier colours: prime #0ca30c, good #2a78d6, blocked #d03b3b, low muted grey.
  * Never colour-only. Green and red fail colour-blind separation, so every day carries
    an icon and a text label, and blocked days additionally carry a diagonal hatch.
    Do not strip these; they are the accessibility story, not decoration.
  * Light and dark are both hand-picked palettes, not an auto-invert.
  * The data-theme toggle beats the OS preference in both directions.

Changed for mobile:
  * The month grid is one month per screen with swipe, not eight side by side.
  * Filters sit in a sticky row and stay reachable one-handed.
  * A web manifest, icons and a service worker make it installable and readable offline.

Everything the page references is a relative URL (./sw.js, ./icon-192.png). This deploys
to GitHub Pages as a project site under /events-tracker/, so a root-absolute path would
work locally and 404 in production. The day data is inlined at build time rather than
fetched, so there is no runtime request to get wrong and the page renders offline from a
single cached document.

Run:  python src/build_site.py
"""
import argparse
import html
import json
import re
import struct
import subprocess
import sys
import zlib
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TIERS = {
    "prime":   {"icon": "check",   "label": "PRIME",   "blurb": "book this"},
    "good":    {"icon": "dot",     "label": "GOOD",    "blurb": "workable"},
    "weak":    {"icon": "dash",    "label": "LOW",     "blurb": "weeknight or diluted"},
    "poor":    {"icon": "dash",    "label": "LOW",     "blurb": "weeknight or diluted"},
    "blocked": {"icon": "cross",   "label": "BLOCKED", "blurb": "direct clash or Ramadan"},
}

# One inline sprite, drawn on a 24x24 grid with a 2px round-capped stroke so every icon
# reads as one family. Inline rather than a font or a CDN: the page has to render from
# cache with no network, and a strict relative-path deploy leaves nothing to fetch.
ICONS = {
    "events":   '<path d="M8 6h12M8 12h12M8 18h12"/><circle cx="4" cy="6" r="1.1"/>'
                '<circle cx="4" cy="12" r="1.1"/><circle cx="4" cy="18" r="1.1"/>',
    "calendar": '<rect x="3" y="5" width="18" height="16" rx="2.5"/>'
                '<path d="M3 10h18M8 3v4M16 3v4"/>',
    "checklist": '<path d="M9 3h6a1 1 0 0 1 1 1v1H8V4a1 1 0 0 1 1-1z"/>'
                 '<path d="M16 5h2a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7'
                 'a2 2 0 0 1 2-2h2"/><path d="M9 13l2.2 2.2L15.5 11"/>',
    "sun":      '<circle cx="12" cy="12" r="4.2"/><path d="M12 2v2.2M12 19.8V22M2 12h2.2'
                'M19.8 12H22M4.9 4.9l1.6 1.6M17.5 17.5l1.6 1.6M19.1 4.9l-1.6 1.6'
                'M6.5 17.5l-1.6 1.6"/>',
    "moon":     '<path d="M20 14.2A8.2 8.2 0 0 1 9.8 4 8.4 8.4 0 1 0 20 14.2z"/>',
    "user":     '<circle cx="12" cy="8" r="3.8"/>'
                '<path d="M4.8 20a7.4 7.4 0 0 1 14.4 0"/>',
    "refresh":  '<path d="M20.2 12a8.2 8.2 0 1 1-2.4-5.8"/><path d="M20.6 4v4.6H16"/>',
    "left":     '<path d="M15 5l-7 7 7 7"/>',
    "right":    '<path d="M9 5l7 7-7 7"/>',
    "down":     '<path d="M5 9l7 7 7-7"/>',
    "close":    '<path d="M6 6l12 12M18 6L6 18"/>',
    "check":    '<path d="M4.5 12.5l5 5 10-11"/>',
    "dot":      '<circle cx="12" cy="12" r="4.6" fill="currentColor" stroke="none"/>',
    "dash":     '<path d="M7 12h10"/>',
    "cross":    '<path d="M6 6l12 12M18 6L6 18"/>',
}


def sprite():
    symbols = "".join(
        f'<symbol id="i-{name}" viewBox="0 0 24 24">{body}</symbol>'
        for name, body in ICONS.items())
    return (f'<svg xmlns="http://www.w3.org/2000/svg" style="display:none" '
            f'aria-hidden="true">{symbols}</svg>')


def icon(name, cls="ic"):
    return (f'<svg class="{cls}" aria-hidden="true" focusable="false">'
            f'<use href="#i-{name}"></use></svg>')
DOW_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
# Mirrors src/import_checklist.py. "Not needed" is excluded from progress totals, the
# same way the source workbook's dashboard excludes it.
STATUSES = ["Not started", "In progress", "Done", "Not needed"]
MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December"]


def esc(s):
    return html.escape(str(s if s is not None else ""), quote=True)


# ---------------------------------------------------------------- icons

# The mark is a calendar with one date picked out in the prime colour, which is what
# the app is for. A tick on a green tile said "task done", which is a different app.
# Bumped whenever the drawing below changes, so a rebuild replaces icons that already
# exist rather than leaving the old mark in place for anyone who has installed it.
ICON_VERSION = 2
ICON_INK   = (20, 20, 19)       # --ink, the tile the mark sits on
ICON_CREAM = (246, 245, 241)    # --plane, the calendar itself
ICON_GREEN = (12, 163, 12)      # --good, the prime tier, the chosen date

# Everything below is in unit coordinates on the icon square, so one description
# renders at any size and into either shape.
CAL_OUTER = (0.155, 0.295, 0.845, 0.865)   # calendar body
CAL_RADIUS = 0.10
CAL_STROKE = 0.058
CAL_RAIL = (0.415, 0.475)                  # the band under the header, y range
HANGERS = [(0.325, 0.385), (0.615, 0.675)]  # x ranges of the two tabs
HANGER_Y = (0.185, 0.325)
PICKED = (0.385, 0.560, 0.615, 0.790)      # the date cell


def _rrect(x, y, box, r):
    x0, y0, x1, y1 = box
    if not (x0 <= x <= x1 and y0 <= y <= y1):
        return False
    dx = max(x0 + r - x, 0.0, x - (x1 - r))
    dy = max(y0 + r - y, 0.0, y - (y1 - r))
    return dx * dx + dy * dy <= r * r


def _icon_pixel(ux, uy, bleed):
    """Colour index at a point: 0 transparent, 1 ink, 2 cream, 3 green.

    `bleed` draws the maskable variant: background to every edge, and the mark shrunk
    into the safe circle, because Android crops the corners off whatever it is given.
    """
    if bleed:
        inside = True
        k = 0.84
        gx, gy = 0.5 + (ux - 0.5) / k, 0.5 + (uy - 0.5) / k
    else:
        inside = _rrect(ux, uy, (0.0, 0.0, 1.0, 1.0), 0.22)
        gx, gy = ux, uy
    if not inside:
        return 0

    x0, y0, x1, y1 = CAL_OUTER
    inner = (x0 + CAL_STROKE, y0 + CAL_STROKE, x1 - CAL_STROKE, y1 - CAL_STROKE)
    on_frame = (_rrect(gx, gy, CAL_OUTER, CAL_RADIUS)
                and not _rrect(gx, gy, inner, max(CAL_RADIUS - CAL_STROKE, 0.01)))
    on_rail = (_rrect(gx, gy, CAL_OUTER, CAL_RADIUS)
               and CAL_RAIL[0] <= gy <= CAL_RAIL[1])
    on_tab = any(_rrect(gx, gy, (a, HANGER_Y[0], b, HANGER_Y[1]), 0.03)
                 for a, b in HANGERS)
    if _rrect(gx, gy, PICKED, 0.045):
        return 3
    if on_frame or on_rail or on_tab:
        return 2
    return 1


def _icon_rows(size, ss=4, bleed=False):
    """Supersampled RGBA rows, box-downsampled. Pure Python: no imaging library."""
    n = size * ss
    palette = {1: ICON_INK, 2: ICON_CREAM, 3: ICON_GREEN}
    mask = []
    for yy in range(n):
        uy = (yy + 0.5) / n
        row = bytearray(n)
        for xx in range(n):
            row[xx] = _icon_pixel((xx + 0.5) / n, uy, bleed)
        mask.append(row)

    out = bytearray()
    area = ss * ss
    for y in range(size):
        out.append(0)                                   # PNG filter byte: none
        for x in range(size):
            r = g = b = a = 0
            for dy in range(ss):
                src = mask[y * ss + dy]
                for dx in range(ss):
                    v = src[x * ss + dx]
                    if v:
                        a += 255
                        c = palette[v]
                        r += c[0]; g += c[1]; b += c[2]
            if a:
                filled = a // 255
                out += bytes((r // filled, g // filled, b // filled, a // area))
            else:
                out += b"\0\0\0\0"
    return bytes(out)


def write_png(path, size, bleed=False):
    raw = _icon_rows(size, bleed=bleed)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))
    path.write_bytes(png)


# ---------------------------------------------------------------- page pieces

def day_cell(day):
    # Whether a date is in the past is decided at runtime, not baked in here. Doing it
    # at build time would make the page differ every day even when no event changed,
    # and would leave a cached offline copy dimming the wrong days. The tier classes are
    # the default lens; switching lens restyles the cells from the inlined payload.
    tier = TIERS[day["tier"]]
    d = date.fromisoformat(day["date"])
    label = f"{d.strftime('%a %d %b %Y')}, {day['tier']}, score {day['score']}"
    return (
        f'<button type="button" class="day t-{day["tier"]}" data-date="{day["date"]}" '
        f'data-tier="{day["tier"]}" data-dow="{esc(day["dow"])}" '
        f'aria-label="{esc(label)}">'
        f'<span class="dn">{d.day}</span>'
        f'{icon(tier["icon"])}'
        f'<span class="lb">{tier["label"]}</span></button>'
    )


def months_html(days):
    groups = {}
    for day in days:
        d = date.fromisoformat(day["date"])
        groups.setdefault((d.year, d.month), []).append(day)

    panels = []
    for (year, month), items in sorted(groups.items()):
        first = date(year, month, 1)
        cells = ['<div class="day pad" aria-hidden="true"></div>'] * first.weekday()
        # A month at the edge of the window starts partway through.
        cells += (['<div class="day pad" aria-hidden="true"></div>']
                  * (date.fromisoformat(items[0]["date"]).day - 1))
        cells += [day_cell(day) for day in items]
        heads = "".join(f'<div class="hd">{d}</div>' for d in DOW_ORDER)
        panels.append(
            f'<section class="mo" data-month="{year}-{month:02d}" '
            f'aria-label="{MONTH_NAMES[month - 1]} {year}">'
            f'<h3>{MONTH_NAMES[month - 1]} {year}</h3>'
            f'<div class="grid">{heads}{"".join(cells)}</div></section>')
    return "".join(panels)


def whats_on(day):
    bits = []
    if day["direct"]:
        bits.append("Blocking: " + "; ".join(day["direct"]))
    if day["concert"]:
        bits.append("Competing: " + "; ".join(day["concert"]))
    if day["other"]:
        bits.append("Other comedy: " + "; ".join(day["other"]))
    return bits


# ---------------------------------------------------------------- events tab

def event_month(e):
    return (e.get("start") or "")[:7]


def filter_options(events):
    """The four multi-select facets, each as (value, label, count)."""
    def tally(key):
        seen = {}
        for e in events:
            value = key(e)
            if value:
                seen[value] = seen.get(value, 0) + 1
        return seen

    months = tally(event_month)
    month_opts = [(m, f"{MONTH_NAMES[int(m[5:7]) - 1][:3]} {m[:4]}", months[m])
                  for m in sorted(months)]
    def plain(key):
        counts = tally(lambda e: e.get(key))
        return [(v, v, counts[v]) for v in sorted(counts)]
    return {"month": month_opts, "artist": plain("artist"),
            "category": plain("category"), "language": plain("language")}


def facet_html(name, label, options):
    boxes = "".join(
        f'<label class="ms-opt"><input type="checkbox" data-facet="{name}" '
        f'value="{esc(value)}"><span>{esc(text)}</span>'
        f'<i>{count}</i></label>' for value, text, count in options)
    return (f'<details class="ms" data-ms="{name}">'
            f'<summary>{esc(label)}<span class="ms-badge" hidden></span></summary>'
            f'<div class="ms-menu">{boxes}</div></details>')


def events_html(events):
    if not events:
        return '<p class="muted">No events in the dataset.</p>'
    rows = []
    for e in sorted(events, key=lambda x: (x.get("start") or "", x.get("event") or "")):
        when = e.get("start") or ""
        try:
            when = date.fromisoformat(when).strftime("%a %-d %b %Y")
        except ValueError:
            pass
        if e.get("end"):
            try:
                when += " to " + date.fromisoformat(e["end"]).strftime("%-d %b")
            except ValueError:
                pass
        price = f'from AED {e["price_from_aed"]}' if e.get("price_from_aed") else "price n/a"
        language = e.get("language") or ""
        meta = " &middot; ".join(filter(None, [
            esc(e.get("city")), esc(e.get("category")),
            esc(language) if language != "Not stated" else "", esc(price)]))
        note = f'<p class="ev-note">{esc(e["notes"])}</p>' if e.get("notes") else ""
        # Retained after coming off Platinumlist. For a date still ahead that is news:
        # the show was pulled, sold out or cancelled.
        if not e.get("listed", True):
            seen = e.get("last_seen")
            note = (f'<p class="ev-gone">No longer listed on Platinumlist'
                    f'{" &middot; last seen " + esc(seen) if seen else ""}</p>') + note
        rows.append(
            f'<article class="ev" data-month="{esc(event_month(e))}" '
            f'data-start="{esc(e.get("start"))}" data-end="{esc(e.get("end") or "")}" '
            f'data-listed="{1 if e.get("listed", True) else 0}" '
            f'data-artist="{esc(e.get("artist"))}" '
            f'data-category="{esc(e.get("category"))}" '
            f'data-language="{esc(language)}">'
            f'<h4><a href="{esc(e.get("url"))}" rel="noopener noreferrer" '
            f'target="_blank">{esc(e.get("event"))}</a></h4>'
            f'<p class="ev-when">{esc(when)}'
            f'{" &middot; " + esc(e["time"]) if e.get("time") else ""}</p>'
            f'<p class="ev-where">{esc(e.get("venue")) or "Venue not listed"}</p>'
            f'<p class="ev-meta">{meta}</p>{note}</article>')
    return "".join(rows)


# ---------------------------------------------------------------- checklist tab

def choice(name, group, options, current, label=None):
    """A single-choice dropdown built from the same parts as the multi-select facets.

    A native <select> was the last control on the page rendering the operating system's
    own menu, which is why it looked foreign beside everything else. Radio inputs are
    kept, so this is still a real form control for keyboard and screen readers; only
    the presentation is ours.
    """
    rows = "".join(
        f'<label class="ms-opt"><input type="radio" name="{esc(group)}" '
        f'value="{esc(value)}"{" checked" if text == current or value == current else ""}>'
        f'<span>{esc(text)}</span></label>' for value, text in options)
    aria = f' aria-label="{esc(label)}"' if label else ""
    return (f'<details class="ms ms-choice" data-choice="{esc(name)}">'
            f'<summary{aria}><span class="ms-value">{esc(current)}</span>'
            f'{icon("down", "ic ms-caret")}</summary>'
            f'<div class="ms-menu">{rows}</div></details>')


def checklist_html(checklists):
    """The checklist shell only.

    The tasks themselves are rendered in the browser from the inlined payload plus
    whatever has been added locally. Rendering them here as well would mean two
    code paths building the same row, and only one of them able to grow.
    """
    if not checklists:
        return ('<p class="muted">No checklists yet. Import one with '
                '<code>python src/import_checklist.py &lt;workbook.xlsx&gt;</code>.</p>')

    picker = choice("checklist", "cl-which",
                    [(c["id"], c["title"]) for c in checklists],
                    checklists[0]["title"], "Which checklist")

    # The workbook's Setup tab is meant to be filled in, so these are inputs rather
    # than read-only text, saved alongside the task statuses.
    setup = "".join(
        f'<div class="cl-field" data-cl="{esc(c["id"])}" hidden>'
        + "".join(f'<div class="cl-f"><b>{esc(f["label"])}</b>'
                  f'<input type="text" data-field="{esc(f["label"])}" '
                  f'value="{esc(f["value"])}" placeholder="Not set">'
                  f'<i>{esc(f["note"])}</i></div>' for f in c.get("setup", []))
        + '</div>' for c in checklists)

    return f"""
 <div class="cl-account" id="cl-account" hidden></div>
 <div class="cl-bar">
  {picker}
  <label class="cl-date">Show date
   <input type="date" id="cl-date"></label>
  <button type="button" id="cl-export" class="icon-btn">Copy JSON</button>
 </div>
 <p class="cl-hint muted" id="cl-hint">Saved in this browser only. This site is static,
  so nothing is written back to the repository: use <b>Copy JSON</b> and paste into
  <code>data/checklists.json</code> to keep or share a change.</p>
 <div id="cl-progress" class="cl-progress"></div>
 <details class="cl-more">
  <summary>Progress by workstream</summary>
  <div id="cl-streams" class="cl-progress"></div>
 </details>
 <details class="cl-more">
  <summary>Show details and assumptions</summary>
  {setup}
 </details>

 <details class="cl-more cl-add">
  <summary>Add a task</summary>
  <div class="cl-add-form">
   <label class="cl-lab">Task
    <input type="text" id="add-task" placeholder="What needs doing"></label>
   <label class="cl-lab">Why it matters <span>optional</span>
    <input type="text" id="add-why" placeholder="What goes wrong if it slips"></label>
   <div class="cl-add-row">
    <label class="cl-lab">Workstream<span id="add-ws-holder"></span></label>
    <label class="cl-lab">or a new one
     <input type="text" id="add-ws-new" placeholder="New workstream"></label>
    <label class="cl-lab">Owner
     <input type="text" id="add-owner" placeholder="Who owns it"></label>
    <label class="cl-lab">Days before the show
     <input type="number" id="add-dminus" placeholder="e.g. 45" inputmode="numeric">
    </label>
   </div>
   <label class="cl-toggle"><input type="checkbox" id="add-blocking"> This one blocks
    the show</label>
   <div class="cl-add-actions">
    <button type="button" id="add-save" class="btn-primary">Add task</button>
    <span class="muted" id="add-msg"></span>
   </div>
  </div>
 </details>

 <div class="filters">
  <details class="ms" data-ms="workstream">
   <summary>Workstream<span class="ms-badge" hidden></span>{icon("down", "ic ms-caret")}
   </summary>
   <div class="ms-menu" id="cl-ws-menu"></div>
  </details>
  <label class="cl-toggle"><input type="checkbox" id="cl-blockers"> Blockers only</label>
  <label class="cl-toggle"><input type="checkbox" id="cl-open"> Hide done</label>
 </div>
 <div class="cl-tasks" id="cl-tasks"></div>
 <details class="cl-json"><summary>Checklist JSON</summary>
  <textarea id="cl-out" readonly rows="8"></textarea></details>
"""


# ---------------------------------------------------------------- payload

def encode(lenses):
    """Inline payload for the page.

    Three lenses over a year of dates repeats the same reason strings thousands of
    times, so strings are pooled into a table and referenced by index. That is what
    keeps the page a few hundred KB rather than well over a megabyte.
    """
    pool, index = [], {}

    def sid(text):
        if text not in index:
            index[text] = len(pool)
            pool.append(text)
        return index[text]

    days = {}
    for name, scored in lenses.items():
        for day in scored:
            rec = days.setdefault(day["date"], {"d": day["dow"], "h": day["holiday"],
                                                "L": {}})
            rec["L"][name] = {
                "t": day["tier"], "s": day["score"],
                "o": [sid(x) for x in whats_on(day)],
                "r": [sid(x) for x in day["reasons"]],
                "b": [sid(x) for x in day["boosts"]],
            }
    return days, pool


def manifest(stamp):
    return json.dumps({
        "name": "Events Tracker",
        "short_name": "Events Tracker",
        "description": "Which UAE dates are free for an Indian stand-up show, "
                       "scored against everything already on sale.",
        # Relative so the installed app scopes to /events-tracker/ on Pages.
        "start_url": ".",
        "scope": ".",
        "display": "standalone",
        "orientation": "portrait-primary",
        "background_color": "#f9f9f7",
        # Matches the header rather than the tier green, so the installed app's status
        # bar and splash are the app's own surface instead of a colour used for one
        # meaning inside it.
        "theme_color": "#f9f9f7",
        "icons": [
            {"src": "./icon-192.png", "sizes": "192x192", "type": "image/png",
             "purpose": "any"},
            {"src": "./icon-512.png", "sizes": "512x512", "type": "image/png",
             "purpose": "any"},
            {"src": "./icon-maskable-192.png", "sizes": "192x192", "type": "image/png",
             "purpose": "maskable"},
            {"src": "./icon-maskable-512.png", "sizes": "512x512", "type": "image/png",
             "purpose": "maskable"},
        ],
        "version": stamp,
    }, indent=1)


def service_worker(stamp):
    # Relative URLs inside a service worker resolve against the worker's own location,
    # so these stay correct under /events-tracker/ without hardcoding the repo name.
    return f"""// Generated by src/build_site.py. Cache name carries the build stamp so a
// daily rebuild supersedes the previous one.
const CACHE = 'comedy-tracker-{stamp}';
const ASSETS = ['./', './index.html', './manifest.webmanifest',
                './icon-192.png', './icon-512.png',
                './icon-maskable-192.png', './icon-maskable-512.png',
                './viability.json'];

self.addEventListener('install', e => {{
  e.waitUntil(caches.open(CACHE)
    .then(c => Promise.allSettled(ASSETS.map(a => c.add(a))))
    .then(() => self.skipWaiting()));
}});

self.addEventListener('activate', e => {{
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
}});

self.addEventListener('fetch', e => {{
  const req = e.request;
  if (req.method !== 'GET' || new URL(req.url).origin !== self.location.origin) return;
  // The page is rebuilt daily, so prefer the network for documents and fall back to
  // the cache when offline. Everything else is immutable enough to serve cache-first.
  if (req.mode === 'navigate') {{
    e.respondWith(fetch(req)
      .then(r => {{ const copy = r.clone();
                   caches.open(CACHE).then(c => c.put(req, copy)); return r; }})
      .catch(() => caches.match(req).then(r => r || caches.match('./index.html'))));
    return;
  }}
  e.respondWith(caches.match(req).then(hit => hit || fetch(req).then(r => {{
    const copy = r.clone();
    caches.open(CACHE).then(c => c.put(req, copy));
    return r;
  }}).catch(() => hit)));
}});
"""


# ---------------------------------------------------------------- css / js

CSS = """
:root{
 --surface-1:#fcfcfb; --plane:#f9f9f7; --ink:#0b0b0b; --ink-2:#52514e; --muted:#898781;
 --grid:#e1e0d9; --ring:rgba(11,11,11,.10);
 --good:#0ca30c; --info:#2a78d6; --crit:#d03b3b;
 --good-bg:rgba(12,163,12,.10); --info-bg:rgba(42,120,214,.09); --crit-bg:rgba(208,59,59,.08);
 --sheet:#fff;
 color-scheme:light;
}
@media (prefers-color-scheme:dark){ :root:where(:not([data-theme="light"])){
 --surface-1:#1a1a19; --plane:#0d0d0d; --ink:#fff; --ink-2:#c3c2b7; --muted:#898781;
 --grid:#2c2c2a; --ring:rgba(255,255,255,.10); --info:#3987e5;
 --good-bg:rgba(12,163,12,.16); --info-bg:rgba(57,135,229,.15); --crit-bg:rgba(208,59,59,.15);
 --sheet:#1a1a19;
 color-scheme:dark; }}
:root[data-theme="dark"]{
 --surface-1:#1a1a19; --plane:#0d0d0d; --ink:#fff; --ink-2:#c3c2b7; --muted:#898781;
 --grid:#2c2c2a; --ring:rgba(255,255,255,.10); --info:#3987e5;
 --good-bg:rgba(12,163,12,.16); --info-bg:rgba(57,135,229,.15); --crit-bg:rgba(208,59,59,.15);
 --sheet:#1a1a19;
 color-scheme:dark; }

*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
/* Kills the blue/grey rectangle Chrome and Safari flash over a tapped control. */
*{-webkit-tap-highlight-color:transparent}
button{-webkit-user-select:none;user-select:none}
/* Every icon is a sprite reference sized in em, so it follows whatever font-size the
   surrounding rule or container query already sets. */
svg.ic,svg.nv-ic,svg.th-ic{width:1em;height:1em;fill:none;stroke:currentColor;
 stroke-width:2;stroke-linecap:round;stroke-linejoin:round;flex:none;
 vertical-align:-.12em}
body{margin:0;background:var(--plane);color:var(--ink);
 font-family:system-ui,-apple-system,"Segoe UI",sans-serif;font-size:15px;line-height:1.5;
 /* clears the fixed bottom nav */
 padding-bottom:calc(66px + env(safe-area-inset-bottom))}
a{color:inherit}
.muted{color:var(--ink-2)}
.wrap{max-width:1180px;margin:0 auto;padding:0 14px 72px}

/* ---- sticky chrome: title row collapses, controls stay reachable one-handed ---- */
.top{position:sticky;top:0;z-index:30;background:var(--plane);
 border-bottom:1px solid var(--ring);padding:10px 14px 8px}
.top-in{max-width:1180px;margin:0 auto;display:flex;align-items:flex-start;gap:10px}
.top h1{font-size:17px;margin:0;letter-spacing:-.01em;flex:1;line-height:1.25}
/* A phone gets the app name and keeps it: the bottom bar already says which page you
   are on, so repeating it in the header spends the only line there is. */
#page-title{display:none}
.app-name{display:inline}
.top p{margin:2px 0 0;color:var(--ink-2);font-size:12.5px}
.controls{position:sticky;top:0;z-index:29;background:var(--plane);
 border-bottom:1px solid var(--ring);padding:8px 14px}
.controls-in{max-width:1180px;margin:0 auto;display:flex;gap:8px;align-items:center;
 flex-wrap:wrap}
.seg{display:flex;gap:4px;background:var(--surface-1);border:1px solid var(--ring);
 border-radius:10px;padding:3px;max-width:100%;overflow-x:auto;scrollbar-width:none}
.seg::-webkit-scrollbar{display:none}
/* Without nowrap the longer lens labels wrap to two lines on a phone. */
.seg button{white-space:nowrap}
button{font:inherit;font-size:13px;padding:8px 12px;border-radius:8px;cursor:pointer;
 border:1px solid transparent;background:transparent;color:var(--ink);min-height:38px}
.seg button{border:0;padding:7px 11px;min-height:34px}
.seg button[aria-pressed="true"]{background:var(--ink);color:var(--surface-1)}
.icon-btn{border:1px solid var(--ring);background:var(--surface-1);min-width:38px}
/* The theme control is the icon itself: no border, no filled surface. */
.ghost-btn{border:0;background:none;padding:6px;min-width:34px;min-height:34px;
 color:var(--ink-2);display:inline-flex;align-items:center;justify-content:center}
.ghost-btn:hover{color:var(--ink)}
.th-ic{font-size:19px}
.th-moon{display:none}
:root[data-theme="dark"] .th-sun{display:none}
:root[data-theme="dark"] .th-moon{display:inline-block}
@media (prefers-color-scheme:dark){
 :root:where(:not([data-theme="light"])) .th-sun{display:none}
 :root:where(:not([data-theme="light"])) .th-moon{display:inline-block}
}
.count{font-size:12.5px;color:var(--ink-2);margin-left:auto;font-variant-numeric:tabular-nums}

/* ---- the distinction that matters, kept in the page not in a tooltip ---- */
.scope{margin:14px 0 0;padding:11px 13px;border-radius:11px;background:var(--surface-1);
 border:1px solid var(--ring);font-size:13px;color:var(--ink-2)}
.scope b{color:var(--ink)}

h2{font-size:16px;margin:26px 0 10px;letter-spacing:-.01em}
h2 small{font-weight:400;color:var(--ink-2);font-size:12.5px;margin-left:6px}

.badge{display:inline-flex;align-items:center;gap:5px;font-size:10.5px;font-weight:700;
 letter-spacing:.05em;padding:4px 9px;border-radius:20px;border:1px solid currentColor;
 white-space:nowrap}
.b-prime{color:var(--good);background:var(--good-bg)}
.b-good{color:var(--info);background:var(--info-bg)}
.b-blocked{color:var(--crit);background:var(--crit-bg);
 background-image:repeating-linear-gradient(135deg,transparent 0 4px,var(--crit-bg) 4px 8px)}
.b-weak,.b-poor{color:var(--muted)}
/* ---- month grid: one month per screen, swipe between them ---- */
.mo-nav{display:flex;align-items:center;gap:8px;margin-bottom:8px}
.mo-nav .now{flex:1;font-size:14px;font-weight:600}
.months{display:flex;gap:12px;overflow-x:auto;scroll-snap-type:x mandatory;
 scrollbar-width:none;-webkit-overflow-scrolling:touch;margin:0 -14px;padding:0 14px}
.months::-webkit-scrollbar{display:none}
.mo{flex:0 0 100%;scroll-snap-align:center;background:var(--surface-1);
 border:1px solid var(--ring);border-radius:14px;padding:10px;container-type:inline-size}
/* When a panel is too narrow for the word, the icon and the hatch carry the tier
   instead. Both are non-colour signals, so a colour-blind reader still separates the
   tiers; the word is still in the cell's aria-label, the hover summary and the detail
   panel. Truncating it to "BLOCK..." would be worse than dropping it cleanly. */
/* Selectors are .grid-prefixed so they outrank the plain .day/.lb rules declared
   further down; container queries do not add specificity of their own. */
@container (max-width:339px){
 .grid .day{padding:2px}
 .grid .lb{font-size:5.6px}
}
@container (max-width:399px){
 .grid .day{padding:3px}
 .grid .dn{font-size:12.5px}
 .grid .ic{font-size:12px}
}
@container (min-width:400px){
 .grid .lb{font-size:7.5px;letter-spacing:.02em}
}
@container (min-width:470px){
 .grid .lb{font-size:9px;letter-spacing:.05em}
 .grid .dn{font-size:14px}
 .grid .ic{font-size:13px}
}
.mo h3{margin:0 0 8px;font-size:14.5px}
.grid{display:grid;grid-template-columns:repeat(7,1fr);gap:2px}
.hd{font-size:10.5px;color:var(--muted);text-align:center;padding-bottom:3px;
 letter-spacing:.04em}
/* min-width:0 matters. A 1fr track is minmax(auto,1fr) and will not shrink below its
   content, so without this the "BLOCKED" label sets a floor of about 55px per column.
   In a month that is entirely Ramadan-blocked all seven columns hit that floor at once
   and the grid overflows its panel. The ellipsis is a belt-and-braces guard; at the
   sizes below the label fits. */
.day{position:relative;min-height:54px;min-width:0;border-radius:8px;
 border:1px solid var(--grid);padding:3px;background:var(--surface-1);display:flex;
 flex-direction:column;gap:1px;align-items:flex-start;text-align:left;overflow:hidden}
.day.pad{border:0;background:none;pointer-events:none;min-height:0}
.dn{font-size:12.5px;color:var(--ink-2);font-variant-numeric:tabular-nums}
.ic{font-size:12px}
/* Sized so BLOCKED, the longest label, fits a phone cell without being
   ellipsised. Clipping it would leave colour doing the work on its own. */
.lb{font-size:6.5px;letter-spacing:0;color:var(--muted);margin-top:auto;font-weight:700;
 max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.t-prime{background:var(--good-bg);border-color:var(--good)}
.t-prime .ic,.t-prime .lb{color:var(--good);font-weight:700}
.t-prime .dn{color:var(--ink);font-weight:700}
.t-good{background:var(--info-bg);border-color:var(--info)}
.t-good .ic,.t-good .lb{color:var(--info);font-weight:650}
.t-blocked{background:var(--crit-bg);border-color:var(--crit);
 background-image:repeating-linear-gradient(135deg,transparent 0 5px,var(--crit-bg) 5px 10px)}
.t-blocked .ic,.t-blocked .lb{color:var(--crit);font-weight:700}
.t-weak,.t-poor{opacity:.75}
.day.past{opacity:.32}
.dim{opacity:.18}
.day:focus-visible,button:focus-visible{outline:2px solid var(--ink);
 outline-offset:2px}
[hidden]{display:none !important}

.legend{display:flex;gap:14px;flex-wrap:wrap;margin:14px 0 0;font-size:12.5px;
 color:var(--ink-2)}
.legend i{display:inline-block;width:11px;height:11px;border-radius:3px;margin-right:5px;
 vertical-align:-1px;border:1px solid var(--ring)}
.lg-item{display:inline-flex;align-items:center;gap:4px}
.lg-item .ic{font-size:13px}
.lg-item.t-prime .ic{color:var(--good)}
.lg-item.t-good .ic{color:var(--info)}
.lg-item.t-blocked .ic{color:var(--crit)}
.lg-item.t-weak .ic{color:var(--muted)}
/* the legend swatch carries the tier background; the row itself must not */
.legend .lg-item{background:none;border:0;padding:0}

/* ---- at a glance ---- */
.stats{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}
.stat{background:var(--surface-1);border:1px solid var(--ring);border-radius:12px;
 padding:13px 15px}
.stat b{display:block;font-size:26px;line-height:1.1;letter-spacing:-.02em}
.stat span{color:var(--ink-2);font-size:12.5px}

/* ---- events list ---- */
.events{display:grid;gap:9px}
.ev{background:var(--surface-1);border:1px solid var(--ring);border-radius:11px;
 padding:11px 12px}
.ev h4{margin:0 0 3px;font-size:14px;line-height:1.3}
.ev p{margin:1px 0;font-size:12.5px;color:var(--ink-2)}
.ev-when{color:var(--ink) !important;font-weight:600}
.ev-note{color:var(--muted) !important;font-style:italic}
.ev-gone{color:var(--crit) !important;font-weight:600}
.ev[data-listed="0"]{border-left:3px solid var(--crit)}

/* ---- limits, kept on the page rather than behind a click ---- */
.limits{margin-top:26px;padding:13px 15px;border-radius:12px;background:var(--surface-1);
 border:1px solid var(--ring);font-size:12.5px;color:var(--ink-2)}
.limits h2{margin-top:0}
.limits li{margin-bottom:5px}
.limits b{color:var(--ink)}
details{margin-top:10px}
summary{cursor:pointer;color:var(--ink-2);padding:5px 0}

/* ---- bottom sheet: reachable with a thumb ---- */
.sheet-bg{position:fixed;inset:0;background:rgba(0,0,0,.42);z-index:40;border:0;padding:0}
.sheet{position:fixed;left:0;right:0;bottom:0;z-index:41;background:var(--sheet);
 border-top-left-radius:16px;border-top-right-radius:16px;padding:14px 16px
 calc(20px + env(safe-area-inset-bottom));max-height:76vh;overflow:auto;
 box-shadow:0 -8px 30px rgba(0,0,0,.18)}
.sheet h3{margin:0 0 2px;font-size:16px}
.sheet .sub{color:var(--ink-2);font-size:12.5px;margin:0 0 10px}
.sheet ul{margin:6px 0 0;padding-left:18px;font-size:13px;color:var(--ink-2)}
.sheet .grab{width:38px;height:4px;border-radius:4px;background:var(--grid);
 margin:0 auto 10px}
.sheet .close{position:absolute;top:10px;right:12px}
.sec-head{display:flex;align-items:baseline;gap:8px}

/* ---- navigation ----
   One set of nav buttons, laid out two ways: a fixed bottom bar on a phone, a left rail
   on a laptop. Duplicating the markup per breakpoint would mean duplicate ids, so the
   switch is entirely in CSS. */
.side{position:fixed;left:0;right:0;bottom:0;z-index:30;display:flex;
 background:var(--surface-1);border-top:1px solid var(--ring);
 padding:5px 8px calc(5px + env(safe-area-inset-bottom))}
.side-brand,.side-foot{display:none}
.side-nav{display:flex;gap:4px;flex:1}
.side-nav button{flex:1;display:flex;flex-direction:column;align-items:center;
 justify-content:center;gap:3px;min-height:48px;padding:5px 4px;border-radius:11px;
 font-size:11px;letter-spacing:.01em;color:var(--muted);white-space:nowrap}
.side-nav button[aria-pressed="true"]{color:var(--ink);background:var(--plane);
 font-weight:650}
.nv-ic{font-size:19px}

/* ---- multi-select facets ---- */
.filters{display:flex;gap:8px;align-items:center;margin:14px 0 4px;
 flex-wrap:nowrap;overflow-x:auto;overflow-y:visible;scrollbar-width:none;
 margin-left:-14px;margin-right:-14px;padding:2px 14px}
.filters::-webkit-scrollbar{display:none}
.filters>*{flex:none}
.ms{position:relative}
.ms>summary{list-style:none;cursor:pointer;font-size:13px;padding:8px 12px;
 border:1px solid var(--ring);border-radius:9px;background:var(--surface-1);
 display:inline-flex;align-items:center;gap:6px;min-height:38px}
.ms>summary::-webkit-details-marker{display:none}
.ms>summary .ms-caret{color:var(--muted);font-size:13px;flex:none}
.ms[open]>summary .ms-caret{transform:rotate(180deg)}
.ms[open]>summary{border-color:var(--ink)}
.ms-badge{background:var(--ink);color:var(--surface-1);border-radius:20px;
 font-size:10.5px;font-weight:700;padding:1px 6px}
.ms-menu{position:fixed;z-index:35;left:10px;right:10px;
 bottom:calc(72px + env(safe-area-inset-bottom));max-height:52vh;overflow:auto;
 background:var(--surface-1);border:1px solid var(--ring);border-radius:12px;padding:6px;
 box-shadow:0 -8px 34px rgba(0,0,0,.24)}
.ms-opt{display:flex;align-items:center;gap:10px;padding:8px 9px;border-radius:8px;
 font-size:13px;cursor:pointer}
.ms-opt:hover{background:var(--plane)}
.ms-opt span{flex:1}
.ms-opt i{color:var(--muted);font-style:normal;font-size:11.5px}
/* accent-color themes the native box and tick in both palettes without rebuilding the
   control, which keeps the real checkbox and radio semantics intact. */
.ms-opt input{accent-color:var(--ink);width:16px;height:16px;margin:0;flex:none}
.ms-opt:has(input:checked){background:var(--plane)}
.ms-opt:has(input:checked) span{font-weight:600}

/* single-choice control: shows the current value and swaps it on pick */
.ms-choice>summary{justify-content:space-between;gap:10px;min-width:150px}
.ms-value{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tk .ms-choice>summary{min-width:118px;font-size:12px;padding:6px 10px;min-height:34px}
.tk .ms-choice .ms-menu{min-width:170px}
.cl-bar .ms-choice>summary{min-width:min(260px,60vw)}
.chip-clear{font-size:12.5px;color:var(--ink-2);text-decoration:underline;
 background:none;border:0;padding:6px 2px}

/* ---- checklist ---- */
.cl-bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:14px 0 6px}
/* The date field is the one native control left, so it takes the same shape as the
   dropdowns beside it. */
.cl-bar input[type="date"]{font:inherit;font-size:13px;padding:8px 12px;
 border-radius:9px;border:1px solid var(--ring);background:var(--surface-1);
 color:var(--ink);min-height:38px}
.cl-bar input[type="date"]:focus-visible{outline:2px solid var(--ink);outline-offset:2px}
.cl-date{display:flex;align-items:center;gap:6px;font-size:12.5px;color:var(--ink-2)}
.cl-hint{font-size:12px;margin:2px 0 10px}
.cl-hint code{font-size:11.5px}
.cl-progress{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
 gap:8px;margin-bottom:12px}
.cl-cell{background:var(--surface-1);border:1px solid var(--ring);border-radius:11px;
 padding:10px 12px}
.cl-cell b{display:block;font-size:19px;letter-spacing:-.02em}
.cl-cell span{font-size:11.5px;color:var(--ink-2)}
.cl-bar-track{height:5px;border-radius:4px;background:var(--grid);margin-top:6px;
 overflow:hidden}
.cl-bar-fill{height:100%;background:var(--good);width:0}
.cl-toggle{display:inline-flex;align-items:center;gap:6px;font-size:12.5px;
 color:var(--ink-2);margin-left:10px}
.cl-field{display:grid;gap:8px;margin:10px 0 14px}
.cl-f{background:var(--surface-1);border:1px solid var(--ring);border-radius:10px;
 padding:9px 11px;font-size:12.5px}
.cl-f b{display:block;font-size:13px;margin-bottom:4px}
.cl-f input{font:inherit;font-size:13px;width:100%;padding:6px 8px;border-radius:8px;
 border:1px solid var(--ring);background:var(--plane);color:var(--ink);min-height:34px}
.cl-f i{display:block;color:var(--muted);font-style:normal;margin-top:4px}
.cl-more{margin:4px 0 10px}
.cl-more>summary{font-size:13px;font-weight:600;color:var(--ink-2);padding:7px 0}
.cl-more[open]>summary{margin-bottom:6px}
.cl-tasks{display:grid;gap:8px;margin-top:10px}
.tk{background:var(--surface-1);border:1px solid var(--ring);border-left:3px solid
 var(--grid);border-radius:11px;padding:10px 12px}
.tk[data-blocking="1"]{border-left-color:var(--crit)}
.tk.done{opacity:.55;border-left-color:var(--good)}
.tk-head{display:flex;gap:10px;align-items:flex-start}
.tk-n{font-variant-numeric:tabular-nums;color:var(--muted);font-size:12px;min-width:22px}
.tk-body{flex:1;min-width:0}
.tk-task{margin:0;font-size:13.5px;font-weight:600}
.tk-why{margin:3px 0 0;font-size:12.5px;color:var(--ink-2)}
.tk-meta{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:7px;
 font-size:11.5px;color:var(--muted)}
.tk-ws{font-weight:700;color:var(--ink-2)}
.tk-flag{color:var(--crit);font-weight:700;letter-spacing:.05em}
.tk-due.over{color:var(--crit);font-weight:700}
/* account and sync status */
.cl-account{display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:12.5px;
 color:var(--ink-2);background:var(--surface-1);border:1px solid var(--ring);
 border-radius:11px;padding:10px 12px;margin:12px 0 4px}
.cl-account b{color:var(--ink)}
.cl-account .btn-primary{font-size:12.5px;padding:7px 12px;min-height:34px;
 margin-left:auto}
.sync-dot{width:8px;height:8px;border-radius:50%;background:var(--muted);flex:none}
.sync-dot.on{background:var(--good)}

/* ---- data freshness and the refresh control ---- */
.data-bar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:12px 0 2px;
 font-size:12.5px;color:var(--ink-2)}
.data-when.stale{color:var(--crit);font-weight:600}
.refresh-btn{display:inline-flex;align-items:center;gap:7px;font-size:12.5px;
 padding:7px 12px;min-height:34px}
.refresh-btn[disabled]{opacity:.55;cursor:progress}
.refresh-btn .ic{font-size:14px}
.refresh-btn.spin .ic{animation:spin 1.1s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.data-msg{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.data-msg.bad{color:var(--crit)}
.data-msg .chip-clear{padding:2px 2px}
.run-steps{margin:8px 0 0;padding-left:19px;font-size:12.5px;color:var(--ink-2)}
.run-steps li{margin-bottom:5px}
.run-steps code{font-size:11.5px;background:var(--plane);border:1px solid var(--ring);
 border-radius:5px;padding:1px 4px}

/* ---- the account dialog: one sign-in for the whole app ---- */
/* The header button carries a dot rather than a second colour, so signed-in state is
   legible without relying on the icon changing shade. */
#acct{position:relative}
.acct-dot{position:absolute;top:5px;right:5px;width:7px;height:7px;border-radius:50%;
 background:var(--good);border:1.5px solid var(--plane)}
.acct-sheet{max-width:none}
.acct-lead{margin:0 0 10px;font-size:13px;color:var(--ink-2);
 display:flex;align-items:baseline;gap:7px}
.acct-lead b{color:var(--ink)}
.acct-msg{margin:0 0 10px;font-size:12.5px;color:var(--ink-2);background:var(--plane);
 border:1px solid var(--ring);border-radius:9px;padding:8px 10px}
.acct-msg.bad{color:var(--crit);border-color:var(--crit)}
.acct-form{display:grid;gap:11px}
.acct-lab{display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--ink-2)}
.acct-lab input{font:inherit;font-size:14px;padding:9px 11px;border-radius:9px;
 border:1px solid var(--ring);background:var(--plane);color:var(--ink);min-height:40px;
 width:100%;box-sizing:border-box}
.acct-lab input:focus-visible{outline:2px solid var(--ink);outline-offset:1px}
.acct-actions{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.acct-alt{display:flex;align-items:center;gap:14px;flex-wrap:wrap;
 border-top:1px solid var(--ring);padding-top:10px}
.acct-note{margin:12px 0 0;font-size:11.5px;color:var(--muted)}

/* add-a-task form */
.cl-add-form{display:grid;gap:10px;background:var(--surface-1);border:1px solid var(--ring);
 border-radius:12px;padding:12px 13px;margin-top:4px}
.cl-lab{display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--ink-2)}
.cl-lab span{color:var(--muted)}
.cl-lab input[type="text"],.cl-lab input[type="number"]{font:inherit;font-size:13px;
 padding:8px 10px;border-radius:9px;border:1px solid var(--ring);background:var(--plane);
 color:var(--ink);min-height:38px;width:100%}
.cl-add-row{display:grid;gap:10px;grid-template-columns:1fr}
.cl-add-actions{display:flex;align-items:center;gap:10px}
.btn-primary{background:var(--ink);color:var(--surface-1);border:0;border-radius:9px;
 padding:9px 15px;font-weight:600;min-height:38px}
.tk-del{font-size:11px;color:var(--crit);background:none;border:0;padding:2px 4px;
 text-decoration:underline;min-height:auto}
.tk[data-added="1"] .tk-n::after{content:" new";color:var(--good);font-weight:700}
@media (min-width:700px){
 .cl-add-row{grid-template-columns:repeat(4,1fr)}
}

.cl-json textarea{width:100%;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
 font-size:11px;border-radius:9px;border:1px solid var(--ring);padding:8px;
 background:var(--surface-1);color:var(--ink)}

/* Very narrow phones (320px, an SE-sized screen) leave about 31px of cell for the
   label. Shrink it rather than let BLOCKED be ellipsised. */
/* Hover summary. Pointer devices get the prototype's tooltip back; touch devices never
   see it and use the detail panel instead. */
#tip{position:fixed;z-index:60;max-width:330px;background:var(--ink);color:var(--plane);
 font-size:12.5px;line-height:1.45;padding:9px 11px;border-radius:9px;
 pointer-events:none;opacity:0;transition:opacity .1s;box-shadow:0 6px 24px rgba(0,0,0,.25)}
#tip b{display:block;margin-bottom:3px}
#tip div{margin-top:3px}
#tip.on{opacity:1}

.only-desk{display:none}

@media (min-width:700px){
 .top h1{font-size:22px}
 .wrap{padding:0 20px 72px}
 .events{grid-template-columns:repeat(auto-fill,minmax(300px,1fr))}
 .stats{grid-template-columns:repeat(4,1fr)}
}

/* ---- laptop: not the phone layout stretched wide ---- */
@media (min-width:900px){
 .only-desk{display:inline}
 .only-mob{display:none}

 /* Column count steps up with width. Below, a container query on each panel drops
    the tier label once a panel is too narrow to render it, because at four months to
    a row a cell is about 37px and the word BLOCKED needs 37px on its own. */
 .months{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));
  gap:16px;overflow:visible;margin:0;padding:0}
 .mo{flex:none;padding:16px}
 .mo h3{font-size:16px;margin-bottom:10px}
 .mo-nav{display:none}
 .grid{gap:4px}
 .day{min-height:66px;padding:5px 7px}
 /* On a laptop a four-up panel gives a 31px cell. A label small enough to fit there
    is not readable, so below this width the icon and the hatch carry the tier on
    their own. Both are non-colour signals, and the word is still in the cell's
    aria-label, the hover summary and the detail panel. Phones keep their label at
    similar panel widths, because a 5.6px label on a high-density screen held close
    is legible in a way the same label on a laptop is not. */
 /* Threshold is the panel's content box, which excludes its 16px padding: a 334px
    panel measures 300px here. Three and four months to a row fall below it, two do
    not. */
 @container (max-width:299px){
  .grid .lb{display:none}
  .grid .ic{font-size:15px;margin-top:2px}
  .grid .day{min-height:54px;justify-content:flex-start}
 }
 .hd{font-size:11.5px;padding-bottom:6px}

 /* Controls are sized for thumbs on a phone; on a laptop that reads as oversized. */
 button{font-size:13px;min-height:32px;padding:6px 11px}
 .seg button{min-height:30px}
 .icon-btn{min-width:34px}
 .top{padding:12px 20px 10px}
 .controls{padding:7px 20px}

 /* The events list becomes a dense scannable table rather than a wall of cards. */
 .events{display:block;border:1px solid var(--ring);border-radius:12px;
  background:var(--surface-1);overflow:hidden}
 .ev{display:grid;grid-template-columns:2.3fr 1.2fr 1.7fr 1.1fr;gap:14px;
  align-items:baseline;border:0;border-bottom:1px solid var(--grid);border-radius:0;
  padding:10px 14px}
 .ev:last-child{border-bottom:0}
 .ev:hover{background:var(--plane)}
 .ev h4{margin:0;font-size:13.5px}
 .ev p{margin:0}
 .ev-note,.ev-gone{grid-column:1/-1;margin-top:2px !important}

 /* A bar pinned to the bottom of a 1440px screen is a phone idiom. Centre it. */
 .sheet{left:50%;top:50%;right:auto;bottom:auto;transform:translate(-50%,-50%);
  width:min(520px,92vw);max-height:78vh;border-radius:16px;
  padding:18px 20px 20px;box-shadow:0 24px 60px rgba(0,0,0,.3)}
 .sheet .grab{display:none}

 .filters{flex-wrap:wrap;overflow:visible;margin-left:0;margin-right:0;padding:2px 0}
 .ms-menu{position:absolute;top:calc(100% + 4px);bottom:auto;left:0;right:auto;
  min-width:210px;max-height:290px;box-shadow:0 12px 34px rgba(0,0,0,.16)}
 .cl-progress{grid-template-columns:repeat(auto-fit,minmax(170px,1fr))}
 .tk-task{font-size:14px}

 /* ---- the bottom bar becomes a left rail ---- */
 body{padding-bottom:0}
 .shell{display:grid;grid-template-columns:232px minmax(0,1fr);align-items:start}
 .side{position:sticky;top:0;left:auto;right:auto;bottom:auto;height:100vh;
  flex-direction:column;align-items:stretch;gap:14px;background:var(--plane);
  border-top:0;border-right:1px solid var(--ring);padding:20px 14px}
 .side-brand{display:block;padding:0 8px}
 .side-brand b{display:block;font-size:15px;letter-spacing:-.01em}
 .side-brand span{display:block;font-size:11.5px;color:var(--muted);margin-top:2px}
 .side-nav{flex:0 0 auto;flex-direction:column;gap:2px}
 .side-nav button{flex:0 0 auto;width:100%;flex-direction:row;justify-content:flex-start;
  gap:10px;text-align:left;min-height:36px;border-radius:9px;font-size:13.5px;
  color:var(--ink-2);font-weight:400}
 .side-nav button[aria-pressed="true"]{background:var(--ink);color:var(--surface-1);
  font-weight:600}
 .side-nav button:hover:not([aria-pressed="true"]){background:var(--surface-1)}
 .nv-ic{font-size:17px}
 .side-foot{display:flex;margin-top:auto;align-items:center;gap:8px;padding:0 6px}
 .side-stamp{font-size:11px;color:var(--muted);line-height:1.35}
 .top{position:static;border-bottom:0;padding:22px 24px 0}
 #page-title{display:inline}
 .app-name{display:none}
 .wrap{padding:0 24px 72px}
 .top-in,.wrap{max-width:1180px;margin-left:0}
}
@media (min-width:1150px){
 .months{grid-template-columns:repeat(3,minmax(0,1fr))}
}
@media (min-width:1350px){
 /* Twelve months as three rows of four. */
 .months{grid-template-columns:repeat(4,minmax(0,1fr))}
}
@media (min-width:1400px){
 .top-in,.wrap{max-width:1400px}
}
@media (min-width:1750px){
 .top-in,.wrap{max-width:1680px}
}

@media (prefers-reduced-motion:reduce){*{scroll-behavior:auto !important}}
"""

JS = """
(function(){
 'use strict';
 // Ids are read with getElementById, never as bare identifiers: an id like "f-all" is
 // not a valid JavaScript identifier and referencing it as one throws a ReferenceError
 // that kills every handler defined after it.
 var $ = function(id){ return document.getElementById(id); };
 var all = function(sel){ return Array.prototype.slice.call(document.querySelectorAll(sel)); };
 var DAYS = window.__DAYS__ || {};
 var POOL = window.__POOL__ || [];
 var TIER = window.__TIERS__ || {};
 var LENSES = window.__LENSES__ || {};
 var CHECK = window.__CHECKLISTS__ || [];
 var STATUS_LIST = window.__STATUSES__ || ['Not started'];
 var DEFAULT_LENS = window.__DEFAULT_LENS__ || 'standup';
 var text = function(i){ return POOL[i] || ''; };
 var svgIcon = function(name){
  return '<svg class="ic" aria-hidden="true" focusable="false"><use href="#i-' +
         name + '"></use></svg>';
 };
 var setIcon = function(el, name){
  var use = el && el.querySelector('use');
  if (use) use.setAttribute('href', '#i-' + name);
 };
 var store = {
  get: function(k, d){ try { var v = localStorage.getItem(k);
                             return v === null ? d : v; } catch (e) { return d; } },
  set: function(k, v){ try { localStorage.setItem(k, v); } catch (e) {} }
 };

 function localIso(d){
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') +
         '-' + String(d.getDate()).padStart(2, '0');
 }
 var TODAY = localIso(new Date());

 // ================================================================ tabs
 // The app is Events Tracker; each section is a page within it and names itself in
 // the heading and the browser tab.
 var APP = 'Events Tracker';
 var TABS = ['events', 'calendar', 'checklist'];
 var PAGE = {events: 'Events', calendar: 'Calendar', checklist: 'Checklist'};
 function showTab(name){
  if (TABS.indexOf(name) < 0) name = TABS[0];
  TABS.forEach(function(t){
   var btn = $('tab-' + t), panel = $('panel-' + t);
   if (btn) btn.setAttribute('aria-pressed', String(t === name));
   if (panel) panel.hidden = t !== name;
  });
  var title = $('page-title');
  if (title) title.textContent = PAGE[name];
  document.title = PAGE[name] + ' \u00b7 ' + APP;
  store.set('tab', name);
 }
 TABS.forEach(function(t){
  var btn = $('tab-' + t);
  if (btn) btn.addEventListener('click', function(){ showTab(t); });
 });

 // ================================================================ calendar
 var cells = all('.day[data-tier]');
 var countEl = $('count');
 var mode = 'all';
 var lens = DEFAULT_LENS;

 // Past dates are marked here rather than at build time, so the page is identical
 // whichever day it was built and a cached copy still marks the right days as gone.
 cells.forEach(function(el){
  var past = el.dataset.date < TODAY;
  el.dataset.past = past ? '1' : '0';
  el.classList.toggle('past', past);
 });

 function dayFor(iso){
  var rec = DAYS[iso];
  return rec && rec.L ? rec.L[lens] : null;
 }

 // Switching lens restyles every cell and card from the payload; the markup carries
 // only the default lens.
 function applyLens(next){
  lens = next;
  store.set('lens', next);
  all('[data-lens-opt]').forEach(function(b){
   b.setAttribute('aria-pressed', String(b.dataset.lensOpt === next));
  });
  var blurb = $('lens-blurb');
  if (blurb && LENSES[next]) blurb.textContent = LENSES[next].blurb || '';

  cells.forEach(function(el){
   var d = dayFor(el.dataset.date);
   if (!d) return;
   var t = TIER[d.t] || {icon: '', label: d.t};
   el.className = 'day t-' + d.t + (el.dataset.past === '1' ? ' past' : '');
   el.dataset.tier = d.t;
   setIcon(el.querySelector('.ic'), t.icon);
   el.querySelector('.lb').textContent = t.label;
   var label = el.getAttribute('aria-label') || '';
   el.setAttribute('aria-label',
     label.split(',')[0] + ', ' + d.t + ', score ' + d.s);
  });
  applyFilter(mode);
 }

 function matches(el){
  if (el.dataset.past === '1') return false;
  if (mode === 'wknd') return el.dataset.dow === 'Fri' || el.dataset.dow === 'Sat';
  if (mode === 'prime') return el.dataset.tier === 'prime';
  return true;
 }

 var filters = {all: $('f-all'), wknd: $('f-wknd'), prime: $('f-prime')};
 function applyFilter(next){
  mode = next;
  for (var k in filters) {
   if (filters[k]) filters[k].setAttribute('aria-pressed', String(k === mode));
  }
  var shown = 0;
  cells.forEach(function(el){
   var on = matches(el);
   el.classList.toggle('dim', !on);
   if (on) shown++;
  });
  // Counted from the calendar grid, which holds every date in the window.
  countEl.textContent = shown + (shown === 1 ? ' date shown' : ' dates shown');
  countEl.dataset.count = String(shown);
 }
 Object.keys(filters).forEach(function(k){
  if (filters[k]) filters[k].addEventListener('click', function(){ applyFilter(k); });
 });
 all('[data-lens-opt]').forEach(function(b){
  b.addEventListener('click', function(){ applyLens(b.dataset.lensOpt); });
 });

 // ---- detail sheet
 var sheet = $('sheet'), sheetBg = $('sheet-bg'), lastFocus = null;
 function openSheet(iso){
  var d = dayFor(iso);
  if (!d) return;
  var t = TIER[d.t] || {icon: '', label: d.t};
  var parts = new Date(iso + 'T00:00:00').toDateString().split(' ');
  $('sheet-title').textContent = parts[0] + ' ' + parts[2] + ' ' + parts[1] + ' ' + parts[3];
  $('sheet-sub').innerHTML = '<span class="badge b-' + d.t + '">' + svgIcon(t.icon) +
    '<span>' + t.label + '</span></span> ' +
    'score ' + d.s + ' &middot; ' + ((LENSES[lens] || {}).label || lens) +
    (DAYS[iso].h ? ' &middot; ' + DAYS[iso].h : '');
  function list(title, ids){
   if (!ids || !ids.length) return '';
   return '<p class="sub" style="margin:10px 0 0"><b>' + title + '</b></p><ul>' +
     ids.map(function(i){ return '<li>' + text(i) + '</li>'; }).join('') + '</ul>';
  }
  $('sheet-body').innerHTML =
    list('On that night', d.o) + list('Against it', d.r) + list('In its favour', d.b) +
    (!d.o.length && !d.r.length && !d.b.length
      ? '<p class="sub">Nothing scheduled against this date.</p>' : '');
  lastFocus = document.activeElement;
  sheet.hidden = false; sheetBg.hidden = false;
  $('sheet-close').focus();
 }
 function closeSheet(){
  if (sheet.hidden) return;
  sheet.hidden = true;
  if ((!acctSheet || acctSheet.hidden) && (!runSheet || runSheet.hidden)) {
   sheetBg.hidden = true;
  }
  if (lastFocus && lastFocus.focus) lastFocus.focus();
 }
 cells.forEach(function(el){
  el.addEventListener('click', function(){ openSheet(el.dataset.date); });
 });
 // One backdrop serves both dialogs, so dismissing has to close whichever is open.
 function closeDialogs(){ closeSheet(); closeAcct(); closeRun(); }
 sheetBg.addEventListener('click', closeDialogs);
 $('sheet-close').addEventListener('click', closeSheet);
 document.addEventListener('keydown', function(e){
  if (e.key === 'Escape') closeDialogs();
 });

 // ---- hover summary, pointer devices only
 if (matchMedia('(hover: hover) and (pointer: fine)').matches) {
  var tip = $('tip');
  var showTip = function(el){
   var d = dayFor(el.dataset.date);
   if (!d) return;
   var t = TIER[d.t] || {icon: '', label: d.t};
   var lines = d.o.concat(d.r).slice(0, 3);
   tip.innerHTML = '<b>' + (el.getAttribute('aria-label') || '').split(',')[0] +
     ' &middot; ' + t.label + ' ' + d.s + '</b>' +
     (lines.length ? lines.map(function(i){ return '<div>' + text(i) + '</div>'; }).join('')
                   : '<div>Nothing scheduled against you.</div>');
   tip.classList.add('on');
   var r = el.getBoundingClientRect();
   var w = tip.offsetWidth, h = tip.offsetHeight;
   var x = r.left + r.width / 2 - w / 2;
   var y = r.top - h - 8;
   if (y < 8) y = r.bottom + 8;
   tip.style.left = Math.max(8, Math.min(x, innerWidth - w - 8)) + 'px';
   tip.style.top = y + 'px';
  };
  var hideTip = function(){ tip.classList.remove('on'); };
  cells.forEach(function(el){
   el.addEventListener('mouseenter', function(){ showTip(el); });
   el.addEventListener('focus', function(){ showTip(el); });
   el.addEventListener('mouseleave', hideTip);
   el.addEventListener('blur', hideTip);
  });
 }

 var months = $('months');
 function page(dir){
  var panels = months.querySelectorAll('.mo');
  if (!panels.length) return;
  months.scrollBy({left: dir * (panels[0].getBoundingClientRect().width + 12),
                   behavior: 'smooth'});
 }
 if ($('mo-prev')) $('mo-prev').addEventListener('click', function(){ page(-1); });
 if ($('mo-next')) $('mo-next').addEventListener('click', function(){ page(1); });

 // ================================================================ events tab
 var evs = all('.ev');
 var evCount = $('ev-count');
 // Nothing checked in a facet means that facet is not constraining. Within a facet the
 // checks are OR; across facets they are AND.
 // An event counts as past once its last date has gone, so a run that started last
 // week but ends next month is still current. Past events stay in the dataset until
 // they drop off the listings, and are hidden unless asked for.
 function isPast(el){
  var last = el.dataset.end || el.dataset.start || '';
  return last !== '' && last < TODAY;
 }
 function evFilter(){
  var want = {};
  all('input[data-facet]').forEach(function(box){
   if (!box.checked) return;
   (want[box.dataset.facet] = want[box.dataset.facet] || []).push(box.value);
  });
  var showPast = $('ev-past') && $('ev-past').checked;
  var shown = 0, past = 0;
  evs.forEach(function(el){
   var ok = true;
   for (var facet in want) {
    if (want[facet].indexOf(el.dataset[facet] || '') < 0) { ok = false; break; }
   }
   if (ok && isPast(el)) {
    past++;
    if (!showPast) ok = false;
   }
   el.hidden = !ok;
   if (ok) shown++;
  });
  all('.ms[data-ms]').forEach(function(ms){
   var name = ms.dataset.ms;
   var n = (want[name] || []).length;
   var badge = ms.querySelector('.ms-badge');
   if (badge) { badge.textContent = n; badge.hidden = n === 0; }
  });
  if (evCount) {
   var hidden = showPast ? 0 : past;
   evCount.textContent = shown + (shown === 1 ? ' event' : ' events') +
     (hidden ? ' \u00b7 ' + hidden + ' past hidden' : '');
   evCount.dataset.count = String(shown);
   evCount.dataset.past = String(past);
  }
 }
 all('input[data-facet]').forEach(function(box){
  box.addEventListener('change', evFilter);
 });
 if ($('ev-clear')) $('ev-clear').addEventListener('click', function(){
  all('input[data-facet]').forEach(function(b){ b.checked = false; });
  if ($('ev-past')) $('ev-past').checked = false;
  evFilter();
 });
 if ($('ev-past')) $('ev-past').addEventListener('change', evFilter);
 // A click outside an open facet menu closes it.
 document.addEventListener('click', function(e){
  all('details.ms[open]').forEach(function(d){
   if (!d.contains(e.target)) d.open = false;
  });
 });

 // ================================================================ sync
 // Talks to Supabase over plain REST rather than pulling in supabase-js from a CDN,
 // because the page must render and keep working from cache with no network, and a
 // script tag pointed at another origin breaks that promise.
 //
 // localStorage stays the working store: every edit lands there first and the app is
 // fully usable signed out or offline. The server is a sync target, not the source of
 // truth for the current session.
 var BACKEND = window.__BACKEND__ || {};
 var SYNC_ON = !!(BACKEND.supabase_url && BACKEND.supabase_anon_key);
 var SESSION_KEY = 'sync:session';
 var syncNote = '';

 function session(){
  try { return JSON.parse(store.get(SESSION_KEY, 'null')); } catch (e) { return null; }
 }
 function setSession(s){
  if (s) store.set(SESSION_KEY, JSON.stringify(s));
  else { try { localStorage.removeItem(SESSION_KEY); } catch (e) {} }
 }

 function api(path, opts){
  opts = opts || {};
  var s = session();
  // Both key formats are accepted: the legacy anon JWT and the newer
  // sb_publishable_... key. When there is no session the key itself goes in
  // Authorization, which is what the official client does and what GoTrue expects
  // for the sign-in call.
  var headers = {
   'apikey': BACKEND.supabase_anon_key,
   'Authorization': 'Bearer ' + ((s && s.access_token) || BACKEND.supabase_anon_key),
   'Content-Type': 'application/json'
  };
  Object.keys(opts.headers || {}).forEach(function(k){ headers[k] = opts.headers[k]; });
  return fetch(BACKEND.supabase_url.replace(/\/$/, '') + path, {
   method: opts.method || 'GET', headers: headers,
   body: opts.body ? JSON.stringify(opts.body) : undefined
  });
 }

 // Confirmation and password-reset emails come back with the tokens in the URL
 // fragment. Take them, then strip them from the address bar so they are not left
 // sitting in history.
 var pendingRecovery = false;
 function adoptRedirect(){
  if (!location.hash || location.hash.indexOf('access_token') < 0) return false;
  var parts = {};
  location.hash.replace(/^#/, '').split('&').forEach(function(pair){
   var kv = pair.split('=');
   parts[decodeURIComponent(kv[0])] = decodeURIComponent(kv[1] || '');
  });
  if (!parts.access_token) return false;
  setSession({access_token: parts.access_token, refresh_token: parts.refresh_token,
              expires_at: Date.now() + (Number(parts.expires_in || 3600) * 1000),
              email: ''});
  // A recovery link signs you in so that you can set a password; landing on the
  // checklist with no prompt would leave the old password in place.
  pendingRecovery = parts.type === 'recovery';
  history.replaceState(null, '', location.pathname + location.search);
  return true;
 }

 function refreshIfStale(){
  var s = session();
  if (!s || !s.refresh_token) return Promise.resolve(false);
  if (s.expires_at && s.expires_at - Date.now() > 60000) return Promise.resolve(true);
  return api('/auth/v1/token?grant_type=refresh_token',
             {method: 'POST', body: {refresh_token: s.refresh_token}})
   .then(function(r){ return r.ok ? r.json() : null; })
   .then(function(j){
    if (!j || !j.access_token) { setSession(null); return false; }
    setSession({access_token: j.access_token, refresh_token: j.refresh_token,
                expires_at: Date.now() + (Number(j.expires_in || 3600) * 1000),
                email: (j.user && j.user.email) || (session() || {}).email || ''});
    return true;
   })
   .catch(function(){ return false; });
 }

 function whoAmI(){
  return api('/auth/v1/user').then(function(r){ return r.ok ? r.json() : null; })
   .then(function(j){
    if (!j || !j.email) return null;
    var s = session() || {};
    s.email = j.email;
    setSession(s);
    return j.email;
   }).catch(function(){ return null; });
 }

 // ---- sign in
 // Email and password, not a magic link. A link means the device you are signing in
 // on also needs the mailbox, which on a second phone is the moment you are least
 // likely to have it. The reset link stays for the one case a link is the only way in.
 var authMode = 'in';   // in | up | newpass
 var authBad = false;   // whether syncNote is an error, so it can be coloured

 function say(msg, bad){ syncNote = msg; authBad = !!bad; renderAccount(); }

 function authError(r){
  return r.json().then(function(j){
   return j.error_description || j.msg || j.message || ('Something went wrong (' +
                                                        r.status + ').');
  }).catch(function(){ return 'Something went wrong (' + r.status + ').'; });
 }

 function readable(msg){
  if (/invalid login/i.test(msg)) return 'That email and password do not match an account.';
  if (/not confirmed/i.test(msg)) return 'Confirm your address first: the link is in your inbox.';
  if (/already registered/i.test(msg)) return 'That address already has an account. Sign in instead.';
  return msg;
 }

 function adopt(j){
  setSession({access_token: j.access_token, refresh_token: j.refresh_token,
              expires_at: Date.now() + (Number(j.expires_in || 3600) * 1000),
              email: (j.user && j.user.email) || ''});
 }

 function signIn(email, password){
  say('Signing in...');
  return api('/auth/v1/token?grant_type=password',
             {method: 'POST', body: {email: email, password: password}})
   .then(function(r){
    if (!r.ok) return authError(r).then(function(m){ say(readable(m), true); });
    return r.json().then(function(j){
     adopt(j); authMode = 'in'; say(''); closeAcct();
     return pullAll();
    });
   })
   .catch(function(){ say('Could not reach the server.', true); });
 }

 function signUp(email, password){
  say('Creating the account...');
  return api('/auth/v1/signup', {method: 'POST', body: {email: email, password: password}})
   .then(function(r){
    if (!r.ok) return authError(r).then(function(m){ say(readable(m), true); });
    return r.json().then(function(j){
     if (j.access_token) {           // email confirmation switched off: straight in
      adopt(j); authMode = 'in'; say(''); closeAcct();
      return pullAll();
     }
     authMode = 'in';
     say('Account created. Confirm it from the email just sent, then sign in.');
    });
   })
   .catch(function(){ say('Could not reach the server.', true); });
 }

 function sendReset(email){
  var back = location.origin + location.pathname;
  say('Sending...');
  return api('/auth/v1/recover?redirect_to=' + encodeURIComponent(back),
             {method: 'POST', body: {email: email}})
   .then(function(r){
    if (!r.ok) return authError(r).then(function(m){ say(m, true); });
    say('Check ' + email + ' for a link to set a new password.');
   })
   .catch(function(){ say('Could not reach the server.', true); });
 }

 function setPassword(password){
  say('Saving...');
  return refreshIfStale().then(function(ok){
   if (!ok) { say('That sign-in has expired. Sign in again.', true); return; }
   return api('/auth/v1/user', {method: 'PUT', body: {password: password}})
    .then(function(r){
     if (!r.ok) return authError(r).then(function(m){ say(readable(m), true); });
     pendingRecovery = false; authMode = 'in';
     return whoAmI().then(function(){ say('Password updated.'); });
    });
  }).catch(function(){ say('Could not reach the server.', true); });
 }

 function signOut(){
  // Best effort: the local session goes either way, so a failed revoke cannot strand
  // anyone signed in on their own device.
  api('/auth/v1/logout', {method: 'POST'}).catch(function(){});
  setSession(null); authMode = 'in'; pendingRecovery = false; say('');
 }

 function pull(id){
  return refreshIfStale().then(function(ok){
   if (!ok) return null;
   return api('/rest/v1/checklist_state?select=data,updated_at&id=eq.' +
              encodeURIComponent(id))
    .then(function(r){ return r.ok ? r.json() : null; })
    .then(function(rows){ return rows && rows.length ? rows[0] : null; });
  }).catch(function(){ return null; });
 }

 function push(id){
  if (!SYNC_ON || !session()) return Promise.resolve(false);
  var state = clState(id);
  state.updated_at = new Date().toISOString();
  clSave(id, state, true);
  return refreshIfStale().then(function(ok){
   if (!ok) return false;
   return api('/rest/v1/checklist_state',
     {method: 'POST',
      headers: {'Prefer': 'resolution=merge-duplicates,return=minimal'},
      body: [{id: id, data: state, updated_by: (session() || {}).email || ''}]})
    .then(function(r){
     syncNote = r.ok ? 'Synced just now'
                     : (r.status === 401 || r.status === 403
                        ? 'Signed in, but this address is not on the allowlist.'
                        : 'Could not save to the server (' + r.status + '); kept on this device.');
     renderAccount();
     return r.ok;
    });
  }).catch(function(){
   syncNote = 'Offline; kept on this device.';
   renderAccount();
   return false;
  });
 }

 // Whole-document last-write-wins per checklist. Two people editing different tasks in
 // the same checklist within the same moment will have one write win; with a handful of
 // editors that is a rare, visible loss rather than a silent corruption, and the note
 // says who wrote last.
 function pullAll(){
  if (!SYNC_ON || !session()) return Promise.resolve();
  return Promise.all(CHECK.map(function(c){
   return pull(c.id).then(function(row){
    if (!row) return;
    var localState = clState(c.id);
    var mine = localState.updated_at || '';
    var theirs = row.updated_at || '';
    if (theirs > mine) {
     clSave(c.id, Object.assign({}, row.data, {updated_at: theirs}), true);
     syncNote = 'Updated from the server';
    } else if (mine && mine > theirs) {
     return push(c.id);
    }
   });
  })).then(function(){ renderAccount(); renderChecklist(); });
 }

 // ---- the account dialog
 // The account lives in the header rather than inside the Checklist tab, because it is
 // the app's account, not the checklist's. What it unlocks is still only the checklist:
 // the events list and the calendar are generated daily and identical for everyone, so
 // putting them behind a sign-in would add a door with nothing behind it.
 var acctSheet = $('acct-sheet');

 function openAcct(){
  if (!SYNC_ON || !acctSheet) return;
  say('');
  lastFocus = document.activeElement;
  acctSheet.hidden = false; sheetBg.hidden = false;
  var first = acctSheet.querySelector('input');
  (first || $('acct-close')).focus();
 }

 function closeAcct(){
  if (!acctSheet || acctSheet.hidden) return;
  acctSheet.hidden = true;
  if (sheet.hidden && (!runSheet || runSheet.hidden)) sheetBg.hidden = true;
  if (lastFocus && lastFocus.focus) lastFocus.focus();
 }

 function field(id, type, label, placeholder, complete){
  return '<label class="acct-lab" for="' + id + '">' + esc(label) +
   '<input id="' + id + '" type="' + type + '" autocomplete="' + complete +
   '" placeholder="' + esc(placeholder) + '"></label>';
 }

 function noteHtml(){
  return syncNote
   ? '<p class="acct-msg' + (authBad ? ' bad' : '') + '">' + esc(syncNote) + '</p>' : '';
 }

 function renderAcctBody(){
  var body = $('acct-body');
  if (!body) return;
  var s = session();
  // A sync finishing while someone is halfway through typing must not empty the
  // fields under them, so what is in them survives the re-render.
  var kept = {}, focused = document.activeElement;
  Array.prototype.forEach.call(body.querySelectorAll('input'), function(i){
   kept[i.id] = i.value;
  });
  var restore = function(){
   Object.keys(kept).forEach(function(id){
    var el = $(id);
    if (el && kept[id]) el.value = kept[id];
   });
   if (focused && focused.id && $(focused.id) && $(focused.id) !== focused) {
    var back = $(focused.id);
    if (back.focus) back.focus();
   }
  };

  if (s && (pendingRecovery || authMode === 'newpass')) {
   body.innerHTML = '<p class="acct-lead">Set a new password for <b>' +
     esc(s.email || 'your account') + '</b>.</p>' + noteHtml() +
     '<form class="acct-form" id="acct-form" novalidate>' +
     field('acct-new', 'password', 'New password', 'At least 8 characters', 'new-password') +
     '<div class="acct-actions"><button type="submit" class="btn-primary">Save password' +
     '</button><button type="button" id="acct-skip" class="chip-clear">Not now</button>' +
     '</div></form>';
   $('acct-form').addEventListener('submit', function(e){
    e.preventDefault();
    var p = $('acct-new').value || '';
    if (p.length < 8) { say('Use at least 8 characters.', true); return; }
    setPassword(p);
   });
   $('acct-skip').addEventListener('click', function(){
    pendingRecovery = false; authMode = 'in'; say(''); closeAcct();
   });
   restore();
   return;
  }

  if (s) {
   body.innerHTML = '<p class="acct-lead"><span class="sync-dot on"></span>' +
     '<span>Signed in as <b>' + esc(s.email || 'this account') + '</b>. Your ' +
     'checklists are on every device you sign in on.</span></p>' + noteHtml() +
     '<form class="acct-form" id="acct-form" novalidate>' +
     '<div class="acct-alt"><button type="button" id="acct-change" class="chip-clear">' +
     'Change password</button>' +
     '<button type="button" id="acct-out" class="chip-clear">Sign out</button></div>' +
     '</form>' +
     '<p class="acct-note">The events list and the calendar need no account: they are ' +
     'rebuilt daily and are the same for everyone.</p>';
   $('acct-form').addEventListener('submit', function(e){ e.preventDefault(); });
   $('acct-change').addEventListener('click', function(){
    authMode = 'newpass'; say('');
    var f = $('acct-new'); if (f) f.focus();
   });
   $('acct-out').addEventListener('click', signOut);
   restore();
   return;
  }

  var up = authMode === 'up';
  body.innerHTML = '<p class="acct-lead">' + (up
    ? '<span>Create an account and your checklists sync to every device you sign in ' +
      'on.</span>'
    : '<span>Sign in to use the same checklists on your laptop and your phone.</span>') +
    '</p>' + noteHtml() +
    '<form class="acct-form" id="acct-form" novalidate>' +
    field('acct-email', 'email', 'Email', 'you@example.com', 'email') +
    field('acct-pass', 'password', 'Password',
          up ? 'At least 8 characters' : '', up ? 'new-password' : 'current-password') +
    '<button type="submit" class="btn-primary">' +
    (up ? 'Create account' : 'Sign in') + '</button>' +
    '<div class="acct-alt"><button type="button" id="acct-swap" class="chip-clear">' +
    (up ? 'I already have an account' : 'Create an account') + '</button>' +
    (up ? '' : '<button type="button" id="acct-forgot" class="chip-clear">' +
               'Forgot password</button>') + '</div></form>' +
    '<p class="acct-note">Signing in is not the same as being let in: an address has ' +
    'to be on the allowlist before it can see anything.</p>';

  $('acct-form').addEventListener('submit', function(e){
   e.preventDefault();
   var email = ($('acct-email').value || '').trim();
   var pass = $('acct-pass').value || '';
   if (!email) { say('Enter your email address.', true); return; }
   if (up && pass.length < 8) { say('Use at least 8 characters.', true); return; }
   if (!pass) { say('Enter your password.', true); return; }
   (up ? signUp : signIn)(email, pass);
  });
  $('acct-swap').addEventListener('click', function(){
   authMode = up ? 'in' : 'up'; say('');
   var f = $('acct-email'); if (f) f.focus();
  });
  var forgot = $('acct-forgot');
  if (forgot) forgot.addEventListener('click', function(){
   var email = ($('acct-email').value || '').trim();
   if (!email) { say('Enter your email address first.', true); return; }
   sendReset(email);
  });
  restore();
 }

 // The checklist keeps a one-line status, because that is where the synced data is;
 // the controls themselves are in the dialog.
 function renderClAccount(){
  var box = $('cl-account');
  if (!box) return;
  if (!SYNC_ON) { box.hidden = true; return; }
  box.hidden = false;
  var s = session();
  box.innerHTML = s
   ? '<span class="sync-dot on"></span><span>Syncing as <b>' +
     esc(s.email || 'this account') + '</b></span>' +
     (syncNote ? '<span class="muted">' + esc(syncNote) + '</span>' : '') +
     '<button type="button" class="chip-clear" data-acct-open>Account</button>'
   : '<span class="sync-dot"></span><span>Saved on this device only.</span>' +
     (syncNote ? '<span class="muted">' + esc(syncNote) + '</span>' : '') +
     '<button type="button" class="btn-primary" data-acct-open>Sign in</button>';
  Array.prototype.forEach.call(box.querySelectorAll('[data-acct-open]'), function(b){
   b.addEventListener('click', openAcct);
  });
 }

 function renderAccount(){
  var hint = $('cl-hint');
  if (hint && SYNC_ON) {
   hint.innerHTML = session()
    ? 'Saved as you go and kept in step across your devices. The task list itself ' +
      'lives in the repository: use <b>Copy JSON</b> to fold a change back into ' +
      '<code>data/checklists.json</code>.'
    : 'Saved in this browser. Sign in to have the same checklists on your other ' +
      'devices.';
  }
  var btn = $('acct');
  if (btn) {
   btn.hidden = !SYNC_ON;
   var dot = btn.querySelector('.acct-dot');
   if (dot) dot.hidden = !session();
   btn.setAttribute('aria-label', session() ? 'Account, signed in' : 'Sign in');
  }
  renderAcctBody();
  renderClAccount();
 }

 // ================================================================ checklist tab
 // Tasks are rendered here rather than in the page source, so an imported task and
 // one added in the browser travel the same path and look the same.
 var current = CHECK.length ? CHECK[0].id : null;

 function clKey(id){ return 'checklist:' + id; }
 function clState(id){
  try { return JSON.parse(store.get(clKey(id), '{}')) || {}; } catch (e) { return {}; }
 }
 var pushTimer = null;
 // quiet=true means the write came from sync itself; pushing it back would loop.
 function clSave(id, state, quiet){
  store.set(clKey(id), JSON.stringify(state));
  if (quiet || !SYNC_ON || !session()) return;
  clearTimeout(pushTimer);
  pushTimer = setTimeout(function(){ push(id); }, 800);
 }
 function meta(id){
  return CHECK.filter(function(c){ return c.id === id; })[0] || {tasks: []};
 }
 // Imported tasks first, then anything added locally.
 function tasksFor(id){
  return meta(id).tasks.concat(clState(id).added || []);
 }
 function esc(s){
  return String(s === undefined || s === null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
 }
 function choiceHtml(name, group, options, currentValue){
  var rows = options.map(function(o){
   return '<label class="ms-opt"><input type="radio" name="' + esc(group) + '" value="' +
     esc(o) + '"' + (o === currentValue ? ' checked' : '') + '><span>' + esc(o) +
     '</span></label>';
  }).join('');
  return '<details class="ms ms-choice" data-choice="' + esc(name) + '">' +
    '<summary><span class="ms-value">' + esc(currentValue) + '</span>' +
    '<svg class="ic ms-caret" aria-hidden="true"><use href="#i-down"></use></svg>' +
    '</summary><div class="ms-menu">' + rows + '</div></details>';
 }

 function dueDate(showDate, dMinus){
  if (!showDate || dMinus === null || dMinus === undefined || dMinus === '') return '';
  var d = new Date(showDate + 'T00:00:00');
  d.setDate(d.getDate() - Number(dMinus));
  return localIso(d);
 }

 function taskHtml(id, t, status){
  var off = (t.d_minus === null || t.d_minus === undefined || t.d_minus === '')
    ? '' : (Number(t.d_minus) >= 0 ? 'D-' + t.d_minus : 'D+' + Math.abs(t.d_minus));
  return '<article class="tk" data-cl="' + esc(id) + '" data-n="' + esc(t.n) +
   '" data-ws="' + esc(t.workstream) + '" data-dminus="' +
   esc(t.d_minus === null || t.d_minus === undefined ? '' : t.d_minus) +
   '" data-blocking="' + (t.blocking ? 1 : 0) + '" data-added="' +
   (t.added ? 1 : 0) + '">' +
   '<div class="tk-head"><span class="tk-n">' + esc(t.n) + '</span>' +
   '<div class="tk-body"><p class="tk-task">' + esc(t.task) + '</p>' +
   (t.why ? '<p class="tk-why">' + esc(t.why) + '</p>' : '') + '</div>' +
   choiceHtml('status', 'st-' + id + '-' + t.n, STATUS_LIST, status) +
   '</div><div class="tk-meta"><span class="tk-ws">' + esc(t.workstream) + '</span>' +
   '<span>' + esc(t.owner || 'Unassigned') + '</span>' +
   '<span class="tk-off">' + esc(off) + '</span><span class="tk-due"></span>' +
   (t.blocking ? '<span class="tk-flag">BLOCKING</span>' : '') +
   (t.added ? '<button type="button" class="tk-del" data-del="' + esc(t.n) +
              '">Remove</button>' : '') +
   '</div></article>';
 }

 function renderChecklist(){
  if (!current) return;
  var m = meta(current);
  var state = clState(current);
  var showDate = state.show_date || m.show_date || '';
  if ($('cl-date')) $('cl-date').value = showDate;
  var pick = document.querySelector('.ms-choice[data-choice="checklist"] .ms-value');
  if (pick) pick.textContent = m.title;
  all('.cl-field').forEach(function(f){ f.hidden = f.dataset.cl !== current; });
  var fields = state.fields || {};
  all('.cl-field[data-cl="' + current + '"] input[data-field]').forEach(function(inp){
   var saved = fields[inp.dataset.field];
   if (saved !== undefined && inp.value !== saved) inp.value = saved;
  });

  var list = tasksFor(current);
  var statuses = state.statuses || {};

  // workstream filter, rebuilt because an added task can introduce a new one
  var streams = [];
  list.forEach(function(t){
   if (streams.indexOf(t.workstream) < 0) streams.push(t.workstream);
  });
  var wanted = [];
  all('#cl-ws-menu input').forEach(function(b){ if (b.checked) wanted.push(b.value); });
  $('cl-ws-menu').innerHTML = streams.map(function(s){
   return '<label class="ms-opt"><input type="checkbox" data-ws-filter value="' +
     esc(s) + '"' + (wanted.indexOf(s) >= 0 ? ' checked' : '') + '><span>' + esc(s) +
     '</span></label>';
  }).join('');
  var wsHolder = $('add-ws-holder');
  if (wsHolder && !wsHolder.dataset.built) {
   wsHolder.innerHTML = choiceHtml('add-ws', 'add-ws', streams, streams[0] || '');
   wsHolder.dataset.built = '1';
  }

  var blockersOnly = $('cl-blockers') && $('cl-blockers').checked;
  var hideDone = $('cl-open') && $('cl-open').checked;
  $('cl-tasks').innerHTML = list.map(function(t){
   return taskHtml(current, t, statuses[String(t.n)] || t.status || STATUS_LIST[0]);
  }).join('');

  var totals = {total: 0, done: 0, prog: 0, blockers: 0, overdue: 0};
  var byWs = {};
  all('.tk').forEach(function(el){
   var n = el.dataset.n;
   var status = statuses[n] || el.querySelector('input[type="radio"]:checked').value;
   el.classList.toggle('done', status === 'Done');

   var due = dueDate(showDate, el.dataset.dminus);
   var dueEl = el.querySelector('.tk-due');
   var overdue = due && due < TODAY && status !== 'Done' && status !== 'Not needed';
   dueEl.textContent = due ? (overdue ? 'due ' + due + ' \u00b7 overdue' : 'due ' + due) : '';
   dueEl.classList.toggle('over', !!overdue);

   if (status !== 'Not needed') {
    totals.total++;
    if (status === 'Done') totals.done++;
    else if (status === 'In progress') totals.prog++;
    if (el.dataset.blocking === '1' && status !== 'Done') totals.blockers++;
    if (overdue) totals.overdue++;
    var ws = el.dataset.ws;
    byWs[ws] = byWs[ws] || {t: 0, d: 0};
    byWs[ws].t++;
    if (status === 'Done') byWs[ws].d++;
   }
   el.hidden = (wanted.length && wanted.indexOf(el.dataset.ws) < 0)
     || (blockersOnly && el.dataset.blocking !== '1')
     || (hideDone && status === 'Done');
  });

  var pct = totals.total ? Math.round(100 * totals.done / totals.total) : 0;
  var tiles = [
   ['Complete', pct + '%', pct],
   ['Done', totals.done + ' of ' + totals.total, null],
   ['In progress', totals.prog, null],
   ['Open blockers', totals.blockers, null],
   ['Overdue', totals.overdue, null]
  ];
  $('cl-progress').innerHTML = tiles.map(function(c){
   return '<div class="cl-cell"><b>' + c[1] + '</b><span>' + c[0] + '</span>' +
     (c[2] === null ? '' : '<div class="cl-bar-track"><div class="cl-bar-fill" ' +
      'style="width:' + c[2] + '%"></div></div>') + '</div>';
  }).join('');
  if ($('cl-streams')) {
   $('cl-streams').innerHTML = Object.keys(byWs).map(function(ws){
    var w = byWs[ws];
    return '<div class="cl-cell"><b>' + w.d + '/' + w.t + '</b><span>' + esc(ws) +
      '</span><div class="cl-bar-track"><div class="cl-bar-fill" style="width:' +
      Math.round(100 * w.d / w.t) + '%"></div></div></div>';
   }).join('');
  }

  var out = $('cl-out');
  if (out) {
   var dump = JSON.parse(JSON.stringify(m));
   dump.show_date = showDate || null;
   dump.tasks = list.map(function(t){
    var copy = JSON.parse(JSON.stringify(t));
    copy.status = statuses[String(t.n)] || copy.status || STATUS_LIST[0];
    return copy;
   });
   (dump.setup || []).forEach(function(f){
    if (fields[f.label] !== undefined) f.value = fields[f.label];
   });
   out.value = JSON.stringify(dump, null, 1);
  }
  var badge = document.querySelector('.ms[data-ms="workstream"] .ms-badge');
  if (badge) { badge.textContent = wanted.length; badge.hidden = !wanted.length; }
 }

 // Delegated, because the rows are replaced on every render.
 $('cl-tasks').addEventListener('change', function(e){
  if (!e.target || e.target.type !== 'radio') return;
  var task = e.target.closest('.tk');
  var box = e.target.closest('.ms-choice');
  if (!task) return;
  var state = clState(task.dataset.cl);
  state.statuses = state.statuses || {};
  state.statuses[task.dataset.n] = e.target.value;
  clSave(task.dataset.cl, state);
  if (box) box.open = false;
  renderChecklist();
 });
 $('cl-tasks').addEventListener('click', function(e){
  var del = e.target.closest('.tk-del');
  if (!del) return;
  var state = clState(current);
  state.added = (state.added || []).filter(function(t){
   return String(t.n) !== String(del.dataset.del);
  });
  if (state.statuses) delete state.statuses[del.dataset.del];
  clSave(current, state);
  renderChecklist();
 });
 $('cl-ws-menu').addEventListener('change', renderChecklist);

 if ($('add-save')) $('add-save').addEventListener('click', function(){
  var text = ($('add-task').value || '').trim();
  var msg = $('add-msg');
  if (!text) { msg.textContent = 'Give the task a name first.'; return; }
  var picked = document.querySelector('#add-ws-holder input[type="radio"]:checked');
  var ws = ($('add-ws-new').value || '').trim() ||
           (picked ? picked.value : '') || 'General';
  var raw = ($('add-dminus').value || '').trim();
  var state = clState(current);
  state.added = state.added || [];
  // Numbering continues past everything already in the list, so an added task never
  // collides with an imported one, and removing one does not renumber the rest.
  var highest = 0;
  tasksFor(current).forEach(function(t){ highest = Math.max(highest, Number(t.n) || 0); });
  state.added.push({
   n: highest + 1, workstream: ws, task: text,
   why: ($('add-why').value || '').trim(), owner: ($('add-owner').value || '').trim(),
   blocking: !!$('add-blocking').checked,
   d_minus: raw === '' ? null : Number(raw),
   status: STATUS_LIST[0], added: true
  });
  clSave(current, state);
  ['add-task', 'add-why', 'add-owner', 'add-dminus', 'add-ws-new'].forEach(function(id){
   if ($(id)) $(id).value = '';
  });
  $('add-blocking').checked = false;
  msg.textContent = 'Added to ' + ws + '.';
  setTimeout(function(){ msg.textContent = ''; }, 2500);
  renderChecklist();
 });

 if ($('cl-date')) $('cl-date').addEventListener('change', function(e){
  var state = clState(current);
  state.show_date = e.target.value;
  clSave(current, state);
  renderChecklist();
 });
 ['cl-blockers', 'cl-open'].forEach(function(id){
  if ($(id)) $(id).addEventListener('change', renderChecklist);
 });
 all('.cl-field input[data-field]').forEach(function(inp){
  inp.addEventListener('change', function(){
   var owner = inp.closest('.cl-field').dataset.cl;
   var state = clState(owner);
   state.fields = state.fields || {};
   state.fields[inp.dataset.field] = inp.value;
   clSave(owner, state);
   renderChecklist();
  });
 });
 // The checklist picker lives outside #cl-tasks, so it binds separately.
 var pickBox = document.querySelector('.ms-choice[data-choice="checklist"]');
 if (pickBox) pickBox.addEventListener('change', function(e){
  if (!e.target || e.target.type !== 'radio') return;
  current = e.target.value;
  pickBox.open = false;
  renderChecklist();
 });
 if ($('cl-export')) $('cl-export').addEventListener('click', function(){
  var out = $('cl-out');
  if (!out) return;
  out.select();
  var done = false;
  if (navigator.clipboard && navigator.clipboard.writeText) {
   navigator.clipboard.writeText(out.value); done = true;
  }
  $('cl-export').textContent = done ? 'Copied' : 'Select and copy';
  setTimeout(function(){ $('cl-export').textContent = 'Copy JSON'; }, 1800);
 });

 // ================================================================ account button
 if ($('acct')) $('acct').addEventListener('click', openAcct);
 if ($('acct-close')) $('acct-close').addEventListener('click', closeAcct);

 // ================================================================ refresh
 // This page is static; the scrape is a GitHub Actions workflow. So the button cannot
 // run anything itself, it can only ask GitHub to start the workflow, and GitHub will
 // not take that from an anonymous page. Hence a token: yours, stored in this browser
 // and nowhere else, sent to api.github.com and nowhere else. Without one the button
 // still works, it just hands you off to the Actions page instead.
 var REPO = window.__REPO__ || {};
 var STAMP = window.__STAMP__ || '';
 var TOKEN_KEY = 'gh:token';
 var runSheet = $('run-sheet');
 var runTimer = null;

 function token(){ return store.get(TOKEN_KEY, ''); }
 function actionsUrl(){
  return 'https://github.com/' + REPO.slug + '/actions/workflows/' + REPO.workflow;
 }

 function ghApi(path, opts){
  opts = opts || {};
  return fetch('https://api.github.com/repos/' + REPO.slug + path, {
   method: opts.method || 'GET',
   headers: {'Accept': 'application/vnd.github+json',
             'Authorization': 'Bearer ' + token(),
             'X-GitHub-Api-Version': '2022-11-28'},
   body: opts.body ? JSON.stringify(opts.body) : undefined
  });
 }

 function daysSince(iso){
  var then = new Date(iso + 'T00:00:00'), now = new Date(TODAY + 'T00:00:00');
  return Math.round((now - then) / 86400000);
 }

 function renderStamp(){
  var el = $('data-when');
  if (!el || !STAMP) return;
  var n = daysSince(STAMP);
  var when = n <= 0 ? 'today' : (n === 1 ? 'yesterday' : n + ' days ago');
  el.textContent = 'Listings last checked ' + when;
  // A day behind is normal: the scrape runs at 07:00 Dubai. Two days is not.
  el.classList.toggle('stale', n > 1);
  el.title = 'Data as of ' + STAMP;
 }

 function busy(on){
  var b = $('refresh');
  if (!b) return;
  b.disabled = on;
  b.classList.toggle('spin', on);
 }

 function msg(text, bad, extra){
  var box = $('data-msg');
  if (!box) return;
  box.className = 'data-msg' + (bad ? ' bad' : '');
  box.innerHTML = esc(text) + (extra || '');
  var again = box.querySelector('[data-reload]');
  if (again) again.addEventListener('click', function(){ location.reload(); });
 }

 function startRun(){
  busy(true);
  msg('Asking GitHub to run the scrape...');
  var at = Date.now();
  ghApi('/actions/workflows/' + REPO.workflow + '/dispatches',
        {method: 'POST', body: {ref: REPO.branch}})
   .then(function(r){
    if (r.status === 204) { msg('Started. It takes about three minutes.'); watchRun(at); return; }
    busy(false);
    if (r.status === 401) { msg('GitHub rejected that token.', true); openRun(); return; }
    if (r.status === 403) {
     msg('That token is not allowed to start workflows.', true); openRun(); return;
    }
    if (r.status === 404) {
     msg('GitHub cannot see this repository with that token.', true); openRun(); return;
    }
    msg('GitHub refused the request (' + r.status + ').', true);
   })
   .catch(function(){ busy(false); msg('Could not reach GitHub.', true); });
 }

 // Polls the workflow rather than trusting the 204: a dispatch that starts and then
 // fails its own guards publishes nothing, and saying "done" there would be a lie.
 function watchRun(since){
  var tries = 0;
  clearInterval(runTimer);
  runTimer = setInterval(function(){
   tries += 1;
   if (tries > 60) {
    clearInterval(runTimer); busy(false);
    msg('Still running after twelve minutes.', true,
        ' <a href="' + actionsUrl() + '" target="_blank" rel="noopener">Open GitHub</a>');
    return;
   }
   ghApi('/actions/workflows/' + REPO.workflow +
         '/runs?event=workflow_dispatch&per_page=1')
    .then(function(r){ return r.ok ? r.json() : null; })
    .then(function(j){
     var run = j && j.workflow_runs && j.workflow_runs[0];
     if (!run || new Date(run.created_at).getTime() < since - 120000) {
      msg('Waiting for GitHub to pick it up...');
      return;
     }
     if (run.status !== 'completed') { msg('Running... ' + run.status.replace('_', ' ')); return; }
     clearInterval(runTimer); busy(false);
     if (run.conclusion === 'success') {
      msg('Finished.', false,
          ' <button type="button" class="chip-clear" data-reload>Reload for the new data</button>');
     } else {
      msg('That run ended as ' + run.conclusion + ', so nothing was published.', true,
          ' <a href="' + run.html_url + '" target="_blank" rel="noopener">See why</a>');
     }
    })
    .catch(function(){});
  }, 12000);
 }

 function openRun(){
  if (!runSheet) return;
  renderRunBody();
  lastFocus = document.activeElement;
  runSheet.hidden = false; sheetBg.hidden = false;
  var f = $('gh-token');
  (f || $('run-close')).focus();
 }

 function closeRun(){
  if (!runSheet || runSheet.hidden) return;
  runSheet.hidden = true;
  if (sheet.hidden && (!acctSheet || acctSheet.hidden)) sheetBg.hidden = true;
  if (lastFocus && lastFocus.focus) lastFocus.focus();
 }

 function renderRunBody(){
  var body = $('run-body');
  if (!body) return;
  var have = !!token();
  body.innerHTML =
   '<p class="acct-lead"><span>The scrape runs on GitHub, not in this page. To start ' +
   'it from here, this browser needs a token of your own. It is kept on this device ' +
   'and sent only to github.com.</span></p>' +
   '<ol class="run-steps">' +
   '<li>Open <a href="https://github.com/settings/personal-access-tokens/new" ' +
   'target="_blank" rel="noopener">GitHub fine-grained tokens</a>.</li>' +
   '<li>Repository access: <b>Only select repositories</b>, then <code>' +
   esc(REPO.slug) + '</code>.</li>' +
   '<li>Repository permissions: <b>Actions</b> set to <b>Read and write</b>. Nothing ' +
   'else.</li>' +
   '<li>Generate it, copy it, paste it below.</li></ol>' +
   '<form class="acct-form" id="run-form" novalidate style="margin-top:12px">' +
   '<label class="acct-lab" for="gh-token">Token<input id="gh-token" type="password" ' +
   'autocomplete="off" placeholder="' + (have ? 'Saved on this device' : 'github_pat_...') +
   '"></label>' +
   '<div class="acct-actions"><button type="submit" class="btn-primary">' +
   (have ? 'Replace and run' : 'Save and run') + '</button>' +
   (have ? '<button type="button" id="gh-forget" class="chip-clear">Forget it</button>'
         : '') +
   '<a class="chip-clear" href="' + actionsUrl() + '" target="_blank" ' +
   'rel="noopener">Run it on GitHub instead</a></div></form>' +
   '<p class="acct-note">A fine-grained token scoped to one repository and to Actions ' +
   'can start this workflow and do nothing else. Anyone with the device can use it, ' +
   'so on a shared machine use the GitHub link instead.</p>';

  $('run-form').addEventListener('submit', function(e){
   e.preventDefault();
   var t = ($('gh-token').value || '').trim();
   if (!t && !have) { msg('Paste a token first.', true); return; }
   if (t) store.set(TOKEN_KEY, t);
   closeRun();
   startRun();
  });
  var forget = $('gh-forget');
  if (forget) forget.addEventListener('click', function(){
   try { localStorage.removeItem(TOKEN_KEY); } catch (e) {}
   closeRun();
   msg('Token removed from this device.');
  });
 }

 if ($('refresh') && REPO.slug) {
  $('refresh').hidden = false;
  $('refresh').addEventListener('click', function(){
   if (token()) startRun(); else openRun();
  });
 }
 if ($('run-close')) $('run-close').addEventListener('click', closeRun);
 renderStamp();

 // ================================================================ theme
 $('theme').addEventListener('click', function(){
  var root = document.documentElement;
  var cur = root.getAttribute('data-theme');
  var dark = cur ? cur === 'dark'
                 : matchMedia('(prefers-color-scheme: dark)').matches;
  var next = dark ? 'light' : 'dark';
  root.setAttribute('data-theme', next);
  store.set('theme', next);
 });

 // ================================================================ initial state
 applyLens(LENSES[store.get('lens', '')] ? store.get('lens', '') : DEFAULT_LENS);
 evFilter();
 renderChecklist();
 showTab(store.get('tab', TABS[0]));

 if (SYNC_ON) {
  var arrived = adoptRedirect();
  renderAccount();
  // A reset link lands signed in with a password nobody knows; open the dialog on the
  // one field that matters rather than leaving it to be found.
  if (pendingRecovery) openAcct();
  if (session()) {
   (arrived ? whoAmI() : Promise.resolve((session() || {}).email))
    .then(function(){ renderAccount(); return pullAll(); });
  }
 }

 var panel = months.querySelector('[data-month="' + TODAY.slice(0, 7) + '"]');
 if (panel) months.scrollLeft = panel.offsetLeft - months.offsetLeft;

 if ('serviceWorker' in navigator) {
  window.addEventListener('load', function(){
   navigator.serviceWorker.register('./sw.js').catch(function(){});
  });
 }
})();
"""


# ---------------------------------------------------------------- page

def repo_info(backend):
    """Which repository the refresh button should ask to run the workflow.

    Taken from the git remote so a fork or a rename needs no edit, and overridable
    from data/backend.json for the case where the two differ.
    """
    slug = (backend or {}).get("github_repo", "")
    if not slug:
        try:
            out = subprocess.run(["git", "remote", "get-url", "origin"], cwd=ROOT,
                                 capture_output=True, text=True, timeout=10)
            url = out.stdout.strip() if out.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            url = ""
        m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", url)
        slug = m.group(1) if m else ""
    if not slug:
        return {}
    return {"slug": slug,
            "workflow": (backend or {}).get("github_workflow") or "daily.yml",
            "branch": (backend or {}).get("github_branch") or "main"}


def render(viab, cfg, stamp, checklists, backend=None, repo=None):
    days = viab["days"]
    events = viab.get("events", [])
    lenses = viab.get("lenses") or {viab.get("default_lens", "standup"): days}
    lens_meta = viab.get("lens_meta") or {}
    default_lens = viab.get("default_lens", "standup")

    # Counted over the whole window rather than from today, so the headline numbers
    # match what viability.py reports and do not drift as dates roll past.
    counts = {}
    for day in days:
        counts[day["tier"]] = counts.get(day["tier"], 0) + 1
    clash = len({d["date"] for d in days if d["direct"]})
    ram_s, ram_e = cfg["ramadan"]
    ram_days = sum(1 for d in days if ram_s <= d["date"] <= ram_e)

    first, last = days[0]["date"], days[-1]["date"]
    span = (f'{date.fromisoformat(first).strftime("%B %Y")} to '
            f'{date.fromisoformat(last).strftime("%B %Y")}')

    stats = [
        (counts.get("prime", 0), "prime dates to shortlist"),
        (clash, "nights taken by a competing act"),
        (counts.get("blocked", 0), "dates ruled out in total"),
        (ram_days, f'days lost to Ramadan ({date.fromisoformat(ram_s):%-d %b} to '
                   f'{date.fromisoformat(ram_e):%-d %b %Y})'),
    ]
    stat_html = "".join(f'<div class="stat"><b>{n}</b><span>{esc(t)}</span></div>'
                        for n, t in stats)

    legend = "".join(
        f'<span class="lg-item t-{t}"><i style="background:{bg};border-color:{bc}"></i>'
        f'{icon(TIERS[t]["icon"])} {TIERS[t]["label"].title()} '
        f'&ndash; {TIERS[t]["blurb"]}</span>'
        for t, bg, bc in [
            ("prime", "var(--good-bg)", "var(--good)"),
            ("good", "var(--info-bg)", "var(--info)"),
            ("weak", "var(--surface-1)", "var(--ring)"),
            ("blocked", "var(--crit-bg)", "var(--crit)"),
        ])

    lens_buttons = "".join(
        f'<button type="button" data-lens-opt="{esc(name)}" '
        f'aria-pressed="{"true" if name == default_lens else "false"}">'
        f'{esc(lens_meta.get(name, {}).get("label", name))}</button>'
        for name in lenses)

    facets = filter_options(events)
    facet_bar = "".join(facet_html(name, label, facets[name]) for name, label in [
        ("month", "Month"), ("artist", "Artist"),
        ("category", "Category"), ("language", "Language")])

    payload, pool = encode(lenses)
    tier_js = {k: {"icon": v["icon"], "label": v["label"]} for k, v in TIERS.items()}
    checklist_js = [{"id": c["id"], "title": c["title"], "subtitle": c.get("subtitle", ""),
                     "show_date": c.get("show_date"), "setup": c.get("setup", []),
                     "tasks": c["tasks"]} for c in checklists]

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Events Tracker</title>
<meta name="description" content="Events Tracker: every comedy and desi event on sale in Dubai and Abu Dhabi, which dates are viable for staging a show, and checklists for the shows you are running.">
<link rel="manifest" href="./manifest.webmanifest">
<link rel="icon" href="./icon-192.png" type="image/png">
<link rel="apple-touch-icon" href="./icon-maskable-192.png">
<meta name="theme-color" media="(prefers-color-scheme: light)" content="#f9f9f7">
<meta name="theme-color" media="(prefers-color-scheme: dark)" content="#0d0d0d">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<script>
// Applied before first paint so a stored choice does not flash the other palette.
try{{var t=localStorage.getItem('theme');
if(t==='dark'||t==='light')document.documentElement.setAttribute('data-theme',t);}}catch(e){{}}
</script>
<style>{CSS}</style>
</head>
<body>
{sprite()}
<div class="shell">
<aside class="side">
 <div class="side-brand"><b>Events Tracker</b><span>Dubai and Abu Dhabi</span></div>
 <nav class="side-nav" aria-label="Sections">
  <button type="button" id="tab-events" aria-pressed="true">
   {icon("events", "nv-ic")}<span>Events</span></button>
  <button type="button" id="tab-calendar" aria-pressed="false">
   {icon("calendar", "nv-ic")}<span>Calendar</span></button>
  <button type="button" id="tab-checklist" aria-pressed="false">
   {icon("checklist", "nv-ic")}<span>Checklist</span></button>
 </nav>
 <div class="side-foot">
  <span class="side-stamp">Checked daily<br>data as of {esc(stamp)}</span>
 </div>
</aside>

<main class="col">
<header class="top">
 <div class="top-in">
  <h1><span class="app-name">Events Tracker</span><span id="page-title">Events</span></h1>
  <button type="button" id="acct" class="ghost-btn" hidden
   aria-haspopup="dialog" aria-label="Account">{icon("user", "th-ic")}
   <span class="acct-dot" hidden></span></button>
  <button type="button" id="theme" class="ghost-btn"
   aria-label="Switch theme">{icon("sun", "th-ic th-sun")}{icon("moon", "th-ic th-moon")}</button>
 </div>
</header>

<div class="wrap">

 <!-- ------------------------------------------------------------- events -->
 <section id="panel-events" hidden>
  <div class="data-bar">
   <span class="data-when" id="data-when">Data as of {esc(stamp)}</span>
   <button type="button" id="refresh" class="icon-btn refresh-btn" hidden>
    {icon("refresh")}<span>Refresh now</span></button>
   <span class="data-msg" id="data-msg" role="status" aria-live="polite"></span>
  </div>
  <div class="filters">
   {facet_bar}
   <label class="cl-toggle"><input type="checkbox" id="ev-past"> Show past</label>
   <button type="button" id="ev-clear" class="chip-clear">Clear filters</button>
   <span class="count" id="ev-count" role="status" aria-live="polite"></span>
  </div>
  <div class="events">{events_html(events)}</div>
 </section>

 <!-- ----------------------------------------------------------- calendar -->
 <section id="panel-calendar" hidden>
  <div class="filters">
   <div class="seg" role="group" aria-label="Filter dates">
    <button type="button" id="f-all" aria-pressed="true">All</button>
    <button type="button" id="f-wknd" aria-pressed="false">Fri+Sat</button>
    <button type="button" id="f-prime" aria-pressed="false">Prime</button>
   </div>
   <div class="seg" role="group" aria-label="What are you staging">{lens_buttons}</div>
   <span class="count" id="count" role="status" aria-live="polite"></span>
  </div>
  <p class="muted" style="font-size:12.5px;margin:2px 0 0" id="lens-blurb"></p>

  <section id="calendar-section">
   <h2>Month by month <small class="only-mob">swipe between months</small>
    <small class="only-desk">hover a date for a summary, click for the full
    detail</small></h2>
   <div class="mo-nav">
    <button type="button" id="mo-prev" class="icon-btn"
     aria-label="Previous month">{icon("left")}</button>
    <span class="now">Tap any date for detail</span>
    <button type="button" id="mo-next" class="icon-btn"
     aria-label="Next month">{icon("right")}</button>
   </div>
   <div class="months" id="months">{months_html(days)}</div>
   <div class="legend">{legend}</div>
  </section>

  <h2>At a glance <small>over the whole window, for the default lens</small></h2>
  <div class="stats">{stat_html}</div>

  <section class="limits">
   <h2>What this does not know</h2>
   <ul>
    <li><b>Venue availability is not modelled.</b> Emirates Theatre, the Sheikh Rashid
     Auditorium at the Indian High School and Live@Play in Al Quoz carry most of this
     circuit and book out early. A prime date is only prime if the room is free.</li>
    <li><b>Ramadan, Eid and the 2027 Hijri holidays are forecasts.</b> Ramadan is
     expected from {date.fromisoformat(ram_s):%-d %b %Y} and moves with the moon
     sighting, so anything from February 2027 onward is provisional.</li>
    <li><b>One source.</b> Platinumlist only. Shows sold anywhere else are invisible
     to this.</li>
    <li><b>Some listings carry quirks</b> from the source data, noted on the event
     itself where they apply.</li>
   </ul>
   <details>
    <summary>How the score works</summary>
    <p>Every date starts from its day of the week, because Saturday is where this
    circuit already books: Saturday 5.0, Friday 4.5, Thursday and Sunday 3.0, midweek
    1.5 to 2.0. It then loses points for a major desi concert the same night (-2.5),
    for sitting inside the Dubai Comedy Festival window (-2.5), for a competing act
    the night before or after (-1.0), for late August (-1.0) and for any other comedy
    the same night (-0.8). It gains points for the Eid Al Fitr window (+1.5), a public
    holiday (+1.0) and the December to mid-January peak (+0.5 to +0.7). A direct clash
    with the kind of act your lens blocks on, or any date inside Ramadan, rules the
    date out entirely. Prime is 4.0 and above, good 2.5, low below that.</p>
   </details>
  </section>
 </section>

 <!-- ---------------------------------------------------------- checklist -->
 <section id="panel-checklist" hidden>
  {checklist_html(checklists)}
 </section>
</div>
</main>
</div>

<div id="tip" role="tooltip" aria-hidden="true"></div>
<button type="button" class="sheet-bg" id="sheet-bg" hidden aria-label="Close"></button>
<div class="sheet" id="sheet" role="dialog" aria-modal="true"
 aria-labelledby="sheet-title" hidden>
 <div class="grab"></div>
 <button type="button" class="close icon-btn" id="sheet-close"
  aria-label="Close">{icon("close")}</button>
 <h3 id="sheet-title"></h3>
 <p class="sub" id="sheet-sub"></p>
 <div id="sheet-body"></div>
</div>

<div class="sheet acct-sheet" id="acct-sheet" role="dialog" aria-modal="true"
 aria-labelledby="acct-title" hidden>
 <div class="grab"></div>
 <button type="button" class="close icon-btn" id="acct-close"
  aria-label="Close">{icon("close")}</button>
 <h3 id="acct-title">Account</h3>
 <div id="acct-body"></div>
</div>

<div class="sheet acct-sheet" id="run-sheet" role="dialog" aria-modal="true"
 aria-labelledby="run-title" hidden>
 <div class="grab"></div>
 <button type="button" class="close icon-btn" id="run-close"
  aria-label="Close">{icon("close")}</button>
 <h3 id="run-title">Run the scrape</h3>
 <div id="run-body"></div>
</div>

<script>
window.__DAYS__ = {json.dumps(payload, ensure_ascii=False, separators=(",", ":"))};
window.__POOL__ = {json.dumps(pool, ensure_ascii=False, separators=(",", ":"))};
window.__TIERS__ = {json.dumps(tier_js, ensure_ascii=False)};
window.__LENSES__ = {json.dumps(lens_meta, ensure_ascii=False)};
window.__DEFAULT_LENS__ = {json.dumps(default_lens)};
window.__CHECKLISTS__ = {json.dumps(checklist_js, ensure_ascii=False,
                                    separators=(",", ":"))};
window.__STATUSES__ = {json.dumps(STATUSES, ensure_ascii=False)};
window.__BACKEND__ = {json.dumps(backend or {}, ensure_ascii=False)};
window.__REPO__ = {json.dumps(repo or {}, ensure_ascii=False)};
window.__STAMP__ = {json.dumps(stamp)};
</script>
<script>{JS}</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--viability", default=str(ROOT / "docs" / "viability.json"))
    ap.add_argument("--checklists", default=str(ROOT / "data" / "checklists.json"))
    ap.add_argument("--backend", default=str(ROOT / "data" / "backend.json"))
    ap.add_argument("--out-dir", default=str(ROOT / "docs"))
    ap.add_argument("--force-icons", action="store_true",
                    help="re-rasterise the app icons even if they already exist")
    args = ap.parse_args()

    src = Path(args.viability)
    if not src.exists():
        print(f"missing {src}; run python src/viability.py first")
        return 1
    viab = json.loads(src.read_text())
    cfg = json.loads((ROOT / "data" / "config.json").read_text())
    if not viab.get("days"):
        print("viability.json has no days; refusing to build an empty calendar")
        return 1

    checklists = []
    cl_path = Path(args.checklists)
    if cl_path.exists():
        checklists = json.loads(cl_path.read_text()).get("checklists", [])

    # Empty values mean local-only, which is the state the app ships in.
    backend = {"supabase_url": "", "supabase_anon_key": ""}
    raw_backend = {}
    be_path = Path(args.backend)
    if be_path.exists():
        raw_backend = json.loads(be_path.read_text())
        backend = {k: (raw_backend.get(k) or "").strip() for k in backend}

    stamp = viab.get("generated", date.today().isoformat())
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    repo = repo_info(raw_backend)
    (out / "index.html").write_text(
        render(viab, cfg, stamp, checklists, backend, repo), encoding="utf-8")
    (out / "manifest.webmanifest").write_text(manifest(stamp))
    (out / "sw.js").write_text(service_worker(stamp))
    # The icons are a fixed mark, independent of the data, and rasterising them in pure
    # Python costs about 13 seconds. Regenerate only when missing or asked.
    # Two shapes: a rounded tile for everywhere that shows the icon as given, and a
    # full-bleed maskable one for Android, which crops whatever it is handed to the
    # launcher's shape and would otherwise slice the corners off the tile.
    wanted = [(f"icon-{n}.png", n, False) for n in (192, 512)] + \
             [(f"icon-maskable-{n}.png", n, True) for n in (192, 512)]
    marker = out / "icons.version"
    fresh = marker.read_text().strip() if marker.exists() else ""
    stale = fresh != str(ICON_VERSION)
    drawn = 0
    for name, size, bleed in wanted:
        path = out / name
        if args.force_icons or stale or not path.exists():
            write_png(path, size, bleed=bleed)
            drawn += 1
    marker.write_text(f"{ICON_VERSION}\n")
    # Pages would otherwise run the output through Jekyll, which strips files and
    # directories beginning with an underscore.
    (out / ".nojekyll").write_text("")

    page = (out / "index.html").stat().st_size
    tasks = sum(len(c["tasks"]) for c in checklists)
    print(f"  checklist sync: "
          f"{'Supabase ' + backend['supabase_url'] if backend['supabase_url'] else 'local only (data/backend.json not filled in)'}")
    print(f"built {out/'index.html'} ({page // 1024} KB), {len(viab['days'])} days, "
          f"{len(viab.get('events', []))} events, {len(viab.get('lenses') or {})} lenses, "
          f"{len(checklists)} checklists ({tasks} tasks)")
    print(f"  manifest, service worker written; cache stamp {stamp}; "
          f"{drawn or 'no'} icons drawn (version {ICON_VERSION})")
    print(f"  refresh button: "
          f"{repo['slug'] + ' / ' + repo['workflow'] if repo else 'hidden (no git remote)'}")

    absolute = re.findall(r'(?:href|src)="(/[^/][^"]*)"', (out / "index.html").read_text())
    if absolute:
        print(f"  WARNING root-absolute paths would 404 on a project site: {absolute}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())