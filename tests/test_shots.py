"""Shot chart: zones, ends, FT auto-placement, API."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import court, shots

FIX = Path(__file__).parent / "fixtures"
W = court.WIDTH


def test_zones_half_court():
    assert shots.zone(1.0, W / 2) == "paint"            # under the basket
    assert shots.zone(5.5, W / 2 + 2.0) == "paint"      # inside the key near the FT line
    assert shots.zone(6.0, W / 2 + 4.0) == "mid"        # baseline mid-range, inside the corner-3 line
    assert shots.zone(1.0, 0.5) == "3pt"                # corner 3
    assert shots.zone(court.BASKET_X + 7.0, W / 2) == "3pt"   # top of the arc
    assert shots.zone(court.BASKET_X + 6.5, W / 2) == "mid"   # just inside the arc
    assert shots.zone(20.0, W / 2) == "3pt"             # heave from the far half


def test_half_court_rotation():
    assert shots.to_half(3.0, 4.0, "L") == (3.0, 4.0)
    hx, hy = shots.to_half(25.0, 11.0, "R")
    assert (hx, hy) == pytest.approx((3.0, 4.0))
    assert shots.from_half(hx, hy, "R") == pytest.approx((25.0, 11.0))


def test_end_for_swaps_at_half_time():
    ends = {"home_first": "L"}
    assert shots.end_for(ends, "home", 1) == "L"
    assert shots.end_for(ends, "away", 2) == "R"
    assert shots.end_for(ends, "home", 3) == "R"
    assert shots.end_for(ends, "away", 4) == "L"
    assert shots.end_for(ends, "home", 5) == "R"      # overtime keeps 2nd-half ends
    assert shots.end_for(None, "home", 1) is None


def test_infer_ends_majority():
    att = [{"key": "1", "shot": "2", "team": "home", "period": 1},
           {"key": "2", "shot": "3", "team": "away", "period": 1},
           {"key": "3", "shot": "2", "team": "home", "period": 3},
           {"key": "4", "shot": "ft", "team": "home", "period": 1}]
    loc = {"1": {"x": 3, "y": 7}, "2": {"x": 25, "y": 7}, "3": {"x": 26, "y": 7}, "4": {"x": 26, "y": 7}}
    assert shots.infer_ends(att, loc)["home_first"] == "L"
    assert shots.infer_ends(att, {}) is None


def test_with_locations_ft_auto_and_mismatch():
    att = [{"key": "1", "shot": "ft", "team": "home", "period": 3, "made": True},
           {"key": "2", "shot": "2", "team": "away", "period": 1, "made": False},
           {"key": "3", "shot": "3", "team": "away", "period": 1, "made": True}]
    loc = {"2": {"x": 25.0, "y": 0.5}, "3": {"x": 21.5, "y": 7.5}}
    out = shots.with_locations(att, loc, {"home_first": "L"})
    ft = out[0]
    assert ft["auto"] and ft["zone"] == "ft" and ft["end"] == "R"
    assert (ft["x"], ft["y"]) == pytest.approx((court.LENGTH - court.FT_LINE, W / 2))
    assert (ft["hx"], ft["hy"]) == pytest.approx((court.FT_LINE, W / 2))
    two = out[1]
    assert two["end"] == "R" and two["zone"] == "3pt" and two["mismatch"]   # corner 3 recorded as a 2
    three = out[2]
    assert three["zone"] == "mid" and three["mismatch"]                      # 4 m from the basket recorded as a 3
    s = shots.summary(out)
    assert s["zones"]["ft"] == {"made": 1, "att": 1, "pct": 100}
    assert s["fg"] == {"made": 1, "att": 2, "pct": 50}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BASKET_STATS_DATA", str(tmp_path))
    import importlib
    from app import server, feed
    importlib.reload(server)
    monkeypatch.setattr(feed, "fetch_raw", lambda mid: {
        "match": json.loads((FIX / "match_8439241.json").read_text(encoding="utf-8")),
        "players": json.loads((FIX / "players_8439241.json").read_text(encoding="utf-8")),
        "incidents": json.loads((FIX / "incidents_8439241.json").read_text(encoding="utf-8")),
    })
    return TestClient(server.app)


def test_shots_api(client):
    client.get("/api/match/8439241")
    d = client.get("/api/match/8439241/shots").json()
    att = d["attempts"]
    assert att and all(a["src"] == "feed" for a in att)
    assert d["ends"] is None and d["summary"]["unlocated"] == len(att)
    fg = next(a for a in att if a["shot"] == "2" and a["team"] == "home" and a["period"] == 1)
    # not calibrated -> pixel location rejected, metres accepted
    assert client.put(f"/api/match/8439241/shots/{fg['key']}", json={"px": 100, "py": 100}).status_code == 400
    d = client.put(f"/api/match/8439241/shots/{fg['key']}", json={"x": 3.0, "y": 7.0}).json()
    assert d["ends"] == {"home_first": "L", "inferred": True}
    a = next(a for a in d["attempts"] if a["key"] == fg["key"])
    assert a["located"] and a["zone"] == "paint" and a["end"] == "L"
    # free throws are now auto-placed at the right end
    ft = next(a for a in d["attempts"] if a["shot"] == "ft" and a["team"] == "home" and a["period"] == 1)
    assert ft["auto"] and ft["x"] == pytest.approx(court.FT_LINE)
    ft2 = next(a for a in d["attempts"] if a["shot"] == "ft" and a["team"] == "home" and a["period"] >= 3)
    assert ft2["x"] == pytest.approx(court.LENGTH - court.FT_LINE)
    # explicit ends override the inference
    d = client.put("/api/match/8439241/ends", json={"home_first": "R"}).json()
    assert d["ends"] == {"home_first": "R"}
    a = next(a for a in d["attempts"] if a["key"] == fg["key"])
    assert a["end"] == "R" and a["zone"] == "3pt" and a["mismatch"]   # 3 m from the L baseline is a heave at the R basket
    # off-court rejected, delete works
    assert client.put(f"/api/match/8439241/shots/{fg['key']}", json={"x": -5, "y": 7}).status_code == 400
    d = client.delete(f"/api/match/8439241/shots/{fg['key']}").json()
    assert not next(a for a in d["attempts"] if a["key"] == fg["key"])["located"]
    # tagged misses show up as attempts
    t = client.post("/api/match/8439241/tags", json={"video": 12.5, "stat": "fga3", "team": "away", "personId": "7759476", "period": 2, "clock": 30}).json()
    d = client.get("/api/match/8439241/shots").json()
    tag = next(a for a in d["attempts"] if a["key"] == f"t{t['id']}")
    assert tag["src"] == "tag" and tag["shot"] == "3" and not tag["made"] and tag["video"] == 12.5


def test_shot_location_from_pixels_uses_calibration(client):
    client.get("/api/match/8439241")
    # a plain 4-point calibration: pixels = 100 * metres (+ offset), 1920x1080-ish
    pts = [{"name": "corner_L_bottom", "px": 100, "py": 900}, {"name": "corner_R_bottom", "px": 2900, "py": 900},
           {"name": "corner_R_top", "px": 2900, "py": -600}, {"name": "corner_L_top", "px": 100, "py": -600}]
    r = client.post("/api/match/8439241/calibration", json={"points": pts, "width": 3000, "height": 1000, "fit_distortion": False})
    assert r.status_code == 200 and r.json()["H"]
    d = client.get("/api/match/8439241/shots").json()
    key = d["attempts"][0]["key"]
    d = client.put(f"/api/match/8439241/shots/{key}", json={"px": 100 + 600, "py": 900 - 750}).json()
    a = next(a for a in d["attempts"] if a["key"] == key)
    assert (a["x"], a["y"]) == pytest.approx((6.0, 7.5), abs=0.05)
    assert a["px"] == 700


def _frames_with_shot():
    """Synthetic 10 fps tracking: player 1 stands at x=500, holds the ball until t=5.0, ball flies up/right after."""
    frames = []
    for i in range(80):
        t = round(i / 10, 1)
        p = [[1, 480, 300, 520, 420, 0.9], [2, 800, 310, 840, 430, 0.9]]
        if t <= 5.0:
            b = [495, 320, 505, 330, 0.8]                       # in player 1's box
        elif t <= 6.0:
            k = (t - 5.0) * 10
            b = [500 + 40 * k, 300 - 60 * k, 510 + 40 * k, 310 - 60 * k, 0.7]   # airborne, leaving the box
        else:
            b = None
        frames.append({"t": t, "f": i, "p": p, "b": b})
    return frames


def test_holder_and_release_detection():
    fr = _frames_with_shot()
    assert shots._holder(fr[40]) == 1          # t=4.0
    assert shots._holder(fr[70]) is None       # t=7.0 no ball
    # scorer logged the shot 1.5 s after the release
    p = shots.propose(fr, 6.5, None, None, None)
    assert p["guess_id"] == 1 and p["release_t"] == pytest.approx(5.0) and p["frame_t"] == pytest.approx(5.0)
    assert p["how"].startswith("ball left")
    g = [x for x in p["players"] if x["guess"]]
    assert len(g) == 1 and g[0]["foot"] == [500.0, 420.0]
    assert p["ball"] and p["ball"][0][0] == pytest.approx(3.0)   # trail starts at t-3.5


def test_propose_fallbacks():
    fr = _frames_with_shot()
    for f in fr:
        f["b"] = None
    p = shots.propose(fr, 6.5, None, None, None)
    assert p["guess_id"] is None and "no guess" in p["how"] and len(p["players"]) == 2
    assert p["frame_t"] == pytest.approx(5.0)                  # default: 1.5 s before the logged time
    assert shots.propose(fr, 50.0, None, None, None)["players"] == []
    # ball only seen airborne, never held -> closest player
    fr2 = _frames_with_shot()
    for f in fr2:
        f["b"] = [830, 200, 840, 210, 0.6] if 4.0 <= f["t"] <= 4.3 else None
    p = shots.propose(fr2, 5.5, None, None, None)
    assert p["guess_id"] == 2 and p["how"].startswith("closest")


def test_propose_api_uses_calibration_and_end(client, tmp_path, monkeypatch):
    import gzip, json as _json
    client.get("/api/match/8439241")
    tr = tmp_path / "tracks"; tr.mkdir()
    with gzip.open(tr / "8439241.jsonl.gz", "wt") as fh:
        fh.write(_json.dumps({"meta": {}}) + "\n")
        for f in _frames_with_shot():
            fh.write(_json.dumps(f) + "\n")
    pts = [{"name": "corner_L_bottom", "px": 100, "py": 900}, {"name": "corner_R_bottom", "px": 2900, "py": 900},
           {"name": "corner_R_top", "px": 2900, "py": -600}, {"name": "corner_L_top", "px": 100, "py": -600}]
    client.post("/api/match/8439241/calibration", json={"points": pts, "width": 3000, "height": 1000, "fit_distortion": False})
    key = client.get("/api/match/8439241/shots").json()["attempts"][0]["key"]
    r = client.get(f"/api/match/8439241/shots/{key}/propose", params={"t": 6.5})
    assert r.status_code == 200
    d = r.json()
    g = next(x for x in d["players"] if x["guess"])
    assert g["court"] == pytest.approx([4.0, 4.8], abs=0.05) and g["on"]
    assert client.get("/api/match/8439241/shots/nokey/propose", params={"t": 6.5}).status_code == 200
    (tr / "8439241.jsonl.gz").unlink()
    assert client.get(f"/api/match/8439241/shots/{key}/propose", params={"t": 6.5}).status_code == 404


def test_identity_votes():
    ident = {}
    shots.learn_identity(ident, 7, "111")
    shots.learn_identity(ident, 7, "222")
    assert shots.identity_of(ident, 7) is None            # tie
    shots.learn_identity(ident, 7, "111")
    assert shots.identity_of(ident, 7) == "111"
    assert shots.identity_of(ident, 8) is None


def test_propose_uses_learned_identity_without_ball():
    fr = _frames_with_shot()
    for f in fr:
        f["b"] = None
    ident = {"2": {"555": 2}}
    p = shots.propose(fr, 6.5, None, None, None, identities=ident, shooter_id="555")
    assert p["guess_id"] == 2 and "confirmed" in p["how"]
    assert next(x for x in p["players"] if x["id"] == 2)["personId"] == "555"
    assert next(x for x in p["players"] if x["id"] == 1)["personId"] is None
    # a solid ball-release guess is not overridden by identity
    fr = _frames_with_shot()
    p = shots.propose(fr, 6.5, None, None, None, identities=ident, shooter_id="555")
    assert p["guess_id"] == 1
    # unknown shooter or several candidates -> no identity guess
    assert shots.propose(fr[:1] and [dict(f, b=None) for f in fr], 6.5, None, None, None, identities=ident, shooter_id="999")["guess_id"] is None


def test_api_learns_identity_from_confirmed_shot(client, tmp_path):
    import gzip, json as _json
    client.get("/api/match/8439241")
    tr = tmp_path / "tracks"; tr.mkdir()
    with gzip.open(tr / "8439241.jsonl.gz", "wt") as fh:
        fh.write(_json.dumps({"meta": {}}) + "\n")
        for f in _frames_with_shot():
            f["b"] = None
            fh.write(_json.dumps(f) + "\n")
    pts = [{"name": "corner_L_bottom", "px": 100, "py": 900}, {"name": "corner_R_bottom", "px": 2900, "py": 900},
           {"name": "corner_R_top", "px": 2900, "py": -600}, {"name": "corner_L_top", "px": 100, "py": -600}]
    client.post("/api/match/8439241/calibration", json={"points": pts, "width": 3000, "height": 1000, "fit_distortion": False})
    att = client.get("/api/match/8439241/shots").json()["attempts"]
    shooter = next(a for a in att if a["personId"])
    same = [a for a in att if a["personId"] == shooter["personId"] and a["shot"] != "ft"]
    assert len(same) >= 2
    # confirm shot 1 with track 2 -> identity learned
    d = client.put(f"/api/match/8439241/shots/{same[0]['key']}", json={"px": 820, "py": 430, "track_id": 2}).json()
    ids = client.get("/api/match/8439241/identities").json()
    assert ids["2"]["personId"] == shooter["personId"]
    # next shot by the same player: proposal names track 2 even without a ball
    p = client.get(f"/api/match/8439241/shots/{same[1]['key']}/propose", params={"t": 6.5}).json()
    assert p["guess_id"] == 2 and next(x for x in p["players"] if x["id"] == 2)["personId"] == shooter["personId"]
    # positions endpoint carries the identity for the 2D court
    pos = client.get("/api/match/8439241/positions", params={"t0": 4.0, "t1": 4.1}).json()
    p2 = next(x for x in pos["frames"][0]["p"] if x["id"] == 2)
    assert p2["personId"] == shooter["personId"] and "personId" not in next(x for x in pos["frames"][0]["p"] if x["id"] == 1)
    # location without track_id learns nothing
    other = next(a for a in att if a["personId"] and a["personId"] != shooter["personId"] and a["shot"] != "ft")
    client.put(f"/api/match/8439241/shots/{other['key']}", json={"px": 500, "py": 420})
    assert client.get("/api/match/8439241/identities").json() == ids
