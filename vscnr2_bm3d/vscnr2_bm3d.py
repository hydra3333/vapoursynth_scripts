"""
Replacement for Vapoursynth CNR2 chroma denoising, using bm3dcpu for chroma denoising and bwdif for deinterlacing.
    - Intended for use with chroma-noisey VHS captures eg for VHS-C home movies.
    - Defaults to chroma-only denoising, with optional LUMA denoising via sigma_luma.
    - Handles both progressive and interlaced (PAL/NTSC) YUV sources.
    - For interlaced sources, fields are separated by parity (TFF/BFF), denoised independently, then rewoven before optional bwdif deinterlacing.
    - Use bm3dcpu for denoising
    - Use bwdif for optional deinterlacing and furthermore optional doubling of output framerate

Main Function:
    def cnr2_bm3d(
        clip: vs.VideoNode,
        sigma_uv: float = 3.5,
        sigma_luma: float = 0.0,                # OPTIONAL TO DENOISE LUMA, 0.0=preserve luma, >0.0=optional luma denoise
        radius: int = 1,                        # 0=spatial only, 1-9=temporal window
        full_quality_denoise: bool = True,      # Run two BM3Dv2 passes (Wiener refinement) for high quality which is Slower
        #--- Override auto-detection ONLY if you know better:
        matrix: Optional[str] = None,           # NORMALLY DO NOT SPECIFY THIS e.g. "470bg", "601", "709" - None = auto
        limited: Optional[bool] = None,         # NORMALLY DO NOT SPECIFY THIS True=TV range, False=PC - None = auto
        tff: Optional[bool] = None,             # NORMALLY DO NOT SPECIFY THIS True=TFF, False=BFF - None = auto
        #---
        # Deinterlace after processing?
        deinterlace: bool = False,              # requires vapoursynth-bwdif
        deinterlace_rate: str = "same",         # eg "same"=25i->25p, "double"=25i->50p
        deinterlace_quality: str = "standard",  # "standard"=bwdif, "enhanced"=bwdif+znedi3 via edeint
        # Debug
        show_info: bool = False,                # print detected ClipInfo before processing
    ) -> vs.VideoNode:

    Args:
        clip:         Input YUV clip. Any bit depth and subsampling.
        sigma_uv:     Chroma denoising strength. ~3.5 ≈ CNR2 defaults.
        sigma_luma:   Optional LUMA denoising strength.
                      0.0 preserves LUMA from the source clip, matching the
                      original chroma-only CNR2 behaviour.
                      Use LUMA denoising cautiously because it is much more
                      visually obvious than chroma denoising.
                      Suggested starting values:
                          0.0 = preserve LUMA exactly
                          0.5 = very light LUMA denoise
                          1.0 = light LUMA denoise
                          2.0 = moderate LUMA denoise; use cautiously
        radius:       Temporal radius. 0=spatial only, 1+=temporal (default).
                      This wrapper allows 0..9, pragmatically use 1-4 only.
                      For old VHS chroma denoising, radius 1 and 2 are likely the
                      practical values with 3 and 4 as safety headroom.
                      With field-split interlaced, each unit of radius spans
                      one same-parity field = one full interlaced frame.
        full_quality_denoise: Run two BM3Dv2 passes (Wiener refinement). Slower
                      but meaningfully better quality. Recommended for
                      final encodes.
        matrix:       Override detected colour matrix. None = auto-detect.
        limited:      Override detected range. None = auto-detect.
        tff:          Override detected field order. None = auto-detect.
                      PAL VHS is almost universally TFF.
        deinterlace:  If True, run bwdif deinterlacer on the rewoven interlaced output.
                      Requires vapoursynth-bwdif installed.
        deinterlace_rate:
                      Only used when deinterlace=True.
                      "same"   = same-rate progressive output, e.g. 25i -> 25p or 29.97i -> 29.97p.
                      "double" = double-rate progressive output, e.g. 25i -> 50p or 29.97i -> 59.94p.
                      Case is ignored, so "Same", "SAME", "Double", and "DOUBLE" are accepted.
        deinterlace_quality:
                      Only used when deinterlace=True.
                      "standard" = normal bwdif deinterlacing.
                      "enhanced" = bwdif with a znedi3 edeint helper
                                   for higher-quality spatial prediction.
                      Case is ignored, so "Standard", "STANDARD", "Enhanced", and "ENHANCED" are accepted.
        show_info:    If True, print the detected ClipInfo before processing.
                      Useful for verifying auto-detection on a new source.
Notes:
    Handles both progressive and interlaced (PAL/NTSC) sources automatically.
    For interlaced sources, 
        - fields are separated by parity (TFF/BFF)
        - each same-parity stream is denoised independently (so temporal comparisons are always between same-parity fields)
        - the streams are rewoven back to interlaced
        - optionally deinterlaces with bwdif afterwards, 
          either to same-rate progressive output or double-rate progressive output.
    Format, matrix, range and field-order properties are auto-detected
    from frame properties (via vstools when available), with reasonable PAL fallbacks
    for VHS/SD content (<=576 lines -> 470bg matrix, limited range, TFF).

Dependencies:
    vapoursynth R76+
    vsjetpack             (pip install vsjetpack)            - for vstools stuff including video_heuristics()
    fmtconv               (pip install vapoursynth-fmtconv)  - for format conversions
    vapoursynth-bm3dcpu   (pip install vapoursynth-bm3dcpu)  - for chroma denoising and optional luma denoising
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
    print(inspect_input_clip(clip))

2. Or, pass show_info=True to cnr2_bm3d to see it inline.

3. Then, your own concoction based on these examples:

## LIGHT chroma-only denoise
## interlaced output, gentle chroma clean-up, single BM3D pass
light = cnr2_bm3d(
    clip,
    sigma_uv=1.5,
    radius=1,
    full_quality_denoise=False,
    deinterlace=False,   # stay interlaced; deinterlace downstream in your own pipeline if you need
    show_info=True,      # print detected properties on first call for verification
)

## LIGHT chroma with light OPTIONAL LUMA DENOISE AS WELL
## interlaced output, gentle chroma clean-up, single BM3D pass
light = cnr2_bm3d(
    clip,
    sigma_uv=1.5,
    sigma_luma=0.5,      # do optional light LUMA denoising as well
    radius=1,
    full_quality_denoise=False,
    deinterlace=False,   # stay interlaced; deinterlace downstream in your own pipeline if you need
    show_info=True,      # print detected properties on first call for verification
)

## MEDIUM - approximately CNR2 defaults
## deliver progressive output via bwdif
medium = cnr2_bm3d(
    clip,
    sigma_uv=3.5,
    radius=1,
    full_quality_denoise=True,
    deinterlace=True,
    deinterlace_rate="same",
    deinterlace_quality="standard",
)

## MEDIUM - approximately CNR2 chroma defaults
## with medium OPTIONAL LUMA DENOISE AS WELL
## deliver progressive output via bwdif
medium = cnr2_bm3d(
    clip,
    sigma_uv=3.5,
    sigma_luma=1.0,      # do optional medium LUMA denoising as well
    radius=1,
    full_quality_denoise=True,
    deinterlace=True,
    deinterlace_rate="same",
    deinterlace_quality="enhanced",
)

## HEAVY - badly degraded VHS tape, wider temporal window
## deliver progressive output via bwdif
heavy = cnr2_bm3d(
    clip,
    sigma_uv=8.0,
    radius=2,             # 5 same-parity fields per output field (~200ms context)
    full_quality_denoise=True,
    deinterlace=True,
    deinterlace_rate="double",
    deinterlace_quality="standard",
)

## HEAVY - badly degraded VHS tape, wider temporal window
## with heavy OPTIONAL LUMA DENOISE AS WELL
## deliver progressive output via bwdif
heavy = cnr2_bm3d(
    clip,
    sigma_uv=8.0,
    sigma_luma=2.0,       # do optional heavy LUMA denoising as well
    radius=2,             # 5 same-parity fields per output field (~200ms context)
    full_quality_denoise=True,
    deinterlace=True,
    deinterlace_rate="double",
    deinterlace_quality="enhanced",
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

# expose these functions publically
__all__ = [
    "cnr2_bm3d",
    "inspect_input_clip",
]

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

def _bwdif_field_from_rate_and_order(
    deinterlace_rate: str,
    tff: bool,
) -> int:
    """
    Return the field value used by both bwdif and znedi3.
    bwdif and znedi3 use the same field numbering:
        0 = same-rate output, keep bottom field
        1 = same-rate output, keep top field
        2 = double-rate output, start with bottom field
        3 = double-rate output, start with top field
    """
    if deinterlace_rate == "double":
        if tff:
            # TFF double-rate output should start with the top field.
            bwdif_field = 3
        else:
            # BFF double-rate output should start with the bottom field.
            bwdif_field = 2
    elif tff:
        # Same-rate output from a top-field-first source keeps the top field.
        bwdif_field = 1
    else:
        # Same-rate output from a bottom-field-first source keeps the bottom field.
        bwdif_field = 0
    return bwdif_field

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

def inspect_input_clip(clip: vs.VideoNode) -> ClipInfo:
    """
    Inspect an input clip and return the ClipInfo that cnr2_bm3d() would use
    before manual overrides are applied.

    This is a public diagnostic helper for checking detected or guessed input
    properties such as matrix, range, field order, format, frame count, and
    timing before committing to a full processing run.

    This function does not process or modify the clip.
    """
    return _detect_format(clip)

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

def _get_bwdif_filter():
    """
    Return the loaded bwdif callable.

    Current vapoursynth-bwdif documentation names the callable Bwdif.  Some
    older examples used different capitalization, so this helper accepts the
    documented name first and then checks the older-looking alias.
    """
    if hasattr(core, "bwdif"):
        if hasattr(core.bwdif, "Bwdif"):
            return core.bwdif.Bwdif
        if hasattr(core.bwdif, "BwDif"):
            return core.bwdif.BwDif
    raise RuntimeError(
        "cnr2_bm3d: deinterlace=True requires the bwdif plugin.\n"
        "  Install it into this portable Python with:\n"
        "  pip install vapoursynth-bwdif"
    )

def _get_znedi3_filter():
    """
    Return the loaded znedi3 callable used for enhanced deinterlacing.

    The package we require is vapoursynth-znedi3.  Some builds expose the
    callable through VapourSynth's historical nnedi3 namespace, while others
    may expose it through a znedi3 namespace.  Keep that detail hidden here so
    the rest of this script can simply treat it as the znedi3 enhanced
    interpolation helper.
    """
    if hasattr(core, "znedi3") and hasattr(core.znedi3, "nnedi3"):
        return core.znedi3.nnedi3
    # if it got to here then perhaps znedi namespace is unfortunately nnedi3 
    if hasattr(core, "nnedi3") and hasattr(core.nnedi3, "nnedi3"):
        return core.nnedi3.nnedi3
    raise RuntimeError(
        'cnr2_bm3d: deinterlace_quality="enhanced" requires the znedi3 plugin.\n'
        "  Install it into this portable Python with:\n"
        "  pip install vapoursynth-znedi3\n"
        "  The nnedi3_weights.bin file must also be available as required by "
        "the znedi3 plugin and should have been auto installed into the same folder."
    )

def _check_dependencies(deinterlace: bool, deinterlace_quality: str) -> None:
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
        _get_bwdif_filter()
        if deinterlace_quality == "enhanced":
            _get_znedi3_filter()

# ─────────────────────────────────────────────────────────────────────────────
# User parameter validation helpers
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_deinterlace_rate(deinterlace_rate: str) -> str:
    """
    Normalize the requested deinterlace output rate.

    Accepted values:
        "same"   = same-rate deinterlacing, e.g. 25i -> 25p
        "double" = double-rate/bob deinterlacing, e.g. 25i -> 50p

    Case is ignored so user input such as "Same", "SAME", "Double", and
    "DOUBLE" is accepted, but the rest of the script only sees the canonical
    lower-case values.
    """
    if not isinstance(deinterlace_rate, str):
        raise TypeError('cnr2_bm3d: deinterlace_rate must be "same" or "double"')
    rate = deinterlace_rate.strip().lower()
    if rate not in {"same", "double"}:
        raise ValueError('cnr2_bm3d: deinterlace_rate must be "same" or "double"')
    return rate

def _normalize_deinterlace_quality(deinterlace_quality: str) -> str:
    """
    Normalize the requested deinterlace quality mode.

    Accepted values:
        "standard" = normal bwdif deinterlacing.
        "enhanced" = bwdif with an external znedi3 edeint clip used
                     for higher-quality spatial predictions.

    Case is ignored so user input such as "Standard", "STANDARD", "Enhanced",
    and "ENHANCED" is accepted, but the rest of the script only sees the
    canonical lower-case values.
    """
    if not isinstance(deinterlace_quality, str):
        raise TypeError(
            'cnr2_bm3d: deinterlace_quality must be "standard" or "enhanced"'
        )
    quality = deinterlace_quality.strip().lower()
    if quality not in {"standard", "enhanced"}:
        raise ValueError(
            'cnr2_bm3d: deinterlace_quality must be "standard" or "enhanced"'
        )
    return quality

def _validate_user_parameters(
    clip: vs.VideoNode,
    sigma_uv: float,
    sigma_luma: float,
    radius: int,
    full_quality_denoise: bool,
    matrix: Optional[str],
    limited: Optional[bool],
    tff: Optional[bool],
    deinterlace: bool,
    deinterlace_rate: str,
    deinterlace_quality: str,
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

    if isinstance(sigma_luma, bool) or not isinstance(sigma_luma, (int, float)):
        raise TypeError("cnr2_bm3d: sigma_luma must be a number")
    if (sigma_luma < 0) or (sigma_luma > 20):
        raise ValueError(
            "cnr2_bm3d: sigma_luma must be in the range 0..20. "
            "Values above 20 are likely accidental and can cause extreme luma damage."
        )

    if isinstance(radius, bool) or not isinstance(radius, int):
        raise TypeError("cnr2_bm3d: radius must be an integer")
    if radius < 0:
        raise ValueError("cnr2_bm3d: radius must be >= 0")
    if radius > 9:
        raise ValueError(
            "cnr2_bm3d: radius must be <= 9.  Usually max 4. Larger values are not allowed "
            "by this wrapper because BM3D memory use and processing cost grow "
            "with the temporal window size."
        )

    if not isinstance(full_quality_denoise, bool):
        raise TypeError("cnr2_bm3d: full_quality_denoise must be True or False")

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

    deinterlace_rate_normalized = _normalize_deinterlace_rate(deinterlace_rate)
    if not deinterlace and deinterlace_rate_normalized != "same":
        raise ValueError(
            'cnr2_bm3d: deinterlace_rate="double" requires deinterlace=True'
        )

    deinterlace_quality_normalized = _normalize_deinterlace_quality(deinterlace_quality)
    if not deinterlace and deinterlace_quality_normalized != "standard":
        raise ValueError(
            'cnr2_bm3d: deinterlace_quality="enhanced" requires deinterlace=True'
        )

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
# Core BM3D chroma denoising with optional luma denoising
# (operates on YUV444PS only)
# ─────────────────────────────────────────────────────────────────────────────

def _make_bwdif_edeint_clip(
    clip: vs.VideoNode,
    bwdif_field: int,
) -> vs.VideoNode:
    """
    Create the external spatial-prediction clip used by bwdif's edeint option.

    bwdif requires the edeint clip to match the input clip's width, height,
    and colorspace.  For same-rate bwdif output, the edeint clip must have
    the same number of frames as the input.  For double-rate bwdif output, it
    must have twice as many frames as the input.

    znedi3 uses the same field numbering as bwdif:
        0 = same rate, keep bottom field
        1 = same rate, keep top field
        2 = double rate, start with bottom field
        3 = double rate, start with top field

    Passing the same bwdif_field value to znedi3 therefore gives bwdif an
    edeint clip with the matching frame-rate mode and field order.
    """
    znedi3_filter = _get_znedi3_filter()
    return znedi3_filter(clip, field=bwdif_field)

def _bm3d_chroma_with_optional_luma(
    clip_444ps: vs.VideoNode,
    sigma_uv: float,
    sigma_luma: float,
    radius: int,
    full_quality_denoise: bool,
) -> vs.VideoNode:
    """
    CBM3D chroma denoising, with optional luma denoising, on a YUV444PS clip.

    sigma_luma=0.0 preserves luma from the source clip after BM3D processing.
    That keeps the default behaviour as a chroma-only CNR2.

    sigma_luma>0.0 enables OPTIONAL luma denoising as well as chroma denoising.
    Use this CAUTIOUSLY because luma denoising is much more visually obvious
    than chroma denoising.

    BM3Dv2 handles temporal aggregation internally - no VAggregate call needed.
    """
    sigma = [sigma_luma, sigma_uv, sigma_uv]
    basic = core.bm3dcpu.BM3Dv2(
        clip_444ps,
        sigma=sigma,
        radius=radius,
        chroma=True,  # CBM3D: Y guides block-matching for U and V
    )
    if full_quality_denoise:
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

    if sigma_luma == 0.0:
        # Restore luma from the original float clip.  Even when BM3D is asked
        # not to denoise luma, pulling the luma plane directly from the source
        # avoids any possible float accumulation artefact on the luma plane.
        return core.std.ShufflePlanes(
            [clip_444ps, denoised, denoised],
            planes=[0, 1, 2],
            colorfamily=vs.YUV,
        )
    # Optional luma denoising was requested, so also return BM3D's denoised luma plane.
    return denoised

# ─────────────────────────────────────────────────────────────────────────────
# Main function:  CNR2 replacement using bm3dcpu CBM3D chroma denoising.
# ─────────────────────────────────────────────────────────────────────────────

def cnr2_bm3d(
    clip: vs.VideoNode,
    sigma_uv: float = 3.5,
    sigma_luma: float = 0.0,                # OPTIONAL TO DENOISE LUMA, 0.0=preserve luma, >0.0=optional luma denoise
    radius: int = 1,                        # 0=spatial only, 1-9=temporal window, use 1-4
    full_quality_denoise: bool = True,      # Run two BM3Dv2 passes (Wiener refinement) for high quality which is Slower
    # Override auto-detection ONLY if you know better:
    matrix: Optional[str] = None,           # e.g. "470bg", "601", "709" - None = auto
    limited: Optional[bool] = None,         # True=TV range, False=PC - None = auto
    tff: Optional[bool] = None,             # True=TFF, False=BFF - None = auto
    # Deinterlace after processing?
    deinterlace: bool = False,              # requires vapoursynth-bwdif
    deinterlace_rate: str = "same",         # eg "same"=25i->25p, "double"=25i->50p
    deinterlace_quality: str = "standard",  # # "standard"=bwdif, "enhanced"=bwdif+znedi3 via edeint
    # Debug
    show_info: bool = False,                # print detected ClipInfo before processing
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
        sigma_luma,
        radius,
        full_quality_denoise,
        matrix,
        limited,
        tff,
        deinterlace,
        deinterlace_rate,
        deinterlace_quality,
        show_info,
    )

    # Store canonical lower-case values so later logic does not need to care
    # whether the caller used "same", "Same", "SAME", "enhanced", "ENHANCED",
    # etc.
    deinterlace_rate = _normalize_deinterlace_rate(deinterlace_rate)
    deinterlace_quality = _normalize_deinterlace_quality(deinterlace_quality)
    
    # Check dependencies are accessible.
    # Detection itself now relies (mostly) on vstools, and processing relies on fmtconv/bm3dcpu.
    _check_dependencies(deinterlace, deinterlace_quality)

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
    #    final output variable = progressive_out
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
        denoised = _bm3d_chroma_with_optional_luma(
            clip_f,
            sigma_uv,
            sigma_luma,
            radius,
            full_quality_denoise,
        )
        progressive_out = _from_444ps(denoised, info)
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
        # Optional luma denoising is safe here because each stream contains
        # only one field parity.  BM3D temporal comparisons therefore happen
        # between same-parity fields rather than between mismatched interlaced
        # field lines.
        f_444 = _to_444ps(f, info)
        f_den = _bm3d_chroma_with_optional_luma(
            f_444,
            sigma_uv,
            sigma_luma,
            radius,
            full_quality_denoise,
        )
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
    # bwdif field values:
    #   field=0 -> same-rate progressive output, keep bottom field
    #   field=1 -> same-rate progressive output, keep top field
    #   field=2 -> double-rate progressive output, start with bottom field
    #   field=3 -> double-rate progressive output, start with top field
    #
    # znedi3 uses the same field numbering, which lets the enhanced edeint
    # clip use the same field value chosen for bwdif.
    bwdif_field = _bwdif_field_from_rate_and_order(deinterlace_rate, _tff)
    bwdif_filter = _get_bwdif_filter()
    if deinterlace_quality == "enhanced":
        # Enhanced mode keeps bwdif's motion-adaptive logic, but supplies a
        # znedi3-generated edeint clip for higher-quality spatial
        # predictions instead of bwdif's normal cubic interpolation.
        edeint_clip = _make_bwdif_edeint_clip(interlaced_out, bwdif_field)
        progressive_out = bwdif_filter(
            interlaced_out,
            field=bwdif_field,
            edeint=edeint_clip,
        )
    else:
        # Standard mode uses plain bwdif.
        progressive_out = bwdif_filter(interlaced_out, field=bwdif_field)
    #
    # bwdif has produced progressive output here.  Final frame props must
    # mark this as progressive, regardless of the input field order.
    return _set_output_props(progressive_out, info, field_based=0)
