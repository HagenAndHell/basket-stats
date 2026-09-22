"""API tests with the feed mocked from fixtures (offline)."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

FIX = Path(__file__).parent / "fixtures"


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


def test_match_load_and_cache(client, tmp_path):
    r = client.get("/api/match/8439241")
    assert r.status_code == 200
    d = r.json()
    assert d["feed"]["match"]["result"] == [116, 70]
    assert (tmp_path / "projects" / "8439241.json").exists()


def test_events_video_times_after_sync(client):
    client.get("/api/match/8439241")
    ev = client.get("/api/match/8439241/events").json()
    assert ev and all(e["video"] is None for e in ev)
    client.post("/api/match/8439241/sync", json={"period": 1, "clock": 0, "video": 300})
    client.post("/api/match/8439241/sync", json={"period": 1, "clock": 600, "video": 300 + 800})
    ev = client.get("/api/match/8439241/events").json()
    q1 = [e for e in ev if e["period"] == 1]
    assert all(e["video"] is not None for e in q1)
    first = q1[0]  # 0:54 2p
    assert first["clockStr"] == "0:54" and first["video"] == pytest.approx(300 + 54 * 800 / 600)
    assert all(e["video"] is None for e in ev if e["period"] == 2)
    gc = client.get("/api/match/8439241/clock", params={"video": 700}).json()
    assert gc["period"] == 1 and gc["clock"] == pytest.approx(300)
    # remove anchor
    r = client.delete("/api/match/8439241/sync", params={"period": 1, "clock": 600})
    assert len(r.json()["1"]) == 1


def test_tags_crud(client):
    client.get("/api/match/8439241")
    t = client.post("/api/match/8439241/tags", json={"video": 12.5, "stat": "oreb", "team": "home", "personId": "7759476"}).json()
    assert t["id"] == 1
    assert len(client.get("/api/match/8439241/tags").json()) == 1
    assert client.delete("/api/match/8439241/tags/1").json() == []


def test_video_file_not_found(client):
    client.get("/api/match/8439241")
    r = client.post("/api/match/8439241/video", json={"source": "/nonexistent/file.mp4"})
    assert r.status_code == 400
    assert client.get("/api/match/8439241/video/file").status_code == 404


def test_static_index(client):
    r = client.get("/")
    assert r.status_code == 200 and "basket-stats" in r.text
