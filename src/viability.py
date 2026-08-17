"""
Viability model: scores every date for staging an Indian (English/Hindi) stand-up show in the UAE.

Input:  data/events.json   (list of events, schema in BRIEF.md)
        data/config.json   (weights, blackout windows, holidays)
Output: docs/viability.json  {"generated": iso, "days": [...], "events": [...]}

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
    """direct = competing Indian/desi stand-up; concert = major desi draw; other = any other comedy."""
    hay = f"{ev.get('event','')} {ev.get('artist','')}".lower()
    names = artists["indian_standup_artists"] + artists["series_names"]
    if any(n.lower() in hay for n in names):
        return "direct"
    if ev.get("category") in ("Desi", "Comedy + Desi"):
        return "concert"
    if ev.get("category") == "Comedy":
        return "other"
    return None


def expand(ev):
    """Yield every calendar date an event occupies."""
    s, e = parse(ev.get("start")), parse(ev.get("end"))
    if not s:
        return
    d = s
    while d <= (e or s):
        yield d
        d += timedelta(days=1)


def build(events, cfg, artists):
    w = cfg["weights"]
    base = {int(k): v for k, v in cfg["base_by_weekday"].items()}
    fest_s, fest_e = parse(cfg["festival_window"][0]), parse(cfg["festival_window"][1])
    ram_s, ram_e = parse(cfg["ramadan"][0]), parse(cfg["ramadan"][1])
    eid_s, eid_e = parse(cfg["eid_window"][0]), parse(cfg["eid_window"][1])
    holidays = {parse(k): v for k, v in cfg["holidays"].items()}
    peaks = [(parse(a), parse(b), v) for a, b, v in cfg["peak_windows"]]
    lows = [(parse(a), parse(b), v) for a, b, v in cfg["low_windows"]]

    by_day = {}
    for ev in events:
        kind = classify(ev, artists)
        if not kind:
            continue
        for d in expand(ev):
            by_day.setdefault(d, []).append((ev, kind))

    days = []
    d, end = parse(cfg["range"][0]), parse(cfg["range"][1])
    while d <= end:
        todays = by_day.get(d, [])
        direct = sorted({e["event"] for e, k in todays if k == "direct"})
        concert = sorted({e["event"] for e, k in todays if k == "concert"})
        other = sorted({e["event"] for e, k in todays if k == "other"})
        score, reasons, boosts = base[d.weekday()], [], []

        if ram_s <= d <= ram_e:
            tier, score = "blocked", 0.0
            reasons.append(f"Ramadan (expected {ram_s:%-d %b} to {ram_e:%-d %b %Y}): ticketed evening comedy effectively pauses")
        elif direct:
            tier, score = "blocked", 0.0
            reasons.append("Direct clash: " + "; ".join(direct))
        else:
            near = sorted({e["event"] for dd in (d - timedelta(1), d + timedelta(1))
                           for e, k in by_day.get(dd, []) if k == "direct"})
            if near:
                score += w["adjacent_direct"]
                reasons.append("Indian stand-up the night before or after: " + "; ".join(near))
            if concert:
                score += w["desi_concert"]
                reasons.append("Major desi draw competing for the same wallet: " + "; ".join(concert))
            if other:
                score += w["other_comedy"]
                reasons.append("Other comedy on the same night: " + "; ".join(other))
            if fest_s <= d <= fest_e:
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
    events, cfg, artists = load()
    days = build(events, cfg, artists)
    out = ROOT / "docs" / "viability.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"generated": date.today().isoformat(), "days": days, "events": events},
                              indent=1, ensure_ascii=False))
    from collections import Counter
    c = Counter(x["tier"] for x in days)
    print(f"{len(days)} days scored -> {dict(c)}")
    print(f"direct-clash nights: {len({x['date'] for x in days if x['direct']})}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
