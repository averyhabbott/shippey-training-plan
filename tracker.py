"""Training tracker: pulls runs from Garmin Connect and opens this week's plan in the browser.

Run:  venv/bin/python tracker.py
"""
import datetime as dt
import math
import getpass
import webbrowser
from pathlib import Path

TOKENS = "~/.garminconnect"  # Garmin login key lives here (not the password)
OUT = Path(__file__).with_name("Result.html")
RACE = dt.date(2027, 1, 16)
OPT = "optional"
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# (week #, Monday, target miles, planned miles by day). Target None = hidden week.
# Sat = target / 2.75 (wk 1-5) or / 3.25 (wk 7+), Sun = 0.75x Sat, Tue/Wed/Thu = 0.5x Sat,
# Wed (wk 7+), Thu and Sun are what dynamic_plan() gives an untouched week, so the
# Dynamic Plan starts identical to this one. Weeks land 0-0.5 mi over target (rounding up).
def _w(tue, wed, thu, sat, sun):
    return {"Tue": tue, "Wed": wed, "Thu": thu, "Sat": sat, "Sun": sun}

D = dt.date
PLAN = [
    (0, D(2026, 10, 26), 25, _w(4.5, OPT, 4.5, 9, 7)),
    (1, D(2026, 11, 2), 30, _w(5.5, OPT, 5.5, 11, 8.5)),
    (2, D(2026, 11, 9), 35, _w(6.5, OPT, 6, 12.5, 10)),
    (3, D(2026, 11, 16), 40, _w(7, OPT, 7.5, 14.5, 11)),
    (4, D(2026, 11, 23), 45, _w(8, OPT, 8.5, 16.5, 12.5)),
    (5, D(2026, 11, 30), 50, _w(9, OPT, 9, 18, 14)),
    (6, D(2026, 12, 7), None, "Recovery week. Nothing to track &mdash; enjoy ignoring it."),
    (7, D(2026, 12, 14), 55, _w(8.5, 8.5, 8.5, 17, 13)),
    (8, D(2026, 12, 21), 60, _w(9.5, 9.5, 9, 18.5, 14)),
    (9, D(2026, 12, 28), 65, _w(10, 10, 10, 20, 15)),
    (10, D(2027, 1, 4), 45, _w(6.5, 7.5, 7, 14, 10.5)),
    (11, D(2027, 1, 11), None, "Race week. Rest up &mdash; nothing to track."),
]

STRENGTH_DAYS = {"Tue", "Thu"}  # Tue strength, Thu balance/ankle; logged on Garmin as "Cardio"
_STRENGTH = "step-ups, slow step-downs, split squats, single-leg calf raises, single-leg balance, plank"
_SAT = ("Hilly trail. Walk every steep climb from mile 1. Eat every 30&ndash;40 min. "
        "Loop past your car every 3&ndash;5 mi like the race hub: refill and go in under 3 min.")
NOTES = {  # phase -> day -> workout notes; the planned miles above still set the distance
    "build": {
        "Tue": f"Hills inside the planned miles: 6&ndash;8 &times; ~1 min hard uphill, easy jog down. "
               f"Then 15&ndash;20 min strength: {_STRENGTH}.",
        "Wed": "Optional: easy and flat, or skip.",
        "Thu": "Easy + 6 relaxed 20-sec pickups at the end. Then 10 min balance/ankle work.",
        "Sat": _SAT,
        "Sun": "Easy, on tired legs.",
    },
    "peak": {
        "Tue": f"Longer climbs: 4&ndash;6 &times; 3&ndash;4 min uphill, power-hike half of them. "
               f"Then ~15 min lighter strength: {_STRENGTH}.",
        "Wed": "Easy.",
        "Thu": "Easy + 6 relaxed 20-sec pickups at the end. Then 10 min balance/ankle work.",
        "Sat": _SAT + " Wear full race gear and eat exactly what you'll eat on race day.",
        "Sun": "Easy, hilly if your body is okay with it.",
    },
    "taper": {
        "Tue": "4 short hills. Last strength session: light, ~10 min.",
        "Wed": "Easy.",
        "Thu": "Easy + 4 pickups. Then 5 min balance/ankle work.",
        "Sat": "Hilly trail, same habits as before, just shorter.",
        "Sun": "Easy.",
    },
}


def phase(n):
    return "build" if n <= 5 else "peak" if n <= 9 else "taper"


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

# Self-check: every tracked week's required runs land within 0.5 mi over its target.
for _n, _mon, _target, _days in PLAN:
    if _target is not None:
        _sum = sum(v for v in _days.values() if v != OPT)
        assert 0 <= _sum - _target <= 0.5, f"week {_n} plans {_sum} mi, target {_target}"


def garmin_login():
    from garminconnect import Garmin, GarminConnectAuthenticationError
    try:
        g = Garmin()
        g.login(TOKENS)  # uses the saved login key
        return g
    except GarminConnectAuthenticationError:
        pass  # no saved key yet, or it expired
    print("Garmin sign-in needed (your password is used once and not saved).")
    g = Garmin(input("Garmin email: "), getpass.getpass("Garmin password: "),
               prompt_mfa=lambda: input("Garmin 2-factor code: "))
    g.login(TOKENS)
    return g


def fetch_garmin(start, end):
    """Total running miles per date, and the dates with a Cardio (strength) activity, from Garmin."""
    miles, cardio = {}, set()
    for a in garmin_login().get_activities_by_date(start.isoformat(), end.isoformat()):
        kind = (a.get("activityType") or {}).get("typeKey", "")
        day = dt.date.fromisoformat(a["startTimeLocal"][:10])
        if "cardio" in kind:  # Garmin's "Cardio" is typeKey indoor_cardio
            cardio.add(day)
        elif "running" in kind:
            miles[day] = miles.get(day, 0) + (a.get("distance") or 0) / 1609.344
    return miles, cardio


def fmt(x):
    return f"{x:.1f}".rstrip("0").rstrip(".")


def ceil_half(x):
    return max(math.ceil(x * 2 - 1e-9) / 2, 0)  # 1e-9: float noise must not bump 8.0 to 8.5


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
    dyn["Sun"] = max(ceil_half(0.75 * sat), ceil_half(target - before_sun))
    return dyn


# Self-check: an untouched week's Dynamic Plan equals its Initial Plan (no drift).
for _n, _mon, _target, _days in PLAN:
    if _target is not None:
        assert dynamic_plan(_mon, _target, _days, {}, _mon) == _days, f"week {_n} drifts"

# Self-check: the worked examples from planning.
_wk1 = PLAN[1][1:]
_ex = {D(2026, 11, 3): 7, D(2026, 11, 4): 2}
assert dynamic_plan(*_wk1, _ex, D(2026, 11, 5))["Thu"] == 2
_ex |= {D(2026, 11, 5): 2, D(2026, 11, 7): 9}
assert dynamic_plan(*_wk1, _ex, D(2026, 11, 8))["Sun"] == 10
assert dynamic_plan(*_wk1, {D(2026, 11, 3): 7, D(2026, 11, 4): 5}, D(2026, 11, 5))["Thu"] == 0  # block done -> rest


def week_table(mon, days, target, miles, cardio, today):
    """Days as columns; rows are Initial Plan, Strength, Dynamic Plan, Garmin, and status."""
    dyn = dynamic_plan(mon, target, days, miles, today)
    head, plan_row, dyn_row, got_row, status_row, str_row = [], [], [], [], [], []
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
        head.append(f"<th{c}>{name}<br><small>{day:%b %-d}</small></th>")
        plan_row.append(f"<td{c}>{txt(days.get(name))}</td>")
        dyn_row.append(f"<td{c}>{txt(plan) if plan != days.get(name) else ''}</td>")
        got_row.append(f"<td{c}>{fmt(got) + ' mi' if got else '&ndash;'}</td>")
        status_row.append(f"<td{c}>{status}</td>")
        str_row.append(f"<td{c}>{strength_status(day, name in STRENGTH_DAYS, cardio, today)}</td>")
    return (f"<table><thead><tr><th></th>{''.join(head)}</tr></thead><tbody>"
            f"<tr><th>Initial Plan</th>{''.join(plan_row)}</tr>"
            f"<tr><th>Strength</th>{''.join(str_row)}</tr>"
            f"<tr><th>Dynamic Plan</th>{''.join(dyn_row)}</tr>"
            f"<tr><th>Garmin</th>{''.join(got_row)}</tr>"
            f"<tr><th></th>{''.join(status_row)}</tr>"
            "</tbody></table>")


def week_section(week, miles, cardio, today, current):
    n, mon, target, days = week
    back = "" if current is None or week == current else f'<p><a href="#wk-{current[0]}">&larr; back to this week</a></p>'
    title = f"Week {n} &middot; {mon:%b %-d} &ndash; {mon + dt.timedelta(6):%b %-d}"
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
{week_table(mon, days, target, miles, cardio, today)}
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
            label = "Rest" if n == 6 else "Race"
            tip = f"Week {n} ({mon:%b %-d}): {label.lower()} week"
            mark.append(f'<text x="{cx}" y="{base - 8}" class="axis" text-anchor="middle">{label}</text>')
        else:
            done = sum(miles.get(mon + dt.timedelta(i), 0) for i in range(7))
            tip = f"Week {n} ({mon:%b %-d}): {fmt(done)} of {target} mi"
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


def render(miles, cardio, today):
    mon = max(today - dt.timedelta(today.weekday()), PLAN[0][1])
    current = next((w for w in PLAN if w[1] == mon), None) if today < RACE else None
    to_race = (RACE - today).days

    if today > RACE:
        body = "<p class='big'>Race is done. Nice work.</p>"
    elif today == RACE:
        body = "<p class='big'>Race day! Go get it.</p>"
    else:
        body = ""
    body += "\n".join(week_section(w, miles, cardio, today, current) for w in PLAN)
    default = f"wk-{current[0]}" if current else ""

    countdown = f"{to_race} days to race day (Sat Jan 16) &middot; " if to_race > 0 else ""
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
</style></head><body>
<h1>Training Tracker</h1>
<p class="sub">{countdown}updated {dt.datetime.now():%a %b %-d, %-I:%M %p}</p>
{body}
<h2>All weeks</h2>
<p class="legend"><span class="sw"></span> Actual miles <span class="tk"></span> Target &middot; click a week to see its days</p>
{chart(miles)}
<script>
function show() {{
  const id = location.hash.slice(1) || "{default}";
  document.querySelectorAll(".week").forEach(s => s.hidden = s.id !== id);
}}
addEventListener("hashchange", show); show();
</script>
</body></html>"""


def main():
    today = dt.date.today()
    start = PLAN[0][1]
    miles, cardio = fetch_garmin(start, today) if today >= start else ({}, set())
    if today < start:
        garmin_login()  # still sign in, so first-time setup happens now
    OUT.write_text(render(miles, cardio, today))
    webbrowser.open(OUT.as_uri())
    print(f"Opened {OUT}")


if __name__ == "__main__":
    main()
