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


def test_calibration_and_positions(client, tmp_path, monkeypatch):
    import gzip, json
    from app import server
    client.get("/api/match/8439241")
    lm = client.get("/api/court/landmarks").json()
    assert lm["length"] == 28 and "centre" in lm["landmarks"]
    # too few points
    r = client.post("/api/match/8439241/calibration", json={"points": [{"name": "centre", "px": 1, "py": 1}]})
    assert r.json()["H"] is None
    # identity-ish calibration: pixel = metre * 10
    pts = [{"name": n, "px": lm["landmarks"][n][0] * 10, "py": lm["landmarks"][n][1] * 10} for n in ("corner_L_bottom", "corner_R_bottom", "corner_R_top", "corner_L_top", "centre")]
    r = client.post("/api/match/8439241/calibration", json={"points": pts}).json()
    assert r["H"] is not None and r["error_m"] < 1e-6
    # no tracks yet
    assert client.get("/api/match/8439241/positions", params={"t0": 0, "t1": 10}).status_code == 404
    # fake track file
    (server.TRACKS).mkdir(parents=True, exist_ok=True)
    with gzip.open(server.TRACKS / "8439241.jsonl.gz", "wt") as fh:
        fh.write(json.dumps({"meta": {"fps": 50, "stride": 2}}) + "\n")
        fh.write(json.dumps({"t": 1.0, "f": 50, "p": [[7, 130, 60, 150, 75, 0.9], [8, 500, 200, 520, 240, 0.8]], "b": [140, 40, 150, 50, 0.5]}) + "\n")
        fh.write(json.dumps({"t": 1.04, "f": 52, "p": [[7, 131, 60, 151, 75, 0.9]], "b": None}) + "\n")
    r = client.get("/api/match/8439241/positions", params={"t0": 0.5, "t1": 1.02}).json()
    assert len(r["frames"]) == 1
    p7 = next(p for p in r["frames"][0]["p"] if p["id"] == 7)
    assert p7["foot"] == [140.0, 75.0] and p7["court"] == [14.0, 7.5] and p7["on"] is True
    p8 = next(p for p in r["frames"][0]["p"] if p["id"] == 8)
    assert p8["on"] is False  # 51 m, 24 m -> off court
    assert r["frames"][0]["b"]["c"] == [145.0, 45.0]
    s = client.get("/api/match/8439241/track/summary").json()
    assert s["frames"] == 2 and s["tracks"][0]["id"] == 7 and s["tracks"][0]["n"] == 2
    assert client.get("/api/match/8439241/track").json()["available"] is True
