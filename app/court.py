"""Court calibration: pixel <-> court metres via homography.

Court model: FIBA, 28 x 15 m. Origin = the corner that is bottom-left when the court is
drawn with the baseline at x=0 on the left. x along the length (0..28), y along the width (0..15).

Landmarks the user can click, keyed by name (see LANDMARKS). Four or more non-collinear
points -> cv2.findHomography (RANSAC when >4).
"""
from __future__ import annotations

import numpy as np

LENGTH, WIDTH = 28.0, 15.0
FT_LINE = 5.8          # distance from baseline to free-throw line
LANE_W = 4.9           # width of the key
ARC_R = 6.75           # 3-pt radius from basket centre
BASKET_X = 1.575       # basket centre from baseline
CORNER3_Y = 0.9        # 3pt line distance from sideline in the corner

LANDMARKS: dict[str, tuple[float, float]] = {
    # corners
    "corner_L_bottom": (0.0, 0.0), "corner_L_top": (0.0, WIDTH),
    "corner_R_bottom": (LENGTH, 0.0), "corner_R_top": (LENGTH, WIDTH),
    # half-court line ends and centre
    "half_bottom": (LENGTH / 2, 0.0), "half_top": (LENGTH / 2, WIDTH), "centre": (LENGTH / 2, WIDTH / 2),
    # free-throw lines (key corners at FT line)
    "L_ft_bottom": (FT_LINE, WIDTH / 2 - LANE_W / 2), "L_ft_top": (FT_LINE, WIDTH / 2 + LANE_W / 2),
    "R_ft_bottom": (LENGTH - FT_LINE, WIDTH / 2 - LANE_W / 2), "R_ft_top": (LENGTH - FT_LINE, WIDTH / 2 + LANE_W / 2),
    # key corners at baseline
    "L_key_bottom": (0.0, WIDTH / 2 - LANE_W / 2), "L_key_top": (0.0, WIDTH / 2 + LANE_W / 2),
    "R_key_bottom": (LENGTH, WIDTH / 2 - LANE_W / 2), "R_key_top": (LENGTH, WIDTH / 2 + LANE_W / 2),
    # 3-pt line meets baseline
    "L_3pt_bottom": (0.0, CORNER3_Y), "L_3pt_top": (0.0, WIDTH - CORNER3_Y),
    "R_3pt_bottom": (LENGTH, CORNER3_Y), "R_3pt_top": (LENGTH, WIDTH - CORNER3_Y),
    # 3-pt arc apex (on the centre line of the court)
    "L_3pt_apex": (BASKET_X + ARC_R, WIDTH / 2), "R_3pt_apex": (LENGTH - BASKET_X - ARC_R, WIDTH / 2),
    # baskets (centre of the ring, projected to the floor)
    "L_basket": (BASKET_X, WIDTH / 2), "R_basket": (LENGTH - BASKET_X, WIDTH / 2),
}


def homography(points: list[dict]) -> list[list[float]] | None:
    """points: [{"name": landmark, "px": x, "py": y}, ...]  -> 3x3 pixel->court matrix (as lists) or None."""
    import cv2
    src, dst = [], []
    for p in points:
        if p.get("name") in LANDMARKS:
            src.append([p["px"], p["py"]])
            dst.append(LANDMARKS[p["name"]])
    if len(src) < 4:
        return None
    src_a, dst_a = np.array(src, dtype=np.float64), np.array(dst, dtype=np.float64)
    if len(src) == 4:
        H, _ = cv2.findHomography(src_a, dst_a, 0)
    else:
        H, _ = cv2.findHomography(src_a, dst_a, cv2.RANSAC, 0.5)
    return H.tolist() if H is not None else None


def to_court(H, px: float, py: float) -> tuple[float, float]:
    h = np.asarray(H)
    v = h @ np.array([px, py, 1.0])
    return float(v[0] / v[2]), float(v[1] / v[2])


def to_pixel(H, x: float, y: float) -> tuple[float, float]:
    hinv = np.linalg.inv(np.asarray(H))
    v = hinv @ np.array([x, y, 1.0])
    return float(v[0] / v[2]), float(v[1] / v[2])


def reprojection_error(H, points: list[dict]) -> float:
    """Mean error in metres over the given landmarks."""
    errs = []
    for p in points:
        if p.get("name") in LANDMARKS:
            cx, cy = to_court(H, p["px"], p["py"])
            tx, ty = LANDMARKS[p["name"]]
            errs.append(((cx - tx) ** 2 + (cy - ty) ** 2) ** 0.5)
    return float(np.mean(errs)) if errs else 0.0


def foot_point(box) -> tuple[float, float]:
    """Bottom-centre of a bbox [x1,y1,x2,y2,...] — the player's position on the floor."""
    return (box[0] + box[2]) / 2, box[3]


def on_court(x: float, y: float, margin: float = 1.0) -> bool:
    return -margin <= x <= LENGTH + margin and -margin <= y <= WIDTH + margin
