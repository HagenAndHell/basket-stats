import json
from pathlib import Path

import pytest

from app import feed
from app.project import Project

FIX = Path(__file__).parent / "fixtures"


def raw():
    return {
        "match": json.loads((FIX / "match_8439241.json").read_text(encoding="utf-8")),
        "players": json.loads((FIX / "players_8439241.json").read_text(encoding="utf-8")),
        "incidents": json.loads((FIX / "incidents_8439241.json").read_text(encoding="utf-8")),
    }


@pytest.fixture
def data():
    return feed.normalize(raw())


def test_clock_decoding():
    assert feed.clock_seconds(121) == 81
    assert feed.clock_seconds(1000) == 600
    assert feed.clock_seconds(3) == 3
    assert feed.clock_str(81) == "1:21"


def test_match_info(data):
    m = data["match"]
    assert m["id"] == 8439241
    assert m["home"]["name"] == "Oppsal 1"
    assert m["away"]["name"] == "Bergkameratene - Basket"
    assert m["result"] == [116, 70]
    assert [p["label"] for p in data["periods"]] == ["Q1", "Q2", "Q3", "Q4"]


def test_players(data):
    assert len(data["players"]) == 19
    p = next(p for p in data["players"] if p["no"] == "33" and p["team"] == "home")
    assert p["name"].startswith("Runcie, ")
    assert (p["pts"], p["ft_m"], p["ft_a"], p["fg2_m"], p["fg3_m"], p["fouls"]) == (23, 3, 3, 7, 2, 1)
    assert data["tracked"] == {k: False for k in ("secs", "oreb", "dreb", "ast", "stl", "to", "blk")}


def test_events_scoring_matches_result(data):
    ev = data["events"]
    assert ev == sorted(ev, key=lambda e: (e["period"], e["clock"], e["id"]))
    assert ev[-1]["score"] == [116, 70]
    # per-period totals match the API period results
    for p in data["periods"]:
        pe = [e for e in ev if e["period"] == p["n"]]
        h = sum(e["points"] for e in pe if e["team"] == "home")
        a = sum(e["points"] for e in pe if e["team"] == "away")
        assert (h, a) == (p["home"], p["away"]), p["label"]


def test_events_have_players_and_labels(data):
    shots = [e for e in data["events"] if e["kind"] == "shot"]
    assert len(shots) == 104
    assert all(e["personId"] for e in shots)
    assert all(e["playerName"] for e in shots)
    assert {e["label"] for e in shots} >= {"2-pt made", "3-pt made", "Free throw made", "Free throw missed"}
    misses = [e for e in shots if e["made"] is False]
    assert len(misses) == 8 and all(e["points"] == 0 for e in misses)
    fouls = [e for e in data["events"] if e["kind"] == "foul"]
    assert len(fouls) == 23
    # player fouls in the feed sum to the box score
    from collections import Counter
    c = Counter(e["personId"] for e in fouls if e["personId"])
    for p in data["players"]:
        assert c.get(p["personId"], 0) == p["fouls"], p["name"]


def test_sync_single_anchor(tmp_path):
    pr = Project.load_or_create(tmp_path, 1)
    pr.add_anchor(1, 0, 100.0)
    assert pr.clock_to_video(1, 81) == 181.0
    assert pr.clock_to_video(2, 0) is None
    assert pr.video_to_period_clock(150.0) == (1, 50.0)


def test_sync_two_anchors_interpolates(tmp_path):
    pr = Project.load_or_create(tmp_path, 1)
    pr.add_anchor(1, 0, 100.0)
    pr.add_anchor(1, 600, 100.0 + 900.0)  # 10 min of clock took 15 min of video
    assert pr.clock_to_video(1, 300) == pytest.approx(100.0 + 450.0)
    assert pr.clock_to_video(1, 700) == pytest.approx(100.0 + 1050.0)  # extrapolate at same rate
    p, c = pr.video_to_period_clock(550.0)
    assert (p, c) == (1, pytest.approx(300.0))
    # replacing an anchor at the same clock
    pr.add_anchor(1, 600, 1000.0)
    assert len(pr.anchors(1)) == 2 and pr.anchors(1)[-1]["video"] == 1000.0
    pr.remove_anchor(1, 600)
    assert len(pr.anchors(1)) == 1


def test_project_persistence_and_tags(tmp_path):
    pr = Project.load_or_create(tmp_path, 42)
    t = pr.add_tag({"video": 10.0, "stat": "oreb", "personId": "1"})
    pr.add_tag({"video": 12.0, "stat": "ast", "personId": "2"})
    pr.save()
    pr2 = Project.load_or_create(tmp_path, 42)
    assert len(pr2.data["tags"]) == 2 and pr2.data["tags"][0]["id"] == t["id"] == 1
    pr2.remove_tag(1)
    assert [x["id"] for x in pr2.data["tags"]] == [2]
