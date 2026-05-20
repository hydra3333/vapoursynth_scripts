"""
Replacement for CNR2 chroma denoising, using bm3dcpu for chroma denoising and bwdif for deinterlacing.
    - Intended for use with chroma-noisey VHS captures eg for VHS-C home movies.
    - Handles both progressive and interlaced (PAL/NTSC) YUV sources.
    - For interlaced sources, fields are separated by parity, denoised independently, then rewoven before optional bwdif deinterlacing.
    - Comments suggest PAL however it is likely to work on others if the format/matris in the clip is well formed.

Main Function:
    def cnr2_bm3d(
        clip: vs.VideoNode,
        sigma_uv: float = 3.5,
        radius: int = 1,                # 0=spatial only, 1-9=temporal window, use 1-4
        full_quality: bool = True,
        # Override auto-detection if you know better:
        matrix: Optional[str] = None,   # e.g. "470bg", "601", "709" - None = auto
        limited: Optional[bool] = None, # True=TV range, False=PC - None = auto
        tff: Optional[bool] = None,     # True=TFF, False=BFF - None = auto
        # Deinterlace after processing?
        deinterlace: bool = False,      # requires vapoursynth-bwdif
        # Debug
        show_info: bool = False,        # print detected ClipInfo before processing
    ) -> vs.VideoNode:

    Args:
        clip:         Input YUV clip. Any bit depth and subsampling.
        sigma_uv:     Chroma denoising strength. ~3.5 ≈ CNR2 defaults.
        radius:       Temporal radius. 0=spatial only, 1=temporal (default).
                      This wrapper allows 0..9, pragmatically use 1-4 only.
                      For old VHS chroma denoising, radius 1 and 2 are likely the
                      practical values with 3 and 4 as safety headroom.
                      With field-split interlaced, each unit of radius spans
                      one same-parity field = one full interlaced frame.
        full_quality: Run two BM3Dv2 passes (Wiener refinement). Slower
                      but meaningfully better quality. Recommended for
                      final encodes.
        matrix:       Override detected colour matrix. None = auto-detect.
        limited:      Override detected range. None = auto-detect.
        tff:          Override detected field order. None = auto-detect.
                      PAL VHS is almost universally TFF.
        deinterlace:  If True, run bwdif deinterlacer on the rewoven interlaced output.
                      Requires vapoursynth-bwdif installed.
        show_info:    If True, print the detected ClipInfo before processing.
                      Useful for verifying auto-detection on a new source.
Notes:
    Handles both progressive and interlaced (PAL/NTSC) sources automatically.
    For interlaced sources, 
        - fields are separated by parity
        - each same-parity stream is denoised independently (so temporal comparisons are always between same-parity fields)
        - the streams are rewoven back to interlaced
        - optionally deinterlaces with bwdif afterwards.
    Format, matrix, range and field-order properties are auto-detected
    from frame properties (via vstools when available), with reasonable PAL fallbacks
    for VHS/SD content (<=576 lines -> 470bg matrix, limited range, TFF).

Dependencies:
    vapoursynth R76+
    vsjetpack             (pip install vsjetpack)            - for vstools stuff including video_heuristics()
    fmtconv               (pip install vapoursynth-fmtconv)  - for format conversions
    vapoursynth-bm3dcpu   (pip install vapoursynth-bm3dcpu)  - for denoising chroma
    vapoursynth-bwdif     (pip install vapoursynth-bwdif)    - for optional de3interlacing

Assumptions:
    The following dll files are auto-loaded by vapoursynth:
        vapoursynth\plugins\bwdif.dll
        vapoursynth\plugins\fmtconv.dll
        vapoursynth\plugins\bm3dcpu\manifest.vs
        vapoursynth\plugins\bm3dcpu\bm3dcpu.dll
        vapoursynth\plugins\bm3dcpu\bm3dcpu.zn4.dll

Usage examples: - PAL VHS 720x576 25i YUV420P8

1. To inspect what was detected before committing to a run:
    print(_detect_format(clip))

2. Or, pass show_info=True to cnr2_bm3d to see it inline.

3. Then, your own concoction based on these examples:

## LIGHT chroma denoise - interlaced output, gentle chroma clean-up, single BM3D pass
light = cnr2_bm3d(
    clip,
    sigma_uv=1.5,
    radius=1,
    full_quality=False,
    deinterlace=False,   # stay interlaced; deinterlace downstream in your own pipeline if you need
    show_info=True,      # print detected properties on first call for verification
)

## MEDIUM - approximately CNR2 defaults, deliver progressive output via bwdif
medium = cnr2_bm3d(
    clip,
    sigma_uv=3.5,
    radius=1,
    full_quality=True,
    deinterlace=True,
)

## HEAVY - badly degraded tape, wider temporal window, deliver progressive output via bwdif
heavy = cnr2_bm3d(
    clip,
    sigma_uv=8.0,
    radius=2,             # 5 same-parity fields per output field (~200ms context)
    full_quality=True,
    deinterlace=True,
)
"""

from __future__ import annotations
from dataclasses import dataclass
from fractions import Fraction
from typing import Optional

import vapoursynth as vs
core = vs.core

try:
    from vstools import FieldBased, Matrix, Range, video_heuristics
    _HAS_VSTOOLS = True
except ImportError:
    _HAS_VSTOOLS = False


# ─────────────────────────────────────────────────────────────────────────────
# ClipInfo dataclass - everything detected about a clip in one place
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ClipInfo:
    # ── VS format ─────────────────────────────────────────────────────────────
    color_family:  str    # "YUV", "RGB", "GRAY"
    subsampling:   str    # "4:4:4", "4:2:2", "4:2:0", "4:4:0", etc.
    bit_depth:     int    # 8, 10, 12, 16, 32
    sample_type:   str    # "integer" or "float"
    width:         int
    height:        int
    # ── Timing ────────────────────────────────────────────────────────────────
    fps:           str    # human-readable, e.g. "25 (25000/1000)"
    num_frames:    int
    # ── Detected properties ───────────────────────────────────────────────────
    matrix:        str    # e.g. "470bg", "709", "601"
    limited:       bool   # True = TV/limited range
    is_interlaced: bool
    tff:           Optional[bool]  # True=TFF, False=BFF, None=progressive
    # ── Internal (used by conversion helpers) ─────────────────────────────────
    _fmt_id:       int    # vs.VideoFormat.id for resize target
    def __str__(self) -> str:
        field_order = (
            "TFF" if self.tff is True else
            "BFF" if self.tff is False else
            "progressive"
        )
        range_str = "limited (TV)" if self.limited else "full (PC)"
        return (
            f"ClipInfo:\n"
            f"  Size         : {self.width}x{self.height}\n"
            f"  Color family : {self.color_family}\n"
            f"  Subsampling  : {self.subsampling}\n"
            f"  Bit depth    : {self.bit_depth}-bit {self.sample_type}\n"
            f"  FPS          : {self.fps}\n"
            f"  Frames       : {self.num_frames}\n"
            f"  Matrix       : {self.matrix}\n"
            f"  Range        : {range_str}\n"
            f"  Scan         : {field_order}\n"
        )

# ─────────────────────────────────────────────────────────────────────────────
# Detection helpers  (internal - called by _detect_format)
# ─────────────────────────────────────────────────────────────────────────────

def _detect_subsampling(fmt: vs.VideoFormat) -> str:
    """
    Convert VapourSynth's format-level chroma subsampling shifts to a
    human-readable 4:x:x string.

    This does not use vstools.video_heuristics(), because subsampling is not
    guessed frame metadata.  It is part of the actual VapourSynth VideoFormat:
        subsampling_w = horizontal chroma subsampling shift
        subsampling_h = vertical chroma subsampling shift
    """
    sw, sh = fmt.subsampling_w, fmt.subsampling_h
    _map = {
        (0, 0): "4:4:4",
        (1, 0): "4:2:2",
        (1, 1): "4:2:0",
        (2, 0): "4:1:1",
        (0, 1): "4:4:0",
    }
    # Fallback for unusual subsampling layouts not explicitly listed above.
    return _map.get((sw, sh), f"4:{4 >> sw}:{4 >> sw >> sh}")

def _detect_matrix_str(clip: vs.VideoNode) -> str:
    """
    Return the detected or guessed matrix as a resize/fmtconv-compatible string.

    Prefer vstools.video_heuristics(), because it first reads frame props when
    available, then applies vstools' own resolution-based fallbacks when props
    are missing or explicitly marked as unspecified.

    This avoids maintaining our own PAL/NTSC/HD matrix guessing table here.
    """
    if _HAS_VSTOOLS:
        try:
            heuristics_result = video_heuristics(
                clip,
                props=True,
                prop_in=False,
                assumed_return=True,
            )
            heuristics, _assumed_props = heuristics_result
            m = heuristics["matrix"]

            _map = {
                1:  "709",
                4:  "fcc",
                5:  "470bg",
                6:  "601",
                7:  "240m",
                9:  "2020ncl",
                10: "2020cl",
            }
            return _map.get(int(m), "470bg")
        except Exception:
            pass
    f = clip.get_frame(0)
    m = f.props.get("_Matrix", None)
    if m is not None:
        _map = {
            1: "709",
            5: "470bg",
            6: "601",
            9: "2020ncl",
        }
        return _map.get(int(m), "470bg")
    # Last-resort fallback only, used when vstools is unavailable or failed.
    # 576-line SD is normally PAL/SECAM-style BT.470BG.
    # 480/486-line SD is normally NTSC-style SMPTE 170M / BT.601.
    # HD and above is normally BT.709.
    return "470bg" if clip.height == 576 else "601" if clip.height <= 486 else "709"

def _detect_range(clip: vs.VideoNode) -> bool:
    """
    Return True for limited/TV range, False for full/PC range.

    Prefer vstools.video_heuristics(), because it tracks current VapourSynth
    range-property naming and falls back safely when range props are absent
    or explicitly marked as unspecified.
    """
    if _HAS_VSTOOLS:
        try:
            heuristics_result = video_heuristics(
                clip,
                props=True,
                prop_in=False,
                assumed_return=True,
            )
            heuristics, _assumed_props = heuristics_result
            return heuristics["range"] != Range.FULL
        except Exception:
            pass
    f = clip.get_frame(0)
    # VapourSynth R74+ prefers _Range:
    #   _Range      0 = limited, 1 = full
    #
    # Do not read deprecated _ColorRange.  Reading _ColorRange in newer
    # VapourSynth versions can emit a deprecation warning, which is undesirable
    # in normal vspipe-to-ffmpeg use.
    if "_Range" in f.props:
        return int(f.props["_Range"]) == 0
    # Last-resort default for VHS/SD restoration work.
    return True

def _detect_field_order(clip: vs.VideoNode) -> tuple[bool, Optional[bool]]:
    """
    Returns (is_interlaced, tff_or_none).
    tff_or_none is True for TFF, False for BFF, None for progressive.

    This function deliberately does not guess interlacing from PAL/NTSC frame
    size or frame rate.  If field-order props are absent, the clip is treated
    as progressive unless the caller overrides tff in cnr2_bm3d().
    """
    if _HAS_VSTOOLS:
        try:
            fb = FieldBased.from_video(clip)
            if fb.is_inter:
                return True, (fb == FieldBased.TFF)
            return False, None
        except Exception:
            pass
    f = clip.get_frame(0)
    fb = int(f.props.get("_FieldBased", 0))
    if fb == 2:
        return True, True   # TFF
    if fb == 1:
        return True, False  # BFF
    return False, None      # progressive

# ─────────────────────────────────────────────────────────────────────────────
# _detect_format - the one call to rule them all
# ─────────────────────────────────────────────────────────────────────────────

def _detect_format(clip: vs.VideoNode) -> ClipInfo:
    """
    Inspect a clip and return a ClipInfo dataclass with all detected
    format, colour, and scan properties.

    Exact structural information such as width, height, bit depth, sample
    type, format id, fps, frame count, and subsampling comes directly from
    VapourSynth's clip/format attributes.

    Metadata-style information such as matrix and range is delegated to the
    helper functions below, which prefer vstools.video_heuristics() when
    available and fall back conservatively when needed.

    Example usage:
        info = _detect_format(clip)
        print(info)
        # -> ClipInfo:
        #     Size         : 720x576
        #     Color family : YUV
        #     Subsampling  : 4:2:0
        #     Bit depth    : 8-bit integer
        #     FPS          : 25 (25000/1000)
        #     Frames       : 18000
        #     Matrix       : 470bg
        #     Range        : limited (TV)
        #     Scan         : TFF
    """
    fmt = clip.format
    if fmt is None:
        raise ValueError("_detect_format: clip must have a constant (non-variable) format")
    cf_map = {vs.YUV: "YUV", vs.RGB: "RGB", vs.GRAY: "GRAY"}
    color_family = cf_map.get(fmt.color_family, "UNKNOWN")
    fps_frac = Fraction(clip.fps_num, clip.fps_den)
    fps_str  = f"{float(fps_frac):.3f} ({clip.fps_num}/{clip.fps_den})"
    is_interlaced, tff = _detect_field_order(clip)
    return ClipInfo(
        color_family  = color_family,
        subsampling   = _detect_subsampling(fmt) if fmt.color_family != vs.GRAY else "n/a",
        bit_depth     = fmt.bits_per_sample,
        sample_type   = "float" if fmt.sample_type == vs.FLOAT else "integer",
        width         = clip.width,
        height        = clip.height,
        fps           = fps_str,
        num_frames    = clip.num_frames,
        matrix        = _detect_matrix_str(clip),
        limited       = _detect_range(clip),
        is_interlaced = is_interlaced,
        tff           = tff,
        _fmt_id       = fmt.id,
    )

# ─────────────────────────────────────────────────────────────────────────────
# Output frame property helpers
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_matrix_str(matrix: str) -> str:
    """
    Normalize user-supplied or internally detected matrix names to the small
    canonical string set used throughout this script.

    Accepted examples:
        "709", "bt709", "BT.709"
        "470bg", "bt470bg", "BT.470BG"
        "601", "bt601", "BT.601"
        "170m", "st170m", "smpte170m", "SMPTE ST 170m"
        "240m", "st240m", "smpte240m", "SMPTE ST 240m"
        "2020ncl", "bt2020ncl", "BT.2020NCL"

    Returning one canonical spelling avoids subtle mismatches between:
        - user override validation
        - fmtconv/resize matrix strings
        - output _Matrix frame-property values

    Internally, NTSC SD aliases such as 170m/ST170M are canonicalized to
    "601" only as this script's short user-facing spelling.  The final output
    frame prop is still written as VapourSynth _Matrix value 6, so the output
    keeps the correct NTSC SD / ST170M matrix signalling.
    """
    key = (
        matrix.lower()
        .replace("smpte", "")
        .replace("st", "")
        .replace("bt", "")
        .replace(".", "")
        .replace("_", "")
        .replace("-", "")
        .replace(" ", "")
    )
    _map = {
        "709":     "709",
        "fcc":     "fcc",
        "470bg":   "470bg",
        "601":     "601",
        "170m":    "601",
        "240m":    "240m",
        "2020ncl": "2020ncl",
        "2020cl":  "2020cl",
    }
    if key not in _map:
        raise ValueError(
            "cnr2_bm3d: matrix must be one of: "
            "709, fcc, 470bg, 601/170m, 240m, 2020ncl, 2020cl"
        )
    return _map[key]

def _matrix_str_to_prop_value(matrix: str) -> int:
    """
    Convert the internal matrix string used by this script into the integer
    value used by VapourSynth's _Matrix frame property.

    Keep this mapping explicit rather than relying on vstools at this final
    output-property stage.  Output properties should still be set correctly
    even if vstools is unavailable after initial detection has completed.
    """
    _map = {
        "709":     1,
        "fcc":     4,
        "470bg":   5,
        "601":     6,
        "240m":    7,
        "2020ncl": 9,
        "2020cl":  10,
    }
    return _map[_normalize_matrix_str(matrix)]

def _set_output_props(
    clip: vs.VideoNode,
    info: ClipInfo,
    field_based: int,
) -> vs.VideoNode:
    """
    Set output frame properties so downstream filters and encoders see
    properties that describe the final output clip, not merely the input clip.

    field_based must describe the final output:
        0 = progressive
        1 = bottom field first interlaced
        2 = top field first interlaced

    Range property:
        _Range is the current VapourSynth property:
            0 = limited, 1 = full

    Deprecated _ColorRange is deliberately not written.  Avoiding it prevents
    deprecation warnings and keeps the output aligned with current VapourSynth
    frame-property naming.

    This helper should only be used at final return points, not on intermediate
    separated fields.
    """
    range_new = 0 if info.limited else 1

    return core.std.SetFrameProps(
        clip,
        _Matrix=_matrix_str_to_prop_value(info.matrix),
        _Range=range_new,
        _FieldBased=field_based,
    )

# ─────────────────────────────────────────────────────────────────────────────
# Dependency checking helpers
# ─────────────────────────────────────────────────────────────────────────────

def _check_dependencies(deinterlace: bool) -> None:
    """
    Check required Python imports and VapourSynth plugin namespaces before
    expensive processing starts.

    This function deliberately checks both plugin namespaces and the specific
    filter functions used by this script.  In portable VapourSynth installs,
    a package may be installed but the plugin DLL may not have autoloaded
    correctly, so checking only Python package presence is not sufficient.
    """
    if not _HAS_VSTOOLS:
        raise RuntimeError(
            "cnr2_bm3d: missing required Python dependency vstools.\n"
            "  Install it into this portable Python with:\n"
            "  pip install vsjetpack"
        )
    if not hasattr(core, "fmtc"):
        raise RuntimeError(
            "cnr2_bm3d: missing required VapourSynth plugin fmtconv.\n"
            "  Install it into this portable Python with:\n"
            "  pip install vapoursynth-fmtconv"
        )
    if not hasattr(core.fmtc, "resample"):
        raise RuntimeError(
            "cnr2_bm3d: fmtconv plugin is loaded, but core.fmtc.resample "
            "is unavailable."
        )
    if not hasattr(core.fmtc, "bitdepth"):
        raise RuntimeError(
            "cnr2_bm3d: fmtconv plugin is loaded, but core.fmtc.bitdepth "
            "is unavailable."
        )
    if not hasattr(core, "bm3dcpu"):
        raise RuntimeError(
            "cnr2_bm3d: missing required VapourSynth plugin bm3dcpu.\n"
            "  Install it into this portable Python with:\n"
            "  pip install vapoursynth-bm3dcpu"
        )
    if not hasattr(core.bm3dcpu, "BM3Dv2"):
        raise RuntimeError(
            "cnr2_bm3d: bm3dcpu plugin is loaded, but "
            "core.bm3dcpu.BM3Dv2 is unavailable."
        )
    if deinterlace:
        if not hasattr(core, "bwdif"):
            raise RuntimeError(
                "cnr2_bm3d: deinterlace=True requires the bwdif plugin.\n"
                "  Install it into this portable Python with:\n"
                "  pip install vapoursynth-bwdif"
            )
        if not hasattr(core.bwdif, "BwDif"):
            raise RuntimeError(
                "cnr2_bm3d: bwdif plugin is loaded, but "
                "core.bwdif.BwDif is unavailable."
            )

# ─────────────────────────────────────────────────────────────────────────────
# User parameter validation helpers
# ─────────────────────────────────────────────────────────────────────────────

def _validate_user_parameters(
    clip: vs.VideoNode,
    sigma_uv: float,
    radius: int,
    full_quality: bool,
    matrix: Optional[str],
    limited: Optional[bool],
    tff: Optional[bool],
    deinterlace: bool,
    show_info: bool,
) -> None:
    """
    Validate user-supplied parameters before format detection or expensive
    processing starts.

    This function deliberately rejects VFR/unknown-framerate clips.
    The temporal denoising path relies on frame-to-frame adjacency having a
    predictable meaning, and the interlaced path relies on field operations
    that are only sensible with stable clip timing.
    """
    if isinstance(sigma_uv, bool) or not isinstance(sigma_uv, (int, float)):
        raise TypeError("cnr2_bm3d: sigma_uv must be a number")
    if (sigma_uv < 0) or (sigma_uv > 50):
        raise ValueError(
            "cnr2_bm3d: sigma_uv must be in the range 0..50. "
            "Values above 50 are likely accidental and can cause extreme chroma damage."
        )

    if isinstance(radius, bool) or not isinstance(radius, int):
        raise TypeError("cnr2_bm3d: radius must be an integer")
    if radius < 0:
        raise ValueError("cnr2_bm3d: radius must be >= 0")
    if radius > 4:
        raise ValueError(
            "cnr2_bm3d: radius must be <= 4.  Larger values are not allowed "
            "by this wrapper because BM3D memory use and processing cost grow "
            "with the temporal window size."
        )

    if not isinstance(full_quality, bool):
        raise TypeError("cnr2_bm3d: full_quality must be True or False")

    if matrix is not None:
        if not isinstance(matrix, str):
            raise TypeError("cnr2_bm3d: matrix must be a string or None")
        # Normalize once here to validate supported spellings.  The canonical
        # value is applied later when manual overrides are copied into ClipInfo.
        _normalize_matrix_str(matrix)

    if limited is not None and not isinstance(limited, bool):
        raise TypeError("cnr2_bm3d: limited must be True, False, or None")

    if tff is not None and not isinstance(tff, bool):
        raise TypeError("cnr2_bm3d: tff must be True, False, or None")

    if not isinstance(deinterlace, bool):
        raise TypeError("cnr2_bm3d: deinterlace must be True or False")

    if not isinstance(show_info, bool):
        raise TypeError("cnr2_bm3d: show_info must be True or False")

    if clip.fps_num == 0 or clip.fps_den == 0:
        raise ValueError(
            "cnr2_bm3d: variable-framerate or unknown-framerate clips are "
            "not supported. Convert the source to a constant-framerate clip "
            "before calling cnr2_bm3d."
        )

    if clip.num_frames <= 0:
        raise ValueError(
            "cnr2_bm3d: clip must have a known positive frame count"
        )

# ─────────────────────────────────────────────────────────────────────────────
# Format conversion helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fmtconv_css_from_format(fmt: vs.VideoFormat) -> str:
    """
    Convert a VapourSynth VideoFormat's chroma subsampling shifts into the
    css string expected by fmtconv.

    fmtconv uses strings such as:
        "444" = no chroma subsampling
        "422" = horizontal chroma subsampling only
        "420" = horizontal and vertical chroma subsampling
        "411" = stronger horizontal chroma subsampling

    VapourSynth stores subsampling as binary shifts:
        subsampling_w = horizontal chroma shift
        subsampling_h = vertical chroma shift

    Keep this as an explicit mapping rather than deriving the string with a
    formula.  It is clearer for maintainers and avoids invalid css strings
    such as "22" for 4:2:0.
    """
    _map = {
        (0, 0): "444",
        (1, 0): "422",
        (1, 1): "420",
        (2, 0): "411",
        (0, 1): "440",
    }
    key = (fmt.subsampling_w, fmt.subsampling_h)
    if key not in _map:
        raise ValueError(
            "cnr2_bm3d: unsupported chroma subsampling for fmtconv output "
            f"conversion: subsampling_w={fmt.subsampling_w}, "
            f"subsampling_h={fmt.subsampling_h}"
        )
    return _map[key]

def _to_444ps(clip: vs.VideoNode, info: ClipInfo) -> vs.VideoNode:
    """
    Convert any YUV integer clip -> YUV444PS for BM3D.

    Uses fmtc.resample for chroma upsampling (better placement than
    core.resize for 4:2:0 -> 4:4:4), then fmtc.bitdepth for float
    conversion with range handling.

    After SeparateFields the clip is progressive 720x288, so no interlaced
    chroma placement adjustments are needed.
    """
    fmt = clip.format
    if fmt is None:
        raise ValueError("_to_444ps: clip must have a constant (non-variable) format")
    # Step 1: chroma upsampling to 4:4:4 (integer, same bit depth)
    if fmt.subsampling_w > 0 or fmt.subsampling_h > 0:
        clip = core.fmtc.resample(
            clip,
            css="444",
            kernel="bicubic", a1=0, a2=0.5,  # Catmull-Rom
            fulls=not info.limited,
            fulld=not info.limited,
        )
    # Step 2: integer -> 32-bit float, re-encoding limited -> full range
    clip = core.fmtc.bitdepth(
        clip, bits=32, flt=True,
        fulls=not info.limited,
        fulld=True,   # BM3D always works in full-range float internally
    )
    if clip.format.id != vs.YUV444PS:
        clip = core.resize.Point(clip, format=vs.YUV444PS)
    return clip

def _from_444ps(clip: vs.VideoNode, info: ClipInfo) -> vs.VideoNode:
    """
    Convert YUV444PS float back to the original clip format,
    using the same ClipInfo that drove _to_444ps.
    """
    fmt_target = core.get_video_format(info._fmt_id)

    if fmt_target is None:
        raise ValueError(
            "_from_444ps: original clip format id is not available from "
            "VapourSynth core.get_video_format()"
        )

    # Step 1: float -> target bit depth with range re-encoding
    clip = core.fmtc.bitdepth(
        clip,
        bits=fmt_target.bits_per_sample,
        flt=1 if fmt_target.sample_type == vs.FLOAT else 0,
        fulls=True,           # source (444PS) is full range
        fulld=not info.limited,
    )
    # Step 2: chroma downsampling if needed
    if fmt_target.subsampling_w > 0 or fmt_target.subsampling_h > 0:
        clip = core.fmtc.resample(
            clip,
            css=_fmtconv_css_from_format(fmt_target),
            kernel="bicubic", a1=0, a2=0.5,
            fulls=not info.limited,
            fulld=not info.limited,
        )
    return clip

# ─────────────────────────────────────────────────────────────────────────────
# Core BM3D chroma denoising (operates on YUV444PS only)
# ─────────────────────────────────────────────────────────────────────────────

def _bm3d_chroma(
    clip_444ps: vs.VideoNode,
    sigma_uv: float,
    radius: int,
    full_quality: bool,
) -> vs.VideoNode:
    """
    CBM3D chroma denoising on a YUV444PS clip.
    sigma[0]=0 -> luma is never denoised, only used to guide U/V block-matching.

    BM3Dv2 handles temporal aggregation internally - no VAggregate call needed.
    """
    sigma = [0.0, sigma_uv, sigma_uv]
    basic = core.bm3dcpu.BM3Dv2(
        clip_444ps,
        sigma=sigma,
        radius=radius,
        chroma=True,  # CBM3D: Y guides block-matching for U and V
    )
    if full_quality:
        # Second pass: Wiener filter guided by the basic estimate
        denoised = core.bm3dcpu.BM3Dv2(
            clip_444ps,
            ref=basic,
            sigma=sigma,
            radius=radius,
            chroma=True,
        )
    else:
        denoised = basic
    # Restore luma from the original float clip - sigma[0]=0 means luma in
    # `denoised` is mathematically a no-op, but pulling directly from source
    # avoids any float accumulation artefact on the luma plane.
    return core.std.ShufflePlanes(
        [clip_444ps, denoised, denoised],
        planes=[0, 1, 2],
        colorfamily=vs.YUV,
    )

# ─────────────────────────────────────────────────────────────────────────────
# Main function:  CNR2 replacement using bm3dcpu CBM3D chroma denoising.
# ─────────────────────────────────────────────────────────────────────────────

def cnr2_bm3d(
    clip: vs.VideoNode,
    sigma_uv: float = 3.5,
    radius: int = 1,                # 0=spatial only, 1-9=temporal window, use 1-4
    full_quality: bool = True,
    # Override auto-detection if you know better:
    matrix: Optional[str] = None,   # e.g. "470bg", "601", "709" - None = auto
    limited: Optional[bool] = None, # True=TV range, False=PC - None = auto
    tff: Optional[bool] = None,     # True=TFF, False=BFF - None = auto
    # Deinterlace after processing?
    deinterlace: bool = False,      # requires vapoursynth-bwdif
    # Debug
    show_info: bool = False,        # print detected ClipInfo before processing
) -> vs.VideoNode:

    # ── Basic validation before doing anything expensive ─────────────────────────────────────────

    # Perform some basic clip checks
    if clip.format is None:
        raise ValueError("cnr2_bm3d: clip must have a constant (non-variable) format")
    if clip.format.color_family != vs.YUV:
        raise ValueError("cnr2_bm3d: input must be a YUV clip")

    # Validate the calling parameters to ensure we can successfully do what is asked
    _validate_user_parameters(
        clip,
        sigma_uv,
        radius,
        full_quality,
        matrix,
        limited,
        tff,
        deinterlace,
        show_info,
    )

    # Check dependencies are accessible.
    # Detection itself now relies (mostly) on vstools, and processing relies on fmtconv/bm3dcpu.
    _check_dependencies(deinterlace)

    # ── Detect everything in one call ─────────────────────────────────────────
    info = _detect_format(clip)
    # Apply manual overrides
    if matrix  is not None: info.matrix        = _normalize_matrix_str(matrix)
    if limited is not None: info.limited       = limited
    if tff     is not None:
        info.tff           = tff
        info.is_interlaced = True
    if show_info:
        print(info)
    #
    # Results of the next block will be:
    #
    # progressive input path:
    #    final output variable = result
    #    final _FieldBased = 0
    # interlaced input, deinterlace=False:
    #     final output variable = interlaced_out
    #     final _FieldBased = 2 if _tff else 1
    # interlaced input, deinterlace=True:
    #     final output variable = progressive_out
    #     final _FieldBased = 0
    # 
    # ── Progressive Input path ──────────────────────────────────────────────────────
    if not info.is_interlaced:
        clip_f   = _to_444ps(clip, info)
        denoised = _bm3d_chroma(clip_f, sigma_uv, radius, full_quality)
        progressive_out   = _from_444ps(denoised, info)
        # The progressive input path always returns progressive output.
        return _set_output_props(progressive_out, info, field_based=0)

    # ── Interlaced Input path ───────────────────────────────────────────────────────
    #
    # Why split by parity: with radius≥1, BM3Dv2 compares adjacent frames
    # temporally. On a raw interlaced clip those neighbours have opposite
    # field lines, so block-matching compares misaligned spatial content.
    # Splitting into same-parity streams (top-only, bottom-only) ensures every
    # temporal neighbour shares the same spatial grid.
    #
    #   SeparateFields  ->  [T0, B0, T1, B1, T2, B2, ...]  (50fps alternating)
    #   SelectEvery(2, [0])  ->  [T0, T1, T2, ...]          (25fps, top only)
    #   SelectEvery(2, [1])  ->  [B0, B1, B2, ...]          (25fps, bottom only)
    _tff   = info.tff if info.tff is not None else True  # PAL default
    fields = core.std.SeparateFields(clip, tff=_tff)
    top    = core.std.SelectEvery(fields, cycle=2, offsets=[0])  # 720x288 @25fps
    bot    = core.std.SelectEvery(fields, cycle=2, offsets=[1])  # 720x288 @25fps
    def _denoise_fields(f: vs.VideoNode) -> vs.VideoNode:
        # SeparateFields clips are progressive 720x288 - use same info but
        # the format detection still holds (same format, matrix, range).
        f_444 = _to_444ps(f, info)
        f_den = _bm3d_chroma(f_444, sigma_uv, radius, full_quality)
        return _from_444ps(f_den, info)
    top_den = _denoise_fields(top)
    bot_den = _denoise_fields(bot)
    # Reinterleave -> [T0, B0, T1, B1, ...] at 50fps field stream
    reinterleaved = core.std.Interleave([top_den, bot_den])
    # DoubleWeave -> 720x576 @50fps, then SelectEvery back to 25fps interlaced
    rewoven       = core.std.DoubleWeave(reinterleaved, tff=_tff)
    interlaced_out = core.std.SelectEvery(rewoven, cycle=2, offsets=[0])
    # If the user did not request deinterlacing, return the rewoven interlaced
    # output.  Final frame props must mark this as TFF or BFF according to _tff.
    if not deinterlace:
        return _set_output_props(
            interlaced_out,
            info,
            field_based=2 if _tff else 1,
        )

    # ── Optional bwdif deinterlace ────────────────────────────────────────────
    #
    # field=1 -> keep top field -> 25fps progressive (TFF source)
    # field=0 -> keep bottom field -> 25fps progressive (BFF source)
    # field=2 -> output both fields -> 50fps progressive
    bwdif_field = 1 if _tff else 0
    progressive_out = core.bwdif.BwDif(interlaced_out, field=bwdif_field)
    # bwdif has produced progressive output here.  Final frame props must
    # mark this as progressive, regardless of the input field order.
    return _set_output_props(progressive_out, info, field_based=0)


