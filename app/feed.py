"""Fetch and normalise match data from the NIF "terminlister" API (kamper.basket.no).

Endpoints used:
  ta/Match?matchId=..                 match info, team ids/names, result
  basketball/match/Players/<id>       per-player box score (scores, fouls; other stats when the scorer records them)
  ta/MatchIncidents?matchId=..        shots / fouls / timeouts with period and period clock

The incident `time` field is the period clock as an integer MMSS *elapsed* since the period
started (121 -> 1:21, 1000 -> 10:00 = end of a 10 minute period).
"""
from __future__ import annotations

import httpx

API_BASE = "https://sf14-terminlister-prod-app.azurewebsites.net/"
TIMEOUT = 30.0

# Norwegian scorer labels -> English
SUBTYPE_LABEL = {
    "1p": "Free throw made",
    "1p bom": "Free throw missed",
    "2p": "2-pt made",
    "3p": "3-pt made",
    "2p bom": "2-pt missed",
    "3p bom": "3-pt missed",
    "Defensiv foul": "Foul",
    "Defensiv foul m. bestrafning": "Foul (free throws)",
    "Offensiv foul": "Offensive foul",
    "Teknisk foul": "Technical foul",
    "Teknisk foul (lagledelse)": "Technical foul (bench)",
    "Usportslig foul": "Unsportsmanlike foul",
    "Diskvalifiserende foul": "Disqualifying foul",
}
TYPE_KIND = {"Skudd": "shot", "Foul/utvisning": "foul", "Timeout": "timeout"}


def api_get(path_and_query: str):
    r = httpx.get(API_BASE + path_and_query, timeout=TIMEOUT, headers={"Accept": "application/json"})
    r.raise_for_status()
    return r.json()


def fetch_raw(match_id: int) -> dict:
    return {
        "match": api_get(f"ta/Match?matchId={match_id}"),
        "players": api_get(f"basketball/match/Players/{match_id}"),
        "incidents": api_get(f"ta/MatchIncidents?matchId={match_id}"),
    }


def clock_seconds(t) -> int:
    """MMSS integer -> elapsed seconds in the period."""
    t = int(t or 0)
    return (t // 100) * 60 + (t % 100)


def clock_str(sec: int) -> str:
    return f"{sec // 60}:{sec % 60:02d}"


def _team_side(match: dict, org_id) -> str | None:
    if org_id is None:
        return None
    if str(org_id) == str(match.get("hometeamId")):
        return "home"
    if str(org_id) == str(match.get("awayteamId")):
        return "away"
    return None


def _team_name(match: dict, side: str) -> str:
    return match.get(f"{side}teamOverriddenName") or match.get(f"{side}teamOrgName") or side


def normalize(raw: dict) -> dict:
    match, players, inc = raw["match"], raw["players"], raw["incidents"]
    result = match.get("matchResult") or {}

    # periods, numbered in the order the API lists them (1..4, then overtime)
    periods = []
    period_no = {}
    for i, p in enumerate(inc.get("matchPeriodResults") or [], start=1):
        pid = p["partialResultTypeId"]
        period_no[pid] = i
        periods.append({"id": pid, "n": i, "label": f"Q{i}" if i <= 4 else f"OT{i - 4}",
                        "home": p.get("homeGoals", 0), "away": p.get("awayGoals", 0)})

    # players
    plist = []
    for p in players:
        side = _team_side(match, p.get("orgId"))
        if not side:
            continue
        ft_m, ft_x = p.get("p1Sum") or 0, p.get("p1MissSum") or 0
        p2_m, p2_x = p.get("p2Sum") or 0, p.get("p2MissSum") or 0
        p3_m, p3_x = p.get("p3Sum") or 0, p.get("p3MissSum") or 0
        plist.append({
            "personId": str(p["personId"]), "team": side,
            "no": "" if p.get("shirtNo") is None else str(p["shirtNo"]),
            "first": (p.get("firstName") or "").strip(), "last": (p.get("lastName") or "").strip(),
            "name": ", ".join(x for x in [(p.get("lastName") or "").strip(), (p.get("firstName") or "").strip()] if x),
            "pts": p.get("points") or 0,
            "ft_m": ft_m, "ft_a": ft_m + ft_x,
            "fg2_m": p2_m, "fg2_a": p2_m + p2_x,
            "fg3_m": p3_m, "fg3_a": p3_m + p3_x,
            "fouls": p.get("fouls") or 0,
            "secs": p.get("totalSeconds") or 0,
            "oreb": p.get("offRebSum") or 0, "dreb": p.get("defRebSum") or 0,
            "ast": p.get("assists") or 0, "stl": p.get("stSum") or 0,
            "to": p.get("toSum") or 0, "blk": p.get("blSum") or 0,
        })
    by_id = {p["personId"]: p for p in plist}

    # incidents: parent rows carry the event, child rows carry the player
    rows = inc.get("matchIncidents") or []
    children = {}
    for r in rows:
        if r.get("parentId") is not None:
            children.setdefault(r["parentId"], []).append(r)
    events = []
    for r in rows:
        if r.get("parentId") is not None:
            continue
        kind = TYPE_KIND.get(r.get("incidentType"), "other")
        sub = r.get("incidentSubType")
        side = "home" if r.get("team") == "H" else "away" if r.get("team") == "B" else None
        pid = None
        for c in children.get(r["matchIncidentId"], []):
            if c.get("personId") is not None:
                pid = str(c["personId"])
        pl = by_id.get(pid)
        pts = 0
        made = None
        if kind == "shot":
            made = "bom" not in (sub or "")
            if made:
                pts = int((sub or "0")[0]) if sub and sub[0].isdigit() else 0
        label = SUBTYPE_LABEL.get(sub, sub) if sub else ("Timeout" if kind == "timeout" else (r.get("incidentType") or "Event"))
        csec = clock_seconds(r.get("time"))
        events.append({
            "id": r["matchIncidentId"], "period": period_no.get(r.get("partialResultTypeId"), 0),
            "clock": csec, "clockStr": clock_str(csec),
            "team": side, "kind": kind, "sub": sub, "label": label,
            "points": pts, "made": made,
            "personId": pid, "playerName": pl["name"] if pl else None, "playerNo": pl["no"] if pl else None,
        })
    events.sort(key=lambda e: (e["period"], e["clock"], e["id"]))
    h = a = 0
    for e in events:
        if e["points"] and e["team"] == "home":
            h += e["points"]
        elif e["points"] and e["team"] == "away":
            a += e["points"]
        e["score"] = [h, a]

    return {
        "match": {
            "id": match.get("matchId"),
            "home": {"id": match.get("hometeamId"), "name": _team_name(match, "home")},
            "away": {"id": match.get("awayteamId"), "name": _team_name(match, "away")},
            "tournament": match.get("tournamentName"), "round": match.get("roundName"),
            "date": (match.get("matchDate") or "")[:10], "venue": match.get("activityAreaName"),
            "result": [result.get("homeGoals"), result.get("awayGoals")],
        },
        "periods": periods,
        "players": plist,
        "events": events,
        # which "extended" stats the scorer actually recorded for this match
        "tracked": {k: any(p[k] for p in plist) for k in ("secs", "oreb", "dreb", "ast", "stl", "to", "blk")},
    }


def fetch(match_id: int) -> dict:
    return normalize(fetch_raw(match_id))
