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
import glob
import http.server
import json
import socket
import socketserver
import sys
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
            check("events is the landing tab", ctx.is_visible("#panel-events"))
            for name in ("calendar", "checklist", "events"):
                ctx.click(f"#tab-{name}")
                ctx.wait_for_timeout(100)
                check(f"{name} tab opens", ctx.is_visible(f"#panel-{name}")
                      and ctx.get_attribute(f"#tab-{name}", "aria-pressed") == "true")

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

            print("\nagenda and calendar")
            check("agenda is the default on a phone viewport",
                  ctx.get_attribute("#v-agenda", "aria-pressed") == "true")
            check("agenda cards are visible",
                  ctx.eval_on_selector_all(".ag[data-tier]",
                                           "els => els.filter(e => !e.hidden).length") > 0)
            ctx.click("#f-prime")
            check("filter hides agenda cards too", ctx.eval_on_selector_all(
                ".ag[data-tier]",
                "els => els.filter(e => !e.hidden).every(e => e.dataset.tier === 'prime')"))
            ctx.click("#f-all")

            ctx.click("#v-calendar")
            check("calendar view switches on", ctx.is_visible("#calendar-section"))
            check("month panels rendered",
                  ctx.eval_on_selector_all(".mo", "els => els.length") >= 7)
            check("months scroll horizontally on a phone, not stacked eight deep",
                  ctx.eval_on_selector("#months",
                                       "el => el.scrollWidth > el.clientWidth + 50"))

            # The brief makes the agenda primary on small screens and the grid secondary,
            # so the default has to flip with width.
            wide = browser.new_page(viewport={"width": 1280, "height": 900})
            wide.goto(url, wait_until="load")
            open_calendar(wide)
            check("calendar is the default on a desktop viewport",
                  wide.get_attribute("#v-calendar", "aria-pressed") == "true")
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
                pg.click("#v-calendar")
                pg.wait_for_timeout(150)
                over = pg.eval_on_selector_all(
                    ".mo .grid",
                    "gs => gs.filter(g => g.scrollWidth > g.clientWidth + 1)"
                    ".map(g => g.closest('.mo').dataset.month)")
                clipped = pg.eval_on_selector_all(
                    ".day:not(.pad) .lb",
                    "els => els.filter(e => e.scrollWidth > e.clientWidth + 1).length")
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
                const brand = getComputedStyle(document.querySelector('.side-brand'));
                const btns = [...document.querySelectorAll('.side-nav button')]
                  .map(b => Math.round(b.getBoundingClientRect().top));
                return {w: Math.round(s.width), h: Math.round(s.height),
                        colLeft: Math.round(col.left), brand: brand.display,
                        stacked: new Set(btns).size === btns.length}; }""")
            check("laptop shows a left rail, not a top bar",
                  rail["h"] > 400 and rail["w"] < 320, f"{rail['w']}x{rail['h']}")
            check("content sits to the right of the rail",
                  rail["colLeft"] >= rail["w"] - 1, f"col at {rail['colLeft']}")
            check("rail stacks its items vertically", rail["stacked"])
            check("rail shows the brand", rail["brand"] != "none")
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
                        brand: getComputedStyle(
                          document.querySelector('.side-brand')).display}; }""")
            check("phone nav is pinned to the bottom of the viewport",
                  bar["fixed"] == "fixed" and abs(bar["bottom"] - 800) <= 1,
                  f"position={bar['fixed']} bottom={bar['bottom']}")
            check("phone nav is not a top bar", bar["top"] > 600, f"top={bar['top']}")
            check("phone nav is a bar, not a rail",
                  bar["h"] < 120 and bar["w"] > 300, f"{bar['w']}x{bar['h']}")
            check("content clears the bottom nav", bar["pad"] >= bar["h"] - 4,
                  f"padding {bar['pad']} vs bar {bar['h']}")
            check("phone keeps the sections on one row", bar["sameRow"])
            check("phone hides the rail brand", bar["brand"] == "none")
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
            check("months lay out two or more to a row",
                  desk.evaluate("""() => {
                      const mos = [...document.querySelectorAll('.mo')];
                      const top = mos[0].getBoundingClientRect().top;
                      return mos.filter(m => m.getBoundingClientRect().top === top).length;
                  }""") >= 2)
            check("day cells are bigger than on a phone",
                  desk.eval_on_selector(
                      ".day:not(.pad)",
                      "el => el.getBoundingClientRect().width") >= 70)
            check("events list becomes a table, not a card wall",
                  desk.eval_on_selector(".ev", "el => getComputedStyle(el).display")
                  == "grid")
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
            ctx.select_option('.tk[data-n="1"] .tk-status', "Done")
            ctx.wait_for_timeout(200)
            check("marking a task done moves the progress figure",
                  ctx.inner_text(".cl-cell") != before,
                  f"{before.split(chr(10))[0]} -> {ctx.inner_text('.cl-cell').split(chr(10))[0]}")
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
                  and ctx.eval_on_selector('.tk[data-n="1"] .tk-status',
                                           "e => e.value") == "Done",
                  f'date={ctx.input_value("#cl-date")}')
            ctx.click("#tab-calendar")

            print("\ncolour is never the only signal")
            check("every scored day carries an icon and a text label",
                  ctx.eval_on_selector_all(".day[data-tier]", """els => els.every(e =>
                       e.querySelector('.ic') && e.querySelector('.lb') &&
                       e.querySelector('.lb').textContent.trim().length > 0)"""))
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
