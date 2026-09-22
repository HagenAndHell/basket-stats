"""Player / ball detection and tracking.

Runs YOLO (Ultralytics, COCO classes person=0, sports ball=32) + ByteTrack over a video
and writes a compact per-frame track file:

    data/tracks/<matchId>.jsonl.gz   one JSON object per processed frame:
    {"t": 312.04, "f": 15602, "p": [[track_id, x1, y1, x2, y2, conf], ...], "b": [x1, y1, x2, y2, conf] | null}

Coordinates are pixels in the source video. Processing is done at `stride` frames
(default: every 2nd frame of a 50 fps video -> 25 fps effective) on a resized image.

Device: Ultralytics picks CUDA if available; on AMD/Windows use the ONNX + DirectML
route (see `export_onnx` and BASKET_STATS_ORT=1) - or CPU, which is slow but works.
"""
from __future__ import annotations

import gzip
import json
import os
import threading
import time
from pathlib import Path

PERSON, BALL = 0, 32


class TrackJob:
    """Background tracking job with progress; one per match at a time."""

    def __init__(self, video: str | Path, out: str | Path, start: float = 0.0, end: float | None = None,
                 stride: int = 2, imgsz: int = 1280, model: str = "yolo11s.pt", conf: float = 0.25):
        self.video, self.out = str(video), Path(out)
        self.start, self.end, self.stride, self.imgsz, self.model_name, self.conf = start, end, stride, imgsz, model, conf
        self.status = {"state": "starting", "progress": 0.0, "frames": 0, "fps": 0.0, "error": None, "out": str(out)}
        self._stop = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self._stop = True

    def _run(self):
        try:
            self._track()
        except Exception as e:  # noqa: BLE001
            self.status.update(state="error", error=f"{type(e).__name__}: {e}")

    def _track(self):
        import cv2
        import supervision as sv
        from ultralytics import YOLO

        model = YOLO(self.model_name)
        cap = cv2.VideoCapture(self.video)
        if not cap.isOpened():
            raise RuntimeError(f"cannot open {self.video}")
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        f0 = int(self.start * fps)
        f1 = int(self.end * fps) if self.end is not None else total
        cap.set(cv2.CAP_PROP_POS_FRAMES, f0)
        tracker = sv.ByteTrack(frame_rate=fps / self.stride, lost_track_buffer=int(fps / self.stride * 2), minimum_consecutive_frames=2)

        self.out.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.out.with_suffix(".tmp.gz")
        t_start = time.time()
        n = 0
        with gzip.open(tmp, "wt", encoding="utf-8") as fh:
            fh.write(json.dumps({"meta": {"video": self.video, "fps": fps, "stride": self.stride, "imgsz": self.imgsz,
                                          "model": self.model_name, "start": self.start, "end": self.end}}) + "\n")
            fi = f0
            while fi < f1 and not self._stop:
                ok = cap.grab()
                if not ok:
                    break
                if (fi - f0) % self.stride:
                    fi += 1
                    continue
                ok, frame = cap.retrieve()
                if not ok:
                    break
                res = model.predict(frame, imgsz=self.imgsz, conf=self.conf, classes=[PERSON, BALL], verbose=False)[0]
                det = sv.Detections.from_ultralytics(res)
                persons = det[det.class_id == PERSON]
                balls = det[det.class_id == BALL]
                persons = tracker.update_with_detections(persons)
                p = [[int(tid), *[round(float(v), 1) for v in xyxy], round(float(c), 3)]
                     for xyxy, c, tid in zip(persons.xyxy, persons.confidence, persons.tracker_id) if tid is not None]
                b = None
                if len(balls):
                    i = int(balls.confidence.argmax())
                    b = [*[round(float(v), 1) for v in balls.xyxy[i]], round(float(balls.confidence[i]), 3)]
                fh.write(json.dumps({"t": round(fi / fps, 3), "f": fi, "p": p, "b": b}) + "\n")
                n += 1
                fi += 1
                if n % 10 == 0:
                    el = time.time() - t_start
                    self.status.update(state="running", frames=n, progress=(fi - f0) / max(1, f1 - f0), fps=n / el if el else 0.0)
        cap.release()
        os.replace(tmp, self.out)
        el = time.time() - t_start
        self.status.update(state="stopped" if self._stop else "done", frames=n, progress=1.0 if not self._stop else self.status["progress"], fps=n / el if el else 0.0)


def read_tracks(path: str | Path) -> tuple[dict, list[dict]]:
    meta, frames = {}, []
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            d = json.loads(line)
            if "meta" in d:
                meta = d["meta"]
            else:
                frames.append(d)
    return meta, frames


def export_onnx(model: str = "yolo11s.pt", imgsz: int = 1280) -> str:
    """Export for onnxruntime (DirectML on Windows/AMD). Returns the .onnx path."""
    from ultralytics import YOLO
    return YOLO(model).export(format="onnx", imgsz=imgsz, dynamic=False, simplify=True)
