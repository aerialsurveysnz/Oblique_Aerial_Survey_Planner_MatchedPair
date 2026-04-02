"""
tests/test_geometry.py
======================
Unit tests for geometry.py — all reference values verified against
Oblique_setup9_working_2.xls (Landscape sheet).

Run:
    python -m pytest tests/test_geometry.py -v
or:
    python tests/test_geometry.py   (standalone, no pytest needed)
"""

import math
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from geometry import (
    pixel_size_mm,
    focal_length_px,
    normalize_tilt_angle,
    half_fov_deg,
    diag_pp_to_long_edge_mm,
    flying_height_for_gsd,
    four_corner_footprint,
    ground_intersections_flat_terrain,
    gsd_at_edge_full,
    footprint_dimensions,
    effective_swath_from_sidelap,
    line_spacing_from_sidelap,
    photo_spacing_from_forward_overlap,
    calculate_camera_solution,
    calculate_multicamera_solution,
    m_to_unit,
    unit_to_m,
)

# ---------------------------------------------------------------------------
# Tolerance helper
# ---------------------------------------------------------------------------

def near(a, b, rel=1e-4, abs_tol=None):
    """Return True if a ≈ b within relative or absolute tolerance."""
    if abs_tol is not None:
        return abs(a - b) <= abs_tol
    denom = abs(b) if abs(b) > 1e-15 else 1e-15
    return abs(a - b) / denom <= rel


# ---------------------------------------------------------------------------
# Reference values from the spreadsheet (Landscape sheet)
# Sony A7R V, fl=50mm, oblique 50° from horizontal (= 40° from nadir)
# portrait orientation, pixel_size=0.0038mm, H=469.7368m
# ---------------------------------------------------------------------------

SHEET = dict(
    H               = 469.736842105263,
    px_mm           = 0.0038,
    fl_mm           = 50.0,
    sensor_across   = 24.0768,   # portrait: narrow = across-track
    sensor_along    = 36.1152,   # portrait: long   = along-track
    tilt_nadir      = 40.0,      # 90 - 50° from horiz
    # Derived
    inner_edge      = 233.820120215362,
    outer_edge      = 635.678729443168,
    swath           = 401.858609227806,
    inner_length    = 368.473254414468,
    outer_length    = 555.051409140978,
    inner_gsd_cm    = 3.877,
    outer_gsd_cm    = 5.840,
    slope_inner     = 524.713778596056,
    slope_outer     = 790.405053056663,
    diag_image      = 51.4288156052616,
    fov_lr          = 27.0746652819774,   # full L/R FOV (degrees)
    fov_fa          = 39.7145736919359,   # full F/A FOV (degrees)
    # Nadir camera (fl=21mm, landscape)
    nadir_fl        = 21.0,
    nadir_swath     = 807.84,
    nadir_fa        = 538.56,
    nadir_inner     = 403.92,
    nadir_fa_half   = 269.28,
    target_gsd_cm   = 8.5,
)


# ---------------------------------------------------------------------------
# Unit conversion
# ---------------------------------------------------------------------------

class TestUnitConversion:
    def test_m_to_ft(self):
        assert near(m_to_unit(1.0, "ft"), 3.28084)

    def test_m_to_cm(self):
        assert near(m_to_unit(1.0, "cm"), 100.0)

    def test_m_to_mm(self):
        assert near(m_to_unit(1.0, "mm"), 1000.0)

    def test_ft_roundtrip(self):
        assert near(unit_to_m(m_to_unit(123.456, "ft"), "ft"), 123.456)

    def test_cm_roundtrip(self):
        assert near(unit_to_m(m_to_unit(99.9, "cm"), "cm"), 99.9)


# ---------------------------------------------------------------------------
# Sensor helpers
# ---------------------------------------------------------------------------

class TestPixelSizeMm:
    def test_portrait_a7rv(self):
        # 24.0768mm / 6336px  (portrait, across-track dimension)
        result = pixel_size_mm(24.0768, 6336)
        assert near(result, 0.0038)

    def test_landscape_a7rv(self):
        result = pixel_size_mm(36.1152, 9504)
        assert near(result, 0.0038)

    def test_zero_pixels_raises(self):
        try:
            pixel_size_mm(24.0, 0)
            assert False, "should raise"
        except ValueError:
            pass

    def test_negative_sensor_raises(self):
        try:
            pixel_size_mm(-1.0, 1000)
            assert False, "should raise"
        except ValueError:
            pass


class TestNormalizeTiltAngle:
    def test_nadir_passthrough(self):
        assert near(normalize_tilt_angle(40.0, "nadir"), 40.0)

    def test_horiz_50_gives_40_nadir(self):
        # Spreadsheet uses 50° from horizontal = 40° from nadir
        assert near(normalize_tilt_angle(50.0, "horiz"), 40.0)

    def test_horiz_0_gives_90_nadir(self):
        assert near(normalize_tilt_angle(0.0, "horiz"), 90.0)

    def test_horiz_90_gives_0_nadir(self):
        assert near(normalize_tilt_angle(90.0, "horiz"), 0.0)

    def test_unknown_convention_raises(self):
        try:
            normalize_tilt_angle(30.0, "bad")
            assert False, "should raise"
        except ValueError:
            pass


class TestHalfFovDeg:
    def test_portrait_lr_fov(self):
        # Full L/R FOV = 27.0747° → half = 13.537°
        result = half_fov_deg(SHEET["sensor_across"], SHEET["fl_mm"])
        assert near(2 * result, SHEET["fov_lr"], rel=1e-4)

    def test_portrait_fa_fov(self):
        # Full F/A FOV = 39.7146° → half = 19.857°
        result = half_fov_deg(SHEET["sensor_along"], SHEET["fl_mm"])
        assert near(2 * result, SHEET["fov_fa"], rel=1e-4)


class TestDiagPPtoLongEdge:
    def test_matches_spreadsheet(self):
        # sqrt((24.0768/2)^2 + 50^2) = 51.4288 mm
        result = diag_pp_to_long_edge_mm(SHEET["sensor_across"], SHEET["fl_mm"])
        assert near(result, SHEET["diag_image"])


class TestFlyingHeightForGsd:
    def test_nadir_height_matches_spreadsheet(self):
        # H = 8.5cm * 21mm / 0.0038mm = 469.737m
        H = flying_height_for_gsd(
            SHEET["target_gsd_cm"] / 100.0,
            SHEET["nadir_fl"],
            SHEET["px_mm"],
        )
        assert near(H, SHEET["H"])


# ---------------------------------------------------------------------------
# Four-corner footprint — verified against spreadsheet corner coordinates
# ---------------------------------------------------------------------------

class TestFourCornerFootprint:
    def _fp(self):
        return four_corner_footprint(
            SHEET["H"], SHEET["tilt_nadir"],
            SHEET["sensor_across"], SHEET["sensor_along"], SHEET["fl_mm"],
        )

    def test_near_edge_matches_spreadsheet(self):
        fp = self._fp()
        assert near(fp["near_edge_m"], SHEET["inner_edge"])

    def test_far_edge_matches_spreadsheet(self):
        fp = self._fp()
        assert near(fp["far_edge_m"], SHEET["outer_edge"])

    def test_near_length_matches_spreadsheet(self):
        fp = self._fp()
        assert near(fp["near_length_m"], SHEET["inner_length"])

    def test_far_length_matches_spreadsheet(self):
        fp = self._fp()
        assert near(fp["far_length_m"], SHEET["outer_length"])

    def test_near_top_corner_gx(self):
        fp = self._fp()
        assert near(fp["near_top"][0], 233.820120215362)

    def test_near_top_corner_gy(self):
        fp = self._fp()
        assert near(fp["near_top"][1], 184.236627207234)

    def test_far_top_corner_gx(self):
        fp = self._fp()
        assert near(fp["far_top"][0], 635.678729443168)

    def test_far_top_corner_gy(self):
        fp = self._fp()
        assert near(fp["far_top"][1], 277.525704570489)

    def test_nadir_camera_symmetric(self):
        # For 0° tilt the footprint should be symmetric about nadir
        fp = four_corner_footprint(1000.0, 0.0, 35.7, 23.8, 35.0)
        assert near(fp["near_edge_m"], -fp["far_edge_m"], abs_tol=1e-9)
        assert near(fp["centre_m"], 0.0, abs_tol=1e-9)

    def test_far_edge_farther_than_near(self):
        fp = self._fp()
        assert fp["far_edge_m"] > fp["near_edge_m"]

    def test_far_length_larger_than_near_length(self):
        # Far edge always has larger along-track footprint for oblique cameras
        fp = self._fp()
        assert fp["far_length_m"] > fp["near_length_m"]


# ---------------------------------------------------------------------------
# Ground intersections
# ---------------------------------------------------------------------------

class TestGroundIntersections:
    def _gi(self):
        return ground_intersections_flat_terrain(
            SHEET["H"], SHEET["tilt_nadir"],
            SHEET["sensor_across"], SHEET["sensor_along"], SHEET["fl_mm"],
        )

    def test_near_edge(self):
        assert near(self._gi().near_edge_m, SHEET["inner_edge"])

    def test_far_edge(self):
        assert near(self._gi().far_edge_m, SHEET["outer_edge"])

    def test_near_slant_matches_slope(self):
        # Slant = sqrt(H^2 + Gx_near^2), sheet calls this 'Slope PP to close edge'
        assert near(self._gi().near_slant_m, SHEET["slope_inner"])

    def test_far_slant_matches_slope(self):
        assert near(self._gi().far_slant_m, SHEET["slope_outer"])

    def test_near_length(self):
        assert near(self._gi().near_length_m, SHEET["inner_length"])

    def test_far_length(self):
        assert near(self._gi().far_length_m, SHEET["outer_length"])

    def test_centre_at_h_tan_theta(self):
        H     = SHEET["H"]
        theta = math.radians(SHEET["tilt_nadir"])
        gi    = self._gi()
        assert near(gi.centre_m, H * math.tan(theta))

    def test_nadir_camera_centre_zero(self):
        gi = ground_intersections_flat_terrain(1000.0, 0.0, 35.7, 23.8, 35.0)
        assert near(gi.centre_m, 0.0, abs_tol=1e-9)

    def test_nadir_camera_45deg_slant(self):
        # At 45° tilt, centre slant = H / cos(45°) = H * sqrt(2)
        H  = 1000.0
        gi = ground_intersections_flat_terrain(1000.0, 45.0, 24.0, 36.0, 50.0)
        assert near(gi.centre_slant_m, H / math.cos(math.radians(45.0)))

    def test_raises_zero_altitude(self):
        try:
            ground_intersections_flat_terrain(0.0, 30.0, 24.0, 36.0, 50.0)
            assert False, "should raise"
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# GSD
# ---------------------------------------------------------------------------

class TestGsdAtEdgeFull:
    def test_inner_gsd_matches_spreadsheet(self):
        gsd = gsd_at_edge_full(
            SHEET["H"], SHEET["inner_edge"],
            SHEET["px_mm"], SHEET["fl_mm"], SHEET["sensor_across"],
        )
        assert near(gsd * 100, SHEET["inner_gsd_cm"], rel=1e-3)

    def test_outer_gsd_matches_spreadsheet(self):
        gsd = gsd_at_edge_full(
            SHEET["H"], SHEET["outer_edge"],
            SHEET["px_mm"], SHEET["fl_mm"], SHEET["sensor_across"],
        )
        assert near(gsd * 100, SHEET["outer_gsd_cm"], rel=1e-3)

    def test_nadir_gsd_formula(self):
        # At nadir (Gx=0): slant=H, diag=sqrt(0+fl^2)=fl, GSD=px*H*1000/fl/1000=px/fl*H
        H, px, fl = 500.0, 0.004, 50.0
        gsd = gsd_at_edge_full(H, 0.0, px, fl, sensor_across_mm=fl * 2)
        # sensor_across=2*fl → diag=sqrt(fl^2+fl^2)=fl*sqrt(2), hmm, let me use exact:
        # For a nadir check with sensor_across → 0: diag → fl, GSD → px*H/fl
        # Use a narrow sensor so diag ≈ fl
        narrow_sensor = 0.001  # very narrow → diag ≈ fl
        gsd2 = gsd_at_edge_full(H, 0.0, px, fl, sensor_across_mm=narrow_sensor)
        expected = (px / fl) * H  # nadir formula
        assert near(gsd2, expected, rel=1e-3)

    def test_gsd_increases_from_near_to_far(self):
        # GSD must increase from near edge to far edge for an oblique camera
        gsd_near = gsd_at_edge_full(
            SHEET["H"], SHEET["inner_edge"],
            SHEET["px_mm"], SHEET["fl_mm"], SHEET["sensor_across"],
        )
        gsd_far = gsd_at_edge_full(
            SHEET["H"], SHEET["outer_edge"],
            SHEET["px_mm"], SHEET["fl_mm"], SHEET["sensor_across"],
        )
        assert gsd_far > gsd_near

    def test_gsd_scales_linearly_with_altitude(self):
        # GSD ∝ H at nadir (Gx=0), slant = H so GSD = px*H*1000/diag/1000
        gsd1 = gsd_at_edge_full(500.0,  0.0, 0.004, 50.0, 0.001)
        gsd2 = gsd_at_edge_full(1000.0, 0.0, 0.004, 50.0, 0.001)
        assert near(gsd2, 2.0 * gsd1)


# ---------------------------------------------------------------------------
# Footprint dimensions
# ---------------------------------------------------------------------------

class TestFootprintDimensions:
    def _fp(self):
        return footprint_dimensions(
            SHEET["H"], SHEET["tilt_nadir"],
            SHEET["sensor_across"], SHEET["sensor_along"], SHEET["fl_mm"],
        )

    def test_across_track_width(self):
        assert near(self._fp().across_track_m, SHEET["swath"])

    def test_near_length(self):
        assert near(self._fp().near_length_m, SHEET["inner_length"])

    def test_far_length(self):
        assert near(self._fp().far_length_m, SHEET["outer_length"])

    def test_near_smaller_than_far(self):
        fp = self._fp()
        assert fp.near_length_m < fp.far_length_m

    def test_centre_length_between_near_and_far(self):
        fp = self._fp()
        assert fp.near_length_m < fp.centre_length_m < fp.far_length_m

    def test_nadir_camera_symmetric_footprint(self):
        fp = footprint_dimensions(1000.0, 0.0, 35.7, 23.8, 35.0)
        assert near(fp.near_edge_m, -fp.far_edge_m, abs_tol=1e-9)


# ---------------------------------------------------------------------------
# Nadir camera (from spreadsheet Landscape sheet)
# ---------------------------------------------------------------------------

class TestNadirCamera:
    def test_nadir_swath(self):
        # Nadir camera: fl=21mm, landscape (wide=36.1152mm across-track)
        H  = SHEET["H"]
        fp = footprint_dimensions(H, 0.0, SHEET["sensor_along"], SHEET["sensor_across"], SHEET["nadir_fl"])
        # landscape: sensor_along is the WIDE axis → across-track
        # nadir_swath = 807.84m
        assert near(fp.across_track_m, SHEET["nadir_swath"])

    def test_nadir_inner_half_swath(self):
        H  = SHEET["H"]
        fp = footprint_dimensions(H, 0.0, SHEET["sensor_along"], SHEET["sensor_across"], SHEET["nadir_fl"])
        assert near(fp.far_edge_m, SHEET["nadir_inner"])

    def test_nadir_fa_half(self):
        H  = SHEET["H"]
        # For nadir camera, centre_length = along-track footprint
        fp = footprint_dimensions(H, 0.0, SHEET["sensor_along"], SHEET["sensor_across"], SHEET["nadir_fl"])
        assert near(fp.centre_length_m, SHEET["nadir_fa"])


# ---------------------------------------------------------------------------
# Swath and spacing helpers
# ---------------------------------------------------------------------------

class TestEffectiveSwath:
    def test_zero_sidelap(self):
        assert near(effective_swath_from_sidelap(100.0, 0.0), 100.0)

    def test_thirty_percent(self):
        assert near(effective_swath_from_sidelap(100.0, 0.3), 70.0)

    def test_raises_ge_one(self):
        try:
            effective_swath_from_sidelap(100.0, 1.0)
            assert False
        except ValueError:
            pass


class TestLineSpacing:
    def test_thirty_percent(self):
        assert near(line_spacing_from_sidelap(200.0, 0.3), 140.0)


class TestPhotoSpacing:
    def test_sixty_percent(self):
        assert near(photo_spacing_from_forward_overlap(100.0, 0.6), 40.0)

    def test_eighty_percent(self):
        assert near(photo_spacing_from_forward_overlap(100.0, 0.8), 20.0)

    def test_raises_ge_one(self):
        try:
            photo_spacing_from_forward_overlap(100.0, 1.0)
            assert False
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# calculate_camera_solution integration
# ---------------------------------------------------------------------------

class TestCalculateCameraSolution:
    def _sol(self):
        # Portrait oblique camera matching Landscape sheet
        # Native: w=36.1152mm (long), h=24.0768mm (short)
        # Portrait → narrow (h) across-track
        return calculate_camera_solution(
            altitude_m=SHEET["H"],
            tilt_from_nadir_deg=SHEET["tilt_nadir"],
            sensor_w_native_mm=SHEET["sensor_along"],   # 36.1152 = long axis
            sensor_h_native_mm=SHEET["sensor_across"],  # 24.0768 = short axis
            image_w_native_px=9504,
            image_h_native_px=6336,
            focal_length_mm=SHEET["fl_mm"],
            orientation="portrait",
            tilt_axis="across",
            label="Right oblique",
        )

    def test_pixel_size(self):
        assert near(self._sol().pixel_size_mm, SHEET["px_mm"])

    def test_near_edge(self):
        assert near(self._sol().near_edge_m, SHEET["inner_edge"])

    def test_far_edge(self):
        assert near(self._sol().far_edge_m, SHEET["outer_edge"])

    def test_near_gsd(self):
        assert near(self._sol().near_gsd_m * 100, SHEET["inner_gsd_cm"], rel=1e-3)

    def test_far_gsd(self):
        assert near(self._sol().far_gsd_m * 100, SHEET["outer_gsd_cm"], rel=1e-3)

    def test_footprint_across(self):
        assert near(self._sol().footprint_across_m, SHEET["swath"])

    def test_near_length(self):
        assert near(self._sol().near_length_m, SHEET["inner_length"])

    def test_far_length(self):
        assert near(self._sol().far_length_m, SHEET["outer_length"])

    def test_near_slant(self):
        assert near(self._sol().near_slant_m, SHEET["slope_inner"])

    def test_far_slant(self):
        assert near(self._sol().far_slant_m, SHEET["slope_outer"])

    def test_fov_lr(self):
        assert near(self._sol().full_fov_across_deg, SHEET["fov_lr"])

    def test_fov_fa(self):
        assert near(self._sol().full_fov_along_deg, SHEET["fov_fa"])


# ---------------------------------------------------------------------------
# calculate_multicamera_solution integration
# ---------------------------------------------------------------------------

class TestMulticameraSolution:
    def _make_system(self):
        def _cam(tilt, lbl):
            return calculate_camera_solution(
                SHEET["H"], tilt,
                sensor_w_native_mm=SHEET["sensor_along"],
                sensor_h_native_mm=SHEET["sensor_across"],
                image_w_native_px=9504,
                image_h_native_px=6336,
                focal_length_mm=SHEET["fl_mm"],
                orientation="portrait",
                tilt_axis="across",
                label=lbl,
            )
        return [_cam(SHEET["tilt_nadir"], "Right oblique"), _cam(-SHEET["tilt_nadir"], "Left oblique")]

    def test_combined_swath(self):
        sols = self._make_system()
        mc = calculate_multicamera_solution(
            sols, "2_oblique", SHEET["H"], 50.0, 0.6, 0.3, False,
        )
        # Combined swath = 2 * outer_edge (symmetric)
        expected = 2 * SHEET["outer_edge"]
        assert near(mc.combined_swath_m, expected)

    def test_line_spacing(self):
        sols = self._make_system()
        mc = calculate_multicamera_solution(
            sols, "2_oblique", SHEET["H"], 50.0, 0.6, 0.3, False,
        )
        assert near(mc.recommended_line_spacing_m, mc.combined_swath_m * 0.7)

    def test_photo_spacing_uses_near_length(self):
        sols = self._make_system()
        mc = calculate_multicamera_solution(
            sols, "2_oblique", SHEET["H"], 50.0, 0.6, 0.3, False,
        )
        expected = SHEET["inner_length"] * (1 - 0.6)
        assert near(mc.recommended_photo_spacing_m, expected)

    def test_forward_overlap_near_matches_target(self):
        sols = self._make_system()
        mc = calculate_multicamera_solution(
            sols, "2_oblique", SHEET["H"], 50.0, 0.6, 0.3, False,
        )
        assert near(mc.forward_overlap_near, 0.6)

    def test_forward_overlap_far_exceeds_near(self):
        # Because far footprint is larger, same photo spacing gives higher overlap there
        sols = self._make_system()
        mc = calculate_multicamera_solution(
            sols, "2_oblique", SHEET["H"], 50.0, 0.6, 0.3, False,
        )
        assert mc.forward_overlap_far > mc.forward_overlap_near

    def test_reciprocal_recommended_for_oblique(self):
        sols = self._make_system()
        mc = calculate_multicamera_solution(
            sols, "2_oblique", SHEET["H"], 50.0, 0.6, 0.3, False,
        )
        assert mc.reciprocal_recommended is True


# ---------------------------------------------------------------------------
# Standalone runner (no pytest)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import traceback

    classes = [
        TestUnitConversion, TestPixelSizeMm, TestNormalizeTiltAngle,
        TestHalfFovDeg, TestDiagPPtoLongEdge, TestFlyingHeightForGsd,
        TestFourCornerFootprint, TestGroundIntersections, TestGsdAtEdgeFull,
        TestFootprintDimensions, TestNadirCamera, TestEffectiveSwath,
        TestLineSpacing, TestPhotoSpacing, TestCalculateCameraSolution,
        TestMulticameraSolution,
    ]

    passed = failed = 0
    for cls in classes:
        obj = cls()
        for name in [m for m in dir(cls) if m.startswith("test_")]:
            try:
                getattr(obj, name)()
                print(f"  PASS  {cls.__name__}.{name}")
                passed += 1
            except Exception as e:
                print(f"  FAIL  {cls.__name__}.{name}: {e}")
                traceback.print_exc()
                failed += 1

    print(f"\n{'='*50}")
    print(f"  {passed} passed   {failed} failed")
