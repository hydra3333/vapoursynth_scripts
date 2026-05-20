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
        radius: int = 1,
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
    # Older scripts may still expose _ColorRange:
    #   _ColorRange 0 = full,    1 = limited
    #
    # Do not read _ColorRange unless _Range is absent, because reading
    # _ColorRange in newer VapourSynth versions emits a deprecation warning.
    if "_Range" in f.props:
        return int(f.props["_Range"]) == 0

    if "_ColorRange" in f.props:
        return int(f.props["_ColorRange"]) != 0

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
# Format conversion helpers
# ─────────────────────────────────────────────────────────────────────────────

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
            css=f"{1 << fmt_target.subsampling_w}{1 << fmt_target.subsampling_h}",
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
    radius: int = 1,
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

    if clip.format is None:
        raise ValueError("cnr2_bm3d: clip must have a constant (non-variable) format")
    if clip.format.color_family != vs.YUV:
        raise ValueError("cnr2_bm3d: input must be a YUV clip")

    # ── Detect everything in one call ─────────────────────────────────────────
    info = _detect_format(clip)
    # Apply manual overrides
    if matrix  is not None: info.matrix        = matrix
    if limited is not None: info.limited       = limited
    if tff     is not None:
        info.tff           = tff
        info.is_interlaced = True
    if show_info:
        print(info)

    # ── Progressive Input path ──────────────────────────────────────────────────────
    if not info.is_interlaced:
        clip_f   = _to_444ps(clip, info)
        denoised = _bm3d_chroma(clip_f, sigma_uv, radius, full_quality)
        result   = _from_444ps(denoised, info)
        return result

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
    if not deinterlace:
        return interlaced_out

    # ── Optional bwdif deinterlace ────────────────────────────────────────────
    if not hasattr(core, "bwdif"):
        raise RuntimeError(
            "cnr2_bm3d: deinterlace=True requires the bwdif plugin.\n"
            "  pip install vapoursynth-bwdif "
        )
    # field=1 -> keep top field -> 25fps progressive (TFF source)
    # field=0 -> keep bottom field -> 25fps progressive (BFF source)
    # field=2 -> output both fields -> 50fps progressive
    bwdif_field = 1 if _tff else 0
    result = core.bwdif.BwDif(interlaced_out, field=bwdif_field)
    return result
