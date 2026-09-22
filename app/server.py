"""basket-stats local server.  Run:  python -m app  (then open http://localhost:8765)"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import court, feed, track, video
from .project import Project

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("BASKET_STATS_DATA", ROOT / "data"))
PROJECTS = DATA / "projects"
VIDEOS = DATA / "videos"
CLIPS = DATA / "clips"
TRACKS = DATA / "tracks"

app = FastAPI(title="basket-stats")


_api_lock = asyncio.Lock()


@app.middleware("http")
async def serialize_api(request, call_next):
    # project files are tiny; handling API requests one at a time keeps read-modify-write safe
    if request.url.path.startswith("/api/") and "/video/file" not in request.url.path:
        async with _api_lock:
            return await call_next(request)
    return await call_next(request)
_downloads: dict[str, video.Download] = {}
_trackjobs: dict[str, track.TrackJob] = {}
_trackcache: dict[str, tuple[float, dict, list]] = {}  # matchId -> (mtime, meta, frames)


def project(match_id: int) -> Project:
    return Project.load_or_create(PROJECTS, match_id)


# ---------- feed / match
@app.get("/api/match/{match_id}")
def get_match(match_id: int, refresh: bool = False):
    pr = project(match_id)
    if refresh or not pr.data.get("feed"):
        try:
            pr.data["feed"] = feed.fetch(match_id)
        except Exception as e:  # noqa: BLE001
            if not pr.data.get("feed"):
                raise HTTPException(502, f"feed fetch failed: {e}") from e
        pr.save()
    return pr.data


class VideoIn(BaseModel):
    source: str  # local path or YouTube URL


@app.post("/api/match/{match_id}/video")
def set_video(match_id: int, body: VideoIn):
    pr = project(match_id)
    src = body.source.strip()
    yid = video.youtube_id(src) if ("youtu" in src or len(src) == 11) else None
    if yid:
        info = video.youtube_info(src)
        pr.data["video"] = {"type": "youtube", "id": info["id"], "title": info["title"], "duration": info["duration"],
                            "width": info["width"], "height": info["height"], "fps": info["fps"], "local": None}
    else:
        p = Path(src)
        if not p.is_file():
            raise HTTPException(400, f"file not found: {src}")
        pv = video.probe(p)
        pr.data["video"] = {"type": "file", "path": str(p), "title": p.name, **pv, "local": str(p)}
    pr.save()
    return pr.data["video"]


@app.post("/api/match/{match_id}/video/download")
def download_video(match_id: int):
    pr = project(match_id)
    v = pr.data.get("video")
    if not v or v["type"] != "youtube":
        raise HTTPException(400, "no YouTube video set")
    key = str(match_id)
    d = _downloads.get(key)
    if not d or d.status["state"] in ("error",):
        d = _downloads[key] = video.Download(f"https://www.youtube.com/watch?v={v['id']}", VIDEOS)
    return d.status


@app.get("/api/match/{match_id}/video/download")
def download_status(match_id: int):
    d = _downloads.get(str(match_id))
    if not d:
        return {"state": "idle"}
    if d.status["state"] == "done":
        pr = project(match_id)
        if pr.data.get("video") and pr.data["video"].get("local") != d.status["path"]:
            pr.data["video"]["local"] = d.status["path"]
            pr.save()
    return d.status


@app.get("/api/match/{match_id}/video/file")
def video_file(match_id: int):
    pr = project(match_id)
    v = pr.data.get("video") or {}
    local = v.get("local")
    if not local or not Path(local).is_file():
        raise HTTPException(404, "no local video")
    return FileResponse(local, media_type="video/mp4")


# ---------- sync
class AnchorIn(BaseModel):
    period: int
    clock: float   # elapsed seconds in period
    video: float   # seconds in video


@app.post("/api/match/{match_id}/sync")
def add_anchor(match_id: int, a: AnchorIn):
    pr = project(match_id)
    pr.add_anchor(a.period, a.clock, a.video)
    pr.save()
    return pr.data["sync"]


@app.delete("/api/match/{match_id}/sync")
def remove_anchor(match_id: int, period: int, clock: float):
    pr = project(match_id)
    pr.remove_anchor(period, clock)
    pr.save()
    return pr.data["sync"]


@app.get("/api/match/{match_id}/events")
def events_with_video(match_id: int):
    """Feed events plus computed video timestamps (null where the period is not synced)."""
    pr = project(match_id)
    fd = pr.data.get("feed") or {}
    out = []
    for e in fd.get("events", []):
        e = dict(e)
        e["video"] = pr.clock_to_video(e["period"], e["clock"])
        out.append(e)
    return out


@app.get("/api/match/{match_id}/clock")
def clock_at(match_id: int, video: float = Query(...)):
    r = project(match_id).video_to_period_clock(video)
    return {"period": r[0], "clock": r[1]} if r else {"period": None, "clock": None}


# ---------- tags (manual stats)
class TagIn(BaseModel):
    video: float
    period: int | None = None
    clock: float | None = None
    team: str | None = None
    personId: str | None = None
    stat: str          # oreb dreb ast stl to blk fga2 fga3 ...
    note: str | None = None


@app.get("/api/match/{match_id}/tags")
def get_tags(match_id: int):
    return project(match_id).data["tags"]


@app.post("/api/match/{match_id}/tags")
def add_tag(match_id: int, t: TagIn):
    pr = project(match_id)
    tag = pr.add_tag(t.model_dump())
    pr.save()
    return tag


@app.delete("/api/match/{match_id}/tags/{tag_id}")
def del_tag(match_id: int, tag_id: int):
    pr = project(match_id)
    pr.remove_tag(tag_id)
    pr.save()
    return pr.data["tags"]


# ---------- clips
class ClipIn(BaseModel):
    start: float
    duration: float = 12.0
    name: str = "clip"


@app.post("/api/match/{match_id}/clip")
def make_clip(match_id: int, c: ClipIn):
    pr = project(match_id)
    v = pr.data.get("video") or {}
    if not v.get("local"):
        raise HTTPException(400, "download the video first (clips need a local file)")
    safe = "".join(ch if ch.isalnum() or ch in "-_ ." else "_" for ch in c.name)[:80]
    out = CLIPS / str(match_id) / f"{safe}_{int(c.start)}s.mp4"
    video.export_clip(v["local"], c.start, c.duration, out)
    return {"path": str(out)}


# ---------- court calibration
class CalibIn(BaseModel):
    points: list[dict]   # [{"name": landmark, "px": .., "py": ..}]


@app.get("/api/court/landmarks")
def landmarks():
    return {"landmarks": court.LANDMARKS, "length": court.LENGTH, "width": court.WIDTH,
            "ft_line": court.FT_LINE, "lane_w": court.LANE_W, "arc_r": court.ARC_R, "basket_x": court.BASKET_X, "corner3_y": court.CORNER3_Y}


@app.post("/api/match/{match_id}/calibration")
def set_calibration(match_id: int, c: CalibIn):
    pr = project(match_id)
    H = court.homography(c.points)
    pr.data["calibration"] = {"points": c.points, "H": H,
                              "error_m": court.reprojection_error(H, c.points) if H else None}
    pr.save()
    return pr.data["calibration"]


@app.get("/api/match/{match_id}/frame")
def frame_at(match_id: int, t: float = Query(...), width: int = 1280):
    """JPEG of the local video at time t (for clicking landmarks). width=0 -> full resolution."""
    import cv2
    from fastapi.responses import Response
    pr = project(match_id)
    v = pr.data.get("video") or {}
    if not v.get("local"):
        raise HTTPException(400, "needs a local/downloaded video")
    cap = cv2.VideoCapture(v["local"])
    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
    ok, fr = cap.read()
    cap.release()
    if not ok:
        raise HTTPException(404, "no frame")
    h, w = fr.shape[:2]
    if width and w > width:
        fr = cv2.resize(fr, (width, int(h * width / w)))
    ok, buf = cv2.imencode(".jpg", fr, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return Response(buf.tobytes(), media_type="image/jpeg", headers={"X-Source-Width": str(w), "X-Source-Height": str(h)})


# ---------- tracking
class TrackIn(BaseModel):
    start: float = 0.0
    end: float | None = None
    stride: int = 2
    imgsz: int = 1280
    model: str = "yolo11s.pt"


@app.post("/api/match/{match_id}/track")
def start_track(match_id: int, t: TrackIn):
    pr = project(match_id)
    v = pr.data.get("video") or {}
    if not v.get("local"):
        raise HTTPException(400, "needs a local/downloaded video")
    key = str(match_id)
    j = _trackjobs.get(key)
    if j and j.thread.is_alive():
        return j.status
    j = _trackjobs[key] = track.TrackJob(v["local"], TRACKS / f"{match_id}.jsonl.gz", start=t.start, end=t.end,
                                          stride=t.stride, imgsz=t.imgsz, model=t.model)
    return j.status


@app.get("/api/match/{match_id}/track")
def track_status(match_id: int):
    j = _trackjobs.get(str(match_id))
    path = TRACKS / f"{match_id}.jsonl.gz"
    st = dict(j.status) if j else {"state": "idle"}
    st["available"] = path.exists()
    return st


@app.delete("/api/match/{match_id}/track")
def stop_track(match_id: int):
    j = _trackjobs.get(str(match_id))
    if j:
        j.stop()
    return {"ok": True}


def _tracks(match_id: int):
    path = TRACKS / f"{match_id}.jsonl.gz"
    if not path.exists():
        raise HTTPException(404, "no tracking data — run tracking first")
    mt = path.stat().st_mtime
    c = _trackcache.get(str(match_id))
    if not c or c[0] != mt:
        meta, frames = track.read_tracks(path)
        c = _trackcache[str(match_id)] = (mt, meta, frames)
    return c[1], c[2]


@app.get("/api/match/{match_id}/positions")
def positions(match_id: int, t0: float = Query(...), t1: float = Query(...)):
    """Tracked positions between video times t0..t1, in pixels and (if calibrated) court metres."""
    import bisect
    pr = project(match_id)
    meta, frames = _tracks(match_id)
    H = (pr.data.get("calibration") or {}).get("H")
    ts = [f["t"] for f in frames]
    i0, i1 = bisect.bisect_left(ts, t0), bisect.bisect_right(ts, t1)
    out = []
    for f in frames[i0:i1]:
        ps = []
        for p in f["p"]:
            fx, fy = court.foot_point(p[1:5])
            item = {"id": p[0], "box": p[1:5], "foot": [round(fx, 1), round(fy, 1)]}
            if H:
                cx, cy = court.to_court(H, fx, fy)
                item["court"] = [round(cx, 2), round(cy, 2)]
                item["on"] = court.on_court(cx, cy)
            ps.append(item)
        b = None
        if f["b"]:
            bx, by = (f["b"][0] + f["b"][2]) / 2, (f["b"][1] + f["b"][3]) / 2
            b = {"box": f["b"][:4], "c": [round(bx, 1), round(by, 1)]}
        out.append({"t": f["t"], "p": ps, "b": b})
    return {"meta": meta, "frames": out}


@app.get("/api/match/{match_id}/track/summary")
def track_summary(match_id: int):
    meta, frames = _tracks(match_id)
    ids = {}
    for f in frames:
        for p in f["p"]:
            d = ids.setdefault(p[0], {"id": p[0], "first": f["t"], "last": f["t"], "n": 0})
            d["last"] = f["t"]; d["n"] += 1
    return {"meta": meta, "frames": len(frames), "t0": frames[0]["t"] if frames else None, "t1": frames[-1]["t"] if frames else None,
            "tracks": sorted(ids.values(), key=lambda d: -d["n"])}


@app.get("/api/health")
def health():
    return {"ok": True, "ffmpeg": video.have_ffmpeg(), "data": str(DATA)}


app.mount("/", StaticFiles(directory=ROOT / "static", html=True), name="static")


def main():
    import uvicorn
    port = int(os.environ.get("PORT", "8765"))
    print(f"basket-stats  ->  http://localhost:{port}   (data dir: {DATA})")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
