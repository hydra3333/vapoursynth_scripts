r"""
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

Dependencies:
    vapoursynth R76+
    pymediainfo            (pip install vapoursynth-bestSource) - for info about video sources
    vapoursynth-bestSource (pip install pymediainfo)            - for info about video sources
    vsjetpack              (pip install vsjetpack)              - for vstools stuff including video_heuristics()
    fmtconv                (pip install vapoursynth-fmtconv)    - for format conversions
    vapoursynth-bm3dcpu    (pip install vapoursynth-bm3dcpu)    - for chroma denoising and optional luma denoising
    vapoursynth-bwdif      (pip install vapoursynth-bwdif)      - for optional de3interlacing

Assumptions:
    The following dll files are auto-loaded by vapoursynth:
        vapoursynth\plugins\libbestsource.dll
        vapoursynth\plugins\bwdif.dll
        vapoursynth\plugins\fmtconv.dll
        vapoursynth\plugins\bm3dcpu\manifest.vs
        vapoursynth\plugins\bm3dcpu\bm3dcpu.dll
        vapoursynth\plugins\bm3dcpu\bm3dcpu.zn4.dll

"""

from __future__ import annotations
import sys
import os
import gc
from pathlib import Path
import shutil
import tempfile
import json
from typing import List, Dict, Optional, Union
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Optional

import vapoursynth as vs
core = vs.core

try:
    # we need to use Vap[oursynth enums rather than hard coded values, so import stuff from vstools
    from vstools import (
        ChromaLocation,
        FieldBased,
        Matrix,
        Primaries,
        Range,
        Transfer,
        video_heuristics,
    )
    _HAS_VSTOOLS = True
except ImportError as e:
    _HAS_VSTOOLS = False
    #raise RuntimeError(
    #    "Missing dependency for heuristics, vsjetpack vstools.\n"
    #    "  Install it into this portable Python like.\n"
    #    "     python.exe -m pip install vsjetpack"
    #) from e
    pass

try:
    from pymediainfo import MediaInfo
    _HAS_PYMEDIAINFO = True
except ImportError as e:
    _HAS_PYMEDIAINFO = False
    #raise RuntimeError(
    #    "cnr2_bm3d_precheck_video_file: missing Python dependency pymediainfo.\n"
    #    "  Install it into this portable Python like.\n"
    #    "     python.exe -m pip install pymediainfo"
    #) from e
    pass

# expose these functions publically
__all__ = [
    "cnr2_bm3d",
    "inspect_input_clip",
    "cnr2_bm3d_precheck_video_file",
]

# -----------------------------------------------------------------------------
# Print module-owned diagnostic text to stderr.
# -----------------------------------------------------------------------------
def _print_stderr(*args: Any, **kwargs: Any) -> None:
    """
    Print module-owned diagnostic text to stderr.
    VapourSynth scripts are commonly run through vspipe with stdout carrying
    video frames into another program such as ffmpeg.  Any human-readable text
    written to stdout can corrupt that pipe.  Keep all diagnostics, reports,
    warnings, and optional show_info output on stderr.
    """
    kwargs["file"] = sys.stderr
    kwargs["flush"] = True
    print(*args, **kwargs)

# -----------------------------------------------------------------------------
# ClipInfo dataclass - everything detected about a clip in one place
# -----------------------------------------------------------------------------

@dataclass
class ClipInfo:
    # -- VS format -------------------------------------------------------------
    color_family:  str    # "YUV", "RGB", "GRAY"
    subsampling:   str    # "4:4:4", "4:2:2", "4:2:0", "4:4:0", etc.
    bit_depth:     int    # 8, 10, 12, 16, 32
    sample_type:   str    # "integer" or "float"
    width:         int
    height:        int
    # -- Timing ----------------------------------------------------------------
    fps:           str    # human-readable, e.g. "25 (25000/1000)"
    num_frames:    int
    # -- Detected properties ---------------------------------------------------
    matrix:        str    # e.g. "470bg", "709", "601"
    limited:       bool   # True = TV/limited range
    is_interlaced: bool
    tff:           Optional[bool]  # True=TFF, False=BFF, None=progressive
    # -- Internal (used by conversion helpers) ---------------------------------
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

# -----------------------------------------------------------------------------
# Detection helpers  (internal - called by _detect_format)
# -----------------------------------------------------------------------------

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

# -----------------------------------------------------------------------------
# _detect_format - the one call to rule them all
# -----------------------------------------------------------------------------

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
        _print_stderr(info)
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

# -----------------------------------------------------------------------------
# pre-check source video file to assist user in identifying correct properties
# -----------------------------------------------------------------------------

def cnr2_bm3d_precheck_video_file(
    source_filename: str,
    *,
    override_FieldBased: Optional[int] = None,
    override_Matrix: Optional[int] = None,
    override_Range: Optional[int] = None,
    override_Primaries: Optional[int] = None,
    override_Transfer: Optional[int] = None,
    override_ChromaLocation: Optional[int] = None,
    override_SARNum: Optional[int] = None,
    override_SARDen: Optional[int] = None,
    override_Rotation: Optional[int] = None,
    override_FlipHorizontal: Optional[int] = None,
    override_FlipVertical: Optional[int] = None,
) -> None:
    """
    Diagnostic-only precheck helper for source video files.

    Operates independently of cnr2_bm3d() with its own checks.

    IT HAS BEEN FOUND NECESSARY BECAUSE:
        VHS capture files OFTEN have missing, incomplete, incorrect, or
        ambiguous metadata.  This is especially common with AVI captures,
        lossless captures, DVD/VOB/MPEG sources, and files produced by
        older capture workflows and USB hardware video capture devices.

    This function inspects a source file using pymediainfo, opens it briefly
    with BestSource to inspect actual first-frame VapourSynth properties, then
    runs vstools.video_heuristics() after applying known blended preliminary
    properties.

    It prints:
        [1] pymediainfo source metadata
        [2] BestSource first-frame VapourSynth properties
        [3] BLENDED preliminary properties before vstools heuristics
        [4] vstools.video_heuristics() after applying known BLENDED preliminary props
        [5] Suggested SetFrameProps() code
        [6] PRECHECK RESULT
        [7] Reference: relevant VapourSynth frame properties
        [8] Reference: props deliberately not recommended for copying

    This helper does NOT return a processed clip.  It is intended to be run,
    reviewed, and then commented out before running cnr2_bm3d().  The user
    should copy/review the printed SetFrameProps() block and apply it to their
    real source clip BEFORE calling cnr2_bm3d().

    Override parameters deliberately use actual VapourSynth frame-property
    names and numeric property values.
    """

    UNKNOWN = "unknown"
    PARTIAL = "partial"
    OMIT = "-"
    PULLDOWN = "pulldown"

    if not _HAS_VSTOOLS:
        raise RuntimeError(
            "cnr2_bm3d_precheck_video_file: missing dependency vsjetpack/vstools.\n"
            "  Install it into this portable Python with:\n"
            "     python.exe -m pip install vsjetpack"
        )
    if not _HAS_PYMEDIAINFO:
        raise RuntimeError(
            "cnr2_bm3d_precheck_video_file: missing dependency pymediainfo.\n"
            "  Install it into this portable Python with:\n"
            "     python.exe -m pip install pymediainfo"
        )
    if not hasattr(core, "bs") or not hasattr(core.bs, "VideoSource"):
        raise RuntimeError(
            "cnr2_bm3d_precheck_video_file: BestSource is required for the "
            "diagnostic first-frame VapourSynth property check.\n"
            "  Install it into this portable Python with:\n"
            "     python.exe -m pip install BestSource"
        )
    RECOMMENDED_COPY_PROPS = [
        "_FieldBased",
        "_Matrix",
        "_Range",
        "_Primaries",
        "_Transfer",
        "_ChromaLocation",
        "_SARNum",
        "_SARDen",
        "Rotation",
        "FlipHorizontal",
        "FlipVertical",
    ]
    COLOUR_PROPS = [
        "_Matrix",
        "_Range",
        "_Primaries",
        "_Transfer",
        "_ChromaLocation",
    ]
    SOURCE_CODEC_FLAGS_NOT_RECOMMENDED = [
        "_PictType",
        "RepeatField",
        "TopFieldFirst",
    ]
    PROP_TO_ENUM = {
        "_FieldBased": FieldBased,
        "_Matrix": Matrix,
        "_Range": Range,
        "_Primaries": Primaries,
        "_Transfer": Transfer,
        "_ChromaLocation": ChromaLocation,
    }
    PROP_TO_OVERRIDE_NAME = {
        "_FieldBased": "override_FieldBased",
        "_Matrix": "override_Matrix",
        "_Range": "override_Range",
        "_Primaries": "override_Primaries",
        "_Transfer": "override_Transfer",
        "_ChromaLocation": "override_ChromaLocation",
        "_SARNum": "override_SARNum",
        "_SARDen": "override_SARDen",
        "Rotation": "override_Rotation",
        "FlipHorizontal": "override_FlipHorizontal",
        "FlipVertical": "override_FlipVertical",
    }

    # ------------------------------------------------------------------
    # Small print / value helpers
    # ------------------------------------------------------------------

    def _print_heading(title: str) -> None:
        _print_stderr("=" * 100)
        _print_stderr(title)
        _print_stderr("=" * 100)

    def _print_section(title: str) -> None:
        if title:
            _print_stderr("")
            _print_stderr(title)
        _print_stderr("-" * 100)

    def _safe_str(value: Any) -> str:
        if value is None:
            return "not reported"
        return str(value)

    def _is_known(value: Any) -> bool:
        return value not in (None, UNKNOWN, PARTIAL, OMIT, PULLDOWN)

    def _enum_valid_values(enum_cls: Any) -> set[int]:
        return {int(enum_value) for enum_value in enum_cls}

    def _enum_name(prop: str, value: Any) -> str:
        enum_cls = PROP_TO_ENUM.get(prop)
        if enum_cls is None:
            return ""
        try:
            return enum_cls(int(value)).name
        except Exception:
            return "unrecognised"

    def _meaning_for_prop(prop: str, value: Any) -> str:
        if value == PARTIAL:
            return "partial / incomplete information"
        if value == PULLDOWN:
            return "progressive telecine / 2:3 pulldown"
        if not _is_known(value):
            return "unknown"
        if prop in PROP_TO_ENUM:
            return _enum_name(prop, value)
        if prop in {"FlipHorizontal", "FlipVertical"}:
            return "true" if int(value) else "false"
        return ""

    def _print_table(headers: list[str], rows: list[list[Any]]) -> None:
        widths = [len(header) for header in headers]
        for row in rows:
            for i, cell in enumerate(row):
                widths[i] = max(widths[i], len(str(cell)))
        _print_stderr("  ".join(headers[i].ljust(widths[i]) for i in range(len(headers))))
        _print_stderr("  ".join("-" * widths[i] for i in range(len(headers))))
        for row in rows:
            _print_stderr("  ".join(str(row[i]).ljust(widths[i]) for i in range(len(headers))))

    def _format_fps(num: Any, den: Any) -> str:
        try:
            num_i = int(num)
            den_i = int(den)
            if num_i > 0 and den_i > 0:
                return f"{num_i / den_i:.3f}"
        except Exception:
            pass
        return UNKNOWN

    # ------------------------------------------------------------------
    # Override validation
    # ------------------------------------------------------------------

    def _validate_override_int(
        name: str,
        value: Optional[int],
        allowed: Optional[set[int]] = None,
        minimum: Optional[int] = None,
    ) -> None:
        if value is None:
            return
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} must be an integer or None")
        if allowed is not None and value not in allowed:
            allowed_text = ", ".join(str(v) for v in sorted(allowed))
            raise ValueError(f"{name} must be one of: {allowed_text}")
        if minimum is not None and value < minimum:
            raise ValueError(f"{name} must be >= {minimum}")

    def _validate_overrides() -> dict[str, int]:
        _validate_override_int(
            "override_FieldBased",
            override_FieldBased,
            _enum_valid_values(FieldBased),
        )
        _validate_override_int(
            "override_Matrix",
            override_Matrix,
            _enum_valid_values(Matrix),
        )
        _validate_override_int(
            "override_Range",
            override_Range,
            _enum_valid_values(Range),
        )
        _validate_override_int(
            "override_Primaries",
            override_Primaries,
            _enum_valid_values(Primaries),
        )
        _validate_override_int(
            "override_Transfer",
            override_Transfer,
            _enum_valid_values(Transfer),
        )
        _validate_override_int(
            "override_ChromaLocation",
            override_ChromaLocation,
            _enum_valid_values(ChromaLocation),
        )
        _validate_override_int("override_SARNum", override_SARNum, minimum=1)
        _validate_override_int("override_SARDen", override_SARDen, minimum=1)
        _validate_override_int("override_Rotation", override_Rotation)
        _validate_override_int("override_FlipHorizontal", override_FlipHorizontal, {0, 1})
        _validate_override_int("override_FlipVertical", override_FlipVertical, {0, 1})
        overrides: dict[str, int] = {}
        if override_FieldBased is not None:
            overrides["_FieldBased"] = override_FieldBased
        if override_Matrix is not None:
            overrides["_Matrix"] = override_Matrix
        if override_Range is not None:
            overrides["_Range"] = override_Range
        if override_Primaries is not None:
            overrides["_Primaries"] = override_Primaries
        if override_Transfer is not None:
            overrides["_Transfer"] = override_Transfer
        if override_ChromaLocation is not None:
            overrides["_ChromaLocation"] = override_ChromaLocation
        if override_SARNum is not None:
            overrides["_SARNum"] = override_SARNum
        if override_SARDen is not None:
            overrides["_SARDen"] = override_SARDen
        if override_Rotation is not None:
            overrides["Rotation"] = override_Rotation
        if override_FlipHorizontal is not None:
            overrides["FlipHorizontal"] = override_FlipHorizontal
        if override_FlipVertical is not None:
            overrides["FlipVertical"] = override_FlipVertical
        return overrides

    # ------------------------------------------------------------------
    # pymediainfo helpers
    # ------------------------------------------------------------------

    def _lookup_video_track(media_info: Any) -> Any:
        video_tracks = [
            track for track in media_info.tracks
            if getattr(track, "track_type", None) == "Video"
        ]
        if not video_tracks:
            raise ValueError(
                "cnr2_bm3d_precheck_video_file: no video track was found by pymediainfo"
            )
        if len(video_tracks) > 1:
            _print_stderr(
                "WARNING: pymediainfo found multiple video tracks. "
                "Using the first video track for this diagnostic."
            )
        return video_tracks[0]

    def _parse_positive_fraction(value: Any) -> Optional[tuple[int, int]]:
        if value is None:
            return None
        try:
            frac = Fraction(str(value)).limit_denominator(1000)
        except Exception:
            return None
        if frac.numerator <= 0 or frac.denominator <= 0:
            return None
        return frac.numerator, frac.denominator

    def _derive_dar_from_sar(
        width: Any,
        height: Any,
        sar_num: Any,
        sar_den: Any,
    ) -> str:
        """
        Derive display aspect ratio (DAR) from frame size and sample aspect ratio.
        VapourSynth normally stores sample/pixel aspect ratio as:
            _SARNum
            _SARDen
        DAR is not normally stored as a standard frame prop here. It is derived:
            DAR = width * SARNum / (height * SARDen)
        This is printed for human readability because users recognise common
        display ratios such as 4:3 and 16:9 more readily than values such as
        16:15, 32:27, 1067:800, etc.
        Deliberately limit the displayed DAR denominator quite strongly so that
        decimal MediaInfo PAR values such as 1.067 produce useful standard DAR
        output such as 4:3 rather than ugly near-equivalents such as 1067:800.
        """
        try:
            dar = Fraction(
                int(width) * int(sar_num),
                int(height) * int(sar_den),
            ).limit_denominator(100)
            return f"{dar.numerator}:{dar.denominator}"
        except Exception:
            return UNKNOWN

    def _normalise_mediainfo_text(value: Any) -> str:
        if value is None:
            return ""
        return (
            str(value)
            .strip()
            .lower()
            .replace(".", "")
            .replace("-", "")
            .replace("_", "")
            .replace("/", "")
            .replace(" ", "")
        )

    def _scan_order_to_field_based(scan_order: Any) -> Any:
        text = _normalise_mediainfo_text(scan_order)
        if text == "":
            return UNKNOWN
        if text in {"bff", "bottomfieldfirst", "bottomfirst"}:
            return int(FieldBased.BFF)
        if text in {"tff", "topfieldfirst", "topfirst"}:
            return int(FieldBased.TFF)
        if "23pulldown" in text or "232pulldown" in text:
            return PULLDOWN
        return UNKNOWN

    def _scan_type_to_preliminary_field_based(scan_type: Any, scan_order: Any) -> Any:
        scan_type_text = _normalise_mediainfo_text(scan_type)
        scan_order_text = _normalise_mediainfo_text(scan_order)
        if scan_type_text == "":
            return UNKNOWN
        if scan_type_text == "interlaced":
            field_based = _scan_order_to_field_based(scan_order)
            if field_based in (int(FieldBased.BFF), int(FieldBased.TFF)):
                return field_based
            return PARTIAL
        if scan_type_text == "progressive":
            if scan_order_text == "":
                return int(FieldBased.PROGRESSIVE)
            if _scan_order_to_field_based(scan_order) == PULLDOWN:
                return PULLDOWN
            return UNKNOWN
        return UNKNOWN

    def _map_mediainfo_matrix(value: Any) -> Any:
        text = _normalise_mediainfo_text(value)
        if text == "":
            return UNKNOWN
        if "709" in text:
            return int(Matrix.BT709)
        if "470" in text:
            return int(Matrix.BT470_BG)
        if "601" in text or "170" in text or "ntsc" in text:
            return int(Matrix.ST170_M)
        if "2020" in text and "cl" in text and "ncl" not in text:
            return int(Matrix.BT2020_CL)
        if "2020" in text:
            return int(Matrix.BT2020_NCL)
        return UNKNOWN

    def _map_mediainfo_primaries(
        value: Any,
        *,
        standard: Any = None,
        height: Any = None,
    ) -> Any:
        """
        Map pymediainfo colour-primaries strings to VapourSynth/vstools Primaries.
        Important SD distinction:
            BT.601 PAL / 625-line SD  -> Primaries.BT470_BG
            BT.601 NTSC / 525-line SD -> Primaries.ST170_M
        MediaInfo may report values such as "BT.601 PAL" or "BT.601 NTSC".
        If the PAL/NTSC qualifier is absent, use source standard and then
        frame height as a fallback tiebreaker.
        This avoids incorrectly mapping PAL BT.601 material to ST170_M.
        """
        text = _normalise_mediainfo_text(value)
        standard_text = _normalise_mediainfo_text(standard)
        try:
            height_i = int(height)
        except Exception:
            height_i = 0
        if text == "":
            return UNKNOWN
        if "709" in text:
            return int(Primaries.BT709)
        if "2020" in text:
            return int(Primaries.BT2020)
        if "470" in text:
            return int(Primaries.BT470_BG)
        # Explicit PAL/NTSC qualifiers win.
        if "pal" in text:
            return int(Primaries.BT470_BG)
        if "ntsc" in text or "170" in text:
            return int(Primaries.ST170_M)
        if "601" in text:
            # BT.601 uses different primaries for 625-line/PAL and 525-line/NTSC.
            # Prefer explicit standard metadata, then frame height as fallback.
            if "pal" in standard_text or "625" in standard_text:
                return int(Primaries.BT470_BG)
            if "ntsc" in standard_text or "525" in standard_text:
                return int(Primaries.ST170_M)
            if height_i >= 576:
                return int(Primaries.BT470_BG)
            if 0 < height_i <= 486:
                return int(Primaries.ST170_M)
            return UNKNOWN
        return UNKNOWN

    def _map_mediainfo_transfer(value: Any) -> Any:
        text = _normalise_mediainfo_text(value)
        if text == "":
            return UNKNOWN
        if "709" in text:
            return int(Transfer.BT709)
        if "470" in text:
            return int(Transfer.BT470_BG)
        if "601" in text or "170" in text:
            return int(Transfer.BT601)
        if "srgb" in text or "iec6196621" in text:
            return int(Transfer.IEC_61966_2_1)
        if "2084" in text or "pq" in text:
            return int(Transfer.ST2084)
        if "hlg" in text or "aribb67" in text:
            return int(Transfer.ARIB_B67)
        return UNKNOWN

    def _map_mediainfo_range(value: Any) -> Any:
        text = _normalise_mediainfo_text(value)
        if text == "":
            return UNKNOWN
        if "limited" in text or "tv" in text:
            return int(Range.LIMITED)
        if "full" in text or "pc" in text:
            return int(Range.FULL)
        return UNKNOWN

    def _map_mediainfo_chromaloc(value: Any) -> Any:
        text = _normalise_mediainfo_text(value)
        if text == "":
            return UNKNOWN
        if text in {"left", "mpeg2"}:
            return int(ChromaLocation.LEFT)
        if text in {"center", "centre", "jpeg"}:
            return int(ChromaLocation.CENTER)
        if text == "topleft":
            return int(ChromaLocation.TOP_LEFT)
        if text == "top":
            return int(ChromaLocation.TOP)
        if text == "bottomleft":
            return int(ChromaLocation.BOTTOM_LEFT)
        if text == "bottom":
            return int(ChromaLocation.BOTTOM)
        return UNKNOWN

    # ------------------------------------------------------------------
    # Frame-prop / BestSource / heuristic helpers
    # ------------------------------------------------------------------

    def _props_from_frame(frame: vs.VideoFrame) -> dict[str, Any]:
        props: dict[str, Any] = {}
        for prop in RECOMMENDED_COPY_PROPS:
            if prop in frame.props:
                props[prop] = frame.props[prop]
        return props

    def _is_unspecified_prop(prop: str, value: Any) -> bool:
        if value is None:
            return True
        try:
            value_i = int(value)
        except Exception:
            return True
        if prop == "_Matrix":
            return value_i == int(Matrix.UNSPECIFIED)
        if prop == "_Primaries":
            return value_i == int(Primaries.UNSPECIFIED)
        if prop == "_Transfer":
            return value_i == int(Transfer.UNSPECIFIED)
        return False

    def _known_props_only(props: dict[str, Any]) -> dict[str, int]:
        result: dict[str, int] = {}
        for key, value in props.items():
            if key not in RECOMMENDED_COPY_PROPS:
                continue
            if not _is_known(value):
                continue
            if _is_unspecified_prop(key, value):
                continue
            try:
                result[key] = int(value)
            except Exception:
                continue
        return result

    def _try_open_bestsource_first_video_track(
        source: str,
    ) -> tuple[Optional[vs.VideoNode], Optional[int]]:
        for track in range(0, 9):
            try:
                candidate_clip = core.bs.VideoSource(source, track=track)
                candidate_clip.get_frame(0)
                return candidate_clip, track
            except Exception:
                continue
        _print_stderr(
            "WARNING: BestSource could not open any video track from this file. "
            "Sections [2] and [4] will be incomplete."
        )
        return None, None

    def _run_vstools_heuristics(
        prepared_clip: vs.VideoNode,
    ) -> tuple[dict[str, Any], list[str]]:
        try:
            heuristics_result = video_heuristics(
                prepared_clip,
                props=True,
                prop_in=False,
                assumed_return=True,
            )
            heuristics, assumed_props = heuristics_result
            mapped: dict[str, Any] = {}
            if "matrix" in heuristics:
                mapped["_Matrix"] = int(heuristics["matrix"])
            if "range" in heuristics:
                mapped["_Range"] = int(heuristics["range"])
            if "primaries" in heuristics:
                mapped["_Primaries"] = int(heuristics["primaries"])
            if "transfer" in heuristics:
                mapped["_Transfer"] = int(heuristics["transfer"])
            if "chromaloc" in heuristics:
                mapped["_ChromaLocation"] = int(heuristics["chromaloc"])
            return mapped, list(assumed_props)
        except Exception as e:
            _print_stderr(f"WARNING: vstools.video_heuristics() failed: {type(e).__name__}: {e}")
            return {}, []

    # ------------------------------------------------------------------
    # Final output helpers
    # ------------------------------------------------------------------

    def _print_setframeprops_block(
        final_props: dict[str, Any],
        ready: bool,
        width: Any,
        height: Any,
    ) -> None:
        if ready:
            _print_stderr("[5] Suggested SetFrameProps() code - ready to review and copy")
        else:
            _print_stderr("[5] Suggested SetFrameProps() code - NOT READY TO COPY until all ? values are fixed")
        _print_stderr("-" * 100)
        _print_stderr("# Review these values before using them.")
        _print_stderr("# Apply them to the clip before calling cnr2_bm3d().")
        if not ready:
            _print_stderr("# This block is not valid Python until all ? placeholders are replaced.")
        suggested_dar = _derive_dar_from_sar(
            width,
            height,
            final_props.get("_SARNum", None),
            final_props.get("_SARDen", None),
        )
        _print_stderr("")
        if suggested_dar != UNKNOWN:
            _print_stderr(f"# Derived DAR for readability: {suggested_dar}")
        _print_stderr("clip = core.std.SetFrameProps(")
        _print_stderr("    clip,")
        for prop in RECOMMENDED_COPY_PROPS:
            value = final_props.get(prop, UNKNOWN)
            if not _is_known(value):
                if prop == "_FieldBased":
                    _print_stderr("    _FieldBased=?,       # REQUIRED: inspect the source video and choose the correct")
                    _print_stderr("                         # override_FieldBased value in cnr2_bm3d_precheck_video_file().")
                else:
                    _print_stderr(f"    {prop}=?,       # REQUIRED: determine this value before copying this block")
                continue
            meaning = _meaning_for_prop(prop, value)
            comment = f"  # {meaning}" if meaning else ""
            if prop == "_SARNum":
                comment = "  # sample aspect ratio numerator"
            elif prop == "_SARDen":
                comment = "  # sample aspect ratio denominator"
            elif prop == "Rotation":
                comment = "  # preserve; this script does not rotate pixels"
            elif prop == "FlipHorizontal":
                comment = "  # preserve; this script does not flip pixels"
            elif prop == "FlipVertical":
                comment = "  # preserve; this script does not flip pixels"
            _print_stderr(f"    {prop}={int(value)}, {comment}")
        _print_stderr(")")

    def _print_valid_values_for_prop(prop: str, indent: str = "    # ") -> None:
        enum_cls = PROP_TO_ENUM.get(prop)
        if enum_cls is not None:
            _print_stderr(f"{indent}Valid {PROP_TO_OVERRIDE_NAME[prop]} values:")
            for enum_value in enum_cls:
                _print_stderr(f"{indent}  {int(enum_value)} = {enum_value.name}")
            return
        if prop == "Rotation":
            _print_stderr(f"{indent}Valid override_Rotation values: integer rotation metadata, usually 0/90/180/270")
            return
        if prop in {"FlipHorizontal", "FlipVertical"}:
            _print_stderr(f"{indent}Valid {PROP_TO_OVERRIDE_NAME[prop]} values:")
            _print_stderr(f"{indent}  0 = false")
            _print_stderr(f"{indent}  1 = true")
            return

    def _print_reference_tables() -> None:
        _print_section("[7] Reference: relevant VapourSynth frame properties")
        rows: list[list[Any]] = []
        for prop in [
            "_FieldBased",
            "_Range",
            "_Matrix",
            "_Primaries",
            "_Transfer",
            "_ChromaLocation",
        ]:
            enum_cls = PROP_TO_ENUM[prop]
            values = ", ".join(
                f"{int(enum_value)}={enum_value.name}"
                for enum_value in enum_cls
            )
            rows.append([prop, values])
        rows.extend([
            ["_SARNum/_SARDen", "positive integers, e.g. 1/1, 16/15, 32/27"],
            ["Rotation", "usually 0, 90, 180, 270 if present"],
            ["FlipHorizontal", "0=false, 1=true if present"],
            ["FlipVertical", "0=false, 1=true if present"],
        ])
        _print_table(["VS prop", "Valid / common values"], rows)
        _print_section("[8] Reference: props deliberately not recommended for copying")
        _print_table(
            ["VS/source prop", "Reason"],
            [
                ["_PictType", "encoded/source frame type; not valid after filtering"],
                ["RepeatField", "MPEG/pulldown/source flag; not valid after filtering"],
                ["TopFieldFirst", "MPEG/source flag; use _FieldBased for processing field order instead"],
            ],
        )

    def _print_suggested_updated_precheck_call(
        failures: list[str],
        existing_overrides: dict[str, int],
    ) -> None:
        _print_stderr("Suggested updated precheck call:")
        _print_stderr("")
        _print_stderr("cnr2_bm3d_precheck_video_file(")
        _print_stderr("    source_filename,")
        for prop, value in existing_overrides.items():
            override_name = PROP_TO_OVERRIDE_NAME[prop]
            meaning = _meaning_for_prop(prop, value)
            _print_stderr(f"    {override_name}={value},   # existing override: {meaning}")
        failure_props: list[str] = []
        for prop in RECOMMENDED_COPY_PROPS:
            if any(prop in failure for failure in failures):
                failure_props.append(prop)
        for prop in failure_props:
            override_name = PROP_TO_OVERRIDE_NAME[prop]
            _print_stderr(f"    # {prop} is missing or indeterminate.")
            _print_stderr("    # Inspect the source video and choose the correct value.")
            _print_valid_values_for_prop(prop, indent="    # ")
            _print_stderr(f"    {override_name}=?,   # replace ? with the correct value for this video")
        _print_stderr(")")

    # ------------------------------------------------------------------
    # Start actual precheck logic.
    # ------------------------------------------------------------------

    overrides = _validate_overrides()

    _print_heading("cnr2_bm3d_precheck_video_file()")
    _print_stderr(f"Source: {source_filename}")
    _print_stderr("Purpose: Inspect source-file metadata, first-frame VapourSynth properties, and vstools heuristics.")
    _print_stderr("         Print recommended SetFrameProps() code to apply before calling cnr2_bm3d().")
    try:
        media_info = MediaInfo.parse(source_filename)
        video_track = _lookup_video_track(media_info)
    except Exception as e:
        raise RuntimeError(
            "cnr2_bm3d_precheck_video_file: pymediainfo could not inspect "
            f"the source file: {type(e).__name__}: {e}"
        ) from e
    scan_type = getattr(video_track, "scan_type", None)
    scan_order = getattr(video_track, "scan_order", None)
    mediainfo_props: dict[str, Any] = {}
    mediainfo_props["_FieldBased"] = _scan_type_to_preliminary_field_based(scan_type, scan_order)
    mediainfo_props["_Matrix"] = _map_mediainfo_matrix(
        getattr(video_track, "matrix_coefficients", None)
    )
    mediainfo_props["_Primaries"] = _map_mediainfo_primaries(
        getattr(video_track, "color_primaries", None),
        standard=getattr(video_track, "standard", None),
        height=getattr(video_track, "height", None),
    )
    mediainfo_props["_Transfer"] = _map_mediainfo_transfer(
        getattr(video_track, "transfer_characteristics", None)
    )
    mediainfo_props["_Range"] = _map_mediainfo_range(
        getattr(video_track, "color_range", None)
    )
    mediainfo_chromaloc_source = (
        getattr(video_track, "chroma_location", None)
        or getattr(video_track, "chroma_siting", None)
        or getattr(video_track, "chroma_subsampling_position", None)
    )
    mediainfo_props["_ChromaLocation"] = _map_mediainfo_chromaloc(
        mediainfo_chromaloc_source
    )
    par = _parse_positive_fraction(getattr(video_track, "pixel_aspect_ratio", None))
    if par is not None:
        mediainfo_props["_SARNum"], mediainfo_props["_SARDen"] = par
    mediainfo_derived_dar = _derive_dar_from_sar(
        getattr(video_track, "width", None),
        getattr(video_track, "height", None),
        mediainfo_props.get("_SARNum", None),
        mediainfo_props.get("_SARDen", None),
    )
    _print_section("[1] pymediainfo source metadata")
    table1_rows: list[list[Any]] = []

    def _add_mediainfo_row(
        source_field: str,
        source_value: Any,
        vs_prop: str,
        vs_value: Any,
        notes: str,
    ) -> None:
        table1_rows.append([
            source_field,
            _safe_str(source_value),
            vs_prop,
            vs_value,
            notes,
        ])

    _add_mediainfo_row(
        "scan_type",
        scan_type,
        "_FieldBased",
        mediainfo_props["_FieldBased"],
        "source scan type",
    )
    _add_mediainfo_row(
        "scan_order",
        scan_order,
        "_FieldBased",
        (
            mediainfo_props["_FieldBased"]
            if mediainfo_props["_FieldBased"] in (
                int(FieldBased.PROGRESSIVE),
                int(FieldBased.BFF),
                int(FieldBased.TFF),
            )
            else UNKNOWN
        ),
        "field order / pulldown indicator",
    )
    _add_mediainfo_row("standard", getattr(video_track, "standard", None), OMIT, OMIT, "PAL/NTSC context")
    _add_mediainfo_row("format", getattr(video_track, "format", None), OMIT, OMIT, "source codec/container info")
    _add_mediainfo_row("codec_id", getattr(video_track, "codec_id", None), OMIT, OMIT, "source codec id")
    _add_mediainfo_row("width", getattr(video_track, "width", None), OMIT, OMIT, "pixels")
    _add_mediainfo_row("height", getattr(video_track, "height", None), OMIT, OMIT, "pixels")
    _add_mediainfo_row("frame_rate", getattr(video_track, "frame_rate", None), OMIT, OMIT, "frames per second")
    _add_mediainfo_row("framerate_num", getattr(video_track, "framerate_num", None), OMIT, OMIT, "fps numerator")
    _add_mediainfo_row("framerate_den", getattr(video_track, "framerate_den", None), OMIT, OMIT, "fps denominator")
    _add_mediainfo_row("color_space", getattr(video_track, "color_space", None), OMIT, OMIT, "source colour family")
    _add_mediainfo_row("chroma_subsampling", getattr(video_track, "chroma_subsampling", None), OMIT, OMIT, "source chroma subsampling")
    _add_mediainfo_row("bit_depth", getattr(video_track, "bit_depth", None), OMIT, OMIT, "source bit depth")
    _add_mediainfo_row(
        "matrix_coefficients",
        getattr(video_track, "matrix_coefficients", None),
        "_Matrix",
        mediainfo_props["_Matrix"],
        _meaning_for_prop("_Matrix", mediainfo_props["_Matrix"]),
    )
    _add_mediainfo_row(
        "color_primaries",
        getattr(video_track, "color_primaries", None),
        "_Primaries",
        mediainfo_props["_Primaries"],
        _meaning_for_prop("_Primaries", mediainfo_props["_Primaries"]),
    )
    _add_mediainfo_row(
        "transfer_characteristics",
        getattr(video_track, "transfer_characteristics", None),
        "_Transfer",
        mediainfo_props["_Transfer"],
        _meaning_for_prop("_Transfer", mediainfo_props["_Transfer"]),
    )
    _add_mediainfo_row(
        "color_range",
        getattr(video_track, "color_range", None),
        "_Range",
        mediainfo_props["_Range"],
        _meaning_for_prop("_Range", mediainfo_props["_Range"]),
    )
    _add_mediainfo_row(
        "chroma_location",
        mediainfo_chromaloc_source,
        "_ChromaLocation",
        mediainfo_props["_ChromaLocation"],
        _meaning_for_prop("_ChromaLocation", mediainfo_props["_ChromaLocation"]),
    )
    _add_mediainfo_row(
        "pixel_aspect_ratio",
        getattr(video_track, "pixel_aspect_ratio", None),
        "_SARNum/_SARDen",
        f"{mediainfo_props.get('_SARNum', UNKNOWN)}/{mediainfo_props.get('_SARDen', UNKNOWN)}",
        "sample aspect ratio",
    )
    _add_mediainfo_row(
        "display_aspect_ratio",
        getattr(video_track, "display_aspect_ratio", None),
        OMIT,
        OMIT,
        "display aspect ratio",
    )
    _add_mediainfo_row(
        "derived_display_aspect_ratio",
        mediainfo_derived_dar,
        OMIT,
        OMIT,
        "DAR derived from width/height and SAR",
    )
    _print_table(
        ["Source field", "Source value", "VS prop", "VS value", "Meaning / notes"],
        table1_rows,
    )
    clip = None
    frame = None
    prepared_clip = None
    try:
        bestsource_track = None
        frame_props: dict[str, Any] = {}
        clip_timing: dict[str, Any] = {
            "FPS": UNKNOWN,
            "fps_num": UNKNOWN,
            "fps_den": UNKNOWN,
            "frames": UNKNOWN,
        }
        clip, bestsource_track = _try_open_bestsource_first_video_track(source_filename)
        if clip is not None:
            frame = clip.get_frame(0)
            frame_props = _props_from_frame(frame)
            clip_timing = {
                "FPS": _format_fps(clip.fps_num, clip.fps_den),
                "fps_num": clip.fps_num,
                "fps_den": clip.fps_den,
                "frames": clip.num_frames,
            }
        _print_section("[2] BestSource first-frame VapourSynth properties")
        if clip is None or frame is None:
            _print_stderr("BestSource diagnostic open failed or was unavailable.")
        else:
            _print_stderr(f"BestSource video track used for diagnostic open: {bestsource_track}")
            table2_rows: list[list[Any]] = []

            for prop in RECOMMENDED_COPY_PROPS:
                if prop in frame_props:
                    value = frame_props[prop]
                    table2_rows.append([prop, value, _meaning_for_prop(prop, value)])

            for prop in SOURCE_CODEC_FLAGS_NOT_RECOMMENDED:
                if prop in frame.props:
                    table2_rows.append([
                        prop,
                        frame.props[prop],
                        "source/codec flag; not recommended to copy",
                    ])
            _print_table(["VS prop", "VS value", "Meaning / notes"], table2_rows)
            _print_stderr("Clip timing from BestSource:")
            _print_table(
                ["Timing field", "Value"],
                [
                    ["FPS", clip_timing["FPS"]],
                    ["fps_num", clip_timing["fps_num"]],
                    ["fps_den", clip_timing["fps_den"]],
                    ["frames", clip_timing["frames"]],
                ],
            )
        blended_props: dict[str, Any] = {}
        # Start with actual first-frame props where available and meaningful.
        for prop, value in frame_props.items():
            if not _is_unspecified_prop(prop, value):
                blended_props[prop] = value
        # Overlay pymediainfo where it provides a recognised or deliberately
        # partial source-level value. This intentionally beats BestSource for
        # _FieldBased because BestSource can report progressive for AVI captures
        # that MediaInfo reports as interlaced.
        for prop, value in mediainfo_props.items():
            if _is_known(value) or value in {PARTIAL, PULLDOWN}:
                blended_props[prop] = value
        # Prefer exact SAR frame props from the opened VapourSynth clip when present.
        # MediaInfo often reports decimal PAR such as 1.067, which becomes 1067/1000.
        # BestSource may expose cleaner exact DVD/VapourSynth values, e.g. 16/15.
        for prop in ["_SARNum", "_SARDen"]:
            if prop in frame_props and _is_known(frame_props[prop]):
                blended_props[prop] = frame_props[prop]
        # Apply user overrides before heuristics so heuristics see the best
        # available preliminary clip props.
        for prop, value in overrides.items():
            blended_props[prop] = value
        _print_section("[3] BLENDED preliminary properties before vstools heuristics")
        table3_rows: list[list[Any]] = []
        for prop in RECOMMENDED_COPY_PROPS:
            value = blended_props.get(prop, UNKNOWN)
            source_note = "available"
            if prop in overrides:
                source_note = "user override"
            elif prop in mediainfo_props and (
                mediainfo_props[prop] == value
                or mediainfo_props[prop] in {PARTIAL, PULLDOWN}
            ):
                source_note = "from pymediainfo / source metadata"
            elif prop in frame_props and frame_props[prop] == value:
                source_note = "from BestSource first-frame prop"
            elif value == UNKNOWN:
                source_note = "not reported or not determined"
            table3_rows.append([prop, value, source_note])
        _print_table(["VS prop", "VS value", "Source / notes"], table3_rows)
        blended_derived_dar = _derive_dar_from_sar(
            getattr(video_track, "width", None),
            getattr(video_track, "height", None),
            blended_props.get("_SARNum", None),
            blended_props.get("_SARDen", None),
        )
        _print_stderr("Clip timing:")
        _print_table(
            ["Timing field", "Value"],
            [
                ["FPS", clip_timing["FPS"]],
                ["fps_num", clip_timing["fps_num"]],
                ["fps_den", clip_timing["fps_den"]],
                ["frames", clip_timing["frames"]],
                ["derived_DAR", blended_derived_dar],

            ],
        )
        heuristics_props: dict[str, Any] = {}
        assumed_props: list[str] = []
        if clip is not None:
            props_to_apply = _known_props_only(blended_props)
            prepared_clip = (
                core.std.SetFrameProps(clip, **props_to_apply)
                if props_to_apply
                else clip
            )
            heuristics_props, assumed_props = _run_vstools_heuristics(prepared_clip)
        _print_section("[4] vstools.video_heuristics() after applying known BLENDED preliminary props")
        if not heuristics_props:
            _print_stderr("No vstools heuristic properties were available.")
        else:
            table4_rows: list[list[Any]] = []
            for prop in COLOUR_PROPS:
                if prop in heuristics_props:
                    note = _meaning_for_prop(prop, heuristics_props[prop])
                    if prop in assumed_props:
                        note = f"{note}; assumed by heuristics"
                    table4_rows.append([prop, heuristics_props[prop], note])
            _print_table(["VS prop", "VS value", "Meaning / notes"], table4_rows)
        if assumed_props:
            _print_stderr(f"vstools assumed props: {assumed_props}")
        heuristic_input_dar = _derive_dar_from_sar(
            getattr(video_track, "width", None),
            getattr(video_track, "height", None),
            blended_props.get("_SARNum", None),
            blended_props.get("_SARDen", None),
        )
        _print_stderr("Clip timing:")
        _print_table(
            ["Timing field", "Value"],
            [
                ["FPS", clip_timing["FPS"]],
                ["fps_num", clip_timing["fps_num"]],
                ["fps_den", clip_timing["fps_den"]],
                ["frames", clip_timing["frames"]],
                ["derived_DAR", heuristic_input_dar],
            ],
        )
        final_props = dict(blended_props)
        # Use heuristics to repair/fill colour/range/chroma values. User
        # overrides are applied again afterwards so explicit caller choices
        # always win.
        for prop in COLOUR_PROPS:
            if prop in heuristics_props:
                final_props[prop] = heuristics_props[prop]
        for prop, value in overrides.items():
            final_props[prop] = value
        failures: list[str] = []
        if final_props.get("_FieldBased") == PULLDOWN:
            failures.append(
                "_FieldBased is not applicable: source appears to be progressive "
                "telecine / 2:3 pulldown. Use IVTC/field matching before "
                "cnr2_bm3d, or use denoising only."
            )
        elif final_props.get("_FieldBased") == PARTIAL:
            failures.append(
                "_FieldBased is indeterminate: source is interlaced but field "
                "order is not reported or not recognised."
            )
        elif not _is_known(final_props.get("_FieldBased")):
            failures.append("_FieldBased is missing or indeterminate.")
        for prop in ["_Matrix", "_Range", "_Primaries", "_Transfer"]:
            value = final_props.get(prop)
            if not _is_known(value) or _is_unspecified_prop(prop, value):
                failures.append(f"{prop} is missing, unspecified, or indeterminate.")
        chroma_subsampling = getattr(video_track, "chroma_subsampling", None)
        if chroma_subsampling is not None and "4:2:0" in str(chroma_subsampling):
            value = final_props.get("_ChromaLocation")
            if not _is_known(value):
                failures.append(
                    "_ChromaLocation is missing or indeterminate for a 4:2:0 source."
                )
        ready = not failures
        _print_section("")
        _print_setframeprops_block(
            final_props,
            ready=ready,
            width=getattr(video_track, "width", None),
            height=getattr(video_track, "height", None),
        )
        _print_section("[6] PRECHECK RESULT")
        precheck_stop_message = (
            "cnr2_bm3d_precheck_video_file: diagnostic precheck complete."
        )
        if ready:
            _print_stderr("PASS")
            _print_stderr("Recommended properties were generated.")
            _print_stderr("Next steps:")
            _print_stderr("  1. Review the SetFrameProps() block in section [5].")
            _print_stderr("  2. Copy it into your real .vpy after opening the source.")
            _print_stderr("  3. Comment out or remove the cnr2_bm3d_precheck_video_file() call.")
            _print_stderr("  4. Re-run the script and call cnr2_bm3d() on the prepared clip.")
            _print_stderr("  5. This precheck will deliberately stop the script now.")
            precheck_stop_message = (
                "cnr2_bm3d_precheck_video_file: PASS; diagnostic precheck complete. "
                "Review/copy the SetFrameProps() block, comment out this precheck call, "
                "then re-run the real script."
            )
        else:
            _print_stderr("FAIL")
            _print_stderr("Problems found:")
            for failure in failures:
                _print_stderr(f"  - {failure}")
            _print_stderr("What to do next:")
            _print_stderr("  1. Inspect the source video and determine the correct missing value(s).")
            _print_stderr("  2. Re-run this precheck with only the necessary override_* value(s).")
            _print_stderr("  3. Repeat until this precheck passes.")
            _print_stderr("  4. When it passes:")
            _print_stderr("       - comment out the cnr2_bm3d_precheck_video_file() call")
            _print_stderr("       - copy/review the SetFrameProps() block from section [5]")
            _print_stderr("       - apply that SetFrameProps() block to your real clip before calling cnr2_bm3d()")
            _print_stderr("")
            _print_suggested_updated_precheck_call(failures, overrides)
            precheck_stop_message = (
                "cnr2_bm3d_precheck_video_file: FAIL; diagnostic precheck complete. "
                "Fix missing/indeterminate values with override_* arguments, rerun the precheck, "
                "and do not continue to cnr2_bm3d() yet."
            )
        _print_reference_tables()
        #raise RuntimeError(precheck_stop_message)
        _print_stderr("")
        _print_stderr(precheck_stop_message)
        sys.exit(0)
    finally:
        # Minimise lingering references to temporary diagnostic objects.  The
        # actual file/source lifetime is controlled by the VapourSynth source
        # plugin, but deleting these references allows Python/plugin cleanup as
        # soon as possible.
        try:
            del frame
        except Exception:
            pass
        try:
            del prepared_clip
        except Exception:
            pass
        try:
            del clip
        except Exception:
            pass
        gc.collect()

# -----------------------------------------------------------------------------
# Output frame property helpers
# -----------------------------------------------------------------------------

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

# -----------------------------------------------------------------------------
# Dependency checking helpers
# -----------------------------------------------------------------------------

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
            "Missing dependency for heuristics, vsjetpack vstools.\n"
            "  Install it into this portable Python with:\n"
            "     python.exe -m pip install vsjetpack"
        )
    if not _HAS_PYMEDIAINFO:
        raise RuntimeError(
            "cnr2_bm3d_precheck_video_file: missing Python dependency pymediainfo.\n"
            "  Install it into this portable Python with:\n"
            "     python.exe -m pip install pymediainfo"
        )
    if not hasattr(core, "bs"):
        raise RuntimeError(
            "Missing required VapourSynth plugin bestsource.\n"
            "  Install it into this portable Python with:\n"
            "     python.exe -m pip install BestSource"
        )
    if not hasattr(core.bs, "VideoSource"):
        raise RuntimeError(
            "bestsource plugin is loaded, but core.bs.VideoSource "
            "is unavailable."
        )
    if not hasattr(core, "fmtc"):
        raise RuntimeError(
            "cnr2_bm3d: missing required VapourSynth plugin fmtconv.\n"
            "  Install it into this portable Python with:\n"
            "     python.exe -m install vapoursynth-fmtconv"
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
            "     python.exe -m install vapoursynth-bm3dcpu"
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

# -----------------------------------------------------------------------------
# User parameter validation helpers
# -----------------------------------------------------------------------------

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

# -----------------------------------------------------------------------------
# Format conversion helpers
# -----------------------------------------------------------------------------

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

# -----------------------------------------------------------------------------
# Core BM3D chroma denoising with optional luma denoising
# (operates on YUV444PS only)
# -----------------------------------------------------------------------------

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

# -----------------------------------------------------------------------------
# Main function:  CNR2 replacement using bm3dcpu CBM3D chroma denoising.
# -----------------------------------------------------------------------------

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

    # -- Basic validation before doing anything expensive -----------------------------------------

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

    # -- Detect everything in one call -----------------------------------------
    info = _detect_format(clip)
    # Apply manual overrides
    if matrix  is not None: info.matrix        = _normalize_matrix_str(matrix)
    if limited is not None: info.limited       = limited
    if tff     is not None:
        info.tff           = tff
        info.is_interlaced = True
    if show_info:
        _print_stderr(info)
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
    # -- Progressive Input path ------------------------------------------------------
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

    # -- Interlaced Input path -------------------------------------------------------
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

    # -- Optional bwdif deinterlace --------------------------------------------
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
