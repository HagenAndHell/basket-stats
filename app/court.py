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


# ---------- lens distortion: division model (Fitzgibbon), well suited to strong barrel/fisheye ----------
# r = |p - c| / f (f = image half-diagonal);  p_u = c + (p - c) / (1 + k1 r^2 + k2 r^4)
# Barrel (fisheye) lenses give k1 < 0. The inverse (undistorted -> distorted) is solved 1-D numerically.

def _undistort_pts(pts, dist):
    pts = np.asarray(pts, dtype=np.float64)
    if not dist:
        return pts
    cx, cy, k1, k2, f = dist["cx"], dist["cy"], dist["k1"], dist["k2"], dist["f"]
    d = pts - [cx, cy]
    r2 = (d ** 2).sum(axis=1) / (f * f)
    den = 1 + k1 * r2 + k2 * r2 * r2
    den = np.where(np.abs(den) < 1e-6, 1e-6, den)
    return [cx, cy] + d / den[:, None]


def _distort_pt(x, y, dist):
    """undistorted pixel -> distorted pixel. Solve r_d / (1 + k1 r_d^2 + k2 r_d^4) = r_u for r_d by
    bisection on the increasing branch (never diverges); beyond the model's reach we clamp."""
    if not dist:
        return x, y
    cx, cy, k1, k2, f = dist["cx"], dist["cy"], dist["k1"], dist["k2"], dist["f"]
    ux, uy = (x - cx) / f, (y - cy) / f
    ru = (ux * ux + uy * uy) ** 0.5
    if ru < 1e-12:
        return x, y
    g = lambda r: r / (1 + k1 * r * r + k2 * r ** 4)  # noqa: E731
    # end of the increasing branch
    r_hi, step, prev = 0.0, 0.01, 0.0
    while r_hi < 3.0:
        val = g(r_hi + step)
        if val <= prev or (1 + k1 * (r_hi + step) ** 2 + k2 * (r_hi + step) ** 4) <= 0:
            break
        prev, r_hi = val, r_hi + step
    if g(r_hi) <= ru:
        rd = r_hi
    else:
        lo, hi = 0.0, r_hi
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            if g(mid) < ru:
                lo = mid
            else:
                hi = mid
        rd = 0.5 * (lo + hi)
    s_ = rd / ru
    return cx + ux * s_ * f, cy + uy * s_ * f


def _fit(src, dst, w, h, fit_distortion: bool):
    """Return (H as 3x3 list, dist dict|None, rms error m)."""
    import cv2
    src_a, dst_a = np.asarray(src, dtype=np.float64), np.asarray(dst, dtype=np.float64)
    f = 0.5 * (w * w + h * h) ** 0.5

    def solve_H(pts):
        H, _ = cv2.findHomography(pts, dst_a, 0)
        return H

    def err(H, pts):
        if H is None:
            return 1e9
        v = (H @ np.c_[pts, np.ones(len(pts))].T).T
        proj = v[:, :2] / v[:, 2:3]
        return float(np.sqrt(((proj - dst_a) ** 2).sum(axis=1).mean()))

    H0 = solve_H(src_a)
    e0 = err(H0, src_a)
    if not fit_distortion or len(src) < 6 or H0 is None:
        return H0, None, e0
    fit_k2 = len(src) >= 9

    from scipy.optimize import least_squares

    # the model must stay valid out to the frame corners (as seen from the fitted centre), + small margin
    def rr_for(cx, cy):
        r_max = max(np.hypot(x - cx, y - cy) for x in (0, w) for y in (0, h)) / f * 1.05
        return np.linspace(0, r_max, 40)

    def residuals(x):
        k1, k2 = x[2], x[3] if fit_k2 else 0.0
        dist = {"cx": x[0], "cy": x[1], "k1": k1, "k2": k2, "f": f}
        u = _undistort_pts(src_a, dist)
        H = solve_H(u)
        if H is None:
            return np.full(len(src) * 2 + 40, 1e3)
        v = (H @ np.c_[u, np.ones(len(u))].T).T
        proj = v[:, :2] / v[:, 2:3]
        # penalty: the division model must stay increasing over the whole frame:
        # d/dr [r / D(r)] > 0  <=>  D - r D' > 0  with D = 1 + k1 r^2 + k2 r^4  ->  1 - k1 r^2 - 3 k2 r^4 > 0
        rr = rr_for(x[0], x[1])
        dg = 1 - k1 * rr ** 2 - 3 * k2 * rr ** 4
        den = 1 + k1 * rr ** 2 + k2 * rr ** 4
        pen = (np.maximum(0.0, 0.15 - dg) + np.maximum(0.0, 0.15 - den)) * 50.0
        return np.concatenate([(proj - dst_a).ravel(), pen])

    x0 = np.array([w / 2, h / 2, 0.0, 0.0])
    lo = [w * 0.25, h * 0.25, -3.0, -3.0 if fit_k2 else -1e-9]
    hi = [w * 0.75, h * 0.75, 3.0, 3.0 if fit_k2 else 1e-9]
    res = least_squares(residuals, x0, bounds=(lo, hi), x_scale=[w * 0.1, h * 0.1, 0.1, 0.1])
    dist = {"cx": float(res.x[0]), "cy": float(res.x[1]), "k1": float(res.x[2]), "k2": float(res.x[3]) if fit_k2 else 0.0, "f": f}
    u = _undistort_pts(src_a, dist)
    H1 = solve_H(u)
    e1 = err(H1, u)
    if H1 is None or e1 >= e0 * 0.98:  # distortion did not help
        return H0, None, e0
    return H1, dist, e1


def residuals_px(H, dist, points: list[dict]) -> list[dict]:
    """Per landmark: where the model puts it vs where it was clicked (pixels)."""
    out = []
    for p in points:
        if p.get("name") in LANDMARKS:
            rx, ry = to_pixel(H, *LANDMARKS[p["name"]], dist)
            out.append({"name": p["name"], "dx": round(rx - p["px"], 1), "dy": round(ry - p["py"], 1),
                        "px_err": round(float(np.hypot(rx - p["px"], ry - p["py"])), 1)})
    return out


def calibrate(points: list[dict], width: int, height: int, fit_distortion: bool = True) -> dict | None:
    """points: [{"name","px","py"}]. Returns {"H", "dist", "error_m", "error_plain_m"} or None (<4 points)."""
    src, dst = [], []
    for p in points:
        if p.get("name") in LANDMARKS:
            src.append([p["px"], p["py"]])
            dst.append(LANDMARKS[p["name"]])
    if len(src) < 4:
        return None
    H0, _, e0 = _fit(src, dst, width, height, False)
    H, dist, e = _fit(src, dst, width, height, fit_distortion)
    if H is None:
        return None
    return {"H": H.tolist(), "dist": dist, "error_m": e, "error_plain_m": e0, "width": width, "height": height,
            "residuals": residuals_px(H.tolist(), dist, points)}


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


def to_court(H, px: float, py: float, dist: dict | None = None) -> tuple[float, float]:
    if dist:
        px, py = _undistort_pts([[px, py]], dist)[0]
    h = np.asarray(H)
    v = h @ np.array([px, py, 1.0])
    return float(v[0] / v[2]), float(v[1] / v[2])


def to_pixel(H, x: float, y: float, dist: dict | None = None) -> tuple[float, float]:
    hinv = np.linalg.inv(np.asarray(H))
    v = hinv @ np.array([x, y, 1.0])
    ux, uy = float(v[0] / v[2]), float(v[1] / v[2])
    return _distort_pt(ux, uy, dist) if dist else (ux, uy)


def reprojection_error(H, points: list[dict], dist: dict | None = None) -> float:
    """Mean error in metres over the given landmarks."""
    errs = []
    for p in points:
        if p.get("name") in LANDMARKS:
            cx, cy = to_court(H, p["px"], p["py"], dist)
            tx, ty = LANDMARKS[p["name"]]
            errs.append(((cx - tx) ** 2 + (cy - ty) ** 2) ** 0.5)
    return float(np.mean(errs)) if errs else 0.0


def foot_point(box) -> tuple[float, float]:
    """Bottom-centre of a bbox [x1,y1,x2,y2,...] — the player's position on the floor."""
    return (box[0] + box[2]) / 2, box[3]


def on_court(x: float, y: float, margin: float = 1.0) -> bool:
    return -margin <= x <= LENGTH + margin and -margin <= y <= WIDTH + margin


def _densify(pts, step=0.5):
    out = []
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        n = max(1, int(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5 / step))
        for i in range(n):
            out.append((x1 + (x2 - x1) * i / n, y1 + (y2 - y1) * i / n))
    out.append(pts[-1])
    return out


def court_lines() -> list[list[tuple[float, float]]]:
    """Court markings as polylines in metres (densified so lens distortion shows as curves)."""
    import math
    L, W, ft, lw, r, bx, c3 = LENGTH, WIDTH, FT_LINE, LANE_W, ARC_R, BASKET_X, CORNER3_Y
    segs = [
        [(0, 0), (L, 0), (L, W), (0, W), (0, 0)],
        [(L / 2, 0), (L / 2, W)],
        [(0, W / 2 - lw / 2), (ft, W / 2 - lw / 2), (ft, W / 2 + lw / 2), (0, W / 2 + lw / 2)],
        [(L, W / 2 - lw / 2), (L - ft, W / 2 - lw / 2), (L - ft, W / 2 + lw / 2), (L, W / 2 + lw / 2)],
    ]
    def arc(cx, d):
        pts = []
        a = -math.pi / 2
        while a <= math.pi / 2 + 1e-9:
            x, y = cx + d * r * math.cos(a), W / 2 + r * math.sin(a)
            if c3 <= y <= W - c3:
                pts.append((x, y))
            a += math.pi / 60
        return pts
    a1, a2 = arc(bx, 1), arc(L - bx, -1)
    segs.append([(0, c3), a1[0], *a1, (0, W - c3)])
    segs.append([(L, c3), a2[0], *a2, (L, W - c3)])
    segs.append([(L / 2 + 1.8 * math.cos(t * math.pi / 30), W / 2 + 1.8 * math.sin(t * math.pi / 30)) for t in range(61)])
    return [_densify(s) for s in segs]
