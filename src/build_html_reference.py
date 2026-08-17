import json, calendar, html
from datetime import date
from calendar_model import build, FEST_START, FEST_END, RAM_START, RAM_END

days = build()
by = {d["date"]: d for d in days}

MONTHS = [(2026,8),(2026,9),(2026,10),(2026,11),(2026,12),(2027,1),(2027,2),(2027,3)]
MN = ["","January","February","March","April","May","June","July","August","September","October","November","December"]

LABEL = {"prime":"PRIME","good":"GOOD","weak":"LOW","poor":"LOW","blocked":"BLOCKED"}
ICON  = {"prime":"✓","good":"●","weak":"","poor":"","blocked":"✕"}

def cell(y, m, dnum):
    iso = date(y, m, dnum).isoformat()
    d = by.get(iso)
    if not d:
        return f'<div class="day out"><span class="dn">{dnum}</span></div>'
    t = d["tier"]
    bits = []
    if d["holiday"]: bits.append("Public holiday: " + d["holiday"])
    for r in d["boosts"]: bits.append("+ " + r)
    for r in d["reasons"]: bits.append("- " + r)
    if not bits: bits.append("Nothing scheduled against you.")
    tip = html.escape(" || ".join(bits))
    wk = "wknd" if d["dow"] in ("Fri","Sat") else ""
    return (f'<div class="day t-{t} {wk}" data-tier="{t}" data-dow="{d["dow"]}" '
            f'data-tip="{tip}" data-date="{iso}" tabindex="0">'
            f'<span class="dn">{dnum}</span>'
            f'<span class="ic">{ICON[t]}</span>'
            f'<span class="lb">{LABEL[t]}</span></div>')

def month_html(y, m):
    cal = calendar.Calendar(firstweekday=0)  # Monday first
    weeks = cal.monthdayscalendar(y, m)
    out = [f'<section class="mo"><h3>{MN[m]} {y}</h3><div class="grid">']
    for h in ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]:
        out.append(f'<div class="hd">{h}</div>')
    for w in weeks:
        for dn in w:
            out.append('<div class="day pad"></div>' if dn == 0 else cell(y, m, dn))
    out.append('</div></section>')
    return "".join(out)

prime = [d for d in days if d["tier"] == "prime"]
prime_sorted = sorted(prime, key=lambda d: (-d["score"], d["date"]))
top = prime_sorted[:10]

def pretty(iso):
    y, m, dd = map(int, iso.split("-"))
    return f'{date(y,m,dd).strftime("%a")} {dd} {MN[m][:3]} {y}'

cards = "".join(
    f'<article class="card"><div class="cd-date">{pretty(d["date"])}</div>'
    f'<div class="cd-score">{d["score"]}<span>/7</span></div>'
    f'<div class="cd-why">{html.escape(d["boosts"][0]) if d["boosts"] else "Clear weekend night, no competing desi show"}</div></article>'
    for d in top)

rows = "".join(
    f'<tr><td>{pretty(d["date"])}</td><td>{d["dow"]}</td><td class="num">{d["score"]}</td>'
    f'<td><span class="pill p-{d["tier"]}">{LABEL[d["tier"]]}</span></td>'
    f'<td>{html.escape("; ".join(d["boosts"] + d["reasons"]) or "Clear night")}</td></tr>'
    for d in days if d["dow"] in ("Thu","Fri","Sat") and d["tier"] in ("prime", "good"))

blocked_rows = "".join(
    f'<tr><td>{pretty(d["date"])}</td><td>{d["dow"]}</td>'
    f'<td>{html.escape("; ".join(d["direct"]) or "Ramadan (expected 8 Feb - 9 Mar 2027)")}</td></tr>'
    for d in days if d["tier"] == "blocked" and (d["direct"] or d["date"] == RAM_START.isoformat()))

n_prime = len(prime)
n_blocked = len([d for d in days if d["tier"] == "blocked"])
n_direct = len(set(d["date"] for d in days if d["direct"]))

HTML = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Viable dates - Indian stand-up comedy, UAE</title>
<style>
:root{{
 --surface-1:#fcfcfb; --plane:#f9f9f7; --ink:#0b0b0b; --ink-2:#52514e; --muted:#898781;
 --grid:#e1e0d9; --ring:rgba(11,11,11,.10);
 --good:#0ca30c; --info:#2a78d6; --crit:#d03b3b; --warn:#fab219;
 --good-bg:rgba(12,163,12,.10); --info-bg:rgba(42,120,214,.09); --crit-bg:rgba(208,59,59,.08);
 color-scheme:light;
}}
@media (prefers-color-scheme:dark){{ :root:where(:not([data-theme="light"])){{
 --surface-1:#1a1a19; --plane:#0d0d0d; --ink:#fff; --ink-2:#c3c2b7; --muted:#898781;
 --grid:#2c2c2a; --ring:rgba(255,255,255,.10); --info:#3987e5;
 --good-bg:rgba(12,163,12,.16); --info-bg:rgba(57,135,229,.15); --crit-bg:rgba(208,59,59,.15);
 color-scheme:dark; }}}}
:root[data-theme="dark"]{{
 --surface-1:#1a1a19; --plane:#0d0d0d; --ink:#fff; --ink-2:#c3c2b7; --muted:#898781;
 --grid:#2c2c2a; --ring:rgba(255,255,255,.10); --info:#3987e5;
 --good-bg:rgba(12,163,12,.16); --info-bg:rgba(57,135,229,.15); --crit-bg:rgba(208,59,59,.15);
 color-scheme:dark; }}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--plane);color:var(--ink);
 font-family:system-ui,-apple-system,"Segoe UI",sans-serif;font-size:15px;line-height:1.5}}
.wrap{{max-width:1180px;margin:0 auto;padding:28px 20px 64px}}
header h1{{font-size:26px;margin:0 0 6px;letter-spacing:-.01em}}
header p{{margin:0;color:var(--ink-2);max-width:74ch}}
.bar{{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:22px 0 8px}}
button{{font:inherit;font-size:13px;padding:7px 13px;border-radius:8px;cursor:pointer;
 border:1px solid var(--ring);background:var(--surface-1);color:var(--ink)}}
button[aria-pressed="true"]{{background:var(--ink);color:var(--surface-1);border-color:var(--ink)}}
.spacer{{flex:1}}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:20px 0 6px}}
.stat{{background:var(--surface-1);border:1px solid var(--ring);border-radius:12px;padding:14px 16px}}
.stat b{{display:block;font-size:28px;line-height:1.1;letter-spacing:-.02em}}
.stat span{{color:var(--ink-2);font-size:12.5px}}
h2{{font-size:17px;margin:34px 0 12px;letter-spacing:-.01em}}
h2 small{{font-weight:400;color:var(--ink-2);font-size:13px;margin-left:8px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fill,minmax(196px,1fr));gap:12px}}
.card{{background:var(--surface-1);border:1px solid var(--ring);border-left:3px solid var(--good);
 border-radius:12px;padding:13px 15px}}
.cd-date{{font-weight:650;font-size:14.5px}}
.cd-score{{font-size:24px;font-weight:650;letter-spacing:-.02em;margin:2px 0 4px}}
.cd-score span{{font-size:13px;font-weight:400;color:var(--muted)}}
.cd-why{{font-size:12.5px;color:var(--ink-2)}}
.months{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:20px}}
.mo{{background:var(--surface-1);border:1px solid var(--ring);border-radius:14px;padding:14px 14px 16px}}
.mo h3{{margin:0 0 10px;font-size:15px}}
.grid{{display:grid;grid-template-columns:repeat(7,1fr);gap:4px}}
.hd{{font-size:11px;color:var(--muted);text-align:center;padding-bottom:4px;letter-spacing:.04em}}
.day{{position:relative;min-height:52px;border-radius:8px;border:1px solid var(--grid);
 padding:4px 5px;background:var(--surface-1);display:flex;flex-direction:column;gap:1px}}
.day.pad{{border:0;background:none}}
.dn{{font-size:12.5px;color:var(--ink-2);font-variant-numeric:tabular-nums}}
.ic{{font-size:12px;line-height:1}}
.lb{{font-size:8.5px;letter-spacing:.06em;color:var(--muted);margin-top:auto}}
.t-prime{{background:var(--good-bg);border-color:var(--good)}}
.t-prime .ic,.t-prime .lb{{color:var(--good);font-weight:700}}
.t-prime .dn{{color:var(--ink);font-weight:700}}
.t-good{{background:var(--info-bg);border-color:var(--info)}}
.t-good .ic,.t-good .lb{{color:var(--info);font-weight:650}}
.t-blocked{{background:var(--crit-bg);border-color:var(--crit);
 background-image:repeating-linear-gradient(135deg,transparent 0 5px,var(--crit-bg) 5px 10px)}}
.t-blocked .ic,.t-blocked .lb{{color:var(--crit);font-weight:700}}
.t-weak,.t-poor{{opacity:.72}}
.dim{{opacity:.2}}
.day:hover,.day:focus{{outline:2px solid var(--ink);outline-offset:1px;z-index:5}}
#tip{{position:fixed;z-index:99;max-width:330px;background:var(--ink);color:var(--surface-1);
 font-size:12.5px;line-height:1.45;padding:9px 11px;border-radius:9px;pointer-events:none;
 opacity:0;transition:opacity .1s}}
#tip b{{display:block;margin-bottom:3px}}
#tip div{{margin-top:3px}}
.legend{{display:flex;gap:18px;flex-wrap:wrap;margin:14px 0 0;font-size:12.5px;color:var(--ink-2)}}
.legend i{{display:inline-block;width:11px;height:11px;border-radius:3px;margin-right:6px;
 vertical-align:-1px;border:1px solid var(--ring)}}
table{{width:100%;border-collapse:collapse;background:var(--surface-1);
 border:1px solid var(--ring);border-radius:12px;overflow:hidden;font-size:13px}}
th{{text-align:left;padding:9px 12px;background:var(--plane);color:var(--ink-2);
 font-weight:600;border-bottom:1px solid var(--grid)}}
td{{padding:8px 12px;border-bottom:1px solid var(--grid);vertical-align:top}}
td.num{{font-variant-numeric:tabular-nums}}
.pill{{font-size:10.5px;font-weight:700;letter-spacing:.05em;padding:2px 7px;border-radius:20px;
 border:1px solid currentColor}}
.p-prime{{color:var(--good)}} .p-good{{color:var(--info)}}
details{{margin-top:12px}} summary{{cursor:pointer;font-size:13.5px;color:var(--ink-2);padding:6px 0}}
.note{{font-size:12.5px;color:var(--ink-2);margin-top:26px;max-width:80ch}}
.note b{{color:var(--ink)}}
</style></head><body>
<div class="wrap">
<header>
<h1>Viable dates for an Indian stand-up show</h1>
<p>Dubai and Abu Dhabi, {date.today().strftime('%B %Y')} through March 2027. Every date is scored against
what is already on sale on Platinumlist, plus the season, the holidays and the Ramadan pause. Hover or tap
any day to see what is competing with it.</p>
</header>

<div class="stats">
<div class="stat"><b>{n_prime}</b><span>prime dates to shortlist</span></div>
<div class="stat"><b>{n_direct}</b><span>nights already taken by a competing Indian act</span></div>
<div class="stat"><b>{n_blocked}</b><span>dates ruled out in total</span></div>
<div class="stat"><b>30</b><span>days lost to Ramadan (8 Feb - 9 Mar 2027)</span></div>
</div>

<h2>Best ten dates <small>highest score first</small></h2>
<div class="cards">{cards}</div>

<div class="bar">
<button id="f-all" aria-pressed="true">All days</button>
<button id="f-wknd" aria-pressed="false">Fri + Sat only</button>
<button id="f-prime" aria-pressed="false">Prime only</button>
<span class="spacer"></span>
<button id="theme">Dark / light</button>
</div>

<div class="months">{"".join(month_html(y,m) for y,m in MONTHS)}</div>

<div class="legend">
<span><i style="background:var(--good-bg);border-color:var(--good)"></i>✓ Prime - book this</span>
<span><i style="background:var(--info-bg);border-color:var(--info)"></i>● Good - workable</span>
<span><i style="background:var(--surface-1)"></i>Low - weeknight or diluted</span>
<span><i style="background:var(--crit-bg);border-color:var(--crit)"></i>✕ Blocked - direct clash or Ramadan</span>
</div>

<h2>Every workable Thursday, Friday and Saturday</h2>
<table><thead><tr><th>Date</th><th>Day</th><th>Score</th><th>Rating</th><th>What is on that night</th></tr></thead>
<tbody>{rows}</tbody></table>

<details><summary>Dates already taken by a competing Indian act (avoid these)</summary>
<table style="margin-top:10px"><thead><tr><th>Date</th><th>Day</th><th>Who is on</th></tr></thead>
<tbody>{blocked_rows}</tbody></table></details>

<p class="note"><b>How the score works.</b> Every date starts from its day of the week, because
Saturday is where the UAE's desi comedy audience actually turns up: Saturday 5.0, Friday 4.5,
Thursday and Sunday 3.0, midweek 1.5 to 2.0. Then it loses points for a major desi concert on the
same night (-2.5), for sitting inside the Dubai Comedy Festival window of 9-18 Oct when the market
is saturated and the theatres are booked (-2.5), for another Indian act the night before or after
(-1.0), for late August when a large share of residents are still away (-1.0), and for any other
comedy on the same night (-0.8). It gains points for the Eid Al Fitr window (+1.5), a public
holiday (+1.0), and the December-to-January visitor peak (+0.5 to +0.7). A direct clash with an
Indian stand-up show, or a date inside Ramadan, blocks the date outright.<br><br>
<b>What to check before you commit.</b> Ramadan and Eid dates are astronomical forecasts and move
with the moon sighting, so treat February and March 2027 as provisional. Venue availability is not
modelled here: Emirates Theatre, the Sheikh Rashid Auditorium at the Indian High School and
Live@Play in Al Quoz carry most of this circuit and book out well ahead, so a prime date is only
prime if the room is free. Competitor listings also keep being added, so re-run this against
Platinumlist before locking a date more than a month out.</p>
</div>
<div id="tip" role="tooltip"></div>
<script>
const tip=document.getElementById('tip');
document.querySelectorAll('.day[data-tip]').forEach(el=>{{
 const show=()=>{{
  const parts=el.dataset.tip.split(' || ');
  tip.innerHTML='<b>'+el.dataset.date+'</b>'+parts.map(p=>'<div>'+p+'</div>').join('');
  const r=el.getBoundingClientRect();
  tip.style.opacity=1;
  const w=tip.offsetWidth,h=tip.offsetHeight;
  let x=r.left+r.width/2-w/2, y=r.top-h-8;
  if(y<8) y=r.bottom+8;
  tip.style.left=Math.max(8,Math.min(x,innerWidth-w-8))+'px';
  tip.style.top=y+'px';
 }};
 const hide=()=>tip.style.opacity=0;
 el.addEventListener('mouseenter',show); el.addEventListener('focus',show);
 el.addEventListener('mouseleave',hide); el.addEventListener('blur',hide);
}});
const $=id=>document.getElementById(id);
const btns={{all:$('f-all'), wknd:$('f-wknd'), prime:$('f-prime')}};
function apply(mode){{
 for(const k in btns) btns[k].setAttribute('aria-pressed', String(k===mode));
 document.querySelectorAll('.day[data-tier]').forEach(el=>{{
  const t=el.dataset.tier, w=(el.dataset.dow==='Fri'||el.dataset.dow==='Sat');
  let on=true;
  if(mode==='wknd') on=w;
  if(mode==='prime') on=(t==='prime');
  el.classList.toggle('dim',!on);
 }});
}}
btns.all.addEventListener('click',()=>apply('all'));
btns.wknd.addEventListener('click',()=>apply('wknd'));
btns.prime.addEventListener('click',()=>apply('prime'));
$('theme').addEventListener('click',()=>{{
 const cur=document.documentElement.getAttribute('data-theme');
 const dark = cur ? cur==='dark' : matchMedia('(prefers-color-scheme: dark)').matches;
 document.documentElement.setAttribute('data-theme', dark?'light':'dark');
}});
</script></body></html>"""

open("/home/claude/out/Indian_Standup_Viable_Dates.html","w").write(HTML)
print("prime", n_prime, "blocked", n_blocked, "direct-clash nights", n_direct)
