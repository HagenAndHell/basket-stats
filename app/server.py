"""basket-stats local server.  Run:  python -m app  (then open http://localhost:8765)"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import feed, video
from .project import Project

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("BASKET_STATS_DATA", ROOT / "data"))
PROJECTS = DATA / "projects"
VIDEOS = DATA / "videos"
CLIPS = DATA / "clips"

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


@app.get("/api/health")
def health():
    return {"ok": True, "ffmpeg": video.have_ffmpeg(), "data": str(DATA)}


app.mount("/", StaticFiles(directory=ROOT / "static", html=True), name="static")


def main():
    import uvicorn
    port = int(os.environ.get("PORT", "8765"))
    print(f"basket-stats  ->  http://localhost:{port}   (data dir: {DATA})")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
