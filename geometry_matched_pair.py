"""
geometry.py
===========
Pure geometry functions for 4-camera oblique aerial survey planning.

Verified against Oblique_setup9_working_2.xls — all values match to 3+ decimal places.

Coordinate convention
---------------------
  x  →  across-track  (positive = oblique camera side, away from nadir)
  y  →  along-track   (positive forward)
  z  →  vertical      (positive up, aircraft is at z = H)

Angle convention
----------------
  tilt_from_nadir : 0° = straight down, 90° = horizontal
  tilt_from_horiz : 0° = horizontal,    90° = straight down  (reference spreadsheet uses this)

Internally all calculations use tilt_from_nadir.
Call normalize_tilt_angle() to convert.

=============================================================================
FIX 1 — Sensor orientation
=============================================================================
The reference spreadsheet mounts Left/Right oblique cameras in PORTRAIT
orientation so the NARROW sensor axis is across-track.  This limits the
extreme GSD stretch at the far edge and maximises the along-track footprint.

  Left/Right oblique camera (portrait):
    sensor_across_mm = narrow dimension  (e.g. 24.08 mm for A7R V)
    sensor_along_mm  = long dimension    (e.g. 36.11 mm for A7R V)

  Nadir camera (landscape, typical):
    sensor_across_mm = long dimension    (e.g. 36.11 mm)
    sensor_along_mm  = narrow dimension  (e.g. 24.08 mm)

The caller is responsible for passing them in the right order.
calculate_camera_solution() documents the expected order.

=============================================================================
FIX 2 — Ground intercepts: exact 4-corner projection
=============================================================================
Two tilt axes are supported:

  tilt_axis='across'  — camera tilts about the ALONG-TRACK (Y) axis.
                        Used for Left/Right oblique cameras.
                        Rotation matrix R_y(θ):
                            r_x =  cos(θ)·sx + sin(θ)
                            r_y =  sy
                            r_z = -sin(θ)·sx + cos(θ)

  tilt_axis='along'   — camera tilts about the ACROSS-TRACK (X) axis.
                        Used for Fore/Aft oblique cameras.
                        Rotation matrix R_x(θ):
                            r_x =  sx
                            r_y =  cos(θ)·sy + sin(θ)   (for forward tilt)
                            r_z = -sin(θ)·sy + cos(θ)

In both cases sx = tan(pixel_angle_across), sy = tan(pixel_angle_along),
and the ground intercept is G_x = H·r_x/r_z, G_y = H·r_y/r_z.

For the across-track tilt:
    G_x(near) = H · tan(θ − φ_w)
    G_x(far)  = H · tan(θ + φ_w)
    G_y varies across the image width (larger at the far side)

For the along-track tilt (symmetric about the across-track axis):
    G_y(fore) = H · tan(θ + φ_h)   (forward = positive y)
    G_y(aft)  = H · tan(θ − φ_h)   (may be negative if tilt < half_fov)
    G_x varies across the image height (larger at the far/fore side)

=============================================================================
FIX 3 — GSD formula: slant-plane definition (matches reference spreadsheet)
=============================================================================
    slant_2d  = sqrt(H² + G_x²)          [2D slant to the edge midpoint]
    diag      = sqrt((sensor_across/2)² + focal_length²)   [mm, in image plane]
    GSD       = pixel_size_mm · slant_2d_mm / diag_mm

This equals: GSD = (pixel_size / focal_length) · slant_2d · cos(φ_w)

Physically: 'diag' is the actual 3D distance from the rear nodal point to the
midpoint of the long sensor edge — the effective focal distance for that ray.
The difference from the pure focal-length formula is the factor cos(φ_w), which
is ~0.97 at typical FOVs (small but consistent).

This is a slant-plane GSD.  It differs from the ground-projected GSD:
    GSD_ground = (pixel_size / focal_length) · H / cos²(α)
which gives the pixel footprint as projected onto the horizontal ground plane
(larger at high oblique angles).  The reference spreadsheet uses the slant-plane
definition; use that for consistency with published flight plans.

=============================================================================
FIX 4 — Flying height
=============================================================================
The reference spreadsheet sets H so the NADIR camera achieves the target GSD:

    H = GSD_target_m · focal_length_nadir_mm / pixel_size_mm

The oblique cameras at the same H will produce larger GSD values by design.
"""

import math
from dataclasses import dataclass, field
from typing import List, Tuple


# ---------------------------------------------------------------------------
# Unit helpers
# ---------------------------------------------------------------------------

def m_to_unit(value_m: float, unit: str) -> float:
    """Convert metres to the requested display unit."""
    factors = {"m": 1.0, "ft": 3.28084, "cm": 100.0, "mm": 1000.0}
    if unit not in factors:
        raise ValueError(f"Unknown unit {unit!r}. Use: m, ft, cm, mm.")
    return value_m * factors[unit]


def unit_to_m(value: float, unit: str) -> float:
    """Convert from display unit to metres."""
    factors = {"m": 1.0, "ft": 0.3048, "cm": 0.01, "mm": 0.001}
    if unit not in factors:
        raise ValueError(f"Unknown unit {unit!r}. Use: m, ft, cm, mm.")
    return value * factors[unit]


def mm_to_unit(value_mm: float, unit: str) -> float:
    """Convert millimetres to the requested display unit."""
    return m_to_unit(value_mm * 0.001, unit)


# ---------------------------------------------------------------------------
# Sensor / camera helpers
# ---------------------------------------------------------------------------

def pixel_size_mm(sensor_dim_mm: float, image_dim_px: int) -> float:
    """
    Physical pixel size (mm), assuming square pixels.

        pixel_size = sensor_dimension_mm / image_dimension_px

    Args:
        sensor_dim_mm : sensor width or height in mm (pass the across-track dim)
        image_dim_px  : corresponding pixel count

    Returns:
        pixel size in mm
    """
    if sensor_dim_mm <= 0:
        raise ValueError("sensor_dim_mm must be > 0")
    if image_dim_px <= 0:
        raise ValueError("image_dim_px must be > 0")
    return sensor_dim_mm / image_dim_px


def focal_length_px(focal_length_mm: float, px_size_mm: float) -> float:
    """
    Focal length in pixels.

        f_px = focal_length_mm / pixel_size_mm
    """
    if px_size_mm <= 0:
        raise ValueError("px_size_mm must be > 0")
    return focal_length_mm / px_size_mm


def normalize_tilt_angle(angle_deg: float, convention: str) -> float:
    """
    Return the tilt angle measured *from nadir* (degrees).

    Args:
        angle_deg  : tilt value
        convention : 'nadir' — 0 = straight down, 90 = horizontal
                     'horiz' — 0 = horizontal,    90 = straight down

    Returns:
        tilt from nadir in degrees
    """
    if convention == "nadir":
        return float(angle_deg)
    elif convention == "horiz":
        return 90.0 - float(angle_deg)
    else:
        raise ValueError(f"Unknown convention {convention!r}. Use 'nadir' or 'horiz'.")


def half_fov_deg(sensor_dim_mm: float, focal_length_mm: float) -> float:
    """
    Half field-of-view for one sensor axis (degrees).

        half_fov = atan(sensor_dim / (2 · focal_length))
    """
    return math.degrees(math.atan(sensor_dim_mm / (2.0 * focal_length_mm)))


def diag_pp_to_long_edge_mm(sensor_across_mm: float, focal_length_mm: float) -> float:
    """
    3D distance in the image from the principal point to the midpoint of the
    long (across-track) sensor edge.

        diag = sqrt( (sensor_across / 2)² + focal_length² )

    This is used as the effective focal distance in the GSD formula (FIX 3).
    It equals focal_length / cos(half_fov_across).

    Args:
        sensor_across_mm : across-track sensor dimension in mm
        focal_length_mm  : focal length in mm

    Returns:
        diagonal distance in mm
    """
    return math.sqrt((sensor_across_mm / 2.0) ** 2 + focal_length_mm ** 2)


def flying_height_for_gsd(
    target_gsd_m: float,
    focal_length_mm: float,
    px_size_mm: float,
) -> float:
    """
    AGL altitude so a nadir camera achieves the target GSD (FIX 4).

        H = target_gsd_m · focal_length_mm / pixel_size_mm

    The oblique cameras at the same height will have larger (worse) GSD by design.

    Args:
        target_gsd_m    : desired nadir GSD in metres per pixel
        focal_length_mm : nadir camera focal length in mm
        px_size_mm      : pixel size in mm

    Returns:
        altitude AGL in metres
    """
    if px_size_mm <= 0:
        raise ValueError("px_size_mm must be > 0")
    if focal_length_mm <= 0:
        raise ValueError("focal_length_mm must be > 0")
    return target_gsd_m * focal_length_mm / px_size_mm


# ---------------------------------------------------------------------------
# Core ray projection (exact pinhole, flat terrain)
# ---------------------------------------------------------------------------

def _project_ray(
    sx: float,
    sy: float,
    theta_rad: float,
    H: float,
    tilt_axis: str = "across",
) -> Tuple[float, float]:
    """
    Project a normalised camera ray to flat ground.

    tilt_axis='across'  — camera rotates about the along-track (Y) axis.
        R_y(θ):  r_x =  cos(θ)·sx + sin(θ)
                 r_y =  sy
                 r_z = -sin(θ)·sx + cos(θ)

    tilt_axis='along'   — camera rotates about the across-track (X) axis.
        R_x(θ):  r_x =  sx
                 r_y =  cos(θ)·sy + sin(θ)   (positive θ tilts forward)
                 r_z = -sin(θ)·sy + cos(θ)

    Args:
        sx        : tan(pixel_angle_across) in camera frame
        sy        : tan(pixel_angle_along) in camera frame
        theta_rad : camera tilt from nadir in radians
        H         : altitude AGL in metres
        tilt_axis : 'across' (L/R oblique) or 'along' (fore/aft oblique)

    Returns:
        (G_x, G_y) ground position in metres, or (±inf, ±inf) if ray is horizontal.
    """
    if tilt_axis == "across":
        r_x =  math.cos(theta_rad) * sx + math.sin(theta_rad)
        r_y =  sy
        r_z = -math.sin(theta_rad) * sx + math.cos(theta_rad)
    elif tilt_axis == "along":
        r_x =  sx
        r_y =  math.cos(theta_rad) * sy + math.sin(theta_rad)
        r_z = -math.sin(theta_rad) * sy + math.cos(theta_rad)
    else:
        raise ValueError(f"tilt_axis must be 'across' or 'along', got {tilt_axis!r}")

    if r_z <= 1e-12:
        return math.copysign(float("inf"), r_x), math.copysign(float("inf"), r_y)
    return H * r_x / r_z, H * r_y / r_z


def four_corner_footprint(
    altitude_m: float,
    tilt_from_nadir_deg: float,
    sensor_across_mm: float,
    sensor_along_mm: float,
    focal_length_mm: float,
    tilt_axis: str = "across",
) -> dict:
    """
    Exact 4-corner ground footprint of a tilted camera on flat terrain.

    Supports both tilt axes (FIX 2 extended):

        tilt_axis='across'  — tilts left or right (L/R oblique cameras).
            near/far refer to across-track distance from nadir.
            near_edge_m < far_edge_m (both positive for a tilted camera).

        tilt_axis='along'   — tilts forward or backward (fore/aft oblique cameras).
            near/far refer to along-track distance from nadir.
            For a forward-tilting camera:
                far_edge_m  = G_y at the forward edge  (largest +y)
                near_edge_m = G_y at the rearward edge (smallest y, may be negative)

    Corner naming is consistent in both cases:
        near_top = closer-to-nadir, forward (+y)
        near_bot = closer-to-nadir, rearward (−y)
        far_top  = farther-from-nadir, forward (+y)
        far_bot  = farther-from-nadir, rearward (−y)

    For 'across' tilt:  near/far varies in G_x; top/bot varies in G_y.
    For 'along' tilt:   near/far varies in G_y; left/right varies in G_x.

    Args:
        altitude_m          : AGL altitude in metres (> 0)
        tilt_from_nadir_deg : camera tilt from nadir in degrees
        sensor_across_mm    : across-track sensor dimension in mm
        sensor_along_mm     : along-track sensor dimension in mm
        focal_length_mm     : focal length in mm
        tilt_axis           : 'across' or 'along'

    Returns:
        dict with keys:
            near_top, near_bot, far_top, far_bot — (G_x, G_y) tuples in metres
            near_edge_m    — distance from nadir to the near edge
                             (G_x for 'across', G_y for 'along')
            far_edge_m     — distance from nadir to the far edge
            near_length_m  — footprint extent perpendicular to tilt direction at near edge
            far_length_m   — footprint extent perpendicular to tilt direction at far edge
            centre_m       — distance from nadir to image centre ray
            tilt_axis      — echoed back
    """
    if altitude_m <= 0:
        raise ValueError("altitude_m must be > 0")

    theta = math.radians(tilt_from_nadir_deg)
    phi_w = math.atan(sensor_across_mm / (2.0 * focal_length_mm))
    phi_h = math.atan(sensor_along_mm  / (2.0 * focal_length_mm))

    sx_near = -math.tan(phi_w)
    sx_far  = +math.tan(phi_w)
    sy_top  = +math.tan(phi_h)
    sy_bot  = -math.tan(phi_h)

    near_top = _project_ray(sx_near, sy_top, theta, altitude_m, tilt_axis)
    near_bot = _project_ray(sx_near, sy_bot, theta, altitude_m, tilt_axis)
    far_top  = _project_ray(sx_far,  sy_top, theta, altitude_m, tilt_axis)
    far_bot  = _project_ray(sx_far,  sy_bot, theta, altitude_m, tilt_axis)
    centre   = _project_ray(0.0,     0.0,    theta, altitude_m, tilt_axis)

    if tilt_axis == "across":
        # near/far in G_x direction; length measured in G_y direction
        near_edge_m   = near_top[0]                        # G_x at near column
        far_edge_m    = far_top[0]                         # G_x at far column
        near_length_m = abs(near_top[1] - near_bot[1])    # along-track at near edge
        far_length_m  = abs(far_top[1]  - far_bot[1])     # along-track at far edge
        centre_m      = centre[0]
    else:  # 'along'
        # For a fore/aft camera tilted forward:
        # near side = rearward (smaller G_y), far side = forward (larger G_y)
        # near_top/near_bot have smaller |G_y|; far_top/far_bot have larger |G_y|
        # But our sx/sy assignment above puts sy_top=+phi_h (forward) → this is the far side
        # Reassign: for along-tilt, "far" = forward (larger G_y positive)
        #           "near" = rearward
        # near_top (sx_near, sy_top) → left-forward corner
        # far_top  (sx_far,  sy_top) → right-forward corner
        # So the "far" edge in the along direction is G_y of far_top/near_top
        # and the "near" edge is G_y of far_bot/near_bot
        # Width (across-track) at far edge: distance between near_top and far_top in G_x
        # Width at near edge: distance between near_bot and far_bot in G_x
        near_edge_m   = (near_bot[1] + far_bot[1]) / 2.0   # G_y at rear edge (avg)
        far_edge_m    = (near_top[1] + far_top[1]) / 2.0   # G_y at fore edge (avg)
        near_length_m = abs(near_bot[0] - far_bot[0])      # across-track width at rear
        far_length_m  = abs(near_top[0] - far_top[0])      # across-track width at fore
        centre_m      = centre[1]

    return dict(
        near_top=near_top,
        near_bot=near_bot,
        far_top=far_top,
        far_bot=far_bot,
        near_edge_m=near_edge_m,
        far_edge_m=far_edge_m,
        near_length_m=near_length_m,
        far_length_m=far_length_m,
        centre_m=centre_m,
        tilt_axis=tilt_axis,
    )


# ---------------------------------------------------------------------------
# Public ground-intersection dataclass and function
# ---------------------------------------------------------------------------

@dataclass
class GroundIntersections:
    """
    Ground intercept distances from the aircraft nadir track (metres).

    All Gx distances: positive = oblique camera side; negative = opposite side.
    For a nadir camera (tilt=0) near_edge_m < 0 and far_edge_m > 0 (symmetric).

    Slant ranges are 2D: sqrt(H² + Gx²), i.e. in the cross-section plane.
    """
    near_edge_m: float       # across-track: near footprint edge from nadir
    centre_m: float          # across-track: image centre ray from nadir
    far_edge_m: float        # across-track: far footprint edge from nadir
    near_slant_m: float      # 2D slant to near edge midpoint
    centre_slant_m: float    # 2D slant to image centre
    far_slant_m: float       # 2D slant to far edge midpoint
    near_angle_deg: float    # nadir angle at near edge = atan(Gx_near / H)
    centre_angle_deg: float  # nadir angle at image centre = tilt_from_nadir
    far_angle_deg: float     # nadir angle at far edge = atan(Gx_far / H)
    near_length_m: float     # along-track footprint at near edge (exact)
    far_length_m: float      # along-track footprint at far edge (exact)


def ground_intersections_flat_terrain(
    altitude_m: float,
    tilt_from_nadir_deg: float,
    sensor_across_mm: float,
    sensor_along_mm: float,
    focal_length_mm: float,
    tilt_axis: str = "across",
) -> GroundIntersections:
    """
    Exact ground intercepts on flat terrain using the pinhole model (FIX 2).

    Pass sensor_across_mm as the dimension in the across-track direction.
    Pass tilt_axis='along' for fore/aft cameras.

    Args:
        altitude_m          : AGL altitude in metres
        tilt_from_nadir_deg : camera tilt from nadir in degrees (0 = nadir)
        sensor_across_mm    : across-track sensor dimension in mm
        sensor_along_mm     : along-track sensor dimension in mm
        focal_length_mm     : focal length in mm
        tilt_axis           : 'across' (L/R oblique) or 'along' (fore/aft oblique)

    Returns:
        GroundIntersections dataclass
    """
    fp = four_corner_footprint(
        altitude_m, tilt_from_nadir_deg,
        sensor_across_mm, sensor_along_mm, focal_length_mm,
        tilt_axis=tilt_axis,
    )
    H = altitude_m

    def _slant(gx, gy):
        # 2D slant in the plane containing the tilt direction
        if tilt_axis == "across":
            return math.sqrt(H ** 2 + gx ** 2)
        else:
            return math.sqrt(H ** 2 + gy ** 2)

    def _angle(gx, gy):
        if tilt_axis == "across":
            return math.degrees(math.atan2(gx, H))
        else:
            return math.degrees(math.atan2(gy, H))

    # Representative ground points for near/centre/far
    if tilt_axis == "across":
        gx_near, gy_near = fp["near_edge_m"], 0.0
        gx_ctr,  gy_ctr  = fp["centre_m"],    0.0
        gx_far,  gy_far  = fp["far_edge_m"],  0.0
    else:
        gx_near, gy_near = 0.0, fp["near_edge_m"]
        gx_ctr,  gy_ctr  = 0.0, fp["centre_m"]
        gx_far,  gy_far  = 0.0, fp["far_edge_m"]

    return GroundIntersections(
        near_edge_m=fp["near_edge_m"],
        centre_m=fp["centre_m"],
        far_edge_m=fp["far_edge_m"],
        near_slant_m=_slant(gx_near, gy_near),
        centre_slant_m=_slant(gx_ctr, gy_ctr),
        far_slant_m=_slant(gx_far, gy_far),
        near_angle_deg=_angle(gx_near, gy_near),
        centre_angle_deg=_angle(gx_ctr, gy_ctr),
        far_angle_deg=_angle(gx_far, gy_far),
        near_length_m=fp["near_length_m"],
        far_length_m=fp["far_length_m"],
    )


# ---------------------------------------------------------------------------
# GSD (FIX 3 — slant-plane formula matching reference spreadsheet)
# ---------------------------------------------------------------------------

def gsd_at_edge_full(
    altitude_m: float,
    gx_m: float,
    px_size_mm_val: float,
    focal_length_mm: float,
    sensor_across_mm: float,
) -> float:
    """
    GSD at a ground point specified by its across-track distance from nadir.

    Formula (verified against reference spreadsheet):

        slant_2d_mm = sqrt(H² + Gx²) · 1000         [2D slant range in mm]
        diag_mm     = sqrt((sensor_across/2)² + fl²)  [image-plane diagonal to long edge]
        GSD_m       = pixel_size_mm · slant_2d_mm / diag_mm / 1000

    Equivalent to: GSD = (pixel_size / focal_length) · slant_2d · cos(half_fov_across)

    This is a slant-plane GSD. See module docstring for discussion of slant vs ground.

    Args:
        altitude_m       : AGL altitude in metres
        gx_m             : across-track ground distance from nadir in metres
        px_size_mm_val   : pixel size in mm
        focal_length_mm  : focal length in mm
        sensor_across_mm : across-track sensor dimension in mm

    Returns:
        GSD in metres per pixel
    """
    slant_2d_mm = math.sqrt(altitude_m ** 2 + gx_m ** 2) * 1000.0
    diag_mm     = diag_pp_to_long_edge_mm(sensor_across_mm, focal_length_mm)
    return (px_size_mm_val * slant_2d_mm / diag_mm) / 1000.0  # convert mm→m


# ---------------------------------------------------------------------------
# Footprint dimensions dataclass
# ---------------------------------------------------------------------------

@dataclass
class FootprintDimensions:
    """Ground footprint of one camera image on flat terrain (all in metres)."""
    across_track_m: float    # far_edge − near_edge
    near_length_m: float     # along-track extent at near (inner) edge — SMALLEST value
    centre_length_m: float   # along-track extent at image centre column
    far_length_m: float      # along-track extent at far (outer) edge — LARGEST value
    near_edge_m: float       # across-track: near edge from nadir
    far_edge_m: float        # across-track: far edge from nadir
    centre_m: float          # across-track: image centre from nadir


def footprint_dimensions(
    altitude_m: float,
    tilt_from_nadir_deg: float,
    sensor_across_mm: float,
    sensor_along_mm: float,
    focal_length_mm: float,
    tilt_axis: str = "across",
) -> FootprintDimensions:
    """
    Exact ground footprint using the 4-corner pinhole projection (FIX 2).

    The footprint perpendicular to the tilt direction is NOT constant —
    it is smaller at the near edge and larger at the far edge.

    Args:
        altitude_m          : AGL altitude in metres
        tilt_from_nadir_deg : camera tilt from nadir in degrees
        sensor_across_mm    : across-track sensor dimension in mm
        sensor_along_mm     : along-track sensor dimension in mm
        focal_length_mm     : focal length in mm
        tilt_axis           : 'across' or 'along'

    Returns:
        FootprintDimensions dataclass
    """
    fp = four_corner_footprint(
        altitude_m, tilt_from_nadir_deg,
        sensor_across_mm, sensor_along_mm, focal_length_mm,
        tilt_axis=tilt_axis,
    )

    theta = math.radians(tilt_from_nadir_deg)

    if tilt_axis == "across":
        phi_along = math.atan(sensor_along_mm / (2.0 * focal_length_mm))
        centre_length = 2.0 * altitude_m * math.tan(phi_along) / math.cos(theta)
        across_track_m = fp["far_edge_m"] - fp["near_edge_m"]
    else:
        # For along-tilt: "across" is the dimension perpendicular to the tilt
        phi_across = math.atan(sensor_across_mm / (2.0 * focal_length_mm))
        centre_length = 2.0 * altitude_m * math.tan(phi_across) / math.cos(theta)
        across_track_m = fp["far_edge_m"] - fp["near_edge_m"]  # now measured in along-track

    return FootprintDimensions(
        across_track_m=across_track_m,
        near_length_m=fp["near_length_m"],
        centre_length_m=centre_length,
        far_length_m=fp["far_length_m"],
        near_edge_m=fp["near_edge_m"],
        far_edge_m=fp["far_edge_m"],
        centre_m=fp["centre_m"],
    )


# ---------------------------------------------------------------------------
# Swath and spacing helpers
# ---------------------------------------------------------------------------

def effective_swath_from_sidelap(footprint_across_m: float, sidelap_fraction: float) -> float:
    """
    Usable (non-overlapping) swath per strip.

        effective_swath = footprint_across · (1 − sidelap_fraction)
    """
    if not 0.0 <= sidelap_fraction < 1.0:
        raise ValueError("sidelap_fraction must be in [0, 1)")
    return footprint_across_m * (1.0 - sidelap_fraction)


def line_spacing_from_sidelap(combined_swath_m: float, sidelap_fraction: float) -> float:
    """
    Nadir-track to nadir-track line spacing for a target sidelap.

        line_spacing = combined_swath · (1 − sidelap_fraction)

    Pass the full system swath (all cameras combined), not a single camera footprint.
    """
    return effective_swath_from_sidelap(combined_swath_m, sidelap_fraction)


def photo_spacing_from_forward_overlap(
    footprint_along_m: float,
    forward_overlap_fraction: float,
) -> float:
    """
    Along-track photo spacing for a target forward overlap.

        photo_spacing = footprint_along · (1 − forward_overlap_fraction)

    Use footprint_along_m = near_length_m (smallest value) for a conservative
    estimate that ensures the target overlap is met at the near edge.
    """
    if not 0.0 <= forward_overlap_fraction < 1.0:
        raise ValueError("forward_overlap_fraction must be in [0, 1)")
    return footprint_along_m * (1.0 - forward_overlap_fraction)


# ---------------------------------------------------------------------------
# Per-camera complete solution
# ---------------------------------------------------------------------------

@dataclass
class CameraSolution:
    """Complete geometry solution for one physical camera."""
    label: str
    tilt_from_nadir_deg: float
    orientation: str              # 'portrait' or 'landscape'
    tilt_axis: str                # 'across' (L/R) or 'along' (fore/aft)
    # Sensor
    pixel_size_mm: float
    sensor_across_mm: float       # physical across-track dimension after orientation applied
    sensor_along_mm: float        # physical along-track dimension after orientation applied
    sensor_w_native_mm: float     # native long axis of sensor (as manufactured)
    sensor_h_native_mm: float     # native short axis of sensor
    diag_image_mm: float
    half_fov_across_deg: float
    half_fov_along_deg: float
    full_fov_across_deg: float
    full_fov_along_deg: float
    # Ground intercepts (from nadir track, metres)
    near_edge_m: float
    centre_m: float
    far_edge_m: float
    # 2D slant ranges (metres)
    near_slant_m: float
    centre_slant_m: float
    far_slant_m: float
    # Nadir angles at key positions (degrees)
    near_angle_deg: float
    centre_angle_deg: float
    far_angle_deg: float
    # GSD — slant-plane definition (metres/pixel)
    near_gsd_m: float
    centre_gsd_m: float
    far_gsd_m: float
    # Footprint (metres)
    footprint_across_m: float
    near_length_m: float
    centre_length_m: float
    far_length_m: float
    # 4 ground corners (G_x, G_y) in metres from nadir
    corner_near_top: tuple
    corner_near_bot: tuple
    corner_far_top: tuple
    corner_far_bot: tuple


def calculate_camera_solution(
    altitude_m: float,
    tilt_from_nadir_deg: float,
    sensor_w_native_mm: float,
    sensor_h_native_mm: float,
    image_w_native_px: int,
    image_h_native_px: int,
    focal_length_mm: float,
    orientation: str = "portrait",
    tilt_axis: str = "across",
    label: str = "",
) -> CameraSolution:
    """
    Full geometry solution for a single camera.

    ORIENTATION — controls which sensor dimension is across-track:

        'portrait'  — narrow (short) axis across-track, long axis along-track.
                      Recommended for L/R oblique cameras.
                      sensor_across = sensor_h_native (shorter)
                      sensor_along  = sensor_w_native (longer)

        'landscape' — long axis across-track, narrow axis along-track.
                      Typical for nadir cameras.
                      sensor_across = sensor_w_native (longer)
                      sensor_along  = sensor_h_native (shorter)

    TILT AXIS:
        'across' — camera tilts left/right (about along-track Y axis). Default.
        'along'  — camera tilts fore/aft   (about across-track X axis).

    Args:
        altitude_m          : AGL altitude in metres
        tilt_from_nadir_deg : camera tilt from nadir in degrees
        sensor_w_native_mm  : native LONG sensor dimension in mm (manufacturer spec)
        sensor_h_native_mm  : native SHORT sensor dimension in mm
        image_w_native_px   : pixel count along native width
        image_h_native_px   : pixel count along native height
        focal_length_mm     : focal length in mm
        orientation         : 'portrait' or 'landscape'
        tilt_axis           : 'across' or 'along'
        label               : display label

    Returns:
        CameraSolution dataclass
    """
    # Resolve sensor dimensions based on orientation
    if orientation == "portrait":
        sensor_across_mm  = sensor_h_native_mm   # narrow → across-track
        sensor_along_mm   = sensor_w_native_mm   # long   → along-track
        image_across_px   = image_h_native_px
        image_along_px    = image_w_native_px
    elif orientation == "landscape":
        sensor_across_mm  = sensor_w_native_mm   # long   → across-track
        sensor_along_mm   = sensor_h_native_mm   # narrow → along-track
        image_across_px   = image_w_native_px
        image_along_px    = image_h_native_px
    else:
        raise ValueError(f"orientation must be 'portrait' or 'landscape', got {orientation!r}")

    gi   = ground_intersections_flat_terrain(
        altitude_m, tilt_from_nadir_deg,
        sensor_across_mm, sensor_along_mm, focal_length_mm,
        tilt_axis=tilt_axis,
    )
    fp   = footprint_dimensions(
        altitude_m, tilt_from_nadir_deg,
        sensor_across_mm, sensor_along_mm, focal_length_mm,
        tilt_axis=tilt_axis,
    )
    fcfp = four_corner_footprint(
        altitude_m, tilt_from_nadir_deg,
        sensor_across_mm, sensor_along_mm, focal_length_mm,
        tilt_axis=tilt_axis,
    )

    px_sz = pixel_size_mm(sensor_across_mm, image_across_px)
    diag  = diag_pp_to_long_edge_mm(sensor_across_mm, focal_length_mm)

    near_gsd   = gsd_at_edge_full(altitude_m, gi.near_edge_m,  px_sz, focal_length_mm, sensor_across_mm)
    centre_gsd = gsd_at_edge_full(altitude_m, gi.centre_m,     px_sz, focal_length_mm, sensor_across_mm)
    far_gsd    = gsd_at_edge_full(altitude_m, gi.far_edge_m,   px_sz, focal_length_mm, sensor_across_mm)

    return CameraSolution(
        label=label,
        tilt_from_nadir_deg=tilt_from_nadir_deg,
        orientation=orientation,
        tilt_axis=tilt_axis,
        pixel_size_mm=px_sz,
        sensor_across_mm=sensor_across_mm,
        sensor_along_mm=sensor_along_mm,
        sensor_w_native_mm=sensor_w_native_mm,
        sensor_h_native_mm=sensor_h_native_mm,
        diag_image_mm=diag,
        half_fov_across_deg=half_fov_deg(sensor_across_mm, focal_length_mm),
        half_fov_along_deg=half_fov_deg(sensor_along_mm,   focal_length_mm),
        full_fov_across_deg=2.0 * half_fov_deg(sensor_across_mm, focal_length_mm),
        full_fov_along_deg=2.0  * half_fov_deg(sensor_along_mm,  focal_length_mm),
        near_edge_m=gi.near_edge_m,
        centre_m=gi.centre_m,
        far_edge_m=gi.far_edge_m,
        near_slant_m=gi.near_slant_m,
        centre_slant_m=gi.centre_slant_m,
        far_slant_m=gi.far_slant_m,
        near_angle_deg=gi.near_angle_deg,
        centre_angle_deg=gi.centre_angle_deg,
        far_angle_deg=gi.far_angle_deg,
        near_gsd_m=near_gsd,
        centre_gsd_m=centre_gsd,
        far_gsd_m=far_gsd,
        footprint_across_m=fp.across_track_m,
        near_length_m=fp.near_length_m,
        centre_length_m=fp.centre_length_m,
        far_length_m=fp.far_length_m,
        corner_near_top=fcfp["near_top"],
        corner_near_bot=fcfp["near_bot"],
        corner_far_top=fcfp["far_top"],
        corner_far_bot=fcfp["far_bot"],
    )




def _matched_rl_pair(camera_solutions):
    """Return (right_sol, left_sol) across-track pair if available, else (None, None)."""
    right = next((cs for cs in camera_solutions if cs.tilt_axis == "across" and "right" in (cs.label or "").lower()), None)
    left = next((cs for cs in camera_solutions if cs.tilt_axis == "across" and "left" in (cs.label or "").lower()), None)
    return right, left


def _polygon_extent_x(cs):
    xs = [cs.corner_near_top[0], cs.corner_near_bot[0], cs.corner_far_top[0], cs.corner_far_bot[0]]
    finite_xs = [x for x in xs if math.isfinite(x)]
    if not finite_xs:
        return None
    return min(finite_xs), max(finite_xs)


def _matched_rl_overlap_fraction(right_sol, left_sol, line_spacing):
    """
    Overlap fraction for matched reciprocal Right-vs-Left pair, using the same
    x-extent logic as the app's matched-frame overlay.
    """
    rext = _polygon_extent_x(right_sol)
    lext = _polygon_extent_x(left_sol)
    if rext is None or lext is None:
        return None
    r_x0, r_x1 = rext
    l_x0, l_x1 = lext
    band_x0 = max(r_x0, l_x0 + line_spacing)
    band_x1 = min(r_x1, l_x1 + line_spacing)
    overlap_width = max(0.0, band_x1 - band_x0)
    right_width = max(1e-9, r_x1 - r_x0)
    return max(0.0, min(1.0, overlap_width / right_width))


def _line_spacing_for_matched_rl(right_sol, left_sol, target_overlap_fraction):
    """
    Solve line spacing so the matched reciprocal Right-vs-Left across-track pair
    achieves the requested sidelap fraction.

    For reciprocal R/L obliques, overlap is not monotonic with spacing:
    it is typically zero at spacing=0, rises to a peak, then falls again.
    For planning we want the larger-spacing solution on the descending branch
    because that gives the most efficient line spacing for the requested overlap.
    """
    if not 0.0 <= target_overlap_fraction < 1.0:
        raise ValueError("sidelap_fraction must be in [0, 1)")

    rext = _polygon_extent_x(right_sol)
    lext = _polygon_extent_x(left_sol)
    if rext is None or lext is None:
        return None

    r_x0, r_x1 = rext
    l_x0, l_x1 = lext

    hi = max(abs(v) for v in [r_x0, r_x1, l_x0, l_x1]) * 6.0 + 1.0
    if not math.isfinite(hi) or hi <= 0:
        return None

    def overlap(sp):
        ov = _matched_rl_overlap_fraction(right_sol, left_sol, sp)
        if ov is None or not math.isfinite(ov):
            return None
        return ov

    # Sample the curve to find the peak and then the descending-branch crossing.
    sample_count = 1201
    spacings = [hi * i / (sample_count - 1) for i in range(sample_count)]
    pairs = [(sp, overlap(sp)) for sp in spacings]
    pairs = [(sp, ov) for sp, ov in pairs if ov is not None]
    if not pairs:
        return None

    peak_sp, peak_ov = max(pairs, key=lambda t: t[1])
    if target_overlap_fraction > peak_ov + 1e-9:
        return None

    # Find the first point on the descending branch that drops to/below target.
    lo = peak_sp
    hi_desc = None
    prev_sp, prev_ov = peak_sp, peak_ov
    for sp, ov in pairs:
        if sp < peak_sp:
            continue
        if ov <= target_overlap_fraction:
            lo = prev_sp
            hi_desc = sp
            break
        prev_sp, prev_ov = sp, ov

    if hi_desc is None:
        end_ov = pairs[-1][1]
        if end_ov > target_overlap_fraction + 1e-9:
            return None
        hi_desc = pairs[-1][0]

    def f(sp):
        ov = overlap(sp)
        if ov is None:
            return None
        return ov - target_overlap_fraction

    f_lo = f(lo)
    f_hi = f(hi_desc)
    if f_lo is None or f_hi is None:
        return None

    # lo should be at/above target, hi_desc at/below target.
    if f_lo < 0 and f_hi > 0:
        lo, hi_desc = hi_desc, lo
        f_lo, f_hi = f_hi, f_lo

    if f_lo < 0:
        return lo
    if f_hi > 0:
        return hi_desc

    for _ in range(80):
        mid = 0.5 * (lo + hi_desc)
        f_mid = f(mid)
        if f_mid is None:
            return None
        if f_mid > 0:
            lo = mid
        else:
            hi_desc = mid
    return 0.5 * (lo + hi_desc)


# ---------------------------------------------------------------------------
# Multi-camera system solution
# ---------------------------------------------------------------------------

@dataclass
class MultiCameraSolution:
    """System-level outputs for a multi-camera oblique array."""
    combined_swath_m: float             # total across-track ground coverage
    recommended_line_spacing_m: float   # nadir-track to nadir-track spacing
    recommended_photo_spacing_m: float  # along-track exposure spacing
    photo_interval_s: float             # seconds between exposures at given speed
    forward_overlap_near: float         # achieved forward overlap at near edge (fraction)
    forward_overlap_centre: float       # achieved forward overlap at image centre (fraction)
    forward_overlap_far: float          # achieved forward overlap at far edge (fraction)
    sidelap_achieved: float             # system-level sidelap (fraction)
    reciprocal_recommended: bool
    warnings: List[str] = field(default_factory=list)


def calculate_multicamera_solution(
    camera_solutions: list,
    arrangement: str,
    altitude_m: float,
    aircraft_speed_ms: float,
    forward_overlap_fraction: float,
    sidelap_fraction: float,
    reciprocal_flying: bool,
) -> MultiCameraSolution:
    """
    System-level outputs from a list of per-camera solutions.

    MATCHED-PAIR MODE:
    - Line spacing is driven by the matched reciprocal Right-vs-Left across-track pair.
    - Achieved sidelap is reported from that controlling pair overlap.
    - If a proper R/L pair is not present, this falls back to the original full-rig swath method.

    Photo spacing still uses near_length_m (the smallest along-track footprint) to
    ensure the target forward overlap is met at the most constrained position.
    """
    warns = []

    if not camera_solutions:
        raise ValueError("No camera solutions provided.")

    # Original full-rig combined swath for reporting/fallback
    all_gx = []
    for cs in camera_solutions:
        all_gx.extend([
            cs.corner_near_top[0], cs.corner_near_bot[0],
            cs.corner_far_top[0],  cs.corner_far_bot[0],
        ])
    combined_swath = max(all_gx) - min(all_gx)

    # Matched reciprocal controlling pair
    right_sol, left_sol = _matched_rl_pair(camera_solutions)
    used_fallback = False
    line_spacing = None
    sidelap_achieved = None

    if right_sol is not None and left_sol is not None:
        line_spacing = _line_spacing_for_matched_rl(right_sol, left_sol, sidelap_fraction)
        if line_spacing is not None:
            sidelap_achieved = _matched_rl_overlap_fraction(right_sol, left_sol, line_spacing)
        else:
            used_fallback = True
            warns.append("Matched R/L pair present, but line spacing could not be solved cleanly; falling back to full-rig swath spacing.")
    else:
        used_fallback = True
        warns.append("No matched Right/Left across-track pair found; falling back to full-rig swath spacing.")

    if line_spacing is None or sidelap_achieved is None:
        line_spacing = line_spacing_from_sidelap(combined_swath, sidelap_fraction)
        sidelap_achieved = 1.0 - line_spacing / combined_swath if combined_swath > 0 else 0.0

    # Photo spacing — use near_length_m for the conservative (minimum footprint) estimate
    near_along = camera_solutions[0].near_length_m
    photo_spacing = photo_spacing_from_forward_overlap(near_along, forward_overlap_fraction)
    photo_interval_s = photo_spacing / aircraft_speed_ms if aircraft_speed_ms > 0 else float("inf")

    def _overlap(fp_m):
        if fp_m <= 0:
            return 0.0
        return max(0.0, min(1.0, 1.0 - photo_spacing / fp_m))

    fwd_near   = _overlap(camera_solutions[0].near_length_m)
    fwd_centre = _overlap(camera_solutions[0].centre_length_m)
    fwd_far    = _overlap(camera_solutions[0].far_length_m)

    reciprocal_recommended = any(abs(cs.centre_angle_deg) > 5.0 for cs in camera_solutions)

    for i, cs in enumerate(camera_solutions):
        lbl = cs.label or f"Camera {i+1}"
        if cs.far_angle_deg >= 80.0:
            warns.append(
                f"{lbl}: far edge angle {cs.far_angle_deg:.1f}° — near the horizon, "
                f"GSD will be very large."
            )
        if cs.near_gsd_m > 0 and cs.far_gsd_m / cs.near_gsd_m > 4.0:
            warns.append(
                f"{lbl}: GSD varies {cs.far_gsd_m/cs.near_gsd_m:.1f}× across image "
                f"({cs.near_gsd_m*100:.1f}→{cs.far_gsd_m*100:.1f} cm/px). "
                f"Consider reducing tilt."
            )

    if photo_interval_s < 1.0:
        warns.append(f"Exposure interval {photo_interval_s:.2f} s is very short — verify camera capability.")
    if photo_interval_s > 30.0:
        warns.append(f"Exposure interval {photo_interval_s:.1f} s is long — verify forward overlap.")
    if line_spacing <= 0:
        warns.append("Line spacing ≤ 0 — sidelap fraction may be ≥ 1.")
    if combined_swath <= 0:
        warns.append("Combined swath is zero — check altitude and tilt.")
    if fwd_near < 0.5:
        warns.append(f"Forward overlap at near edge is only {fwd_near*100:.0f}% — may cause gaps.")
    if not used_fallback:
        warns.append("Line spacing in this version is driven by the matched reciprocal Right-vs-Left pair, not the full rig swath.")

    return MultiCameraSolution(
        combined_swath_m=combined_swath,
        recommended_line_spacing_m=line_spacing,
        recommended_photo_spacing_m=photo_spacing,
        photo_interval_s=photo_interval_s,
        forward_overlap_near=fwd_near,
        forward_overlap_centre=fwd_centre,
        forward_overlap_far=fwd_far,
        sidelap_achieved=sidelap_achieved,
        reciprocal_recommended=reciprocal_recommended,
        warnings=warns,
    )
