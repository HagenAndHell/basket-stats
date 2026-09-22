"""Video helpers: probing local files, YouTube metadata/download, clip export. Needs ffmpeg/ffprobe on PATH."""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
from pathlib import Path


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def probe(path: str | Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,r_frame_rate,duration:format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True).stdout
    d = json.loads(out)
    st = (d.get("streams") or [{}])[0]
    num, _, den = (st.get("r_frame_rate") or "25/1").partition("/")
    fps = float(num) / float(den or 1)
    dur = float(st.get("duration") or d.get("format", {}).get("duration") or 0)
    return {"width": st.get("width"), "height": st.get("height"), "fps": round(fps, 3), "duration": dur}


def youtube_id(url: str) -> str | None:
    import re
    m = re.search(r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else (url if re.fullmatch(r"[A-Za-z0-9_-]{11}", url) else None)


def youtube_info(url: str) -> dict:
    import yt_dlp
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as y:
        d = y.extract_info(url, download=False)
    return {"id": d.get("id"), "title": d.get("title"), "duration": d.get("duration"),
            "width": d.get("width"), "height": d.get("height"), "fps": d.get("fps")}


class Download:
    """Background yt-dlp download (mp4, <=1080p) for offline playback / tracking."""

    def __init__(self, url: str, dest_dir: Path, max_height: int = 1080):
        self.url, self.dest_dir, self.max_height = url, Path(dest_dir), max_height
        self.status = {"state": "starting", "progress": 0.0, "path": None, "error": None}
        threading.Thread(target=self._run, daemon=True).start()

    def _hook(self, d):
        if d.get("status") == "downloading":
            tot = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if tot:
                self.status.update(state="downloading", progress=d.get("downloaded_bytes", 0) / tot)
        elif d.get("status") == "finished":
            self.status.update(state="processing", progress=1.0)

    def _run(self):
        import yt_dlp
        self.dest_dir.mkdir(parents=True, exist_ok=True)
        opts = {
            "quiet": True, "no_warnings": True, "progress_hooks": [self._hook],
            "format": f"bestvideo[ext=mp4][height<={self.max_height}]+bestaudio[ext=m4a]/best[ext=mp4][height<={self.max_height}]/best",
            "merge_output_format": "mp4",
            "outtmpl": str(self.dest_dir / "%(id)s.%(ext)s"),
        }
        try:
            with yt_dlp.YoutubeDL(opts) as y:
                info = y.extract_info(self.url, download=True)
                path = y.prepare_filename(info)
                if not path.endswith(".mp4"):
                    path = str(Path(path).with_suffix(".mp4"))
            self.status.update(state="done", progress=1.0, path=path)
        except Exception as e:  # noqa: BLE001
            self.status.update(state="error", error=str(e))


def export_clip(src: str | Path, start: float, duration: float, out: str | Path, reencode: bool = False) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    start = max(0.0, start)
    if reencode:
        cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{duration:.3f}",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", str(out)]
    else:
        cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{duration:.3f}",
               "-c", "copy", "-avoid_negative_ts", "make_zero", str(out)]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return out
