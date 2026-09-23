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


# ---------- proposals from tracking data
def _holder(frame: dict, pad: float = 0.35) -> int | None:
    """Track id of the person whose (padded) box contains the ball centre, or None."""
    b = frame.get("b")
    if not b:
        return None
    bx, by = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    best = None
    for p in frame["p"]:
        tid, x1, y1, x2, y2 = p[0], p[1], p[2], p[3], p[4]
        w, h = x2 - x1, y2 - y1
        if x1 - pad * w <= bx <= x2 + pad * w and y1 - pad * h <= by <= y2 + 0.1 * h:
            d = math.hypot(bx - (x1 + x2) / 2, by - (y1 + y2) / 2)
            if best is None or d < best[0]:
                best = (d, tid)
    return best[1] if best else None


def propose(frames: list[dict], t: float, H, dist, end: str | None, before: float = 3.5, after: float = 0.8) -> dict:
    """Guess the shooter and the release moment for a shot logged at video time t.

    Looks at tracked frames in [t-before, t+after]. Release = the last moment before t+after at which
    someone was holding the ball (ball centre inside their padded box) and then the ball left the box
    (went up / away). Shooter = that holder. Falls back to the nearest player to the ball if the ball
    is only seen airborne; no guess if the ball is never detected.
    Returns players at the chosen frame (foot points, court coords), the ball trail and the guess.
    """
    import bisect
    ts = [f["t"] for f in frames]
    i0, i1 = bisect.bisect_left(ts, t - before), bisect.bisect_right(ts, t + after)
    win = frames[i0:i1]
    if not win:
        return {"frame_t": None, "players": [], "ball": [], "guess_id": None, "release_t": None, "how": "no tracking data here"}

    holders = [(f["t"], _holder(f)) for f in win]
    release_t, guess, how = None, None, None
    # last transition holder -> not holder (ball airborne for >= 2 consecutive frames)
    for i in range(len(holders) - 2, -1, -1):
        tid = holders[i][1]
        if tid is None:
            continue
        if all(h[1] != tid for h in holders[i + 1:i + 3]) and any(f.get("b") for f in win[i + 1:i + 3]):
            release_t, guess, how = holders[i][0], tid, "ball left this player's hands"
            break
    if guess is None:
        # nearest player to the ball at the last frame with a ball (before t+after)
        for f in reversed(win):
            if f.get("b") and f["p"]:
                bx, by = (f["b"][0] + f["b"][2]) / 2, (f["b"][1] + f["b"][3]) / 2
                p = min(f["p"], key=lambda p: math.hypot(bx - (p[1] + p[3]) / 2, by - (p[2] + p[4]) / 2))
                release_t, guess, how = f["t"], p[0], "closest player to the ball"
                break
    frame_t = release_t if release_t is not None else max(ts[i0], t - 1.5)
    fr = win[min(range(len(win)), key=lambda i: abs(win[i]["t"] - frame_t))]

    players = []
    for p in fr["p"]:
        fx, fy = court.foot_point(p[1:5])
        item = {"id": p[0], "box": p[1:5], "foot": [round(fx, 1), round(fy, 1)], "guess": p[0] == guess}
        if H:
            cx, cy = court.to_court(H, fx, fy, dist)
            item["court"] = [round(cx, 2), round(cy, 2)]
            item["on"] = court.on_court(cx, cy)
            if end:  # attacking half only is a weak prior; report it, let the UI decide
                hx, _ = to_half(cx, cy, end)
                item["attacking_half"] = hx < court.LENGTH / 2
        players.append(item)
    ball = [[f["t"], round((f["b"][0] + f["b"][2]) / 2, 1), round((f["b"][1] + f["b"][3]) / 2, 1)] for f in win if f.get("b")]
    return {"frame_t": fr["t"], "players": players, "ball": ball, "guess_id": guess, "release_t": release_t,
            "how": how or "ball not detected — no guess"}
