import pytest

from app import court


def synthetic_H():
    """Fake camera: court metre (x,y) -> pixel via a known homography (a perspective-ish map)."""
    import numpy as np
    # dst pixels for the 4 corners of a 28x15 court seen in perspective
    src = np.array([[0, 0], [28, 0], [28, 15], [0, 15]], dtype=np.float64)
    dst = np.array([[200, 900], [1700, 900], [1400, 300], [500, 300]], dtype=np.float64)
    import cv2
    Hm, _ = cv2.findHomography(src, dst, 0)  # court->pixel
    return Hm


def test_homography_roundtrip():
    Hm = synthetic_H()
    pts = []
    for name in ["corner_L_bottom", "corner_R_bottom", "corner_R_top", "corner_L_top", "centre", "L_ft_bottom", "R_3pt_apex"]:
        x, y = court.LANDMARKS[name]
        v = Hm @ [x, y, 1]
        pts.append({"name": name, "px": v[0] / v[2], "py": v[1] / v[2]})
    H = court.homography(pts)
    assert H is not None
    assert court.reprojection_error(H, pts) < 1e-6
    # unknown pixel maps back consistently
    px, py = court.to_pixel(H, 10.0, 7.5)
    x, y = court.to_court(H, px, py)
    assert (x, y) == (pytest.approx(10.0), pytest.approx(7.5))


def test_homography_needs_four_points():
    assert court.homography([{"name": "centre", "px": 1, "py": 1}]) is None
    assert court.homography([{"name": "bogus", "px": 1, "py": 1}] * 5) is None


def test_foot_point_and_on_court():
    assert court.foot_point([100, 200, 140, 300, 0.9]) == (120, 300)
    assert court.on_court(0, 0) and court.on_court(28.5, 15.5) and not court.on_court(-2, 5)


def test_landmarks_sane():
    assert court.LANDMARKS["R_basket"] == (28 - 1.575, 7.5)
    assert court.LANDMARKS["L_3pt_apex"][0] == pytest.approx(1.575 + 6.75)
    assert len(court.LANDMARKS) >= 20


def test_distortion_fit_recovers_synthetic_fisheye():
    import numpy as np
    Hm = synthetic_H()                        # court -> ideal pixel
    W, Hh = 1920, 1080
    true = {"cx": 980.0, "cy": 530.0, "k1": -0.18, "k2": 0.03, "f": 0.5 * (W * W + Hh * Hh) ** 0.5}
    names = ["corner_L_bottom", "corner_R_bottom", "corner_R_top", "corner_L_top", "centre", "half_bottom", "half_top",
             "L_ft_bottom", "L_ft_top", "R_ft_bottom", "R_ft_top", "L_3pt_apex", "R_3pt_apex", "L_key_bottom", "R_key_top"]
    pts = []
    for n in names:
        x, y = court.LANDMARKS[n]
        v = Hm @ [x, y, 1]
        ux, uy = v[0] / v[2], v[1] / v[2]
        dx, dy = court._distort_pt(ux, uy, true)          # what the fisheye camera would record
        pts.append({"name": n, "px": dx, "py": dy})
    cal = court.calibrate(pts, W, Hh)
    assert cal["error_plain_m"] > 0.15                      # plain homography is clearly off
    assert cal["dist"] is not None
    assert cal["error_m"] < 0.02                            # distortion model fixes it
    assert abs(cal["dist"]["k1"] - true["k1"]) < 0.05
    # round trip through the fitted model
    px, py = court.to_pixel(cal["H"], 10.0, 4.0, cal["dist"])
    x, y = court.to_court(cal["H"], px, py, cal["dist"])
    assert (x, y) == (pytest.approx(10.0, abs=1e-3), pytest.approx(4.0, abs=1e-3))


def test_distortion_skipped_with_few_points():
    Hm = synthetic_H()
    pts = []
    for n in ["corner_L_bottom", "corner_R_bottom", "corner_R_top", "corner_L_top", "centre"]:
        x, y = court.LANDMARKS[n]; v = Hm @ [x, y, 1]
        pts.append({"name": n, "px": v[0] / v[2], "py": v[1] / v[2]})
    cal = court.calibrate(pts, 1920, 1080)
    assert cal["dist"] is None and cal["error_m"] < 1e-6


def test_court_lines_shape():
    lines = court.court_lines()
    assert len(lines) == 7 and all(len(seg) > 10 for seg in lines)


def test_residuals_reported():
    Hm = synthetic_H()
    pts = []
    for n in ["corner_L_bottom", "corner_R_bottom", "corner_R_top", "corner_L_top", "centre"]:
        x, y = court.LANDMARKS[n]; v = Hm @ [x, y, 1]
        pts.append({"name": n, "px": v[0] / v[2], "py": v[1] / v[2]})
    pts[-1]["px"] += 80  # mis-click the centre
    cal = court.calibrate(pts, 1920, 1080)
    r = {x["name"]: x for x in cal["residuals"]}
    assert len(r) == 5 and r["centre"]["px_err"] == max(x["px_err"] for x in r.values()) and r["centre"]["px_err"] > 20


def test_distort_inverse_is_robust_everywhere():
    import numpy as np
    w, h = 3840, 2160
    f = 0.5 * (w * w + h * h) ** 0.5
    for k1, k2 in ((-0.1, 0), (-0.3, 0), (-0.5, 0.1), (-0.3, 0.05), (0.2, 0)):
        dist = {"cx": w / 2 + 100, "cy": h / 2 - 50, "k1": k1, "k2": k2, "f": f}
        for pt in [(95.2, 1689.0), (3651.9, 1381.3), (1920, 1080), (400, 600), (10, 10), (3830, 2150)]:
            r = np.hypot(pt[0] - dist["cx"], pt[1] - dist["cy"]) / f
            if 1 - k1 * r * r - 3 * k2 * r ** 4 <= 0.05 or 1 + k1 * r * r + k2 * r ** 4 <= 0.05:
                continue  # beyond the fold: not invertible by construction
            u = court._undistort_pts([pt], dist)[0]
            back = court._distort_pt(u[0], u[1], dist)
            assert np.hypot(back[0] - pt[0], back[1] - pt[1]) < 0.05, (k1, k2, pt)


def test_fit_keeps_model_invertible_over_frame():
    """Strong synthetic fisheye on a 4K frame with points near the edges: fitted model must stay monotonic."""
    import numpy as np
    W, Hh = 3840, 2160
    Hm = synthetic_H()
    Hm = np.diag([2.0, 2.0, 1.0]) @ Hm   # scale synthetic camera to 4K
    f = 0.5 * (W * W + Hh * Hh) ** 0.5
    true = {"cx": 1900.0, "cy": 1100.0, "k1": -0.28, "k2": 0.04, "f": f}
    pts = []
    for n, (x, y) in court.LANDMARKS.items():
        v = Hm @ [x, y, 1]
        dx, dy = court._distort_pt(v[0] / v[2], v[1] / v[2], true)
        if 0 <= dx <= W and 0 <= dy <= Hh:
            pts.append({"name": n, "px": dx, "py": dy})
    assert len(pts) >= 10
    cal = court.calibrate(pts, W, Hh)
    assert cal["dist"] is not None and cal["error_m"] < 0.05
    d = cal["dist"]
    r_max = max(np.hypot(x - d["cx"], y - d["cy"]) for x in (0, W) for y in (0, Hh)) / f
    rr = np.linspace(0, r_max, 50)
    assert np.all(1 - d["k1"] * rr ** 2 - 3 * d["k2"] * rr ** 4 > 0.1)
    # clicked corners reproject close to where they were clicked
    assert max(r["px_err"] for r in cal["residuals"]) < 3


USER_POINTS_4K = """corner_L_top 1024 988.4
corner_R_bottom 3651.9 1381.3
corner_R_top 2769.2 942.2
half_bottom 2475.1 1860.9
half_top 1964.3 954.6
centre 2071 1151
L_ft_bottom 1059.9 1333
L_ft_top 1263.3 1100
R_ft_bottom 2971.5 1216.7
R_ft_top 2683.3 1047.2
L_3pt_bottom 188.4 1605.4
L_3pt_top 993.9 1009.1
R_3pt_bottom 3576.3 1337.4
R_3pt_top 2803.2 957.2
L_3pt_apex 1453 1181.6
R_3pt_apex 2614.4 1127.4
corner_L_bottom 99.2 1688.7"""


def test_real_fisheye_calibration_from_user_points():
    pts = [{"name": a, "px": float(b), "py": float(c)} for a, b, c in (l.split() for l in USER_POINTS_4K.splitlines())]
    cal = court.calibrate(pts, 3840, 2160)
    assert cal["error_plain_m"] > 0.8            # plain homography is hopeless on this lens
    assert cal["dist"] is not None and -1.0 < cal["dist"]["k1"] < -0.45   # strong barrel distortion, in the right basin
    assert cal["error_m"] < 0.15
    assert max(r["px_err"] for r in cal["residuals"]) < 40
    # the near sideline drawn through the model passes near the clicked half_bottom
    hb = next(p for p in pts if p["name"] == "half_bottom")
    px, py = court.to_pixel(cal["H"], 14.0, 0.0, cal["dist"])
    assert abs(px - hb["px"]) < 40 and abs(py - hb["py"]) < 40
