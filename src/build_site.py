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
  * The agenda list is the default under 700px; the month grid is secondary.
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
import sys
import zlib
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TIERS = {
    "prime":   {"icon": "✓", "label": "PRIME",   "blurb": "book this"},
    "good":    {"icon": "●", "label": "GOOD",    "blurb": "workable"},
    "weak":    {"icon": "·", "label": "LOW",     "blurb": "weeknight or diluted"},
    "poor":    {"icon": "·", "label": "LOW",     "blurb": "weeknight or diluted"},
    "blocked": {"icon": "✕", "label": "BLOCKED", "blurb": "direct clash or Ramadan"},
}
DOW_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
# Mirrors src/import_checklist.py. "Not needed" is excluded from progress totals, the
# same way the source workbook's dashboard excludes it.
STATUSES = ["Not started", "In progress", "Done", "Not needed"]
MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December"]


def esc(s):
    return html.escape(str(s if s is not None else ""), quote=True)


# ---------------------------------------------------------------- icons

def _rounded_rect_check(size, ss=4):
    """Flat-colour app icon: rounded square plus a tick. Supersampled for smooth edges."""
    n = size * ss
    radius = n * 0.22
    bg = (12, 163, 12)          # --good, the prime tier colour
    fg = (255, 255, 255)
    # tick as two segments, in unit coordinates
    pts = [(0.27, 0.53), (0.43, 0.69), (0.75, 0.33)]
    segs = [((pts[i][0] * n, pts[i][1] * n), (pts[i + 1][0] * n, pts[i + 1][1] * n))
            for i in range(len(pts) - 1)]
    stroke = n * 0.085

    def in_round_rect(x, y):
        cx = min(max(x, radius), n - radius)
        cy = min(max(y, radius), n - radius)
        dx, dy = x - cx, y - cy
        return dx * dx + dy * dy <= radius * radius

    def near_tick(x, y):
        for (x1, y1), (x2, y2) in segs:
            vx, vy = x2 - x1, y2 - y1
            wx, wy = x - x1, y - y1
            L = vx * vx + vy * vy
            t = 0.0 if L == 0 else max(0.0, min(1.0, (wx * vx + wy * vy) / L))
            px, py = x1 + t * vx, y1 + t * vy
            if (x - px) ** 2 + (y - py) ** 2 <= (stroke / 2) ** 2:
                return True
        return False

    # Render the supersampled mask one row at a time, then box-downsample.
    rows = []
    for yy in range(n):
        row = bytearray(n)
        for xx in range(n):
            if in_round_rect(xx + .5, yy + .5):
                row[xx] = 2 if near_tick(xx + .5, yy + .5) else 1
        rows.append(row)

    out = bytearray()
    area = ss * ss
    for y in range(size):
        out.append(0)                                   # PNG filter byte: none
        for x in range(size):
            r = g = b = a = 0
            for dy in range(ss):
                src = rows[y * ss + dy]
                for dx in range(ss):
                    v = src[x * ss + dx]
                    if v:
                        a += 255
                        c = fg if v == 2 else bg
                        r += c[0]; g += c[1]; b += c[2]
            if a:
                filled = a // 255
                out += bytes((r // filled, g // filled, b // filled, a // area))
            else:
                out += b"\0\0\0\0"
    return bytes(out)


def write_png(path, size):
    raw = _rounded_rect_check(size)

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
        f'<span class="ic" aria-hidden="true">{tier["icon"]}</span>'
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


def agenda_html(days):
    cards = []
    for day in days:
        tier = TIERS[day["tier"]]
        d = date.fromisoformat(day["date"])
        holiday = (f'<span class="hol">{esc(day["holiday"])}</span>'
                   if day["holiday"] else "")
        cards.append(
            f'<article class="ag t-{day["tier"]}" data-date="{day["date"]}" '
            f'data-tier="{day["tier"]}" data-dow="{esc(day["dow"])}">'
            f'<div class="ag-top">'
            f'<div class="ag-when"><b>{d.strftime("%a %-d %b")}</b>'
            f'<span>{d.year}</span>{holiday}</div>'
            f'<div class="badge b-{day["tier"]}">'
            f'<span class="ic" aria-hidden="true">{tier["icon"]}</span>'
            f'<span class="badge-lb">{tier["label"]}</span></div>'
            f'</div>'
            f'<div class="ag-score">{day["score"]}<span> score</span></div>'
            f'<ul class="ag-on"></ul>'
            f'<button type="button" class="ag-more" data-open="{day["date"]}">'
            f'Why this score</button>'
            f'</article>')
    return "".join(cards)


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
        rows.append(
            f'<article class="ev" data-month="{esc(event_month(e))}" '
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

def checklist_html(checklists):
    if not checklists:
        return ('<p class="muted">No checklists yet. Import one with '
                '<code>python src/import_checklist.py &lt;workbook.xlsx&gt;</code>.</p>')
    tasks = []
    for cl in checklists:
        for t in cl["tasks"]:
            why = f'<p class="tk-why">{esc(t["why"])}</p>' if t.get("why") else ""
            blocking = ('<span class="tk-flag">BLOCKING</span>' if t.get("blocking")
                        else "")
            d_minus = t.get("d_minus")  # used in the markup below and for due dates
            offset = ("" if d_minus is None else
                      (f"D-{d_minus}" if d_minus >= 0 else f"D+{abs(d_minus)}"))
            tasks.append(
                f'<article class="tk" data-cl="{esc(cl["id"])}" data-n="{t["n"]}" '
                f'data-ws="{esc(t["workstream"])}" '
                f'data-dminus="{"" if d_minus is None else d_minus}" '
                f'data-blocking="{1 if t.get("blocking") else 0}" hidden>'
                f'<div class="tk-head">'
                f'<span class="tk-n">{t["n"]}</span>'
                f'<div class="tk-body"><p class="tk-task">{esc(t["task"])}</p>{why}</div>'
                f'<select class="tk-status" aria-label="Status for task {t["n"]}">'
                + "".join(f'<option value="{esc(s)}"'
                          f'{" selected" if s == t.get("status") else ""}>{esc(s)}</option>'
                          for s in STATUSES)
                + '</select></div>'
                f'<div class="tk-meta"><span class="tk-ws">{esc(t["workstream"])}</span>'
                f'<span>{esc(t.get("owner") or "Unassigned")}</span>'
                f'<span class="tk-off">{offset}</span>'
                f'<span class="tk-due"></span>{blocking}</div>'
                f'</article>')

    picker = "".join(f'<option value="{esc(c["id"])}">{esc(c["title"])}</option>'
                     for c in checklists)
    streams = []
    for cl in checklists:
        for t in cl["tasks"]:
            if t["workstream"] not in streams:
                streams.append(t["workstream"])
    chips = "".join(
        f'<label class="ms-opt"><input type="checkbox" data-ws-filter '
        f'value="{esc(s)}"><span>{esc(s)}</span></label>' for s in streams)

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
 <div class="cl-bar">
  <select id="cl-pick" aria-label="Which checklist">{picker}</select>
  <label class="cl-date">Show date
   <input type="date" id="cl-date"></label>
  <button type="button" id="cl-export" class="icon-btn">Copy JSON</button>
 </div>
 <p class="cl-hint muted">Saved in this browser only. This site is static, so nothing
  is written back to the repository: use <b>Copy JSON</b> and paste into
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
 <div class="filters">
  <details class="ms" data-ms="workstream">
   <summary>Workstream<span class="ms-badge" hidden></span></summary>
   <div class="ms-menu">{chips}</div>
  </details>
  <label class="cl-toggle"><input type="checkbox" id="cl-blockers"> Blockers only</label>
  <label class="cl-toggle"><input type="checkbox" id="cl-open"> Hide done</label>
 </div>
 <div class="cl-tasks">{"".join(tasks)}</div>
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
        "name": "UAE comedy and desi events tracker",
        "short_name": "Viable dates",
        "description": "Which UAE dates are free for an Indian stand-up show, "
                       "scored against everything already on sale.",
        # Relative so the installed app scopes to /events-tracker/ on Pages.
        "start_url": ".",
        "scope": ".",
        "display": "standalone",
        "orientation": "portrait-primary",
        "background_color": "#f9f9f7",
        "theme_color": "#0ca30c",
        "icons": [
            {"src": "./icon-192.png", "sizes": "192x192", "type": "image/png",
             "purpose": "any"},
            {"src": "./icon-512.png", "sizes": "512x512", "type": "image/png",
             "purpose": "any"},
            {"src": "./icon-512.png", "sizes": "512x512", "type": "image/png",
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
                './icon-192.png', './icon-512.png', './viability.json'];

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
body{margin:0;background:var(--plane);color:var(--ink);
 font-family:system-ui,-apple-system,"Segoe UI",sans-serif;font-size:15px;line-height:1.5;
 padding-bottom:env(safe-area-inset-bottom)}
a{color:inherit}
.muted{color:var(--ink-2)}
.wrap{max-width:1180px;margin:0 auto;padding:0 14px 72px}

/* ---- sticky chrome: title row collapses, controls stay reachable one-handed ---- */
.top{position:sticky;top:0;z-index:30;background:var(--plane);
 border-bottom:1px solid var(--ring);padding:10px 14px 8px}
.top-in{max-width:1180px;margin:0 auto;display:flex;align-items:flex-start;gap:10px}
.top h1{font-size:17px;margin:0;letter-spacing:-.01em;flex:1;line-height:1.25}
.top p{margin:2px 0 0;color:var(--ink-2);font-size:12.5px}
.controls{position:sticky;top:0;z-index:29;background:var(--plane);
 border-bottom:1px solid var(--ring);padding:8px 14px}
.controls-in{max-width:1180px;margin:0 auto;display:flex;gap:8px;align-items:center;
 flex-wrap:wrap}
.seg{display:flex;gap:4px;background:var(--surface-1);border:1px solid var(--ring);
 border-radius:10px;padding:3px}
button{font:inherit;font-size:13px;padding:8px 12px;border-radius:8px;cursor:pointer;
 border:1px solid transparent;background:transparent;color:var(--ink);min-height:38px}
.seg button{border:0;padding:7px 11px;min-height:34px}
.seg button[aria-pressed="true"]{background:var(--ink);color:var(--surface-1)}
.icon-btn{border:1px solid var(--ring);background:var(--surface-1);min-width:38px}
.count{font-size:12.5px;color:var(--ink-2);margin-left:auto;font-variant-numeric:tabular-nums}

/* ---- the distinction that matters, kept in the page not in a tooltip ---- */
.scope{margin:14px 0 0;padding:11px 13px;border-radius:11px;background:var(--surface-1);
 border:1px solid var(--ring);font-size:13px;color:var(--ink-2)}
.scope b{color:var(--ink)}

h2{font-size:16px;margin:26px 0 10px;letter-spacing:-.01em}
h2 small{font-weight:400;color:var(--ink-2);font-size:12.5px;margin-left:6px}

/* ---- agenda: the default on phones ---- */
.agenda{display:grid;gap:10px}
.ag{background:var(--surface-1);border:1px solid var(--ring);border-left:3px solid var(--muted);
 border-radius:12px;padding:12px 13px}
.ag.t-prime{border-left-color:var(--good)}
.ag.t-good{border-left-color:var(--info)}
.ag.t-blocked{border-left-color:var(--crit)}
.ag-top{display:flex;align-items:flex-start;gap:10px}
.ag-when{flex:1;line-height:1.3}
.ag-when b{font-size:15.5px}
.ag-when span{color:var(--muted);font-size:12.5px;margin-left:5px}
.hol{display:block;color:var(--ink-2);font-size:12px;margin-left:0}
.badge{display:inline-flex;align-items:center;gap:5px;font-size:10.5px;font-weight:700;
 letter-spacing:.05em;padding:4px 9px;border-radius:20px;border:1px solid currentColor;
 white-space:nowrap}
.b-prime{color:var(--good);background:var(--good-bg)}
.b-good{color:var(--info);background:var(--info-bg)}
.b-blocked{color:var(--crit);background:var(--crit-bg);
 background-image:repeating-linear-gradient(135deg,transparent 0 4px,var(--crit-bg) 4px 8px)}
.b-weak,.b-poor{color:var(--muted)}
.ag-score{font-size:23px;font-weight:650;letter-spacing:-.02em;margin:4px 0 2px}
.ag-score span{font-size:12px;font-weight:400;color:var(--muted)}
.ag-on{margin:4px 0 0;padding-left:17px;font-size:12.5px;color:var(--ink-2)}
.ag-on .clear{list-style:none;margin-left:-17px;color:var(--good)}
.ag-more{margin-top:6px;padding:5px 9px;font-size:12px;border:1px solid var(--ring);
 background:var(--plane)}

/* ---- month grid: one month per screen, swipe between them ---- */
.mo-nav{display:flex;align-items:center;gap:8px;margin-bottom:8px}
.mo-nav .now{flex:1;font-size:14px;font-weight:600}
.months{display:flex;gap:12px;overflow-x:auto;scroll-snap-type:x mandatory;
 scrollbar-width:none;-webkit-overflow-scrolling:touch;margin:0 -14px;padding:0 14px}
.months::-webkit-scrollbar{display:none}
.mo{flex:0 0 100%;scroll-snap-align:center;background:var(--surface-1);
 border:1px solid var(--ring);border-radius:14px;padding:10px}
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
.ic{font-size:12px;line-height:1}
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
.day:focus-visible,.ag-more:focus-visible,button:focus-visible{outline:2px solid var(--ink);
 outline-offset:2px}
[hidden]{display:none !important}

.legend{display:flex;gap:14px;flex-wrap:wrap;margin:14px 0 0;font-size:12.5px;
 color:var(--ink-2)}
.legend i{display:inline-block;width:11px;height:11px;border-radius:3px;margin-right:5px;
 vertical-align:-1px;border:1px solid var(--ring)}

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

/* ---- tabs ---- */
.tabs{display:flex;gap:4px;background:var(--surface-1);border:1px solid var(--ring);
 border-radius:10px;padding:3px;overflow-x:auto;scrollbar-width:none}
.tabs::-webkit-scrollbar{display:none}
.tabs button{border:0;white-space:nowrap;min-height:34px;padding:7px 13px;flex:1}
.tabs button[aria-pressed="true"]{background:var(--ink);color:var(--surface-1)}

/* ---- multi-select facets ---- */
.filters{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:14px 0 4px}
.ms{position:relative}
.ms>summary{list-style:none;cursor:pointer;font-size:13px;padding:8px 12px;
 border:1px solid var(--ring);border-radius:9px;background:var(--surface-1);
 display:inline-flex;align-items:center;gap:6px;min-height:38px}
.ms>summary::-webkit-details-marker{display:none}
.ms>summary::after{content:"\\25BE";color:var(--muted);font-size:10px}
.ms[open]>summary{border-color:var(--ink)}
.ms-badge{background:var(--ink);color:var(--surface-1);border-radius:20px;
 font-size:10.5px;font-weight:700;padding:1px 6px}
.ms-menu{position:absolute;z-index:25;top:calc(100% + 4px);left:0;min-width:210px;
 max-height:290px;overflow:auto;background:var(--surface-1);border:1px solid var(--ring);
 border-radius:11px;padding:6px;box-shadow:0 12px 34px rgba(0,0,0,.16)}
.ms-opt{display:flex;align-items:center;gap:8px;padding:7px 8px;border-radius:7px;
 font-size:13px;cursor:pointer}
.ms-opt:hover{background:var(--plane)}
.ms-opt span{flex:1}
.ms-opt i{color:var(--muted);font-style:normal;font-size:11.5px}
.chip-clear{font-size:12.5px;color:var(--ink-2);text-decoration:underline;
 background:none;border:0;padding:6px 2px}

/* ---- checklist ---- */
.cl-bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:14px 0 6px}
.cl-bar select,.cl-bar input[type="date"]{font:inherit;font-size:13px;padding:7px 10px;
 border-radius:9px;border:1px solid var(--ring);background:var(--surface-1);
 color:var(--ink);min-height:38px}
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
.tk-status{font:inherit;font-size:12px;padding:5px 7px;border-radius:8px;
 border:1px solid var(--ring);background:var(--plane);color:var(--ink)}
.tk-meta{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:7px;
 font-size:11.5px;color:var(--muted)}
.tk-ws{font-weight:700;color:var(--ink-2)}
.tk-flag{color:var(--crit);font-weight:700;letter-spacing:.05em}
.tk-due.over{color:var(--crit);font-weight:700}
.cl-json textarea{width:100%;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
 font-size:11px;border-radius:9px;border:1px solid var(--ring);padding:8px;
 background:var(--surface-1);color:var(--ink)}

/* Very narrow phones (320px, an SE-sized screen) leave about 31px of cell for the
   label. Shrink it rather than let BLOCKED be ellipsised. */
@media (max-width:359px){
 .day{padding:2px}
 .lb{font-size:5.8px}
}

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
 .agenda{grid-template-columns:repeat(auto-fill,minmax(280px,1fr))}
 .events{grid-template-columns:repeat(auto-fill,minmax(300px,1fr))}
 .stats{grid-template-columns:repeat(4,1fr)}
}

/* ---- laptop: not the phone layout stretched wide ---- */
@media (min-width:900px){
 .only-desk{display:inline}
 .only-mob{display:none}

 /* Seven columns each have to fit the word BLOCKED without truncating. Measured, that
    needs a cell of about 66px, so a panel of about 520px. Three months to a row forces
    a 55px cell and clips the label, which would break the never-colour-only rule, so
    two roomy months beat three cramped ones. */
 .months{display:grid;grid-template-columns:repeat(auto-fit,minmax(520px,1fr));
  gap:16px;overflow:visible;margin:0;padding:0}
 .mo{flex:none;padding:16px}
 .mo h3{font-size:16px;margin-bottom:10px}
 .mo-nav{display:none}
 .grid{gap:4px}
 .day{min-height:66px;padding:5px 7px}
 .dn{font-size:14px}
 .ic{font-size:13px}
 .lb{font-size:9px;letter-spacing:.05em}
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
 .ev-note{grid-column:1/-1;margin-top:2px !important}

 /* A bar pinned to the bottom of a 1440px screen is a phone idiom. Centre it. */
 .sheet{left:50%;top:50%;right:auto;bottom:auto;transform:translate(-50%,-50%);
  width:min(520px,92vw);max-height:78vh;border-radius:16px;
  padding:18px 20px 20px;box-shadow:0 24px 60px rgba(0,0,0,.3)}
 .sheet .grab{display:none}

 .tabs{flex:0 0 auto}
 .tabs button{flex:0 0 auto}
 .cl-progress{grid-template-columns:repeat(auto-fit,minmax(170px,1fr))}
 .tk-task{font-size:14px}
}

@media (min-width:1200px){
 .wrap,.top-in,.controls-in{max-width:1400px}
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
 var DEFAULT_LENS = window.__DEFAULT_LENS__ || 'standup';
 var text = function(i){ return POOL[i] || ''; };
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
 var TABS = ['events', 'calendar', 'checklist'];
 function showTab(name){
  if (TABS.indexOf(name) < 0) name = TABS[0];
  TABS.forEach(function(t){
   var btn = $('tab-' + t), panel = $('panel-' + t);
   if (btn) btn.setAttribute('aria-pressed', String(t === name));
   if (panel) panel.hidden = t !== name;
  });
  store.set('tab', name);
 }
 TABS.forEach(function(t){
  var btn = $('tab-' + t);
  if (btn) btn.addEventListener('click', function(){ showTab(t); });
 });

 // ================================================================ calendar
 var cells = all('.day[data-tier]');
 var cards = all('.ag[data-tier]');
 var countEl = $('count');
 var mode = 'all';
 var lens = DEFAULT_LENS;

 // Past dates are marked here rather than at build time, so the page is identical
 // whichever day it was built and a cached copy still marks the right days as gone.
 cells.concat(cards).forEach(function(el){
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
   el.querySelector('.ic').textContent = t.icon;
   el.querySelector('.lb').textContent = t.label;
   var label = el.getAttribute('aria-label') || '';
   el.setAttribute('aria-label',
     label.split(',')[0] + ', ' + d.t + ', score ' + d.s);
  });
  cards.forEach(function(el){
   var d = dayFor(el.dataset.date);
   if (!d) return;
   var t = TIER[d.t] || {icon: '', label: d.t};
   el.className = 'ag t-' + d.t + (el.dataset.past === '1' ? ' past' : '');
   el.dataset.tier = d.t;
   el.querySelector('.badge').className = 'badge b-' + d.t;
   el.querySelector('.badge .ic').textContent = t.icon;
   el.querySelector('.badge-lb').textContent = t.label;
   el.querySelector('.ag-score').innerHTML = d.s + '<span> score</span>';
   var ul = el.querySelector('.ag-on');
   ul.innerHTML = d.o.length
     ? d.o.map(function(i){ return '<li>' + text(i) + '</li>'; }).join('')
     : '<li class="clear">Nothing scheduled against you</li>';
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
  cards.forEach(function(el){ el.hidden = !matches(el); });
  // Counted from the calendar, which holds every date in the window, so the number is
  // the same in either view.
  countEl.textContent = shown + (shown === 1 ? ' date shown' : ' dates shown');
  countEl.dataset.count = String(shown);
 }
 Object.keys(filters).forEach(function(k){
  if (filters[k]) filters[k].addEventListener('click', function(){ applyFilter(k); });
 });
 all('[data-lens-opt]').forEach(function(b){
  b.addEventListener('click', function(){ applyLens(b.dataset.lensOpt); });
 });

 var views = {agenda: $('v-agenda'), calendar: $('v-calendar')};
 function applyView(next){
  for (var k in views) {
   if (views[k]) views[k].setAttribute('aria-pressed', String(k === next));
  }
  $('agenda-section').hidden = next !== 'agenda';
  $('calendar-section').hidden = next !== 'calendar';
  store.set('view', next);
 }
 Object.keys(views).forEach(function(k){
  if (views[k]) views[k].addEventListener('click', function(){ applyView(k); });
 });

 // ---- detail sheet
 var sheet = $('sheet'), sheetBg = $('sheet-bg'), lastFocus = null;
 function openSheet(iso){
  var d = dayFor(iso);
  if (!d) return;
  var t = TIER[d.t] || {icon: '', label: d.t};
  var parts = new Date(iso + 'T00:00:00').toDateString().split(' ');
  $('sheet-title').textContent = parts[0] + ' ' + parts[2] + ' ' + parts[1] + ' ' + parts[3];
  $('sheet-sub').innerHTML = '<span class="badge b-' + d.t + '"><span class="ic" ' +
    'aria-hidden="true">' + t.icon + '</span><span>' + t.label + '</span></span> ' +
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
  sheet.hidden = true; sheetBg.hidden = true;
  if (lastFocus && lastFocus.focus) lastFocus.focus();
 }
 cells.forEach(function(el){
  el.addEventListener('click', function(){ openSheet(el.dataset.date); });
 });
 all('.ag-more').forEach(function(el){
  el.addEventListener('click', function(){ openSheet(el.dataset.open); });
 });
 sheetBg.addEventListener('click', closeSheet);
 $('sheet-close').addEventListener('click', closeSheet);
 document.addEventListener('keydown', function(e){
  if (e.key === 'Escape' && !sheet.hidden) closeSheet();
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
 function evFilter(){
  var want = {};
  all('input[data-facet]').forEach(function(box){
   if (!box.checked) return;
   (want[box.dataset.facet] = want[box.dataset.facet] || []).push(box.value);
  });
  var shown = 0;
  evs.forEach(function(el){
   var ok = true;
   for (var facet in want) {
    if (want[facet].indexOf(el.dataset[facet] || '') < 0) { ok = false; break; }
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
   evCount.textContent = shown + (shown === 1 ? ' event' : ' events');
   evCount.dataset.count = String(shown);
  }
 }
 all('input[data-facet]').forEach(function(box){
  box.addEventListener('change', evFilter);
 });
 if ($('ev-clear')) $('ev-clear').addEventListener('click', function(){
  all('input[data-facet]').forEach(function(b){ b.checked = false; });
  evFilter();
 });
 // A click outside an open facet menu closes it.
 document.addEventListener('click', function(e){
  all('details.ms[open]').forEach(function(d){
   if (!d.contains(e.target)) d.open = false;
  });
 });

 // ================================================================ checklist tab
 var clTasks = all('.tk');
 var current = CHECK.length ? CHECK[0].id : null;

 function clKey(id){ return 'checklist:' + id; }
 function clState(id){
  try { return JSON.parse(store.get(clKey(id), '{}')) || {}; } catch (e) { return {}; }
 }
 function clSave(id, state){ store.set(clKey(id), JSON.stringify(state)); }

 function dueDate(showDate, dMinus){
  if (!showDate || dMinus === null || dMinus === undefined) return '';
  var d = new Date(showDate + 'T00:00:00');
  d.setDate(d.getDate() - dMinus);
  return localIso(d);
 }

 function renderChecklist(){
  if (!current) return;
  var meta = CHECK.filter(function(c){ return c.id === current; })[0];
  var state = clState(current);
  var showDate = state.show_date || meta.show_date || '';
  if ($('cl-date')) $('cl-date').value = showDate;
  all('.cl-field').forEach(function(f){ f.hidden = f.dataset.cl !== current; });
  var fields = state.fields || {};
  all('.cl-field[data-cl="' + current + '"] input[data-field]').forEach(function(inp){
   var saved = fields[inp.dataset.field];
   if (saved !== undefined && inp.value !== saved) inp.value = saved;
  });

  var wsWanted = [];
  all('input[data-ws-filter]').forEach(function(b){
   if (b.checked) wsWanted.push(b.value);
  });
  var blockersOnly = $('cl-blockers') && $('cl-blockers').checked;
  var hideDone = $('cl-open') && $('cl-open').checked;

  var totals = {total: 0, done: 0, prog: 0, todo: 0, blockers: 0, overdue: 0};
  var byWs = {};
  clTasks.forEach(function(el){
   var mine = el.dataset.cl === current;
   var n = el.dataset.n;
   var status = state.statuses && state.statuses[n] ? state.statuses[n]
                                                    : el.querySelector('.tk-status').value;
   var sel = el.querySelector('.tk-status');
   if (sel.value !== status) sel.value = status;
   el.classList.toggle('done', status === 'Done');

   var due = dueDate(showDate, Number(el.dataset.dminus));
   var dueEl = el.querySelector('.tk-due');
   var overdue = due && due < TODAY && status !== 'Done' && status !== 'Not needed';
   dueEl.textContent = due ? (overdue ? 'due ' + due + ' · overdue' : 'due ' + due) : '';
   dueEl.classList.toggle('over', !!overdue);

   if (mine && status !== 'Not needed') {
    totals.total++;
    if (status === 'Done') totals.done++;
    else if (status === 'In progress') totals.prog++;
    else totals.todo++;
    if (el.dataset.blocking === '1' && status !== 'Done') totals.blockers++;
    if (overdue) totals.overdue++;
    var ws = el.dataset.ws;
    byWs[ws] = byWs[ws] || {t: 0, d: 0};
    byWs[ws].t++;
    if (status === 'Done') byWs[ws].d++;
   }

   var visible = mine
     && (!wsWanted.length || wsWanted.indexOf(el.dataset.ws) >= 0)
     && (!blockersOnly || el.dataset.blocking === '1')
     && (!hideDone || status !== 'Done');
   el.hidden = !visible;
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
  // Kept behind a disclosure: eight more tiles pushed the tasks themselves two
  // screens down on a phone.
  if ($('cl-streams')) {
   $('cl-streams').innerHTML = Object.keys(byWs).map(function(ws){
    var w = byWs[ws];
    return '<div class="cl-cell"><b>' + w.d + '/' + w.t + '</b><span>' + ws +
      '</span><div class="cl-bar-track"><div class="cl-bar-fill" style="width:' +
      Math.round(100 * w.d / w.t) + '%"></div></div></div>';
   }).join('');
  }

  var out = $('cl-out');
  if (out) {
   var dump = JSON.parse(JSON.stringify(meta));
   dump.show_date = showDate || null;
   dump.tasks.forEach(function(t){
    if (state.statuses && state.statuses[String(t.n)]) t.status = state.statuses[String(t.n)];
   });
   (dump.setup || []).forEach(function(f){
    if (fields[f.label] !== undefined) f.value = fields[f.label];
   });
   out.value = JSON.stringify(dump, null, 1);
  }
  var badge = document.querySelector('.ms[data-ms="workstream"] .ms-badge');
  if (badge) { badge.textContent = wsWanted.length; badge.hidden = !wsWanted.length; }
 }

 clTasks.forEach(function(el){
  el.querySelector('.tk-status').addEventListener('change', function(e){
   var state = clState(el.dataset.cl);
   state.statuses = state.statuses || {};
   state.statuses[el.dataset.n] = e.target.value;
   clSave(el.dataset.cl, state);
   renderChecklist();
  });
 });
 if ($('cl-pick')) $('cl-pick').addEventListener('change', function(e){
  current = e.target.value; renderChecklist();
 });
 if ($('cl-date')) $('cl-date').addEventListener('change', function(e){
  var state = clState(current);
  state.show_date = e.target.value;
  clSave(current, state);
  renderChecklist();
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
 ['cl-blockers', 'cl-open'].forEach(function(id){
  if ($(id)) $(id).addEventListener('change', renderChecklist);
 });
 all('input[data-ws-filter]').forEach(function(b){
  b.addEventListener('change', renderChecklist);
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
 var startView = store.get('view', '');
 if (startView !== 'agenda' && startView !== 'calendar') {
  startView = matchMedia('(max-width: 699px)').matches ? 'agenda' : 'calendar';
 }
 applyView(startView);
 applyLens(LENSES[store.get('lens', '')] ? store.get('lens', '') : DEFAULT_LENS);
 evFilter();
 renderChecklist();
 showTab(store.get('tab', TABS[0]));

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

def render(viab, cfg, stamp, checklists):
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
        f'<span><i style="background:{bg};border-color:{bc}"></i>'
        f'{TIERS[t]["icon"]} {TIERS[t]["label"].title()} &ndash; {TIERS[t]["blurb"]}</span>'
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
<title>Viable dates &ndash; Indian stand-up comedy, UAE</title>
<meta name="description" content="Which UAE dates are free for an Indian stand-up show, scored against everything already on sale on Platinumlist.">
<link rel="manifest" href="./manifest.webmanifest">
<link rel="icon" href="./icon-192.png" type="image/png">
<link rel="apple-touch-icon" href="./icon-192.png">
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
<header class="top">
 <div class="top-in">
  <div style="flex:1">
   <h1>Viable dates for an Indian stand-up show</h1>
   <p>Dubai and Abu Dhabi, {esc(span)}. Checked daily; data as of {esc(stamp)}.</p>
  </div>
  <button type="button" id="theme" class="icon-btn"
   aria-label="Switch theme">&#9681;</button>
 </div>
</header>

<div class="controls">
 <div class="controls-in">
  <div class="tabs" role="group" aria-label="Sections">
   <button type="button" id="tab-events" aria-pressed="true">Events</button>
   <button type="button" id="tab-calendar" aria-pressed="false">Calendar</button>
   <button type="button" id="tab-checklist" aria-pressed="false">Checklist</button>
  </div>
 </div>
</div>

<div class="wrap">

 <!-- ------------------------------------------------------------- events -->
 <section id="panel-events" hidden>
  <h2>Everything on sale <small>{len(events)} events, filter to narrow it</small></h2>
  <div class="filters">
   {facet_bar}
   <button type="button" id="ev-clear" class="chip-clear">Clear filters</button>
   <span class="count" id="ev-count" role="status" aria-live="polite"></span>
  </div>
  <div class="events">{events_html(events)}</div>
 </section>

 <!-- ----------------------------------------------------------- calendar -->
 <section id="panel-calendar" hidden>
  <p class="scope"><b>The calendar shows every comedy and desi event on sale.</b>
  The colour answers a narrower question, set by the lens below: could <em>you</em>
  stage that kind of show that night. A busy night for Arabic comedy is not a blocked
  night for you.</p>

  <div class="filters">
   <div class="seg" role="group" aria-label="Filter dates">
    <button type="button" id="f-all" aria-pressed="true">All</button>
    <button type="button" id="f-wknd" aria-pressed="false">Fri+Sat</button>
    <button type="button" id="f-prime" aria-pressed="false">Prime</button>
   </div>
   <div class="seg" role="group" aria-label="What are you staging">{lens_buttons}</div>
   <div class="seg" role="group" aria-label="View">
    <button type="button" id="v-agenda" aria-pressed="false">Agenda</button>
    <button type="button" id="v-calendar" aria-pressed="false">Grid</button>
   </div>
   <span class="count" id="count" role="status" aria-live="polite"></span>
  </div>
  <p class="muted" style="font-size:12.5px;margin:2px 0 0" id="lens-blurb"></p>

  <section id="agenda-section" hidden>
   <h2>Upcoming dates <small>nearest first</small></h2>
   <div class="agenda">{agenda_html(days)}</div>
  </section>

  <section id="calendar-section" hidden>
   <h2>Month by month <small class="only-mob">swipe between months</small>
    <small class="only-desk">hover a date for a summary, click for the full
    detail</small></h2>
   <div class="mo-nav">
    <button type="button" id="mo-prev" class="icon-btn"
     aria-label="Previous month">&#8592;</button>
    <span class="now">Tap any date for detail</span>
    <button type="button" id="mo-next" class="icon-btn"
     aria-label="Next month">&#8594;</button>
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
  <h2>Event checklist <small>for shows you are organising</small></h2>
  {checklist_html(checklists)}
 </section>
</div>

<div id="tip" role="tooltip" aria-hidden="true"></div>
<button type="button" class="sheet-bg" id="sheet-bg" hidden aria-label="Close"></button>
<div class="sheet" id="sheet" role="dialog" aria-modal="true"
 aria-labelledby="sheet-title" hidden>
 <div class="grab"></div>
 <button type="button" class="close icon-btn" id="sheet-close"
  aria-label="Close">&#10005;</button>
 <h3 id="sheet-title"></h3>
 <p class="sub" id="sheet-sub"></p>
 <div id="sheet-body"></div>
</div>

<script>
window.__DAYS__ = {json.dumps(payload, ensure_ascii=False, separators=(",", ":"))};
window.__POOL__ = {json.dumps(pool, ensure_ascii=False, separators=(",", ":"))};
window.__TIERS__ = {json.dumps(tier_js, ensure_ascii=False)};
window.__LENSES__ = {json.dumps(lens_meta, ensure_ascii=False)};
window.__DEFAULT_LENS__ = {json.dumps(default_lens)};
window.__CHECKLISTS__ = {json.dumps(checklist_js, ensure_ascii=False,
                                    separators=(",", ":"))};
</script>
<script>{JS}</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--viability", default=str(ROOT / "docs" / "viability.json"))
    ap.add_argument("--checklists", default=str(ROOT / "data" / "checklists.json"))
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

    stamp = viab.get("generated", date.today().isoformat())
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    (out / "index.html").write_text(render(viab, cfg, stamp, checklists), encoding="utf-8")
    (out / "manifest.webmanifest").write_text(manifest(stamp))
    (out / "sw.js").write_text(service_worker(stamp))
    # The icons are a fixed mark, independent of the data, and rasterising them in pure
    # Python costs about 13 seconds. Regenerate only when missing or asked.
    for size in (192, 512):
        icon = out / f"icon-{size}.png"
        if args.force_icons or not icon.exists():
            write_png(icon, size)
    # Pages would otherwise run the output through Jekyll, which strips files and
    # directories beginning with an underscore.
    (out / ".nojekyll").write_text("")

    page = (out / "index.html").stat().st_size
    tasks = sum(len(c["tasks"]) for c in checklists)
    print(f"built {out/'index.html'} ({page // 1024} KB), {len(viab['days'])} days, "
          f"{len(viab.get('events', []))} events, {len(viab.get('lenses') or {})} lenses, "
          f"{len(checklists)} checklists ({tasks} tasks)")
    print(f"  manifest, service worker and icons written; cache stamp {stamp}")

    absolute = re.findall(r'(?:href|src)="(/[^/][^"]*)"', (out / "index.html").read_text())
    if absolute:
        print(f"  WARNING root-absolute paths would 404 on a project site: {absolute}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())