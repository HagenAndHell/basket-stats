"""Shot chart: every shot attempt (feed shots + tagged misses) with a court location.

Locations are stored per shot key in project.data["shots"]:
    key   feed event id as str, or "t<tagId>" for a tagged miss
    value {"x", "y" (court metres, full court), "px", "py" (pixel clicked, optional), "auto": bool}

Which basket a team attacks:
    project.data["ends"] = {"home_first": "L" | "R"}   (home attacks the L basket, x < 14, in the 1st half)
    Teams swap ends at half time (periods 3+, incl. overtime, use the 2nd-half end).
    If not set, it is inferred from the located field goals (majority side).

The chart uses half-court coordinates (hx, hy): the attacked basket is always at the LEFT end
(x = BASKET_X). Shots at the R basket are rotated 180 degrees: hx = L - x, hy = W - y.

Zones (FIBA):  ft | paint | mid | 3pt   +  "3pt?" mismatch flag when the location disagrees with the feed.
"""
from __future__ import annotations

import math

from . import court

# where the straight corner-3 line meets the arc (x from the baseline)
CORNER3_X = court.BASKET_X + math.sqrt(court.ARC_R ** 2 - (court.WIDTH / 2 - court.CORNER3_Y) ** 2)
FT_SPOT = (court.FT_LINE, court.WIDTH / 2)


def to_half(x: float, y: float, end: str) -> tuple[float, float]:
    """Full-court -> half-court coordinates with the attacked basket at the left."""
    if end == "R":
        return court.LENGTH - x, court.WIDTH - y
    return x, y


def from_half(hx: float, hy: float, end: str) -> tuple[float, float]:
    return to_half(hx, hy, end)  # the rotation is its own inverse


def distance(hx: float, hy: float) -> float:
    return math.hypot(hx - court.BASKET_X, hy - court.WIDTH / 2)


def is_three(hx: float, hy: float) -> bool:
    if hx <= CORNER3_X and (hy < court.CORNER3_Y or hy > court.WIDTH - court.CORNER3_Y):
        return True
    return distance(hx, hy) >= court.ARC_R


def zone(hx: float, hy: float) -> str:
    if is_three(hx, hy):
        return "3pt"
    if hx <= court.FT_LINE and abs(hy - court.WIDTH / 2) <= court.LANE_W / 2:
        return "paint"
    return "mid"


def half_of(period: int) -> int:
    return 1 if period <= 2 else 2


def end_for(ends: dict | None, team: str, period: int) -> str | None:
    """'L' or 'R' basket attacked by `team` in `period`, or None if unknown."""
    hf = (ends or {}).get("home_first")
    if hf not in ("L", "R"):
        return None
    e = hf if team == "home" else ("R" if hf == "L" else "L")
    if half_of(period) == 2:
        e = "R" if e == "L" else "L"
    return e


def infer_ends(attempts: list[dict], located: dict) -> dict | None:
    """Majority vote: located field goals of the home team in the 1st half (or 2nd half, mirrored)."""
    votes = 0.0
    for a in attempts:
        loc = located.get(a["key"])
        if not loc or a["shot"] == "ft" or not a.get("team"):
            continue
        left = loc["x"] < court.LENGTH / 2
        home = a["team"] == "home"
        first = half_of(a["period"]) == 1
        # home, first half, left  -> home_first = L
        votes += 1 if (left == home) == first else -1
    if votes == 0:
        return None
    return {"home_first": "L" if votes > 0 else "R", "inferred": True}


def attempts_from(feed: dict | None, tags: list[dict]) -> list[dict]:
    """All shot attempts, in game order: feed shot events + tagged misses (fga2/fga3)."""
    out = []
    for e in (feed or {}).get("events", []):
        if e.get("kind") != "shot":
            continue
        sub = (e.get("sub") or "")
        shot = "ft" if sub.startswith("1p") else "3" if sub.startswith("3p") else "2"
        out.append({"key": str(e["id"]), "src": "feed", "period": e["period"], "clock": e["clock"], "clockStr": e.get("clockStr"),
                    "team": e.get("team"), "personId": e.get("personId"), "playerName": e.get("playerName"), "playerNo": e.get("playerNo"),
                    "shot": shot, "made": bool(e.get("made")), "points": e.get("points") or 0, "label": e.get("label")})
    for t in tags:
        if t.get("stat") in ("fga2", "fga3"):
            out.append({"key": f"t{t['id']}", "src": "tag", "period": t.get("period") or 0, "clock": t.get("clock") or 0,
                        "clockStr": None, "team": t.get("team"), "personId": t.get("personId"), "playerName": None, "playerNo": None,
                        "shot": t["stat"][-1], "made": False, "points": 0, "label": "2-pt missed" if t["stat"] == "fga2" else "3-pt missed",
                        "video": t.get("video")})
    out.sort(key=lambda a: (a["period"], a["clock"]))
    return out


def with_locations(attempts: list[dict], located: dict, ends: dict | None) -> list[dict]:
    """Attach x,y (full court), hx,hy (half court), zone, dist and consistency flag to every attempt."""
    for a in attempts:
        end = end_for(ends, a["team"], a["period"]) if a.get("team") else None
        a["end"] = end
        loc = located.get(a["key"])
        a["located"] = bool(loc)
        a["auto"] = False
        if not loc and a["shot"] == "ft" and end:
            loc = {"x": from_half(*FT_SPOT, end)[0], "y": from_half(*FT_SPOT, end)[1]}
            a["auto"] = True
        if not loc:
            a.update({"x": None, "y": None, "hx": None, "hy": None, "zone": None, "dist": None, "mismatch": False})
            continue
        x, y = loc["x"], loc["y"]
        if end is None:  # unknown ends: guess from the side of the court the shot was taken
            end = "L" if x < court.LENGTH / 2 else "R"
            a["end"] = end
        hx, hy = to_half(x, y, end)
        z = zone(hx, hy)
        a.update({"x": x, "y": y, "hx": round(hx, 2), "hy": round(hy, 2), "zone": "ft" if a["shot"] == "ft" else z,
                  "dist": round(distance(hx, hy), 2), "px": loc.get("px"), "py": loc.get("py")})
        a["mismatch"] = a["shot"] != "ft" and ((z == "3pt") != (a["shot"] == "3"))
    return attempts


def summary(attempts: list[dict]) -> dict:
    """Made/attempted per zone for the given (already filtered) attempts."""
    zones = {z: {"made": 0, "att": 0} for z in ("paint", "mid", "3pt", "ft")}
    unl = 0
    for a in attempts:
        z = a.get("zone")
        if not z:
            unl += 1
            continue
        zones[z]["att"] += 1
        zones[z]["made"] += 1 if a["made"] else 0
    for z in zones.values():
        z["pct"] = round(100 * z["made"] / z["att"]) if z["att"] else None
    fg = {"made": sum(zones[z]["made"] for z in ("paint", "mid", "3pt")), "att": sum(zones[z]["att"] for z in ("paint", "mid", "3pt"))}
    fg["pct"] = round(100 * fg["made"] / fg["att"]) if fg["att"] else None
    return {"zones": zones, "fg": fg, "unlocated": unl}
