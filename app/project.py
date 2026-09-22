"""Project state: one JSON file per match under data/projects/<matchId>.json.

Sync model: per period a list of anchors {"clock": elapsed seconds in period, "video": seconds in video}.
With one anchor the mapping is video = anchor.video + (clock - anchor.clock).
With several, linear interpolation between neighbours (handles clock stoppages roughly),
extrapolation from the nearest pair outside.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


class Project:
    def __init__(self, path: Path, data: dict):
        self.path, self.data = path, data

    @classmethod
    def load_or_create(cls, root: Path, match_id: int) -> "Project":
        root.mkdir(parents=True, exist_ok=True)
        p = root / f"{match_id}.json"
        if p.exists():
            return cls(p, json.loads(p.read_text(encoding="utf-8")))
        return cls(p, {"matchId": match_id, "video": None, "sync": {}, "tags": [], "feed": None})

    def save(self):
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)  # atomic on both POSIX and Windows

    # ---- sync
    def anchors(self, period: int) -> list[dict]:
        return sorted(self.data["sync"].get(str(period), []), key=lambda a: a["clock"])

    def add_anchor(self, period: int, clock: float, video: float):
        lst = [a for a in self.data["sync"].get(str(period), []) if abs(a["clock"] - clock) > 0.5]
        lst.append({"clock": float(clock), "video": float(video)})
        self.data["sync"][str(period)] = sorted(lst, key=lambda a: a["clock"])

    def remove_anchor(self, period: int, clock: float):
        self.data["sync"][str(period)] = [a for a in self.data["sync"].get(str(period), []) if abs(a["clock"] - clock) > 0.5]

    def clock_to_video(self, period: int, clock: float) -> float | None:
        an = self.anchors(period)
        if not an:
            return None
        if len(an) == 1:
            return an[0]["video"] + (clock - an[0]["clock"])
        # find neighbours
        lo, hi = an[0], an[-1]
        for i in range(len(an) - 1):
            if an[i]["clock"] <= clock <= an[i + 1]["clock"]:
                lo, hi = an[i], an[i + 1]
                break
        else:
            if clock < an[0]["clock"]:
                lo, hi = an[0], an[1]
            else:
                lo, hi = an[-2], an[-1]
        dc = hi["clock"] - lo["clock"]
        if dc <= 0:
            return lo["video"] + (clock - lo["clock"])
        rate = (hi["video"] - lo["video"]) / dc  # video seconds per clock second (>=1 when clock stops)
        return lo["video"] + (clock - lo["clock"]) * rate

    def video_to_period_clock(self, video: float) -> tuple[int, float] | None:
        """Best guess of (period, clock) for a video time: the period whose anchor span contains it, else nearest."""
        best = None
        for pstr, lst in self.data["sync"].items():
            if not lst:
                continue
            p = int(pstr)
            an = sorted(lst, key=lambda a: a["clock"])
            # invert using the same piecewise mapping
            if len(an) == 1:
                clock = an[0]["clock"] + (video - an[0]["video"])
            else:
                lo, hi = an[0], an[1]
                for i in range(len(an) - 1):
                    if an[i]["video"] <= video <= an[i + 1]["video"]:
                        lo, hi = an[i], an[i + 1]
                        break
                else:
                    if video > an[-1]["video"]:
                        lo, hi = an[-2], an[-1]
                dv = hi["video"] - lo["video"]
                clock = lo["clock"] + (video - lo["video"]) * ((hi["clock"] - lo["clock"]) / dv if dv > 0 else 1)
            dist = 0 if an[0]["video"] <= video <= an[-1]["video"] + 600 else min(abs(video - an[0]["video"]), abs(video - an[-1]["video"]))
            if best is None or dist < best[0]:
                best = (dist, p, clock)
        return (best[1], best[2]) if best else None

    # ---- tags (manual stat events: rebounds, assists, ...)
    def add_tag(self, tag: dict) -> dict:
        tag = dict(tag)
        tag["id"] = max([t["id"] for t in self.data["tags"]] + [0]) + 1
        self.data["tags"].append(tag)
        return tag

    def remove_tag(self, tag_id: int):
        self.data["tags"] = [t for t in self.data["tags"] if t["id"] != tag_id]
