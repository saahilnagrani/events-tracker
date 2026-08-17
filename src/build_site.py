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

def day_cell(day, today):
    tier = TIERS[day["tier"]]
    past = day["date"] < today
    d = date.fromisoformat(day["date"])
    classes = f"day t-{day['tier']}" + (" past" if past else "")
    label = (f"{d.strftime('%a %d %b %Y')}, {day['tier']}, "
             f"score {day['score']}" + (", past" if past else ""))
    return (
        f'<button type="button" class="{classes}" data-date="{day["date"]}" '
        f'data-tier="{day["tier"]}" data-dow="{esc(day["dow"])}" '
        f'data-past="{1 if past else 0}" aria-label="{esc(label)}">'
        f'<span class="dn">{d.day}</span>'
        f'<span class="ic" aria-hidden="true">{tier["icon"]}</span>'
        f'<span class="lb">{tier["label"]}</span></button>'
    )


def months_html(days, today):
    groups = {}
    for day in days:
        d = date.fromisoformat(day["date"])
        groups.setdefault((d.year, d.month), []).append(day)

    panels = []
    for (year, month), items in sorted(groups.items()):
        first = date(year, month, 1)
        pad = first.weekday()
        cells = ['<div class="day pad" aria-hidden="true"></div>'] * pad
        # A month at the edge of the model's range starts partway through.
        lead = date.fromisoformat(items[0]["date"]).day - 1
        cells += ['<div class="day pad" aria-hidden="true"></div>'] * lead
        cells += [day_cell(day, today) for day in items]
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
        bits.append("Indian stand-up: " + "; ".join(day["direct"]))
    if day["concert"]:
        bits.append("Desi draw: " + "; ".join(day["concert"]))
    if day["other"]:
        bits.append("Other comedy: " + "; ".join(day["other"]))
    return bits


def agenda_html(days, today):
    cards = []
    for day in days:
        if day["date"] < today:
            continue
        tier = TIERS[day["tier"]]
        d = date.fromisoformat(day["date"])
        on = whats_on(day)
        lines = "".join(f'<li>{esc(b)}</li>' for b in on) or \
                '<li class="clear">Nothing scheduled against you</li>'
        holiday = (f'<span class="hol">{esc(day["holiday"])}</span>'
                   if day["holiday"] else "")
        cards.append(
            f'<article class="ag t-{day["tier"]}" data-date="{day["date"]}" '
            f'data-tier="{day["tier"]}" data-dow="{esc(day["dow"])}" data-past="0">'
            f'<div class="ag-top">'
            f'<div class="ag-when"><b>{d.strftime("%a %-d %b")}</b>'
            f'<span>{d.year}</span>{holiday}</div>'
            f'<div class="badge b-{day["tier"]}">'
            f'<span class="ic" aria-hidden="true">{tier["icon"]}</span>'
            f'<span>{tier["label"]}</span></div>'
            f'</div>'
            f'<div class="ag-score">{day["score"]}<span> score</span></div>'
            f'<ul class="ag-on">{lines}</ul>'
            f'<button type="button" class="ag-more" data-open="{day["date"]}">'
            f'Why this score</button>'
            f'</article>')
    return "".join(cards)


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
        meta = " &middot; ".join(filter(None, [
            esc(e.get("city")), esc(e.get("category")),
            esc(e.get("language")) if e.get("language") != "Not stated" else "",
        ]))
        note = f'<p class="ev-note">{esc(e["notes"])}</p>' if e.get("notes") else ""
        rows.append(
            f'<article class="ev" data-city="{esc(e.get("city"))}" '
            f'data-category="{esc(e.get("category"))}">'
            f'<h4><a href="{esc(e.get("url"))}" rel="noopener noreferrer" '
            f'target="_blank">{esc(e.get("event"))}</a></h4>'
            f'<p class="ev-when">{esc(when)}{" &middot; " + esc(e["time"]) if e.get("time") else ""}</p>'
            f'<p class="ev-where">{esc(e.get("venue")) or "Venue not listed"}</p>'
            f'<p class="ev-meta">{meta} &middot; {esc(price)}</p>{note}</article>')
    return "".join(rows)


def day_payload(days):
    """Detail shown in the sheet. Kept separate so the markup stays small."""
    out = {}
    for day in days:
        out[day["date"]] = {
            "t": day["tier"], "s": day["score"], "d": day["dow"],
            "h": day["holiday"], "r": day["reasons"], "b": day["boosts"],
            "o": whats_on(day),
        }
    return out


# ---------------------------------------------------------------- assets

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
 border:1px solid var(--ring);border-radius:14px;padding:12px}
.mo h3{margin:0 0 8px;font-size:14.5px}
.grid{display:grid;grid-template-columns:repeat(7,1fr);gap:3px}
.hd{font-size:10.5px;color:var(--muted);text-align:center;padding-bottom:3px;
 letter-spacing:.04em}
.day{position:relative;min-height:54px;border-radius:8px;border:1px solid var(--grid);
 padding:3px 4px;background:var(--surface-1);display:flex;flex-direction:column;gap:1px;
 align-items:flex-start;text-align:left}
.day.pad{border:0;background:none;pointer-events:none;min-height:0}
.dn{font-size:12.5px;color:var(--ink-2);font-variant-numeric:tabular-nums}
.ic{font-size:12px;line-height:1}
.lb{font-size:8px;letter-spacing:.05em;color:var(--muted);margin-top:auto;font-weight:600}
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

@media (min-width:700px){
 .top h1{font-size:22px}
 .wrap{padding:0 20px 72px}
 .agenda{grid-template-columns:repeat(auto-fill,minmax(280px,1fr))}
 .events{grid-template-columns:repeat(auto-fill,minmax(300px,1fr))}
}
@media (min-width:900px){
 .months{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));
  overflow:visible;margin:0;padding:0}
 .mo{flex:none}
 .mo-nav{display:none}
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
 var DAYS = window.__DAYS__ || {};
 var TIER = window.__TIERS__ || {};

 var filters = {all: $('f-all'), wknd: $('f-wknd'), prime: $('f-prime')};
 var views = {agenda: $('v-agenda'), calendar: $('v-calendar')};
 var countEl = $('count');
 var cells = Array.prototype.slice.call(document.querySelectorAll('.day[data-tier]'));
 var cards = Array.prototype.slice.call(document.querySelectorAll('.ag[data-tier]'));
 var mode = 'all';

 function matches(el){
  if (el.dataset.past === '1') return false;
  if (mode === 'wknd') return el.dataset.dow === 'Fri' || el.dataset.dow === 'Sat';
  if (mode === 'prime') return el.dataset.tier === 'prime';
  return true;
 }

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
  // Counted from the calendar, which holds every date in range, so the number is the
  // same in either view.
  countEl.textContent = shown + (shown === 1 ? ' date shown' : ' dates shown');
  countEl.dataset.count = String(shown);
 }

 function applyView(next){
  for (var k in views) {
   if (views[k]) views[k].setAttribute('aria-pressed', String(k === next));
  }
  $('agenda-section').hidden = next !== 'agenda';
  $('calendar-section').hidden = next !== 'calendar';
  try { localStorage.setItem('view', next); } catch (e) {}
 }

 Object.keys(filters).forEach(function(k){
  if (filters[k]) filters[k].addEventListener('click', function(){ applyFilter(k); });
 });
 Object.keys(views).forEach(function(k){
  if (views[k]) views[k].addEventListener('click', function(){ applyView(k); });
 });

 // ---- detail sheet
 var sheet = $('sheet'), sheetBg = $('sheet-bg'), lastFocus = null;
 function openSheet(iso){
  var d = DAYS[iso];
  if (!d) return;
  var t = TIER[d.t] || {icon:'', label:d.t};
  var parts = new Date(iso + 'T00:00:00').toDateString().split(' ');
  $('sheet-title').textContent = parts[0] + ' ' + parts[2] + ' ' + parts[1] + ' ' + parts[3];
  $('sheet-sub').innerHTML = '<span class="badge b-' + d.t + '"><span class="ic" ' +
    'aria-hidden="true">' + t.icon + '</span><span>' + t.label + '</span></span> ' +
    'score ' + d.s + (d.h ? ' &middot; ' + d.h : '');
  function list(title, items, cls){
   if (!items || !items.length) return '';
   return '<p class="sub" style="margin:10px 0 0"><b>' + title + '</b></p><ul class="' +
     (cls || '') + '">' + items.map(function(x){
       return '<li>' + String(x).replace(/[<>&]/g, '') + '</li>';
     }).join('') + '</ul>';
  }
  $('sheet-body').innerHTML =
    list('On that night', d.o) +
    list('Against it', d.r) +
    list('In its favour', d.b) +
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
 document.querySelectorAll('.ag-more').forEach(function(el){
  el.addEventListener('click', function(){ openSheet(el.dataset.open); });
 });
 sheetBg.addEventListener('click', closeSheet);
 $('sheet-close').addEventListener('click', closeSheet);
 document.addEventListener('keydown', function(e){
  if (e.key === 'Escape' && !sheet.hidden) closeSheet();
 });

 // ---- month paging for the swipe carousel
 var months = $('months');
 function page(dir){
  var panels = months.querySelectorAll('.mo');
  if (!panels.length) return;
  var w = panels[0].getBoundingClientRect().width + 12;
  months.scrollBy({left: dir * w, behavior: 'smooth'});
 }
 if ($('mo-prev')) $('mo-prev').addEventListener('click', function(){ page(-1); });
 if ($('mo-next')) $('mo-next').addEventListener('click', function(){ page(1); });

 // ---- theme: the explicit choice must beat the OS setting in both directions
 $('theme').addEventListener('click', function(){
  var root = document.documentElement;
  var cur = root.getAttribute('data-theme');
  var dark = cur ? cur === 'dark'
                 : matchMedia('(prefers-color-scheme: dark)').matches;
  var next = dark ? 'light' : 'dark';
  root.setAttribute('data-theme', next);
  try { localStorage.setItem('theme', next); } catch (e) {}
  $('theme').setAttribute('aria-label', 'Switch to ' + (next === 'dark' ? 'light' : 'dark') + ' theme');
 });

 // ---- initial state
 var startView = 'calendar';
 try { startView = localStorage.getItem('view') || ''; } catch (e) { startView = ''; }
 if (startView !== 'agenda' && startView !== 'calendar') {
  startView = matchMedia('(max-width: 699px)').matches ? 'agenda' : 'calendar';
 }
 applyView(startView);
 applyFilter('all');

 // Scroll the carousel to the current month rather than the start of the range.
 var current = new Date().toISOString().slice(0, 7);
 var panel = months.querySelector('[data-month="' + current + '"]');
 if (panel) months.scrollLeft = panel.offsetLeft - months.offsetLeft;

 if ('serviceWorker' in navigator) {
  window.addEventListener('load', function(){
   navigator.serviceWorker.register('./sw.js').catch(function(){});
  });
 }
})();
"""


# ---------------------------------------------------------------- page

def render(viab, cfg, stamp, today):
    days = viab["days"]
    events = viab.get("events", [])
    counts = {}
    for day in days:
        if day["date"] >= today:
            counts[day["tier"]] = counts.get(day["tier"], 0) + 1
    upcoming = [d for d in days if d["date"] >= today]
    clash = len({d["date"] for d in upcoming if d["direct"]})
    ram_s, ram_e = cfg["ramadan"]
    ram_days = sum(1 for d in days if ram_s <= d["date"] <= ram_e)

    first, last = days[0]["date"], days[-1]["date"]
    span = (f'{date.fromisoformat(first).strftime("%B %Y")} to '
            f'{date.fromisoformat(last).strftime("%B %Y")}')

    stats = [
        (counts.get("prime", 0), "prime dates to shortlist"),
        (clash, "nights taken by a competing Indian act"),
        (counts.get("blocked", 0), "dates ruled out in total"),
        (ram_days, f'days lost to Ramadan ({date.fromisoformat(ram_s):%-d %b} to '
                   f'{date.fromisoformat(ram_e):%-d %b %Y})'),
    ]
    stat_html = "".join(
        f'<div class="ag" style="border-left-color:var(--ring)">'
        f'<div class="ag-score">{n}<span></span></div>'
        f'<div class="muted" style="font-size:12.5px">{esc(t)}</div></div>'
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

    tier_js = {k: {"icon": v["icon"], "label": v["label"]} for k, v in TIERS.items()}

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
   <p>Dubai and Abu Dhabi, {esc(span)}. Updated {esc(stamp)}.</p>
  </div>
  <button type="button" id="theme" class="icon-btn"
   aria-label="Switch theme">&#9681;</button>
 </div>
</header>

<div class="controls">
 <div class="controls-in">
  <div class="seg" role="group" aria-label="View">
   <button type="button" id="v-agenda" aria-pressed="false">Agenda</button>
   <button type="button" id="v-calendar" aria-pressed="false">Calendar</button>
  </div>
  <div class="seg" role="group" aria-label="Filter dates">
   <button type="button" id="f-all" aria-pressed="true">All</button>
   <button type="button" id="f-wknd" aria-pressed="false">Fri+Sat</button>
   <button type="button" id="f-prime" aria-pressed="false">Prime</button>
  </div>
  <span class="count" id="count" role="status" aria-live="polite"></span>
 </div>
</div>

<div class="wrap">
 <p class="scope"><b>The calendar shows every comedy and desi event on sale.</b>
 The colour answers a narrower question: could <em>you</em> stage an Indian stand-up
 show that night. A busy night for Arabic comedy is not a blocked night for you.</p>

 <section id="agenda-section" hidden>
  <h2>Upcoming dates <small>nearest first</small></h2>
  <div class="agenda">{agenda_html(days, today)}</div>
 </section>

 <section id="calendar-section" hidden>
  <h2>Month by month <small>swipe or use the arrows</small></h2>
  <div class="mo-nav">
   <button type="button" id="mo-prev" class="icon-btn"
    aria-label="Previous month">&#8592;</button>
   <span class="now">Tap any date for detail</span>
   <button type="button" id="mo-next" class="icon-btn"
    aria-label="Next month">&#8594;</button>
  </div>
  <div class="months" id="months">{months_html(days, today)}</div>
  <div class="legend">{legend}</div>
 </section>

 <h2>At a glance</h2>
 <div class="agenda">{stat_html}</div>

 <h2>Everything on sale <small>{len(events)} events</small></h2>
 <div class="events">{events_html(events)}</div>

 <section class="limits">
  <h2>What this does not know</h2>
  <ul>
   <li><b>Venue availability is not modelled.</b> Emirates Theatre, the Sheikh Rashid
    Auditorium at the Indian High School and Live@Play in Al Quoz carry most of this
    circuit and book out early. A prime date is only prime if the room is free.</li>
   <li><b>Ramadan and Eid are forecasts.</b> Expected
    {date.fromisoformat(ram_s):%-d %b} and
    {date.fromisoformat(cfg["eid_window"][0]):%-d %b %Y}, both subject to moon
    sighting. Anything from February 2027 onward is provisional.</li>
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
   for sitting inside the Dubai Comedy Festival window (-2.5), for another Indian act
   the night before or after (-1.0), for late August (-1.0) and for any other comedy
   the same night (-0.8). It gains points for the Eid Al Fitr window (+1.5), a public
   holiday (+1.0) and the December to mid-January peak (+0.5 to +0.7). A direct clash
   with an Indian stand-up act, or any date inside Ramadan, blocks the date outright.
   Prime is 4.0 and above, good 2.5, low below that.</p>
  </details>
 </section>
</div>

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
window.__DAYS__ = {json.dumps(day_payload(days), ensure_ascii=False)};
window.__TIERS__ = {json.dumps(tier_js, ensure_ascii=False)};
</script>
<script>{JS}</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--viability", default=str(ROOT / "docs" / "viability.json"))
    ap.add_argument("--out-dir", default=str(ROOT / "docs"))
    ap.add_argument("--today", help="override today's date (testing)")
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

    today = args.today or date.today().isoformat()
    stamp = viab.get("generated", today)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    (out / "index.html").write_text(render(viab, cfg, stamp, today), encoding="utf-8")
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
    print(f"built {out/'index.html'} ({page // 1024} KB), "
          f"{len(viab['days'])} days, {len(viab.get('events', []))} events")
    print(f"  manifest, service worker and icons written; cache stamp {stamp}")

    absolute = re.findall(r'(?:href|src)="(/[^/][^"]*)"', (out / "index.html").read_text())
    if absolute:
        print(f"  WARNING root-absolute paths would 404 on a project site: {absolute}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
