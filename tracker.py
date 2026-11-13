"""Training tracker: pulls runs from Garmin Connect and opens this week's plan in the browser.

Run:  python tracker.py   (needs only built-in Python; reads the Garmin API key from .env)
GitHub runs this every hour and publishes the page (.github/workflows/publish.yml).
"""
import datetime as dt
import json
import math
import os
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

API = "https://personal.connectapi.garmin.com"  # Garmin's official Personal API
ENV_FILE = Path(__file__).with_name(".env")  # holds API_KEY='...' on your computer; never uploaded
OUT = Path(__file__).with_name("Result.html")
RACE = dt.date(2027, 1, 16)
OPT = "optional"
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# (week #, Monday, target miles). A text note instead of a target = hidden week. Edit targets here;
# every day's miles are calculated from them by initial_plan() below.
D = dt.date
WEEKS = [
    (0, D(2026, 10, 26), 25),
    (1, D(2026, 11, 2), 30),
    (2, D(2026, 11, 9), 40),
    (3, D(2026, 11, 16), 45),
    (4, D(2026, 11, 23), 50),
    (5, D(2026, 11, 30), "Recovery week. Nothing to track &mdash; enjoy ignoring it."),
    (6, D(2026, 12, 7), 55),
    (7, D(2026, 12, 14), 60),
    (8, D(2026, 12, 21), 65),
    (9, D(2026, 12, 28), 65),
    (10, D(2027, 1, 4), 45),
    (11, D(2027, 1, 11), "Race week. Rest up &mdash; nothing to track."),
]

STRENGTH_DAYS = {"Tue", "Thu"}  # Tue strength, Thu balance/ankle; logged on Garmin as "Cardio" (indoor_cardio)
_STRENGTH = "step-ups, slow step-downs, split squats, single-leg calf raises, single-leg balance, plank"
_ANKLE = ("single-leg balance, eyes open, next to a counter, tapping the free foot forward, "
          "sideways and back (3 &times; 30 sec each leg); "
          "single-leg calf raises off a step, 3 sec down (2 &times; 15 each leg); "
          "single-leg mini squats standing on a pillow (2 &times; 10 each leg); "
          "heel walks then toe walks (30 sec each); "
          "side-to-side hops over a line on one foot (2 &times; 20 sec each leg)")
_SAT = ("Hilly trail. Walk every steep climb from mile 1. Eat every 30&ndash;40 min. "
        "Loop past your car every 3&ndash;5 mi like the race hub: refill and go in under 3 min.")
NOTES = {  # phase -> day -> workout notes; the planned miles above still set the distance
    "build": {
        "Tue": f"Hills inside the planned miles: 6&ndash;8 &times; ~1 min hard uphill, easy jog down. "
               f"Then 15&ndash;20 min strength: {_STRENGTH}.",
        "Wed": "Optional: easy and flat, or skip.",
        "Thu": f"Easy + 6 relaxed 20-sec pickups at the end. Then ~10 min ankle/stability: {_ANKLE}.",
        "Sat": _SAT,
        "Sun": "Easy, on tired legs.",
    },
    "peak": {
        "Tue": f"Longer climbs: 4&ndash;6 &times; 3&ndash;4 min uphill, power-hike half of them. "
               f"Then ~15 min lighter strength: {_STRENGTH}.",
        "Wed": "Easy.",
        "Thu": f"Easy + 6 relaxed 20-sec pickups at the end. Then ~10 min ankle/stability: {_ANKLE}.",
        "Sat": _SAT + " Wear full race gear and eat exactly what you'll eat on race day.",
        "Sun": "Easy, hilly if your body is okay with it.",
    },
    "taper": {
        "Tue": "4 short hills. Last strength session: light, ~10 min.",
        "Wed": "Easy.",
        "Thu": "Easy + 4 pickups. Then ~5 min ankle/stability: single-leg balance, eyes open, next to a counter "
               "(2 &times; 30 sec each leg); single-leg calf raises off a step (1 &times; 15 each leg). No hops.",
        "Sat": "Hilly trail, same habits as before, just shorter.",
        "Sun": "Easy.",
    },
}


def phase(n):
    return "build" if n <= 4 else "peak" if n <= 9 else "taper"


def strength_status(day, planned, done, today):
    if day in done:
        return "&#10003; done" if planned else "&#10003; bonus"
    if not planned:
        return ""
    return "missed" if day < today else "&#9744;"


assert strength_status(D(2026, 11, 3), True, {D(2026, 11, 3)}, D(2026, 11, 4)) == "&#10003; done"
assert strength_status(D(2026, 11, 3), True, set(), D(2026, 11, 4)) == "missed"
assert strength_status(D(2026, 11, 3), True, set(), D(2026, 11, 3)) == "&#9744;"
assert strength_status(D(2026, 11, 5), False, {D(2026, 11, 5)}, D(2026, 11, 6)) == "&#10003; bonus"
assert strength_status(D(2026, 11, 5), False, set(), D(2026, 11, 6)) == ""



def api_key():
    """The Garmin Personal API key: the API_KEY setting (on GitHub), else API_KEY='...' in .env."""
    key = os.environ.get("API_KEY", "").strip()
    if not key and ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "API_KEY":
                key = value.strip().strip("'\"")
    if not key:
        raise SystemExit("No Garmin API key. Put API_KEY='...' in .env, or set the API_KEY secret on GitHub.")
    return key


def garmin_call(path, auth, method="GET", **params):
    url = f"{API}{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
    req = urllib.request.Request(url, method=method, data=b"" if method == "POST" else None,
                                 headers={"Authorization": auth, "User-Agent": "training-tracker"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode().strip()


def garmin_token():
    """Trade the long-lived API key for a 24-hour access token. Nothing to save: each run gets a fresh one."""
    body = garmin_call("/personalapitoken-service/personal-api/personal-api-key/token",
                       f"ApiKey {api_key()}", method="POST")
    return json.loads(body) if body.startswith('"') else body  # Garmin sends the token as plain text


def garmin_activities(start, end):
    """Every activity from start to end (inclusive dates), fetched 100 at a time so none get cut off."""
    auth, acts, page = f"Bearer {garmin_token()}", [], 100
    while True:
        batch = json.loads(garmin_call("/activitylist-service/activities/search/activities", auth,
                                       startDate=start.isoformat(), endDate=end.isoformat(),
                                       start=len(acts), limit=page))
        acts += batch
        if len(batch) < page:
            return acts


def fetch_garmin(start, end):
    """Running miles per date, Cardio (strength) dates, and running climb in ft/mi per date, from Garmin."""
    miles, cardio, gain = {}, set(), {}
    for a in garmin_activities(start, end):
        kind = (a.get("activityType") or {}).get("typeKey", "")
        day = dt.date.fromisoformat(a["startTimeLocal"][:10])
        if kind == "indoor_cardio":  # plan strength/ankle days. NOT strength_training: that's unrelated upper-body work
            cardio.add(day)
        elif "running" in kind:
            miles[day] = miles.get(day, 0) + (a.get("distance") or 0) / 1609.344
            gain[day] = gain.get(day, 0) + (a.get("elevationGain") or 0) * 3.28084  # meters -> feet
    climb = {d: round(gain[d] / m) for d, m in miles.items() if m}  # day's total ft / day's total mi
    return {d: round(m, 1) for d, m in miles.items()}, cardio, climb  # plan math uses the miles shown


def fmt(x):
    return f"{x:.1f}".rstrip("0").rstrip(".")


def md(d):
    """'Nov 9'. (strftime's %-d drops the leading zero on Mac/Linux but crashes on Windows.)"""
    return f"{d:%b} {d.day}"


def ceil_half(x):
    return max(math.ceil(x * 2 - 1e-9) / 2, 0)  # 1e-9: float noise must not bump 8.0 to 8.5


def round_half(x):
    return math.floor(x * 2 + 0.5) / 2  # nearest half mile; exact halves round up


def initial_plan(n, target):
    """Sat = target / 2.75 (wk 0-4) or / 3.25 (wk 6+), Tue = half of Sat; Wed (wk 6+), Thu and Sun
    use the same math as dynamic_plan(), so an untouched week's Dynamic Plan matches. Sun = what's left."""
    sat = round_half(target / (2.75 if n <= 4 else 3.25))
    tue = round_half(sat / 2)
    if n <= 4:  # Tue + Thu = Sat, Wed optional
        wed, thu = OPT, ceil_half(sat - tue)
    else:  # Tue + Wed + Thu = 1.5x Sat
        wed = ceil_half((1.5 * sat - tue) / 2)
        thu = ceil_half(1.5 * sat - tue - wed)
    sun = ceil_half(target - tue - (0 if wed == OPT else wed) - thu - sat)
    return {"Tue": tue, "Wed": wed, "Thu": thu, "Sat": sat, "Sun": sun}


# (week #, Monday, target, days) for tracked weeks; (week #, Monday, None, note) for hidden ones.
PLAN = [(n, mon, t, initial_plan(n, t)) if isinstance(t, int) else (n, mon, None, t) for n, mon, t in WEEKS]


def dynamic_plan(mon, target, days, miles, today):
    """Re-plan Wed/Thu/Sun from actuals. A day is 'actual' once it has a run or has passed (skip = 0)."""
    def val(i, plan):
        day = mon + dt.timedelta(i)
        return miles.get(day, 0) if day < today or day in miles else plan

    sat = days["Sat"]
    dyn = dict(days)
    tue = val(1, days["Tue"])
    if days["Wed"] == OPT:  # weeks 0-5: Tue + Wed + Thu = Sat
        wed = val(2, 0)
        dyn["Thu"] = ceil_half(sat - tue - wed)
    else:  # weeks 7+: Tue + Wed + Thu = 1.5x Sat; Wed gets half of what's left after Tue
        dyn["Wed"] = ceil_half((1.5 * sat - tue) / 2)
        wed = val(2, dyn["Wed"])
        dyn["Thu"] = ceil_half(1.5 * sat - tue - wed)
    before_sun = val(0, 0) + tue + wed + val(3, dyn["Thu"]) + val(4, 0) + val(5, sat)
    dyn["Sun"] = min(days["Sun"], ceil_half(target - before_sun))  # never more than planned; less if the week is already covered
    return dyn


# Self-check: every tracked week's planned days add up to exactly its target.
for _n, _mon, _target, _days in PLAN:
    if _target is not None:
        _sum = sum(v for v in _days.values() if v != OPT)
        assert _sum == _target, f"week {_n} plans {_sum} mi, target {_target}"

# Self-check: an untouched week's Dynamic Plan equals its Initial Plan (no drift).
for _n, _mon, _target, _days in PLAN:
    if _target is not None:
        assert dynamic_plan(_mon, _target, _days, {}, _mon) == _days, f"week {_n} drifts"

# Self-check: the worked examples from planning.
_wk1 = PLAN[1][1:]
_ex = {D(2026, 11, 3): 7, D(2026, 11, 4): 2}
assert dynamic_plan(*_wk1, _ex, D(2026, 11, 5))["Thu"] == 2
_ex |= {D(2026, 11, 5): 2, D(2026, 11, 7): 9}
assert dynamic_plan(*_wk1, _ex, D(2026, 11, 8))["Sun"] == 8  # behind on the week: Sunday capped at plan
assert dynamic_plan(*_wk1, _ex | {D(2026, 11, 7): 14}, D(2026, 11, 8))["Sun"] == 5  # ahead: only what's left
assert dynamic_plan(*_wk1, _ex | {D(2026, 11, 7): 20}, D(2026, 11, 8))["Sun"] == 0  # target already hit
assert dynamic_plan(*_wk1, {D(2026, 11, 3): 7, D(2026, 11, 4): 5}, D(2026, 11, 5))["Thu"] == 0  # block done -> rest


def week_table(mon, days, target, miles, cardio, climb, today):
    """Days as columns; rows are Initial Plan, Dynamic Plan, Strength, Garmin Distance, Garmin Climbing, and status.
    Also one swipeable card per day, which replaces the table on phone-width screens."""
    dyn = dynamic_plan(mon, target, days, miles, today)
    head, plan_row, dyn_row, got_row, climb_row, status_row, str_row, cards = [], [], [], [], [], [], [], []
    txt = lambda p: "optional" if p == OPT else f"{fmt(p)} mi" if p else "rest"
    for i, name in enumerate(DAYS):
        day = mon + dt.timedelta(i)
        plan = dyn.get(name)
        got = miles.get(day, 0)
        if plan in (None, OPT, 0):
            status = "&#10003; bonus" if got else ""
        elif got >= plan - 0.05:
            status = "&#10003; done"
        elif day < today:
            status = "short" if got else "missed"
        else:
            status = "today" if day == today else ""
        c = ' class="today"' if day == today else ""
        cell = {  # same values, labels and order for the table column and the phone card
            "Initial Plan": txt(days.get(name)),
            "Dynamic Plan": txt(plan) if plan != days.get(name) else "",
            "Strength": strength_status(day, name in STRENGTH_DAYS, cardio, today),
            "Garmin Distance": fmt(got) + " mi" if got else "&ndash;",
            "Garmin Climbing": str(climb[day]) + " ft/mi" if day in climb else "&ndash;",
        }
        head.append(f"<th{c}>{name}<br><small>{md(day)}</small></th>")
        plan_row.append(f"<td{c}>{cell['Initial Plan']}</td>")
        dyn_row.append(f"<td{c}>{cell['Dynamic Plan']}</td>")
        str_row.append(f"<td{c}>{cell['Strength']}</td>")
        got_row.append(f"<td{c}>{cell['Garmin Distance']}</td>")
        climb_row.append(f"<td{c}>{cell['Garmin Climbing']}</td>")
        status_row.append(f"<td{c}>{status}</td>")
        rows = cell.items()
        badge = status or ("today" if day == today else "")
        cards.append(f'<article class="card d{i}{" today" if day == today else ""}" data-day="{i}">'
                     f'<header><b>{name}</b> <small>{md(day)}</small><span class="badge">{badge}</span></header>'
                     f'<dl>{"".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in rows)}</dl></article>')
    dots = "".join(f'<button class="dot d{i}" aria-label="{n}"></button>' for i, n in enumerate(DAYS))
    return (f'<div class="days"><button class="nav" data-step="-1" aria-label="Previous day">&lsaquo;</button>'
            f'<div class="cards">{"".join(cards)}</div>'
            f'<button class="nav" data-step="1" aria-label="Next day">&rsaquo;</button>'
            f'<div class="dots">{dots}</div></div>'
            f"<table class=\"wk\"><thead><tr><th></th>{''.join(head)}</tr></thead><tbody>"
            f"<tr><th>Initial Plan</th>{''.join(plan_row)}</tr>"
            f"<tr><th>Dynamic Plan</th>{''.join(dyn_row)}</tr>"
            f"<tr><th>Strength</th>{''.join(str_row)}</tr>"
            f"<tr><th>Garmin Distance</th>{''.join(got_row)}</tr>"
            f"<tr><th>Garmin Climbing</th>{''.join(climb_row)}</tr>"
            f"<tr><th></th>{''.join(status_row)}</tr>"
            "</tbody></table>")


def week_section(week, miles, cardio, climb, today, current):
    n, mon, target, days = week
    back = "" if current is None or week == current else f'<p><a href="#wk-{current[0]}">&larr; back to this week</a></p>'
    title = f"Week {n} &middot; {md(mon)} &ndash; {md(mon + dt.timedelta(6))}"
    if week == current:
        title += " (this week)" if today >= mon else f" (starts Monday)"
    if target is None:
        return f'<section class="week" id="wk-{n}" hidden><h2>{title}</h2>{back}<p class="big">{days}</p></section>'
    done = sum(miles.get(mon + dt.timedelta(i), 0) for i in range(7))
    left = max(target - done, 0)
    return f"""<section class="week" id="wk-{n}" hidden><h2>{title}</h2>{back}
<div class="tiles">
  <div><span>Target</span><b>{target} mi</b></div>
  <div><span>Done</span><b>{fmt(done)} mi</b></div>
  <div><span>Left</span><b>{fmt(left)} mi</b></div>
</div>
{week_table(mon, days, target, miles, cardio, climb, today)}
<h3>Workouts</h3><ul class="notes">{"".join(f"<li><b>{d}:</b> {t}</li>" for d, t in NOTES[phase(n)].items())}</ul></section>"""


def chart(miles):
    """Bar per week = actual miles; dark tick = target. Click a week to see its days."""
    W, H, top, base, slot = 760, 240, 20, 200, 60
    ymax = 70
    y = lambda v: base - (base - top) * v / ymax
    parts = []
    for v in (0, 20, 40, 60):
        parts.append(f'<line x1="40" x2="{W}" y1="{y(v)}" y2="{y(v)}" class="grid"/>'
                     f'<text x="34" y="{y(v) + 4}" class="axis" text-anchor="end">{v}</text>')
    for k, (n, mon, target, days) in enumerate(PLAN):
        cx = 40 + slot * k + slot / 2
        mark = []
        if target is None:
            label = "Rest" if n == 5 else "Race"
            tip = f"Week {n} ({md(mon)}): {label.lower()} week"
            mark.append(f'<text x="{cx}" y="{base - 8}" class="axis" text-anchor="middle">{label}</text>')
        else:
            done = sum(miles.get(mon + dt.timedelta(i), 0) for i in range(7))
            tip = f"Week {n} ({md(mon)}): {fmt(done)} of {target} mi"
            bw = 28
            if done:
                h = base - y(done)
                mark.append(f'<path class="bar" d="M{cx - bw/2},{base} v{-(h - 4)} q0,-4 4,-4 h{bw - 8} '
                            f'q4,0 4,4 v{h - 4} z"/>')
            mark.append(f'<line class="target" x1="{cx - bw/2 - 4}" x2="{cx + bw/2 + 4}" '
                        f'y1="{y(target)}" y2="{y(target)}"/>')
        # the whole column (bigger than the mark) is the hover + click target
        parts.append(f'<a href="#wk-{n}"><title>{tip}</title>{"".join(mark)}'
                     f'<rect x="{cx - slot/2}" y="{top}" width="{slot}" height="{base - top + 26}" fill="transparent"/>'
                     f'<text x="{cx}" y="{base + 18}" class="axis link" text-anchor="middle">Wk {n}</text></a>')
    return (f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Weekly miles, actual vs target">'
            + "".join(parts) + "</svg>")


def render(miles, cardio, climb, today):
    mon = max(today - dt.timedelta(today.weekday()), PLAN[0][1])
    current = next((w for w in PLAN if w[1] == mon), None) if today < RACE else None
    to_race = (RACE - today).days

    if today > RACE:
        body = "<p class='big'>Race is done. Nice work.</p>"
    elif today == RACE:
        body = "<p class='big'>Race day! Go get it.</p>"
    else:
        body = ""
    body += "\n".join(week_section(w, miles, cardio, climb, today, current) for w in PLAN)
    default = f"wk-{current[0]}" if current else ""

    countdown = f"{to_race} days to race day (Sat Jan 16) &middot; " if to_race > 0 else ""
    now = dt.datetime.now()
    updated = f"{now:%a} {md(now)}, {now.hour % 12 or 12}:{now:%M %p}"
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Training Tracker</title>
<style>
:root {{ --bg:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --line:#e4e3df; --bar:#2a78d6; --hl:#eef4fc; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg:#1a1a19; --ink:#f0efec; --ink2:#c3c2b7; --line:#383835; --bar:#3987e5; --hl:#22303f; }}
}}
body {{ background:var(--bg); color:var(--ink); font:16px/1.4 -apple-system,system-ui,sans-serif;
       max-width:680px; margin:24px auto; padding:0 16px; }}
h1 {{ margin:0 }} .sub {{ color:var(--ink2); margin:4px 0 24px }}
.big {{ font-size:1.25em }}
.tiles {{ display:flex; gap:12px; margin:12px 0 }}
.tiles div {{ flex:1; border:1px solid var(--line); border-radius:8px; padding:10px 12px }}
.tiles span {{ display:block; color:var(--ink2); font-size:.85em }} .tiles b {{ font-size:1.5em }}
table {{ width:100%; border-collapse:collapse }}
th, td {{ text-align:left; padding:8px 6px; border-bottom:1px solid var(--line) }}
th {{ color:var(--ink2); font-weight:500 }} small {{ font-weight:400 }}
.today {{ background:var(--hl); font-weight:600 }}
a {{ color:var(--bar) }} .axis.link {{ fill:var(--bar); text-decoration:underline }}
svg {{ width:100%; height:auto }} .grid {{ stroke:var(--line) }}
.axis {{ fill:var(--ink2); font-size:12px }} .bar {{ fill:var(--bar) }}
.target {{ stroke:var(--ink); stroke-width:2 }}
.legend {{ color:var(--ink2); font-size:.9em }}
.notes {{ padding-left:20px }} .notes li {{ margin-bottom:6px }}
.sw {{ display:inline-block; width:12px; height:12px; border-radius:2px; background:var(--bar); vertical-align:-1px }}
.tk {{ display:inline-block; width:16px; height:2px; background:var(--ink); vertical-align:4px; margin-left:12px }}
/* Day colors, ROYGBIV Mon-Sun */
.d0 {{ --c:#e5484d }} .d1 {{ --c:#f76b15 }} .d2 {{ --c:#e2a200 }} .d3 {{ --c:#30a46c }}
.d4 {{ --c:#0090ff }} .d5 {{ --c:#5b5bd6 }} .d6 {{ --c:#8e4ec6 }}
/* Phone: swipeable day cards replace the week table */
.days {{ display:none }}
@media (max-width: 600px) {{
  table.wk {{ display:none }}
  .days {{ display:grid; grid-template-columns:auto minmax(0,1fr) auto; align-items:center; margin:12px -12px }}
}}
.cards {{ display:flex; gap:12px; overflow-x:auto; scroll-snap-type:x mandatory; scrollbar-width:none; padding:2px 0 }}
.cards::-webkit-scrollbar {{ display:none }}
.card {{ flex:0 0 100%; box-sizing:border-box; scroll-snap-align:start; border:2px solid var(--c); border-radius:14px;
         background:color-mix(in srgb, var(--c) 12%, transparent); padding:14px 16px }}
.card.today {{ border-width:3px; padding:13px 15px }}
.card header {{ display:flex; align-items:baseline; gap:8px; margin-bottom:12px }}
.card header b {{ font-size:1.3em }} .card header small {{ color:var(--ink2) }}
.badge {{ margin-left:auto; font-weight:600 }}
.card dl {{ display:grid; grid-template-columns:auto 1fr; gap:8px 20px; margin:0 }}
.card dt {{ color:var(--ink2) }} .card dd {{ margin:0; font-weight:600 }}
.nav {{ background:none; border:0; color:var(--ink2); font-size:2.2em; line-height:1; padding:4px 8px; cursor:pointer }}
.dots {{ grid-column:1 / -1; display:flex; justify-content:center; gap:10px; margin-top:12px }}
.dot {{ width:12px; height:12px; border-radius:50%; border:2px solid var(--c); background:transparent; padding:0; cursor:pointer }}
.dot.on {{ background:var(--c) }}
</style></head><body>
<h1>Training Tracker</h1>
<p class="sub">{countdown}updated {updated}</p>
{body}
<h2>All weeks</h2>
<p class="legend"><span class="sw"></span> Actual miles <span class="tk"></span> Target &middot; click a week to see its days</p>
{chart(miles)}
<script>
// Day cards: swipe, tap the arrows, or tap a dot. Opens on today's card.
function cardIndex(box) {{ return Math.round(box.scrollLeft / (box.children[0].offsetWidth + 12)); }}
function goTo(days, i, how) {{
  const box = days.querySelector(".cards"), cards = box.children;
  i = Math.max(0, Math.min(cards.length - 1, i));
  box.scrollTo({{ left: cards[i].offsetLeft - cards[0].offsetLeft, behavior: how || "smooth" }});
}}
document.querySelectorAll(".days").forEach(days => {{
  const box = days.querySelector(".cards"), dots = days.querySelectorAll(".dot");
  const mark = () => dots.forEach((d, k) => d.classList.toggle("on", k === cardIndex(box)));
  box.addEventListener("scroll", mark, {{ passive: true }});
  days.querySelectorAll(".nav").forEach(b => b.onclick = () => goTo(days, cardIndex(box) + Number(b.dataset.step)));
  dots.forEach((d, k) => d.onclick = () => goTo(days, k));
  mark();
}});
function show() {{
  const id = location.hash.slice(1) || "{default}";
  document.querySelectorAll(".week").forEach(s => s.hidden = s.id !== id);
  const days = id && document.querySelector("#" + id + " .days");
  if (days) {{
    const t = days.querySelector(".card.today");
    goTo(days, t ? Number(t.dataset.day) : 0, "instant");
  }}
}}
addEventListener("hashchange", show); show();
</script>
</body></html>"""


def main():
    today = dt.date.today()
    start = PLAN[0][1]
    miles, cardio, climb = fetch_garmin(start, today) if today >= start else ({}, set(), {})
    OUT.write_text(render(miles, cardio, climb, today), encoding="utf-8")
    if os.environ.get("GITHUB_ACTIONS"):  # on GitHub: just build the page; the workflow publishes it
        print(f"Wrote {OUT}")
    else:
        webbrowser.open(OUT.as_uri())
        print(f"Opened {OUT}")


if __name__ == "__main__":
    main()
