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
