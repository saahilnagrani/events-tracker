"""
Viability model: scores every date for staging an Indian (English/Hindi) stand-up show in the UAE.

Input:  data/events.json   (list of events, schema in BRIEF.md)
        data/config.json   (weights, blackout windows, holidays)
Output: data/viability.json  {"generated": iso, "days": [...], "events": [...]}

This file is already tested against the Aug 2026 dataset and produced 28 prime dates,
18 direct-clash nights and 48 blocked dates. Treat its scoring as the spec; if you change
a weight, expect those counts to move.

Run:  python src/viability.py
"""
import json, sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def load():
    events = json.loads((ROOT / "data" / "events.json").read_text())
    cfg = json.loads((ROOT / "data" / "config.json").read_text())
    artists = json.loads((ROOT / "data" / "artists.json").read_text())
    return events, cfg, artists


def parse(s):
    return date.fromisoformat(s) if s else None


def classify(ev, artists):
    """The kind of event this is, independent of which show you are staging.

    standup      = Indian/desi stand-up, matched against artists.json
    music        = a desi draw that is not stand-up: concerts, ghazal, qawwali, bhajan
    comedy_other = any other comedy

    These are the same three buckets the original model used under the names direct,
    concert and other; only the names changed, so scoring is unaffected.
    """
    hay = f"{ev.get('event','')} {ev.get('artist','')}".lower()
    names = artists["indian_standup_artists"] + artists["series_names"]
    if any(n.lower() in hay for n in names):
        return "standup"
    if ev.get("category") in ("Desi", "Comedy + Desi"):
        return "music"
    if ev.get("category") == "Comedy":
        return "comedy_other"
    return None


def lens_config(cfg, lens):
    """Which event kinds block a date, compete with it, or merely dilute it."""
    default = {"label": "Indian stand-up", "blocks_on": ["standup"],
               "competing": ["music"], "minor": ["comedy_other"],
               "festival_penalty": True}
    return (cfg.get("lenses") or {}).get(lens, default)


def window(cfg):
    """Rolling window when range_months is set, else the fixed tuned range.

    The window starts on the first of the current month, not today, so the current
    month renders whole. Starting mid-month left the calendar's first panel with a
    row of blanks where the first two weeks should be.
    """
    months = cfg.get("range_months")
    if not months:
        return parse(cfg["range"][0]), parse(cfg["range"][1])
    start = date.today().replace(day=1)
    month = start.month - 1 + int(months)
    end = date(start.year + month // 12, month % 12 + 1, 1) - timedelta(days=1)
    return start, end


def expand(ev):
    """Yield every calendar date an event occupies."""
    s, e = parse(ev.get("start")), parse(ev.get("end"))
    if not s:
        return
    d = s
    while d <= (e or s):
        yield d
        d += timedelta(days=1)


def build(events, cfg, artists, lens=None):
    """Score every date in the window for one lens.

    lens defaults to config's default_lens, which reproduces the original tuned model,
    so existing callers (src/changes.py) keep the behaviour they were written against.
    """
    lens = lens or cfg.get("default_lens", "standup")
    w = cfg["weights"]
    base = {int(k): v for k, v in cfg["base_by_weekday"].items()}
    fest_s, fest_e = parse(cfg["festival_window"][0]), parse(cfg["festival_window"][1])
    ram_s, ram_e = parse(cfg["ramadan"][0]), parse(cfg["ramadan"][1])
    eid_s, eid_e = parse(cfg["eid_window"][0]), parse(cfg["eid_window"][1])
    holidays = {parse(k): v for k, v in cfg["holidays"].items()}
    peaks = [(parse(a), parse(b), v) for a, b, v in cfg["peak_windows"]]
    lows = [(parse(a), parse(b), v) for a, b, v in cfg["low_windows"]]

    spec = lens_config(cfg, lens)
    blocks_on, competing = set(spec["blocks_on"]), set(spec["competing"])
    minor = set(spec["minor"])

    by_day = {}
    for ev in events:
        kind = classify(ev, artists)
        if not kind:
            continue
        for d in expand(ev):
            by_day.setdefault(d, []).append((ev, kind))

    days = []
    d, end = window(cfg)
    while d <= end:
        todays = by_day.get(d, [])
        # `direct`, `concert` and `other` keep their original names in the output so
        # docs/viability.json and src/changes.py read the same as before; what lands in
        # each bucket is now decided by the lens.
        direct = sorted({e["event"] for e, k in todays if k in blocks_on})
        concert = sorted({e["event"] for e, k in todays if k in competing})
        other = sorted({e["event"] for e, k in todays if k in minor})
        score, reasons, boosts = base[d.weekday()], [], []

        if ram_s <= d <= ram_e:
            tier, score = "blocked", 0.0
            reasons.append(f"Ramadan (expected {ram_s:%-d %b} to {ram_e:%-d %b %Y}): ticketed evening comedy effectively pauses")
        elif direct:
            tier, score = "blocked", 0.0
            reasons.append("Direct clash: " + "; ".join(direct))
        else:
            near = sorted({e["event"] for dd in (d - timedelta(1), d + timedelta(1))
                           for e, k in by_day.get(dd, []) if k in blocks_on})
            if near:
                score += w["adjacent_direct"]
                reasons.append("Competing act the night before or after: " + "; ".join(near))
            if concert:
                score += w["desi_concert"]
                reasons.append("Major desi draw competing for the same wallet: " + "; ".join(concert))
            if other:
                score += w["other_comedy"]
                reasons.append("Other comedy on the same night: " + "; ".join(other))
            if spec.get("festival_penalty", True) and fest_s <= d <= fest_e:
                score += w["festival_window"]
                reasons.append(f"Inside Dubai Comedy Festival ({fest_s:%-d}-{fest_e:%-d %b}): market saturated, venues booked")
            for a, b, v in lows:
                if a <= d <= b:
                    score += v["delta"]
                    reasons.append(v["label"])
            if eid_s <= d <= eid_e:
                score += w["eid_window"]
                boosts.append("Eid Al Fitr holiday window: high appetite for family outings")
            if d in holidays:
                score += w["public_holiday"]
                boosts.append(holidays[d] + " public holiday")
            if d.weekday() == 6 and (d + timedelta(1)) in holidays:
                score += w["public_holiday"]
                boosts.append("Eve of a public holiday")
            for a, b, v in peaks:
                if a <= d <= b:
                    score += v["delta"]
                    boosts.append(v["label"])
            for nye in cfg["nye_dates"]:
                if d == parse(nye):
                    score += w["nye"]
                    reasons.append("New Year's Eve or Day: crowded out by NYE programming")
            score = max(0.0, score)
            t = cfg["tiers"]
            tier = ("prime" if score >= t["prime"] else "good" if score >= t["good"]
                    else "weak" if score >= t["weak"] else "poor")

        days.append(dict(date=d.isoformat(), dow=DOW[d.weekday()], score=round(score, 1), tier=tier,
                         reasons=reasons, boosts=boosts, direct=direct, concert=concert, other=other,
                         holiday=holidays.get(d, ""),
                         events=[e["event"] for e, _ in todays]))
        d += timedelta(days=1)
    return days


def main():
    from collections import Counter
    events, cfg, artists = load()
    default = cfg.get("default_lens", "standup")
    names = list((cfg.get("lenses") or {default: {}}).keys())

    lenses = {name: build(events, cfg, artists, name) for name in names}
    days = lenses[default]

    # data/, not docs/: the published directory is served to anyone with the URL, and
    # the dataset is no longer public. src/publish.py moves this into Supabase, which
    # is where the page reads it from once somebody has signed in.
    out = ROOT / "data" / "viability.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({
        "generated": date.today().isoformat(),
        "default_lens": default,
        "lens_meta": {n: {k: v for k, v in lens_config(cfg, n).items()
                          if k in ("label", "blurb")} for n in names},
        # The page counts its own headline figures from the days it was given, so the
        # one thing it cannot derive travels with them.
        "ramadan": cfg["ramadan"],
        # `days` stays the default lens so every existing reader keeps working.
        "days": days,
        "lenses": lenses,
        "events": events,
    }, indent=1, ensure_ascii=False))

    for name in names:
        c = Counter(x["tier"] for x in lenses[name])
        clash = len({x["date"] for x in lenses[name] if x["direct"]})
        mark = " (default)" if name == default else ""
        print(f"{len(lenses[name])} days scored, lens '{name}'{mark} -> {dict(c)}, "
              f"clash nights {clash}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
