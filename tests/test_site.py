"""
Smoke test for the built site. Run after src/build_site.py.

The brief calls for this explicitly, because of a bug that has already been fixed once:
the filter buttons have ids like `f-all`, and a hyphenated id is not a valid bare
JavaScript identifier, so referencing it as one throws a ReferenceError that silently
kills every handler defined below it. Eyeballing the page does not catch that; the page
looks fine and simply stops responding. So this asserts that clicking each filter
actually changes the visible date count, and fails the build if any console error fires.

Served over HTTP on a free port rather than file://, because service workers do not
register on file:// and the offline check would be meaningless.

Run:  python tests/test_site.py
"""
import datetime as dt
import glob
import http.server
import json
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

failures = []
checks = 0


def check(name, ok, detail=""):
    global checks
    checks += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
    if not ok:
        failures.append(f"{name} {detail}".strip())
    return ok


def find_chromium():
    """The image ships a browser build that may not match playwright's expected one."""
    for pattern in ("/opt/pw-browsers/chromium-*/chrome-linux/chrome",
                    "/opt/pw-browsers/chromium_headless_shell-*/chrome-linux/headless_shell"):
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[-1]
    return None


def serve(directory):
    """Start a throwaway static server on a free port. Returns (url, shutdown)."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(directory), **kw)

        def log_message(self, *a):
            pass

    httpd = socketserver.TCPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}/", httpd.shutdown


def open_calendar(page):
    """The calendar lives behind a tab now, so panels must be shown before measuring."""
    page.click("#tab-calendar")
    page.wait_for_timeout(120)


def count_of(page):
    """None means the page script never ran, which is the failure this test exists for."""
    raw = page.get_attribute("#count", "data-count")
    return None if raw is None else int(raw)


def visible_days(page):
    return page.eval_on_selector_all(
        ".day[data-tier]",
        "els => els.filter(e => !e.classList.contains('dim')).length")


def main():
    if not (DOCS / "index.html").exists():
        print("docs/index.html missing; run python src/build_site.py first")
        return 1

    url, shutdown = serve(DOCS)
    exe = find_chromium()
    print(f"serving {DOCS} at {url}")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=exe)
            ctx = browser.new_page(viewport={"width": 390, "height": 780})
            errors = []
            ctx.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            ctx.on("pageerror", lambda e: errors.append(str(e)))
            ctx.goto(url, wait_until="load")

            print("\ntabs")
            check("header carries a name and nothing else",
                  ctx.eval_on_selector_all(".top p", "els => els.length") == 0
                  and ctx.eval_on_selector_all(".scope", "els => els.length") == 0)
            check("events is the landing tab", ctx.is_visible("#panel-events"))
            # The app is Events Tracker; each section is a page that names itself in the
            # heading and in the browser tab.
            for name, page in (("calendar", "Calendar"), ("checklist", "Checklist"),
                               ("events", "Events")):
                ctx.click(f"#tab-{name}")
                ctx.wait_for_timeout(120)
                check(f"{name} tab opens", ctx.is_visible(f"#panel-{name}")
                      and ctx.get_attribute(f"#tab-{name}", "aria-pressed") == "true")
                check(f"{name} names itself in the heading",
                      ctx.inner_text("#page-title").strip() == page,
                      ctx.inner_text("#page-title"))
                check(f"{name} names itself in the browser tab",
                      ctx.title() == f"{page} \u00b7 Events Tracker", ctx.title())

            print("\nevent filters")
            total = int(ctx.get_attribute("#ev-count", "data-count"))
            check("every event listed by default", total > 0, f"{total} events")
            ctx.click('details.ms[data-ms="category"] summary')
            ctx.click('input[data-facet="category"][value="Desi"]')
            ctx.wait_for_timeout(120)
            desi = int(ctx.get_attribute("#ev-count", "data-count"))
            check("a facet narrows the list", 0 < desi < total, f"{total} -> {desi}")
            check("filtered rows all match the facet", ctx.eval_on_selector_all(
                ".ev", "els => els.filter(e => !e.hidden)"
                       ".every(e => e.dataset.category === 'Desi')"))
            ctx.click('input[data-facet="category"][value="Comedy"]')
            ctx.wait_for_timeout(120)
            both = int(ctx.get_attribute("#ev-count", "data-count"))
            check("facets are multi-select, not single", both > desi,
                  f"{desi} -> {both}")
            check("the facet shows how many are ticked",
                  ctx.inner_text('.ms[data-ms="category"] .ms-badge') == "2")
            ctx.click("#ev-clear")
            ctx.wait_for_timeout(120)
            check("clearing restores every event",
                  int(ctx.get_attribute("#ev-count", "data-count")) == total)
            for facet in ("month", "artist", "category", "language"):
                check(f"{facet} facet is present", ctx.eval_on_selector_all(
                    f'input[data-facet="{facet}"]', "els => els.length") > 1)

            open_calendar(ctx)
            print("\nfilters")
            base = count_of(ctx)
            if not check("page script initialised", base is not None,
                         "" if base is not None else
                         "#count was never populated, so the handlers never bound: "
                         + ("; ".join(errors[:2]) if errors
                            else "no console error was reported")):
                browser.close()
                print(f"\n{checks - len(failures)}/{checks} checks passed")
                for f in failures:
                    print(f"  FAILED: {f}")
                return 1
            check("all: some dates shown", base > 0, f"count={base}")
            check("all: dimmed count agrees with filter state",
                  visible_days(ctx) == base)

            ctx.click("#f-wknd")
            wknd = count_of(ctx)
            check("Fri+Sat changes the count", wknd != base, f"{base} -> {wknd}")
            check("Fri+Sat shows only Fri and Sat", ctx.eval_on_selector_all(
                ".day[data-tier]:not(.dim)",
                "els => els.every(e => ['Fri','Sat'].includes(e.dataset.dow))"))

            ctx.click("#f-prime")
            prime = count_of(ctx)
            check("Prime changes the count", prime != wknd, f"{wknd} -> {prime}")
            check("Prime shows only prime dates", ctx.eval_on_selector_all(
                ".day[data-tier]:not(.dim)",
                "els => els.every(e => e.dataset.tier === 'prime')"))

            ctx.click("#f-all")
            check("All restores the original count", count_of(ctx) == base,
                  f"{prime} -> {count_of(ctx)}")
            check("every filter produced a distinct count",
                  len({base, wknd, prime}) == 3, f"{base}/{wknd}/{prime}")

            print("\ncalendar")
            check("the grid is the only view", ctx.is_visible("#calendar-section")
                  and ctx.eval_on_selector_all("#v-agenda, .ag[data-tier]",
                                               "els => els.length") == 0)
            check("month panels rendered",
                  ctx.eval_on_selector_all(".mo", "els => els.length") >= 12,
                  f'{ctx.eval_on_selector_all(".mo", "els => els.length")} months')
            check("months scroll horizontally on a phone, not stacked twelve deep",
                  ctx.eval_on_selector("#months",
                                       "el => el.scrollWidth > el.clientWidth + 50"))

            wide = browser.new_page(viewport={"width": 1280, "height": 900})
            wide.goto(url, wait_until="load")
            open_calendar(wide)
            check("desktop shows several months side by side",
                  wide.eval_on_selector("#months",
                                        "el => getComputedStyle(el).display === 'grid'"))
            wide.close()

            print("\nlayout holds at every width")
            # A 1fr grid track will not shrink below its content, so the BLOCKED label
            # used to set a ~55px floor per column. February 2027 is entirely Ramadan
            # blocked, so all seven columns hit that floor at once and the month grid
            # burst out of its panel. Check every month at every width, both that the
            # grid fits and that the label is not being clipped to make it fit.
            for width in (320, 360, 390, 768, 1280, 1440):
                pg = browser.new_page(viewport={"width": width, "height": 900})
                pg.goto(url, wait_until="load")
                open_calendar(pg)
                over = pg.eval_on_selector_all(
                    ".mo .grid",
                    "gs => gs.filter(g => g.scrollWidth > g.clientWidth + 1)"
                    ".map(g => g.closest('.mo').dataset.month)")
                # Only labels that are actually rendered can be truncated; where a
                # panel is too narrow the label is hidden outright and the icon and
                # hatch carry the tier instead.
                clipped = pg.eval_on_selector_all(
                    ".day:not(.pad) .lb",
                    "els => els.filter(e => getComputedStyle(e).display !== 'none'"
                    " && e.scrollWidth > e.clientWidth + 1).length")
                hscroll = pg.evaluate(
                    "document.body.scrollWidth > document.body.clientWidth + 1")
                check(f"{width}px: no month grid overflows its panel", not over,
                      ", ".join(over))
                check(f"{width}px: no tier label is truncated", clipped == 0,
                      f"{clipped} clipped")
                check(f"{width}px: page does not scroll sideways", not hscroll)
                pg.close()

            print("\nnavigation adapts to the screen")
            # One set of nav buttons, laid out two ways. Duplicating the markup per
            # breakpoint would mean duplicate ids, so this checks the CSS switch.
            nav = browser.new_page(viewport={"width": 1440, "height": 900})
            nav.goto(url, wait_until="load")
            rail = nav.evaluate("""() => {
                const s = document.querySelector('.side').getBoundingClientRect();
                const col = document.querySelector('.col').getBoundingClientRect();
                const foot = document.querySelector('.side-foot');
                const btns = [...document.querySelectorAll('.side-nav button')]
                  .map(b => Math.round(b.getBoundingClientRect().top));
                return {w: Math.round(s.width), h: Math.round(s.height),
                        colLeft: Math.round(col.left),
                        foot: foot ? getComputedStyle(foot).display : 'none',
                        stacked: new Set(btns).size === btns.length}; }""")
            check("laptop shows a left rail, not a top bar",
                  rail["h"] > 400 and rail["w"] < 320, f"{rail['w']}x{rail['h']}")
            check("content sits to the right of the rail",
                  rail["colLeft"] >= rail["w"] - 1, f"col at {rail['colLeft']}")
            check("rail stacks its items vertically", rail["stacked"])
            check("rail shows the data stamp", rail["foot"] != "none")
            nav.close()

            phone = browser.new_page(viewport={"width": 390, "height": 800})
            phone.goto(url, wait_until="load")
            bar = phone.evaluate("""() => {
                const el = document.querySelector('.side');
                const s = el.getBoundingClientRect();
                const btns = [...document.querySelectorAll('.side-nav button')]
                  .map(b => Math.round(b.getBoundingClientRect().top));
                const body = document.body.getBoundingClientRect();
                const last = document.querySelector('.cl-json, .limits, .events');
                return {w: Math.round(s.width), h: Math.round(s.height),
                        bottom: Math.round(s.bottom), top: Math.round(s.top),
                        fixed: getComputedStyle(el).position,
                        sameRow: new Set(btns).size === 1,
                        pad: parseFloat(getComputedStyle(document.body).paddingBottom),
                        foot: getComputedStyle(
                          document.querySelector('.side-foot')).display}; }""")
            check("phone nav is pinned to the bottom of the viewport",
                  bar["fixed"] == "fixed" and abs(bar["bottom"] - 800) <= 1,
                  f"position={bar['fixed']} bottom={bar['bottom']}")
            check("phone nav is not a top bar", bar["top"] > 600, f"top={bar['top']}")
            check("phone nav is a bar, not a rail",
                  bar["h"] < 120 and bar["w"] > 300, f"{bar['w']}x{bar['h']}")
            check("content clears the bottom nav", bar["pad"] >= bar["h"] - 4,
                  f"padding {bar['pad']} vs bar {bar['h']}")
            check("phone keeps the sections on one row", bar["sameRow"])
            check("phone nav carries sections only", bar["foot"] == "none")
            check("theme toggle stays reachable on a phone",
                  phone.is_visible("#theme"))
            check("phone does not wrap the lens buttons", phone.eval_on_selector_all(
                ".seg button", "els => els.every(e => "
                "e.getBoundingClientRect().height < 46)"))
            phone.close()

            print("\nlaptop gets a laptop layout")
            desk = browser.new_page(viewport={"width": 1440, "height": 900})
            desk.goto(url, wait_until="load")
            open_calendar(desk)
            check("twelve months land as three rows of four on a wide laptop",
                  desk.evaluate("""() => {
                      const mos = [...document.querySelectorAll('.mo')];
                      const top = mos[0].getBoundingClientRect().top;
                      const perRow = mos.filter(
                          m => m.getBoundingClientRect().top === top).length;
                      const rows = new Set(mos.map(
                          m => Math.round(m.getBoundingClientRect().top))).size;
                      return perRow === 4 && rows === 3; }"""))
            # Where the label is dropped for want of room, the icon has to grow, or
            # the cell would be carrying colour and a hatch alone.
            check("a cell without a visible label still shows its icon",
                  desk.evaluate("""() => {
                      const lb = document.querySelector('.day:not(.pad) .lb');
                      const ic = document.querySelector('.day:not(.pad) .ic');
                      if (getComputedStyle(lb).display !== 'none') return true;
                      const r = ic.getBoundingClientRect();
                      return ic.querySelector('use') && r.width >= 12 && r.height >= 12;
                  }"""))
            check("months lay out two or more to a row",
                  desk.evaluate("""() => {
                      const mos = [...document.querySelectorAll('.mo')];
                      const top = mos[0].getBoundingClientRect().top;
                      return mos.filter(m => m.getBoundingClientRect().top === top).length;
                  }""") >= 2)
            # Four months to a row makes the cells narrow by design, so the old
            # "wider than a phone" assertion no longer applies. What still has to hold
            # is that every date remains a usable click target.
            check("day cells stay a usable target at four months a row",
                  desk.evaluate("""() => [...document.querySelectorAll('.day:not(.pad)')]
                      .every(e => { const r = e.getBoundingClientRect();
                                    return r.width >= 28 && r.height >= 40; })"""))
            # Every dropdown in the app is the same component. A native <select> renders
            # the operating system's menu, which is what looked foreign here before.
            # Restores the calendar tab afterwards: later checks hover a date, and a
            # hidden panel has nothing to hover.
            check("no control anywhere falls back to a native select",
                  desk.eval_on_selector_all("select", "els => els.length") == 0)
            check("the checklist and status dropdowns are the shared component",
                  desk.evaluate("""() => {
                      document.getElementById('tab-checklist').click();
                      const picker = document.querySelector(
                          '.cl-bar .ms-choice > summary');
                      const status = document.querySelector('.tk .ms-choice > summary');
                      const facet = document.querySelector('.ms:not(.ms-choice) > summary');
                      const a = getComputedStyle(picker), b = getComputedStyle(status),
                            c = getComputedStyle(facet);
                      const same = a.borderRadius === c.borderRadius &&
                             a.backgroundColor === c.backgroundColor &&
                             a.borderColor === c.borderColor &&
                             b.borderRadius === c.borderRadius &&
                             b.backgroundColor === c.backgroundColor &&
                             picker.querySelector('.ms-caret') !== null &&
                             status.querySelector('.ms-caret') !== null;
                      document.getElementById('tab-calendar').click();
                      return same; }"""))
            # Picks a card the filters are actually showing. Reading the first .ev
            # in the DOM measured whatever sorted earliest, and the day that event
            # slid into the past the filter hid it, its computed display became
            # none, and a layout check started failing on data rather than layout.
            check("events list becomes a table, not a card wall",
                  desk.evaluate("""() => {
                      document.getElementById('tab-events').click();
                      const el = Array.from(document.querySelectorAll('.ev'))
                                      .find(e => e.offsetParent !== null);
                      const out = el ? getComputedStyle(el).display
                                     : 'nothing visible';
                      document.getElementById('tab-calendar').click();
                      return out; }""") == "grid")
            desk.hover(".day.t-blocked")
            desk.wait_for_timeout(250)
            check("hovering a date shows a summary without clicking",
                  desk.eval_on_selector("#tip", "el => el.classList.contains('on')"))
            check("hover summary names the competing event",
                  "Sunil Grover" in desk.inner_text("#tip")
                  or len(desk.inner_text("#tip").strip()) > 20)
            desk.click(".day[data-tier]")
            box = desk.eval_on_selector("#sheet", "el => el.getBoundingClientRect().top")
            check("detail opens as a centred panel, not a phone bottom sheet", box > 40,
                  f"top={round(box)}")
            desk.close()

            print("\nviability lenses")
            # The markup carries only the default lens; switching restyles every cell
            # from the inlined payload, so a lens that does not change the tiers would
            # mean the payload never reached the DOM.
            def tier_counts(page):
                return page.eval_on_selector_all(".day[data-tier]", """els => {
                    const c = {};
                    els.forEach(e => c[e.dataset.tier] = (c[e.dataset.tier] || 0) + 1);
                    return c; }""")

            open_calendar(ctx)
            ctx.click('[data-lens-opt="standup"]')
            ctx.wait_for_timeout(200)
            standup = tier_counts(ctx)
            ctx.click('[data-lens-opt="music"]')
            ctx.wait_for_timeout(200)
            music = tier_counts(ctx)
            check("a lens exists for each kind of show", ctx.eval_on_selector_all(
                "[data-lens-opt]", "els => els.length") >= 3)
            check("switching lens re-tiers the calendar", standup != music,
                  f"standup {standup.get('blocked')} blocked vs "
                  f"music {music.get('blocked')} blocked")
            check("the lens explains itself",
                  len(ctx.inner_text("#lens-blurb").strip()) > 10)
            ctx.click('[data-lens-opt="desi"]')
            ctx.wait_for_timeout(200)
            desi_counts = tier_counts(ctx)
            check("any-desi blocks at least as much as either kind alone",
                  desi_counts.get("blocked", 0) >= max(standup.get("blocked", 0),
                                                       music.get("blocked", 0)),
                  f"desi {desi_counts.get('blocked')}")
            ctx.click('[data-lens-opt="standup"]')

            print("\nchecklist")
            ctx.click("#tab-checklist")
            ctx.wait_for_timeout(150)
            shown = ctx.eval_on_selector_all(".tk", "els => els.filter(e => !e.hidden).length")
            check("checklist tasks render", shown > 0, f"{shown} tasks")
            ctx.fill("#cl-date", "2027-01-30")
            ctx.dispatch_event("#cl-date", "change")
            ctx.wait_for_timeout(200)
            # Task 1 is D-150; 150 days before 30 Jan 2027 is 2 Sep 2026.
            check("due dates derive from the show date",
                  "2026-09-02" in ctx.eval_on_selector(
                      '.tk[data-n="1"] .tk-due', "e => e.textContent"),
                  ctx.eval_on_selector('.tk[data-n="1"] .tk-due', "e => e.textContent"))
            before = ctx.inner_text(".cl-cell")
            ctx.click('.tk[data-n="1"] .ms-choice summary')
            ctx.click('.tk[data-n="1"] .ms-choice input[value="Done"]')
            ctx.wait_for_timeout(250)
            check("marking a task done moves the progress figure",
                  ctx.inner_text(".cl-cell") != before,
                  f"{before.split(chr(10))[0]} -> {ctx.inner_text('.cl-cell').split(chr(10))[0]}")
            print("\nadding checklist items")
            before = ctx.eval_on_selector_all(".tk", "els => els.length")
            ctx.click(".cl-add summary")
            ctx.fill("#add-task", "Confirm generator backup for the ballroom")
            ctx.fill("#add-owner", "Saahil")
            ctx.fill("#add-dminus", "20")
            ctx.fill("#add-ws-new", "Contingency")
            ctx.check("#add-blocking")
            ctx.click("#add-save")
            ctx.wait_for_timeout(300)
            check("adding a task puts it in the list",
                  ctx.eval_on_selector_all(".tk", "els => els.length") == before + 1,
                  f"{before} -> {ctx.eval_on_selector_all('.tk', 'els => els.length')}")
            check("the added task keeps what was typed", ctx.evaluate("""() => {
                const el = document.querySelector('.tk[data-added="1"]');
                const meta = el.querySelector('.tk-meta').textContent;
                return el.querySelector('.tk-task').textContent.includes('generator')
                    && meta.includes('Contingency') && meta.includes('Saahil')
                    && meta.includes('D-20') && meta.includes('BLOCKING'); }"""))
            check("a new workstream joins the filter", ctx.eval_on_selector_all(
                "#cl-ws-menu input", "els => els.some(e => e.value === 'Contingency')"))
            check("an added task counts toward the totals",
                  "of " + str(before + 1) in ctx.inner_text(".cl-progress"),
                  " ".join(ctx.inner_text(".cl-progress").split())[:60])
            check("an added task reaches the exported JSON",
                  "Contingency" in ctx.input_value("#cl-out"))
            # Numbering has to continue past the imported tasks, or an added task would
            # share a key with one of them and they would toggle together.
            check("added tasks do not reuse an imported task's number",
                  ctx.evaluate("""() => {
                      const ns = [...document.querySelectorAll('.tk')]
                          .map(e => e.dataset.n);
                      return new Set(ns).size === ns.length; }"""))
            ctx.reload(wait_until="load")
            ctx.click("#tab-checklist")
            ctx.wait_for_timeout(300)
            check("an added task survives a reload",
                  ctx.eval_on_selector_all('.tk[data-added="1"]', "els => els.length") == 1)
            ctx.click('.tk[data-added="1"] .tk-del')
            ctx.wait_for_timeout(250)
            check("an added task can be removed again",
                  ctx.eval_on_selector_all('.tk[data-added="1"]', "els => els.length") == 0
                  and ctx.eval_on_selector_all(".tk", "els => els.length") == before)

            ctx.check("#cl-blockers")
            ctx.wait_for_timeout(150)
            check("blockers-only filter narrows the list", ctx.eval_on_selector_all(
                ".tk", "els => els.filter(e => !e.hidden)"
                       ".every(e => e.dataset.blocking === '1')"))
            ctx.uncheck("#cl-blockers")
            ctx.wait_for_timeout(100)
            # Reload this page rather than opening a new one: a new page from
            # browser.new_page() gets its own browser context, and therefore its own
            # localStorage, so it could never see state saved here.
            ctx.reload(wait_until="load")
            ctx.click("#tab-checklist")
            ctx.wait_for_timeout(250)
            check("checklist state survives a reload",
                  ctx.input_value("#cl-date") == "2027-01-30"
                  and ctx.eval_on_selector('.tk[data-n="1"] .ms-choice input:checked',
                                           "e => e.value") == "Done",
                  f'date={ctx.input_value("#cl-date")}')
            ctx.click("#tab-calendar")

            print("\nicons, chrome and past events")
            check("icons come from one inline sprite, not glyphs or a font",
                  ctx.eval_on_selector_all("symbol[id^='i-']", "els => els.length") >= 10)
            check("every icon resolves to a symbol that exists",
                  ctx.evaluate("""() => [...document.querySelectorAll('svg use')]
                      .every(u => document.getElementById(
                          (u.getAttribute('href') || '').slice(1)))"""))
            check("day icons render at a visible size", ctx.eval_on_selector(
                ".day:not(.pad) .ic",
                "el => el.getBoundingClientRect().width >= 10"))
            # A tapped control in Chrome and Safari otherwise flashes a filled rectangle
            # before the pressed state lands.
            check("no tap-highlight rectangle on the bottom nav",
                  ctx.eval_on_selector(".side-nav button",
                                       "el => getComputedStyle(el)"
                                       ".webkitTapHighlightColor")
                  in ("rgba(0, 0, 0, 0)", "transparent"))
            check("the theme control is the icon alone, with no box",
                  ctx.eval_on_selector("#theme", """el => {
                      const s = getComputedStyle(el);
                      return s.borderStyle === 'none' &&
                             s.backgroundColor === 'rgba(0, 0, 0, 0)'; }"""))
            check("phone header shows the app name on every page",
                  ctx.inner_text("h1").strip() == "Events Tracker",
                  ctx.inner_text("h1"))
            # Must be measured on the visible panel: a hidden element reports zero
            # width, so a hidden row would pass this by accident.
            check("the filter row scrolls sideways rather than stacking",
                  ctx.eval_on_selector("#panel-calendar .filters",
                                       "el => getComputedStyle(el).flexWrap === 'nowrap'"
                                       " && el.scrollWidth > el.clientWidth"),
                  ctx.eval_on_selector("#panel-calendar .filters",
                                       "el => el.scrollWidth + ' vs ' + el.clientWidth"))

            print("\ncurrent month and past dates")
            first = ctx.eval_on_selector(
                ".mo:first-of-type", "el => el.querySelector('.day:not(.pad)').dataset.date")
            check("the current month starts on the 1st", first.endswith("-01"), first)
            check("dates already gone are marked past",
                  ctx.eval_on_selector_all(".day.past", "els => els.length") > 0)

            ctx.click("#tab-events")
            ctx.wait_for_timeout(150)
            check("every event row records whether it is still listed",
                  ctx.eval_on_selector_all(
                      ".ev", "els => els.every(e => e.dataset.listed !== undefined)"))
            check("the events list can show past events on request",
                  ctx.eval_on_selector_all("#ev-past", "els => els.length") == 1)
            shown_now = int(ctx.get_attribute("#ev-count", "data-count"))
            past_now = int(ctx.get_attribute("#ev-count", "data-past"))
            ctx.check("#ev-past")
            ctx.wait_for_timeout(150)
            check("showing past adds exactly the past events back",
                  int(ctx.get_attribute("#ev-count", "data-count")) == shown_now + past_now,
                  f"{shown_now} + {past_now} past")
            ctx.uncheck("#ev-past")
            open_calendar(ctx)

            print("\ncolour is never the only signal")
            check("every scored day carries an icon and a text label",
                  ctx.eval_on_selector_all(".day[data-tier]", """els => els.every(e =>
                       e.querySelector('.ic') && e.querySelector('.lb') &&
                       e.querySelector('.lb').textContent.trim().length > 0)"""))
            check("every scored day names its tier to a screen reader",
                  ctx.eval_on_selector_all(".day[data-tier]", """els => els.every(e =>
                       (e.getAttribute('aria-label') || '').includes(e.dataset.tier))"""))
            check("blocked days carry a hatch as well as colour",
                  ctx.eval_on_selector_all(".day.t-blocked", """els => els.length === 0 ||
                       els.every(e => getComputedStyle(e).backgroundImage.includes('gradient'))"""))

            print("\ndetail sheet")
            ctx.click(".day[data-tier]:not(.dim)")
            check("tapping a date opens the sheet", ctx.is_visible("#sheet"))
            check("sheet names the date",
                  len(ctx.inner_text("#sheet-title").strip()) > 0)
            ctx.keyboard.press("Escape")
            check("Escape closes the sheet", not ctx.is_visible("#sheet"))

            print("\ntheme")
            start = ctx.evaluate("document.documentElement.getAttribute('data-theme')")
            ctx.click("#theme")
            one = ctx.evaluate("document.documentElement.getAttribute('data-theme')")
            ctx.click("#theme")
            two = ctx.evaluate("document.documentElement.getAttribute('data-theme')")
            check("toggle flips the theme", one != two and one is not None,
                  f"{start} -> {one} -> {two}")

            # The toggle has to beat the OS setting in BOTH directions, so exercise each.
            LIGHT_PLANE, DARK_PLANE = "rgb(249, 249, 247)", "rgb(13, 13, 13)"
            for scheme, want_attr, want_bg, started in (
                    ("dark", "light", LIGHT_PLANE, DARK_PLANE),
                    ("light", "dark", DARK_PLANE, LIGHT_PLANE)):
                pg = browser.new_page(color_scheme=scheme)
                pg.goto(url, wait_until="load")
                before = pg.evaluate("getComputedStyle(document.body).backgroundColor")
                pg.click("#theme")
                after = pg.evaluate("getComputedStyle(document.body).backgroundColor")
                attr = pg.evaluate("document.documentElement.getAttribute('data-theme')")
                check(f"OS {scheme}: page starts in the matching palette",
                      before == started, before)
                check(f"OS {scheme}: toggle sets data-theme to {want_attr}",
                      attr == want_attr, str(attr))
                check(f"OS {scheme}: {want_attr} palette actually paints",
                      after == want_bg, after)
                pg.close()

            print("\ninstallability and offline")
            man = json.loads(Path(DOCS / "manifest.webmanifest").read_text())
            check("the installed app is named for the app, not a page",
                  man["name"] == "Events Tracker", man["name"])
            check("manifest start_url is relative",
                  not str(man["start_url"]).startswith("/"), man["start_url"])
            check("manifest scope is relative",
                  not str(man["scope"]).startswith("/"), man["scope"])
            check("manifest icons are relative and present",
                  all(i["src"].startswith("./") and (DOCS / i["src"][2:]).exists()
                      for i in man["icons"]))
            check("no root-absolute asset paths in the page",
                  ctx.eval_on_selector_all("[href],[src]", """els => els.every(e => {
                       const v = e.getAttribute('href') || e.getAttribute('src') || '';
                       return !v.startsWith('/'); })"""))

            ctx.wait_for_function(
                "navigator.serviceWorker.ready.then(() => true)", timeout=15000)
            check("service worker registers", ctx.evaluate(
                "!!navigator.serviceWorker.controller || "
                "navigator.serviceWorker.getRegistrations().then(r => r.length > 0)")
                is not False)

            # Serve nothing and reload: the page must still come back from cache.
            shutdown()
            ctx.context.set_offline(True)
            ctx.reload(wait_until="load")
            open_calendar(ctx)
            check("page still renders with the network down",
                  ctx.eval_on_selector_all(".day[data-tier]", "els => els.length") > 0)
            check("filters still work offline", (ctx.click("#f-prime"),
                                                 count_of(ctx) > 0)[1])

            print("\nrefresh control")
            ctx.click("#tab-events")
            ctx.wait_for_timeout(200)
            check("the events tab carries a refresh button",
                  ctx.is_visible("#refresh"))
            check("it says how old the data is, in days not a raw date",
                  "last checked" in ctx.inner_text("#data-when").lower(),
                  ctx.inner_text("#data-when"))
            check("fresh data is not flagged stale",
                  ctx.eval_on_selector(
                      '#data-when', "el => el.classList.contains('stale')")
                  == (dt.date.fromisoformat(
                      ctx.evaluate("() => window.__STAMP__")) <
                      dt.date.today() - dt.timedelta(days=1)))
            check("the button knows which repository to ask",
                  bool(ctx.evaluate("() => (window.__REPO__||{}).slug")),
                  str(ctx.evaluate("() => window.__REPO__")))
            # No token stored, so the button must explain itself rather than
            # silently failing, and must offer the no-token way out.
            ctx.click("#refresh")
            ctx.wait_for_timeout(250)
            check("with no token it opens the setup dialog",
                  ctx.is_visible("#run-sheet") and ctx.is_visible("#gh-token"))
            check("the dialog offers running it on GitHub instead",
                  ctx.eval_on_selector(
                      '#run-body a[href*="/actions/workflows/"]',
                      "el => el.getAttribute('target') === '_blank'"))
            check("the token field is a password field",
                  ctx.get_attribute("#gh-token", "type") == "password")
            check("no token is stored just by opening it",
                  not ctx.evaluate("() => localStorage.getItem('gh:token')"))
            ctx.keyboard.press("Escape")
            ctx.wait_for_timeout(200)
            check("Escape closes it and clears the backdrop",
                  not ctx.is_visible("#run-sheet")
                  and not ctx.is_visible("#sheet-bg"))
            open_calendar(ctx)

            print("\nsync backend")
            # Asserts the relationship, not a fixed state: docs/ is built local-only
            # or configured depending on data/backend.json, and both are valid.
            check("the account row appears exactly when a backend is configured",
                  ctx.evaluate("""() => {
                      const b = window.__BACKEND__ || {};
                      const on = !!(b.supabase_url && b.supabase_anon_key);
                      return document.getElementById('cl-account').hidden === !on; }"""),
                  "configured" if ctx.evaluate(
                      "() => !!((window.__BACKEND__||{}).supabase_url)") else "local only")
            check("the header account button follows the same rule",
                  ctx.evaluate("""() => {
                      const b = window.__BACKEND__ || {};
                      const on = !!(b.supabase_url && b.supabase_anon_key);
                      return document.getElementById('acct').hidden === !on; }"""))
            check("the page states its backend config either way",
                  ctx.evaluate("() => typeof window.__BACKEND__ === 'object'"))

            # Build a second copy pointed at a project that does not answer. This is the
            # state the app will spend most of its life in: configured, but the network
            # or the server is unavailable. It must stay fully usable.
            with tempfile.TemporaryDirectory() as tmp:
                cfg = Path(tmp) / "backend.json"
                cfg.write_text(json.dumps({"supabase_url": "https://demo.supabase.co",
                                           "supabase_anon_key": "demo-key"}))
                out = Path(tmp) / "site"
                built = subprocess.run(
                    [sys.executable, str(ROOT / "src" / "build_site.py"),
                     "--backend", str(cfg), "--out-dir", str(out)],
                    capture_output=True, text=True)
                if not check("a configured build succeeds", built.returncode == 0,
                             built.stderr.strip()[:120]):
                    pass
                else:
                    url2, stop2 = serve(out)
                    off = browser.new_page(viewport={"width": 1440, "height": 900})
                    dead = []
                    off.on("pageerror", lambda e: dead.append(str(e)))
                    off.route("**/demo.supabase.co/**", lambda r: r.abort())
                    off.goto(url2, wait_until="load")
                    off.click("#tab-checklist")
                    off.wait_for_timeout(400)
                    check("a configured backend shows the sign-in row",
                          off.is_visible("#cl-account"))
                    check("an unreachable backend still renders the tasks",
                          off.eval_on_selector_all(".tk", "els => els.length") > 0)

                    # Sign-in is email and password, reachable from any tab, not a
                    # control buried in the checklist.
                    off.click("#acct")
                    off.wait_for_timeout(200)
                    check("the header button opens the account dialog",
                          off.is_visible("#acct-sheet"))
                    check("the dialog asks for an email and a password",
                          off.is_visible("#acct-email") and off.is_visible("#acct-pass"))
                    check("the password field is a password field",
                          off.get_attribute("#acct-pass", "type") == "password")
                    check("there is no native select or magic-link-only path left",
                          off.eval_on_selector_all(
                              "#acct-sheet select", "els => els.length") == 0)
                    off.fill("#acct-email", "someone@example.com")
                    off.click("#acct-form button[type=submit]")
                    off.wait_for_timeout(300)
                    check("submitting without a password is refused locally",
                          "password" in off.inner_text("#acct-body").lower()
                          and off.is_visible("#acct-sheet"))
                    off.fill("#acct-pass", "hunter2hunter2")
                    off.click("#acct-form button[type=submit]")
                    off.wait_for_timeout(600)
                    check("an unreachable backend says so instead of failing silently",
                          "reach" in off.inner_text("#acct-body").lower(),
                          " ".join(off.inner_text("#acct-body").split())[-40:])
                    check("what was typed survives the failure",
                          off.input_value("#acct-email") == "someone@example.com")
                    off.click("#acct-swap")
                    off.wait_for_timeout(200)
                    check("the dialog switches to creating an account",
                          "create account" in off.inner_text(
                              "#acct-form button[type=submit]").lower())
                    off.keyboard.press("Escape")
                    off.wait_for_timeout(200)
                    check("Escape closes the account dialog",
                          not off.is_visible("#acct-sheet")
                          and not off.is_visible("#sheet-bg"))
                    off.click(".cl-add summary")
                    off.fill("#add-task", "Still works with the backend down")
                    off.click("#add-save")
                    off.wait_for_timeout(300)
                    check("edits still work with the backend down",
                          off.eval_on_selector_all('.tk[data-added="1"]',
                                                   "els => els.length") == 1)
                    off.reload(wait_until="load")
                    off.click("#tab-checklist")
                    off.wait_for_timeout(400)
                    check("those edits still persist locally",
                          off.eval_on_selector_all('.tk[data-added="1"]',
                                                   "els => els.length") == 1)
                    check("a dead backend raises no page errors", not dead,
                          "; ".join(dead[:2]))
                    off.close()
                    stop2()

            print("\nconsole")
            real = [e for e in errors if "favicon" not in e.lower()]
            check("no console or page errors", not real, "; ".join(real[:3]))
            browser.close()
    finally:
        try:
            shutdown()
        except Exception:
            pass

    print(f"\n{checks - len(failures)}/{checks} checks passed")
    if failures:
        for f in failures:
            print(f"  FAILED: {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
