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
