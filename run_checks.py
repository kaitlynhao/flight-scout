"""Checks every trip on the watchlist, then sends the report and/or urgent alerts to your phone.

GitHub Actions runs this every 3 hours from 6 AM to midnight (Eastern).
    python run_checks.py            -> auto: report at 9 AM and 6 PM, alerts at every check
    python run_checks.py --report   -> force a report now (for demos)

URGENT ALERT RULE - a trip triggers an alert when it:
    1) passes all your limits (trips beyond your leniency are hidden, so they never alert), AND
    2) costs at or below your target price, OR scores at or above your "great match" score, AND
    3) is new: you haven't been alerted about it yet, or its price dropped $10+ since the last alert.
"""
import argparse
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import flights
import notify
from scout import SHOW_TOP_REPORT, find_trips, pick_top
from usage import BudgetError, get_usage

HERE = os.path.dirname(os.path.abspath(__file__))
WATCHLIST = os.path.join(HERE, "watchlist.json")
ALERT_STATE = os.path.join(HERE, "alert_state.json")   # remembers what you were already alerted about
TIMEZONE = ZoneInfo("America/New_York")
REPORT_HOURS = (9, 18)        # 9 AM and 6 PM Eastern
PRICE_DROP_REALERT = 10       # alert again about the same trip if it got this much cheaper
ALERT_SHOW = 3                # how many matching trips to list in one alert


def _load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def _save(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _clock(dt):
    h, m = map(int, dt.split(" ")[1].split(":"))
    return f"{(h % 12) or 12}:{m:02d}{'am' if h < 12 else 'pm'}"


def _date(d):
    return datetime.strptime(d, "%Y-%m-%d").strftime("%b %-d")


def trip_title(t):
    arrow = "⇄" if t.get("return_date") else "→"
    dates = _date(t["depart_date"]) + (f"–{_date(t['return_date'])}" if t.get("return_date") else "")
    return f"{t['origin']} {arrow} {t['destination']} · {dates}"


def _times(n):
    return f"+{n} other time{'s' if n != 1 else ''}, same price"


def card_lines(card, number=None):
    """A few short lines per option, for phone notifications.
    Every time listed in one option has the SAME price and score (that's how options are grouped);
    different prices always show up as separate options."""
    pair = card["pairs"][0]
    ticket = {"separate": "2 separate tickets", "round_trip": "1 round-trip ticket", "one_way": "one-way"}[card["ticket"]]
    head = f"{number}. " if number else ""
    lines = [f"{head}{card['score']}/100 · ${card['price']} · {ticket}"]

    outs = sorted({p["out"]["depart"] for p in card["pairs"]})
    out = pair["out"]
    extra = f" ({_times(len(outs) - 1)})" if len(outs) > 1 else ""
    lines.append(f"   Departure: {_date(out['depart'][:10])} · {', '.join(out['airlines'])} {_clock(out['depart'])}{extra}")

    if card["round_trip"]:
        backs = sorted({r["depart"] for p in card["pairs"] for r in p["returns"]})
        back = pair["returns"][0]
        extra = f" ({_times(len(backs) - 1)})" if len(backs) > 1 else ""
        lines.append(f"   Return: {_date(back['depart'][:10])} · {', '.join(back['airlines'])} {_clock(back['depart'])}{extra}")
    return lines


def _alert_key(card):
    pair = card["pairs"][0]
    ids = [tuple(pair["out"].get("flight_numbers") or ()), pair["out"]["depart"]]
    if card["round_trip"]:
        r = pair["returns"][0]
        ids += [tuple(r.get("flight_numbers") or ()), r["depart"]]
    return json.dumps([card["ticket"], ids])


def find_alerts(watch, result, state):
    """Cards that should trigger an urgent alert (see the rule at the top of this file)."""
    prefs = watch["prefs"]
    seen = state.get(watch["id"], {})
    hits = []
    for card in result["cards"]:  # only cards that passed your limits are in here
        good_price = card["price"] is not None and card["price"] <= prefs["target_price"]
        great = card["score"] >= prefs.get("great_score", 90)
        if not (good_price or great):
            continue
        key = _alert_key(card)
        last = seen.get(key)
        if last is None or card["price"] <= last - PRICE_DROP_REALERT:
            hits.append((card, good_price, great))
    return hits


def remember_alert(watch, card, state):
    """Only trips you were actually shown get remembered (so the rest can still alert later)."""
    state.setdefault(watch["id"], {})[_alert_key(card)] = card["price"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="send a report now, whatever the time")
    args = ap.parse_args()

    now = datetime.now(TIMEZONE)
    # (also counts a check that GitHub started up to ~1 hour late)
    send_report = args.report or now.hour in REPORT_HOURS or (now.hour - 1) in REPORT_HOURS
    watchlist = _load(WATCHLIST, {"trips": []})
    state = _load(ALERT_STATE, {})
    trips = [w for w in watchlist["trips"] if w.get("active", True)]
    print(f"{now:%a %b %-d, %-I:%M %p} ET · {len(trips)} trip(s) to check · report: {send_report}")

    report_parts, alert_parts, errors = [], [], []
    for w in trips:
        try:
            result = find_trips(w["trip"], w["prefs"])
        except BudgetError as e:
            print(f"Budget limit: {e}")  # details stay in the run log only
            errors.append(f"Skipped {trip_title(w['trip'])}: Flight Scout has paused checks for now.")
            continue
        except Exception as e:  # one broken trip shouldn't stop the others
            errors.append(f"Couldn't check {trip_title(w['trip'])}: {e}")
            continue

        top = pick_top(result, SHOW_TOP_REPORT)
        title = trip_title(w["trip"])
        if top:
            lines = ["\n".join(card_lines(c, i)) for i, c in enumerate(top, 1)]
        else:
            lines = [f"No trips fit your limits right now ({len(result['hidden'])} slightly outside)."]
        report_parts.append(f"✈️ {title}\n\n" + "\n\n".join(lines))

        for card, good_price, great in find_alerts(w, result, state)[:ALERT_SHOW]:
            remember_alert(w, card, state)
            why = []
            if good_price:
                why.append(f"under your ${w['prefs']['target_price']} target")
            if great:
                why.append(f"scores {card['score']}")
            alert_parts.append(f"{title}\n" + "\n".join(card_lines(card)) + f"\n   → {' & '.join(why)}")

    try:
        u = get_usage()
        usage_line = f"Searches this run: {flights.searches_this_run} · month: {u['used']}/{u['plan_total']}"
    except Exception:
        usage_line = f"Searches this run: {flights.searches_this_run}"

    if alert_parts:
        notify.send("🚨 Flight Scout: deal alert", "\n\n".join(alert_parts),
                    urgent=True, tags=["rotating_light"])
        print(f"Sent urgent alert ({len(alert_parts)} trip(s)).")
    if send_report and report_parts:
        part_of_day = "morning" if now.hour < 12 else "afternoon"
        notify.send(f"Flight Scout: {part_of_day} report", "\n\n".join(report_parts),
                    tags=["airplane"])
        print("Sent report.")
    if errors:
        notify.send("Flight Scout: problem", "\n".join(errors), tags=["warning"])
        print("Problems:", *errors, sep="\n  ")

    _save(ALERT_STATE, state)
    print(usage_line)  # shown in the run log only, not on your phone


if __name__ == "__main__":
    main()
