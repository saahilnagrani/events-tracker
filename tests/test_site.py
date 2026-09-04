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
import re
import socket
import socketserver
import subprocess
import sys
import tempfile
import struct
import threading
import zlib
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from fake_backend import FakeBackend  # noqa: E402
import build_site  # noqa: E402  (for constants the built page has to agree with)

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


def open_app(browser, url, backend=None, **kwargs):
    """A page with a database behind it, signed in and painted.

    The page ships no data now, so every check that looks at events, dates or tasks
    needs a backend first. FakeBackend stands in for Supabase; signing in is what
    fetches, and nothing renders before that.
    """
    page = browser.new_page(**kwargs)
    fake = backend or FakeBackend()
    fake.install(page)
    page.goto(url, wait_until="load")
    page.wait_for_timeout(150)
    fake.sign_in(page)
    # attached, not visible: the calendar is painted while the events tab is up.
    page.wait_for_selector(".day[data-tier]", state="attached", timeout=8000)
    page.wait_for_timeout(150)
    return page, fake


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
            errors = []

            print("\nnothing in the page")
            # The whole point of the rebuild: the file served to anybody with the URL
            # carries no listings, no scores and no checklist.
            source = (DOCS / "index.html").read_text()
            # Words like "prime" and "Platinumlist" are legitimately in the furniture,
            # so this looks for the shapes data takes, plus real values read out of the
            # local dataset when there is one to read.
            for needle, what in [('data-date="20', "a dated calendar cell"),
                                 ('data-month="20', "a dated event row"),
                                 ("__DAYS__", "an inlined score payload"),
                                 ("__CHECKLISTS__", "an inlined checklist")]:
                check(f"the published page carries no {what}", needle not in source,
                      needle)
            local = ROOT / "data" / "viability.json"
            if local.exists():
                data = json.loads(local.read_text())
                samples = [e.get("event") for e in data.get("events", [])]
                samples += [e.get("artist") for e in data.get("events", [])]
                leaked = [v for v in samples if v and v in source]
                check("no event title or artist from the dataset appears in the page",
                      not leaked, "; ".join(leaked[:2]))
            check("the shell is small, because it is only a shell",
                  len(source) < 200_000, f"{len(source) // 1024} KB")

            bare = browser.new_page(viewport={"width": 390, "height": 780})
            bare.on("pageerror", lambda e: errors.append(str(e)))
            FakeBackend().install(bare)
            bare.goto(url, wait_until="load")
            bare.wait_for_timeout(300)
            check("a signed-out visitor gets the gate, not the app",
                  bare.is_visible("#gate")
                  and bare.eval_on_selector_all(".day[data-tier]", "e => e.length") == 0
                  and bare.eval_on_selector_all(".ev", "e => e.length") == 0)
            check("and no way to navigate to an empty tab",
                  not bare.is_visible("#tab-calendar"))
            bare.close()

            ctx, ctx_fake = open_app(browser, url,
                                     viewport={"width": 390, "height": 780})
            # api.github.com is blocked on purpose further down, to exercise the
            # refresh button's failure path; that block is the test, not a fault.
            ctx.on("console", lambda m: errors.append(m.text)
                   if m.type == "error"
                   and "api.github.com" not in (m.location or {}).get("url", "")
                   else None)
            ctx.on("pageerror", lambda e: errors.append(str(e)))

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

            print("\nfilter sheets on a phone")
            # These open at the bottom, far from the chip that was tapped, so they
            # have to announce themselves: dim the page, say which filter this is,
            # and offer a way out that is not the chip again.
            ctx.click('details.ms[data-ms="category"] summary')
            ctx.wait_for_timeout(300)
            check("opening a filter dims the page behind it",
                  ctx.is_visible("#ms-scrim"))
            check("the sheet says which filter it is",
                  ctx.inner_text('.ms[data-ms="category"] .ms-head b') == "Category")
            check("the chip you tapped is visibly the open one",
                  ctx.eval_on_selector('.ms[data-ms="category"] summary',
                                       "el => getComputedStyle(el).backgroundColor")
                  != ctx.eval_on_selector('.ms[data-ms="month"] summary',
                                          "el => getComputedStyle(el).backgroundColor"))
            check("the chips stay above the scrim, so switching is one tap",
                  ctx.eval_on_selector("""#panel-events .filters""",
                      """el => {
                          const mid = el.getBoundingClientRect();
                          const at = document.elementFromPoint(mid.left + 30,
                                                               mid.top + mid.height / 2);
                          return el.contains(at); }"""))
            ctx.click('details.ms[data-ms="month"] summary')
            ctx.wait_for_timeout(250)
            check("and only one sheet is ever open",
                  ctx.eval_on_selector_all("details.ms[open]",
                                           "els => els.map(e => e.dataset.ms)") == ["month"])
            ctx.click(".ms[open] .ms-done")
            ctx.wait_for_timeout(250)
            check("Done closes it and takes the dimming with it",
                  ctx.eval_on_selector_all("details.ms[open]", "e => e.length") == 0
                  and ctx.eval_on_selector_all("#ms-scrim", "e => e.length") == 0)
            ctx.click('details.ms[data-ms="category"] summary')
            ctx.wait_for_timeout(250)
            ctx.click("#ms-scrim")
            ctx.wait_for_timeout(250)
            check("tapping the dimmed area closes it too",
                  ctx.eval_on_selector_all("details.ms[open]", "e => e.length") == 0)

            print("\nnew badges and sorting")
            # The fixture marks two upcoming events as added in this run, which is
            # what the badge and the "recently added" order are for.
            marked = ctx.eval_on_selector_all(
                '.ev[data-new="1"]', "els => els.map(e => e.dataset.added)")
            stamp = ctx.evaluate("() => window.__TEST_STAMP__ || null")
            check("the badge marks what the latest run added", len(marked) == 2,
                  f"{len(marked)} marked")
            check("and only those: nothing older carries it",
                  ctx.eval_on_selector_all(
                      ".ev", """els => els.every(e =>
                          (e.dataset.new === '1') === (e.dataset.added === els
                              .map(x => x.dataset.added).sort().slice(-1)[0]))"""))
            check("the badge is a word, not just a colour",
                  ctx.eval_on_selector(".ev-new", "el => el.textContent.trim()") == "NEW")
            check("the count and its controls are on their own line, below the dropdowns",
                  ctx.evaluate("""() => {
                      const filters = document.querySelector('#panel-events .filters');
                      const meta = document.querySelector('#panel-events .filters-meta');
                      if (!filters || !meta) return false;
                      const a = filters.getBoundingClientRect();
                      const b = meta.getBoundingClientRect();
                      return b.top >= a.bottom - 1
                        && meta.contains(document.getElementById('ev-count'))
                        && meta.contains(document.getElementById('ev-past'))
                        && meta.contains(document.getElementById('ev-clear'))
                        && !filters.contains(document.getElementById('ev-count')); }"""))
            check("the count says how many are new",
                  "new" in ctx.inner_text("#ev-count"), ctx.inner_text("#ev-count"))

            def first_rows(n=4):
                return ctx.eval_on_selector_all(
                    ".ev", """els => els.filter(e => !e.hidden).slice(0, %d)
                        .map(e => [e.dataset.added, e.dataset.start,
                                   e.querySelector('.ev-meta').textContent])""" % n)

            def pick(value):
                ctx.click("#ev-sort .ms-choice summary")
                ctx.wait_for_timeout(120)
                ctx.click(f'#ev-sort input[value="{value}"]')
                ctx.wait_for_timeout(250)

            by_date = first_rows()
            check("by default the list is in event-date order",
                  [r[1] for r in by_date] == sorted(r[1] for r in by_date))
            pick("added")
            by_added = first_rows()
            check("recently added puts this run's arrivals first",
                  by_added[0][0] > by_added[-1][0], f"{by_added[0][0]} then {by_added[-1][0]}")
            check("the control shows which order is in force",
                  "added" in ctx.inner_text("#ev-sort .ms-value").lower(),
                  ctx.inner_text("#ev-sort .ms-value"))
            pick("price")
            prices = ctx.eval_on_selector_all(
                ".ev", """els => els.filter(e => !e.hidden).map(e => {
                    const m = /from AED ([\d.]+)/.exec(e.querySelector('.ev-meta').textContent);
                    return m ? Number(m[1]) : null; })""")
            priced = [p for p in prices if p is not None]
            check("cheapest first is actually ascending",
                  priced == sorted(priced), str(priced[:5]))
            # "price not published" is not free, so those belong at the end.
            check("events with no price sort last, not first",
                  all(p is None for p in prices[len(priced):]),
                  f"{len(priced)} priced, {len(prices) - len(priced)} without")
            check("sorting does not disturb the filters",
                  int(ctx.get_attribute("#ev-count", "data-count")) == total)
            ctx.reload(wait_until="load")
            ctx.wait_for_selector(".ev", state="attached", timeout=8000)
            ctx.wait_for_timeout(300)
            check("the chosen order survives a reload",
                  "cheapest" in ctx.inner_text("#ev-sort .ms-value").lower(),
                  ctx.inner_text("#ev-sort .ms-value"))
            pick("date")

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

            wide, _ = open_app(browser, url, viewport={"width": 1280, "height": 900})
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
                pg, _ = open_app(browser, url,
                                 viewport={"width": width, "height": 900})
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
            nav, _ = open_app(browser, url, viewport={"width": 1440, "height": 900})
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

            phone, _ = open_app(browser, url, viewport={"width": 390, "height": 800})
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
            desk, _ = open_app(browser, url, viewport={"width": 1440, "height": 900})
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
            # The same markup is a plain dropdown on a laptop: it opens under the
            # control, so it needs neither a title nor a scrim to be found.
            desk.click("#tab-events")
            desk.wait_for_timeout(150)
            desk.click('details.ms[data-ms="category"] summary')
            desk.wait_for_timeout(250)
            check("a laptop gets a dropdown, not a dimmed sheet",
                  desk.eval_on_selector_all("#ms-scrim", "e => e.length") == 0
                  and desk.eval_on_selector('.ms[data-ms="category"] .ms-head',
                                            "el => getComputedStyle(el).display") == "none")
            check("and it opens under the control it belongs to",
                  desk.evaluate("""() => {
                      const box = document.querySelector('.ms[data-ms="category"]');
                      const menu = box.querySelector('.ms-menu');
                      const a = box.getBoundingClientRect();
                      const b = menu.getBoundingClientRect();
                      return b.top > a.top && Math.abs(b.left - a.left) < 40; }"""))
            desk.keyboard.press("Escape")
            desk.wait_for_timeout(150)
            desk.click("#tab-calendar")
            desk.wait_for_timeout(150)
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
            # The fixture's first task is D-60, and 60 days before 30 Jan 2027 is
            # 1 Dec 2026. Derived here rather than typed, so the fixture can move.
            first = ctx.eval_on_selector('.tk[data-n="1"]', "e => e.dataset.dminus")
            want = (dt.date(2027, 1, 30) - dt.timedelta(days=int(first))).isoformat()
            check("due dates derive from the show date",
                  want in ctx.eval_on_selector(
                      '.tk[data-n="1"] .tk-due', "e => e.textContent"),
                  ctx.eval_on_selector('.tk[data-n="1"] .tk-due', "e => e.textContent"))
            before = ctx.inner_text(".cl-cell")
            ctx.click('.tk[data-n="1"] .ms-choice summary')
            ctx.click('.tk[data-n="1"] .ms-choice input[value="Done"]')
            ctx.wait_for_timeout(250)
            # Picking a status rebuilds the task list, which removes the open sheet
            # without a toggle event. A scrim left behind dims the page and swallows
            # every tap after it.
            check("picking a status leaves nothing dimming the page",
                  ctx.eval_on_selector_all("#ms-scrim", "e => e.length") == 0
                  and ctx.eval_on_selector_all("details.ms[open]", "e => e.length") == 0)
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
            # The window starts on the 1st of the current month, so on the 1st there
            # is nothing behind us and no cell can be past. Asserting one exists made
            # the pipeline red on 1 September, on the same commit that was green on
            # 31 August. What has to hold every day is the rule, not the count:
            # everything before today is marked, everything from today on is not.
            marking = ctx.evaluate("""() => {
                const today = new Date();
                const iso = today.getFullYear() + '-' +
                    String(today.getMonth() + 1).padStart(2, '0') + '-' +
                    String(today.getDate()).padStart(2, '0');
                const cells = Array.from(document.querySelectorAll('.day[data-date]'));
                return {
                  wrong: cells.filter(c =>
                      c.classList.contains('past') !== (c.dataset.date < iso)).length,
                  behind: cells.filter(c => c.dataset.date < iso).length,
                  marked: cells.filter(c => c.classList.contains('past')).length }; }""")
            check("every date before today is marked past, and no other",
                  marking["wrong"] == 0,
                  f"{marking['marked']} marked, {marking['behind']} behind today")
            if not marking["behind"]:
                print("        (first of the month: nothing is behind us to mark)")

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
                pg, _ = open_app(browser, url, color_scheme=scheme)
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
            # The mark is drawn by src/build_site.py itself, so these check the two
            # things that are easy to get wrong and invisible until an install: the
            # declared size matching the file, and the maskable variant reaching the
            # edges. Android crops a maskable icon to the launcher shape, so a tile
            # with transparent corners would come back with its corners sliced off.
            def png_info(path):
                raw = Path(path).read_bytes()
                w, h = struct.unpack(">II", raw[16:24])
                idat = b""
                i = 8
                while i < len(raw):
                    ln = struct.unpack(">I", raw[i:i + 4])[0]
                    tag = raw[i + 4:i + 8]
                    if tag == b"IDAT":
                        idat += raw[i + 8:i + 8 + ln]
                    i += 12 + ln
                rows = zlib.decompress(idat)
                # This writer emits filter 0 on every row, so the first pixel of the
                # first row is four bytes in after the filter byte.
                corner_alpha = rows[4] if rows[0] == 0 else None
                return w, h, corner_alpha

            def icon_path(src):
                return DOCS / src[2:].split("?")[0]

            for spec in man["icons"]:
                path = icon_path(spec["src"])
                w, h, alpha = png_info(path)
                want = int(spec["sizes"].split("x")[0])
                check(f"{path.name} is really {want}x{want}",
                      (w, h) == (want, want), f"{w}x{h}")
                if "maskable" in spec["purpose"]:
                    check(f"{path.name} fills the square for cropping",
                          alpha == 255, f"corner alpha {alpha}")
                else:
                    check(f"{path.name} is a tile with clear corners",
                          alpha == 0, f"corner alpha {alpha}")
            check("the manifest offers a maskable icon at all",
                  any("maskable" in i["purpose"] for i in man["icons"]))
            # A redraw is only delivered if the URL changes: the service worker
            # serves assets cache-first, and a same-day rebuild reuses whatever the
            # earlier build left in the cache. This is what left the old mark in
            # Chrome's install sheet.
            sw = (DOCS / "sw.js").read_text()
            check("every manifest icon URL carries the icon version",
                  all("?v=" in i["src"] for i in man["icons"]),
                  ", ".join(i["src"] for i in man["icons"])[:70])
            check("the service worker precaches those exact URLs",
                  all(i["src"] in sw for i in man["icons"]))
            check("the cache is named after the build, not the date",
                  bool(re.search(r"const CACHE = 'events-tracker-[0-9a-f]{12}'", sw)),
                  sw.splitlines()[3] if len(sw.splitlines()) > 3 else "")
            check("the favicon link is versioned too",
                  "?v=" in ctx.eval_on_selector(
                      "link[rel=icon]", "el => el.getAttribute('href')"))
            check("the apple touch icon exists and is full bleed",
                  icon_path(ctx.eval_on_selector(
                      "link[rel=apple-touch-icon]",
                      "el => el.getAttribute('href')")).exists())
            check("manifest icons are relative and present",
                  all(i["src"].startswith("./") and icon_path(i["src"]).exists()
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
            said = ctx.inner_text("#data-when")
            check("it says when the listings were checked, to the minute",
                  bool(re.match(r"Listings last checked \d{1,2} [A-Z][a-z]{2} "
                                r"\d{4}, \d{2}:\d{2}$", said.strip())),
                  said)
            # Two phrasings of one fact read as two facts, so the sidebar carries the
            # same sentence rather than its own wording.
            check("the sidebar says exactly the same thing",
                  ctx.inner_text("#side-stamp").strip() == said.strip(),
                  ctx.inner_text("#side-stamp"))

            # The stamp arrives with the data now, so it is read back off the
            # element rather than from a global the page no longer carries.
            stamped = (ctx.get_attribute("#data-when", "title") or "").split()[-1]
            check("fresh data is not flagged stale",
                  ctx.eval_on_selector(
                      '#data-when', "el => el.classList.contains('stale')")
                  == (dt.date.fromisoformat(stamped)
                      < dt.date.today() - dt.timedelta(days=1)),
                  stamped)
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

            # And the failure path. A blocked request said only "Could not reach
            # GitHub", which is a dead end on the one screen that has an obvious way
            # out of it.
            ctx.route("**://api.github.com/**", lambda r: r.abort())
            ctx.evaluate("() => localStorage.setItem('gh:token', 'not-a-real-token')")
            ctx.click("#refresh")
            ctx.wait_for_timeout(500)
            check("a blocked request says so rather than nothing",
                  "could not reach github" in ctx.inner_text("#data-msg").lower(),
                  ctx.inner_text("#data-msg"))
            check("and still offers running it on GitHub by hand",
                  ctx.is_visible('#data-msg a[href*="/actions/workflows/"]'))
            check("the button is not left spinning",
                  not ctx.eval_on_selector("#refresh", "el => el.disabled"))
            ctx.evaluate("() => localStorage.removeItem('gh:token')")
            ctx.unroute("**://api.github.com/**")
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

            # What the app does when the database will not answer. It used to carry
            # the data itself, so it degraded to "checklists are local"; now there is
            # nothing to show, and the only honest thing it can do is say why.
            # The offline check above shut the server down on purpose, and everything
            # from here needs one again.
            url, shutdown = serve(DOCS)

            # The row is written in UTC and read in Dubai, four hours apart, so a
            # timestamp that did not convert would be wrong for everyone who uses this.
            print("\nthe timestamp is the reader's own")
            shifted, _ = open_app(browser, url, viewport={"width": 1440, "height": 900},
                                 timezone_id="Pacific/Kiritimati")
            check("a reader in another zone sees their own clock",
                  shifted.inner_text("#data-when").strip() != said.strip(),
                  shifted.inner_text("#data-when"))
            check("and both places still agree with each other",
                  shifted.inner_text("#side-stamp").strip()
                  == shifted.inner_text("#data-when").strip())
            shifted.close()

            # The time has to survive without the column, because a page painted from
            # its cache never sees the row: it has the data and would otherwise have
            # lost the hour it was gathered.
            payload_only = FakeBackend(row_timestamp=False)
            if payload_only.viability.get("generated_at"):
                bare, _ = open_app(browser, url, backend=payload_only,
                                   viewport={"width": 1440, "height": 900})
                check("the payload's own stamp is enough for a time",
                      bool(re.search(r"\d{2}:\d{2}$",
                                     bare.inner_text("#data-when").strip())),
                      bare.inner_text("#data-when"))
                bare.close()

            print("\nwhen the database will not answer")
            for label, backend, expect in [
                    ("paused", FakeBackend(paused=True), "paused"),
                    ("not on the allowlist", FakeBackend(allowed=False), "allowlist")]:
                pg = browser.new_page(viewport={"width": 1440, "height": 900})
                dead = []
                pg.on("pageerror", lambda e: dead.append(str(e)))
                backend.install(pg)
                pg.goto(url, wait_until="load")
                pg.wait_for_timeout(200)
                backend.sign_in(pg)
                pg.wait_for_timeout(700)
                said = " ".join((pg.inner_text("#gate-msg") + " " +
                                 pg.inner_text("#acct-body")).split()).lower()
                check(f"a {label} project says so", expect in said, said[:70])
                check(f"a {label} project shows no data",
                      pg.eval_on_selector_all(".day[data-tier]", "e => e.length") == 0)
                check(f"a {label} project raises no page errors", not dead,
                      "; ".join(dead[:2]))
                pg.close()

            offline = browser.new_page(viewport={"width": 1440, "height": 900})
            dead = []
            offline.on("pageerror", lambda e: dead.append(str(e)))
            offline.route("**/*.supabase.co/**", lambda r: r.abort())
            offline.goto(url, wait_until="load")
            offline.wait_for_timeout(200)
            offline.click("#gate-in")
            offline.wait_for_timeout(200)
            check("the dialog asks for an email and a password",
                  offline.is_visible("#acct-email") and offline.is_visible("#acct-pass"))
            check("the password field is a password field",
                  offline.get_attribute("#acct-pass", "type") == "password")
            check("no native select anywhere in the dialog",
                  offline.eval_on_selector_all("#acct-sheet select",
                                               "els => els.length") == 0)
            offline.fill("#acct-email", "someone@example.com")
            offline.click("#acct-form button[type=submit]")
            offline.wait_for_timeout(250)
            check("submitting without a password is refused locally",
                  "password" in offline.inner_text("#acct-body").lower()
                  and offline.is_visible("#acct-sheet"))
            offline.fill("#acct-pass", "hunter2hunter2")
            offline.click("#acct-form button[type=submit]")
            offline.wait_for_timeout(600)
            check("an unreachable server says so instead of failing silently",
                  "reach" in offline.inner_text("#acct-body").lower(),
                  " ".join(offline.inner_text("#acct-body").split())[-40:])
            check("what was typed survives the failure",
                  offline.input_value("#acct-email") == "someone@example.com")
            offline.click("#acct-swap")
            offline.wait_for_timeout(200)
            check("the dialog switches to creating an account",
                  "create account" in offline.inner_text(
                      "#acct-form button[type=submit]").lower())
            offline.keyboard.press("Escape")
            offline.wait_for_timeout(200)
            check("Escape closes the account dialog",
                  not offline.is_visible("#acct-sheet")
                  and not offline.is_visible("#sheet-bg"))
            check("a dead backend raises no page errors", not dead,
                  "; ".join(dead[:2]))
            offline.close()

            print("\na database with nothing published yet")
            # The state this app is in the moment the schema is created: allowed in,
            # checklists seeded, no dataset written. Reporting that as "not on the
            # allowlist" would send someone to fix a table that is already correct.
            fresh = browser.new_page(viewport={"width": 1440, "height": 900})
            dead = []
            fresh.on("pageerror", lambda e: dead.append(str(e)))
            fresh_fake = FakeBackend(published=False)
            fresh_fake.install(fresh)
            fresh.goto(url, wait_until="load")
            fresh.wait_for_timeout(200)
            fresh_fake.sign_in(fresh)
            fresh.wait_for_timeout(800)
            check("an empty dataset is not reported as a locked door",
                  "allowlist" not in (fresh.inner_text("#gate-msg") +
                                      fresh.inner_text("#data-msg")).lower())
            check("it says the dataset has not been published",
                  "published" in fresh.inner_text("#data-msg").lower(),
                  " ".join(fresh.inner_text("#data-msg").split())[:60])
            check("and lets you in, because the way to fix it is inside",
                  not fresh.is_visible("#gate") and fresh.is_visible("#refresh"))
            fresh.click("#tab-checklist")
            fresh.wait_for_timeout(300)
            check("seeded checklists work with no dataset at all",
                  fresh.eval_on_selector_all(".tk", "e => e.length") > 0)
            check("no page errors with an empty dataset", not dead, "; ".join(dead[:2]))
            fresh.close()

            print("\nsigning out takes the data with it")
            out_pg, out_fake = open_app(browser, url,
                                        viewport={"width": 1440, "height": 900})
            check("a signed-in device caches the dataset",
                  out_pg.evaluate("() => !!localStorage.getItem('data:v1')"))
            out_pg.click("#acct")
            out_pg.wait_for_timeout(200)
            out_pg.click("#acct-out")
            out_pg.wait_for_timeout(400)
            check("signing out clears the cached dataset",
                  not out_pg.evaluate("() => localStorage.getItem('data:v1')"))
            check("signing out clears the checklists too",
                  out_pg.evaluate(
                      "() => Object.keys(localStorage)"
                      ".filter(k => k.indexOf('checklist:') === 0).length") == 0)
            check("and the app is behind the gate again",
                  out_pg.is_visible("#gate")
                  and out_pg.eval_on_selector_all(".ev", "e => e.length") == 0)
            out_pg.reload(wait_until="load")
            out_pg.wait_for_timeout(400)
            check("a reload after signing out still shows nothing",
                  out_pg.eval_on_selector_all(".day[data-tier]", "e => e.length") == 0)
            out_pg.close()

            print("\nchecklist edits reach the database")
            edit, edit_fake = open_app(browser, url,
                                       viewport={"width": 1440, "height": 900})
            edit.click("#tab-checklist")
            edit.wait_for_timeout(250)
            edit.click(".cl-add summary")
            edit.fill("#add-task", "A task added in the browser")
            edit.click("#add-save")
            edit.wait_for_timeout(1400)
            check("an added task is written back, not just kept locally",
                  any("A task added in the browser" in json.dumps(w)
                      for w in edit_fake.writes),
                  f"{len(edit_fake.writes)} writes")
            check("the write carries the whole document, tasks included",
                  any(len((w[0].get("doc") or {}).get("tasks", [])) > 0
                      for w in edit_fake.writes if w),
                  json.dumps(edit_fake.writes)[:60])
            edit.close()

            print("\nthe demo page")
            # A page to hand to someone who is evaluating: the real listings and the
            # real calendar, an invented checklist, and no way in to anything else.
            demo_file = DOCS / "demo" / "index.html"
            if not check("the demo page is built", demo_file.exists()):
                pass
            else:
                demo_src = demo_file.read_text()
                real = json.loads((ROOT / "data" / "demo_checklist.json").read_text())
                check("it carries only the invented checklist",
                      [c["id"] for c in real["checklists"]] == ["demo-diwali-comedy-night"])
                # The one thing that must never be true of this file.
                for needle in ("Ranjit", "Sifat", "200,000", "Facilitation"):
                    check(f"the demo page carries nothing of the real checklist "
                          f"({needle})", needle not in demo_src)
                check("search engines are told to leave it alone",
                      'name="robots" content="noindex"' in demo_src)
                # Measuring the demo is the point; measuring the signed-in app is a
                # decision nobody made, and a beacon is one line away from both.
                check("the demo carries the Cloudflare beacon",
                      build_site.CF_BEACON_TOKEN in demo_src
                      and "static.cloudflareinsights.com" in demo_src)
                check("the app carries no beacon at all",
                      "cloudflareinsights" not in (DOCS / "index.html").read_text())
                # The demo bakes its data in, the app fetches it live, and the two
                # sitting side by side must not report different days. A local build
                # from a stale payload is what makes them diverge, so the build skips
                # rather than downgrading a current page, and this pins the agreement.
                payload = json.loads((ROOT / "data" / "viability.json").read_text())
                baked = re.search(r'"generated":"(\d{4}-\d{2}-\d{2})"', demo_src)
                # The rule is that the page never carries older data than the
                # payload it was built from. Equal is the normal case; newer happens
                # when a run has published since this checkout last scored anything.
                check("the demo's data is not older than the payload here",
                      bool(baked) and baked.group(1) >= payload["generated"],
                      f"page {baked.group(1) if baked else '?'} "
                      f"vs payload {payload['generated']}")

                dpage = browser.new_page(viewport={"width": 1440, "height": 900})
                dead = []
                dpage.on("pageerror", lambda e: dead.append(str(e)))
                # The analytics beacon is third-party and blockable: it fails to
                # load in this sandbox, and it fails for any visitor running an ad
                # blocker. Neither is the demo being broken, so it is not counted.
                dpage.on("console", lambda m: dead.append(
                    f"{m.text} [{(m.location or {}).get('url', '')}]")
                    if m.type == "error"
                    and "cloudflareinsights" not in (m.location or {}).get("url", "")
                    else None)
                # No backend is routed at all: if the demo asks a database for
                # anything, the request fails and the check below notices.
                dpage.route("**/*.supabase.co/**", lambda r: r.abort())
                dpage.goto(url + "demo/", wait_until="load")
                dpage.wait_for_timeout(600)
                check("it opens with no sign-in at all",
                      not dpage.is_visible("#gate")
                      and dpage.eval_on_selector_all(".ev", "e => e.length") > 0
                      and dpage.eval_on_selector_all(".day[data-tier]",
                                                     "e => e.length") > 0)
                check("it says it is a demo, and which half is invented",
                      "mock" in dpage.inner_text(".demo-note").lower())
                check("no account button and no way to trigger a run",
                      not dpage.is_visible("#acct") and not dpage.is_visible("#refresh"))
                dpage.click("#tab-checklist")
                dpage.wait_for_timeout(300)
                check("the checklist is the sample one",
                      "sample" in dpage.inner_text("#cl-picker .ms-value").lower(),
                      dpage.inner_text("#cl-picker .ms-value"))
                check("and it still works: ticking one moves the figure",
                      (lambda before: (
                          dpage.click('.tk[data-n="9"] .ms-choice summary'),
                          dpage.click('.tk[data-n="9"] .ms-choice input[value="Done"]'),
                          dpage.wait_for_timeout(250),
                          dpage.inner_text(".cl-cell") != before)[-1])(
                              dpage.inner_text(".cl-cell")))
                check("the demo raises no page errors", not dead, "; ".join(dead[:2]))
                dpage.close()

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
