# vscnr2_bm3d

Replacement for VapourSynth CNR2-style chroma denoising, using `bm3dcpu` for chroma denoising and optional `bwdif` / `bwdif+znedi3` deinterlacing.    
- Intended for chroma-noisy VHS / VHS-C / analogue captures, especially home movies.
- Defaults to **chroma-only** denoising.
- Supports optional luma denoising via `sigma_luma`.
- Handles progressive and interlaced PAL/NTSC YUV sources.
- For interlaced sources, fields are separated by parity (TFF/BFF), denoised independently, rewoven, then optionally deinterlaced.
- Uses `bm3dcpu` for denoising.
- Uses `bwdif` for optional deinterlacing with optional framerate doubled deinterlacing.
- When `deinterlace_quality="enhanced"`, uses optional `znedi3` as `bwdif`'s `edeint` helper .

#### Notes and Limitations    

Standard and Advanced vapoursynth users will likely instead choose to denoise chroma themselves using their own favoirite denoiser(s).

> [!WARNING]
> Anamorphic PAL/NTSC DVD-style display aspect ratios are not automatically detected.
For example, 720x576 PAL and 720x480 NTSC sources may be intended for 4:3 or 16:9 display,
even when the stored frame dimensions suggest a different ratio.
DAR is derived only from available metadata, thus Display Aspect Ratio (DAR) is calculated incorrectly for that ...
so you may need to set or correct `_SARNum/_SARDen` yourself after denoising and before `set_output()`.    

Common cases:  `DAR = (width * _SARNum) / (height * _SARDen)`    
| Source type                     | Stored frame size | Intended DAR | _SARNum | _SARDen | Derived DAR calculation    |
| ------------------------------- | ----------------: | -----------: | ------: | ------: | -------------------------- |
| PAL 4:3                         |           720x576 |          4:3 |      16 |      15 | 720 / 576 * 16 / 15 = 4:3  |
| PAL 16:9 anamorphic             |           720x576 |         16:9 |      64 |      45 | 720 / 576 * 64 / 45 = 16:9 |
| NTSC 4:3                        |           720x480 |          4:3 |       8 |       9 | 720 / 480 * 8 / 9 = 4:3    |
| NTSC 16:9 anamorphic            |           720x480 |         16:9 |      32 |      27 | 720 / 480 * 32 / 27 = 16:9 |
| Square-pixel PAL storage ratio  |           720x576 |          5:4 |       1 |       1 | 720 / 576 * 1 / 1 = 5:4    |
| Square-pixel NTSC storage ratio |           720x480 |          3:2 |       1 |       1 | 720 / 480 * 1 / 1 = 3:2    |
```
# Example: After denoising, set Frame properties on PAL 720x576 intended for 16:9 display.
clip = core.std.SetFrameProps(
    clip,
    _SARNum=64,
    _SARDen=45,
)
# _SARNum and _SARDen are VapourSynth frame properties.
# Whether that aspect-ratio signalling is preserved in the final
# encoded file depends on the output path, encoder, and container.
```

---

## ⚠️ CRITICAL NOTES - READ THIS BEFORE CONTINUING

> [!WARNING]
> VHS capture files **notoriously often** have missing, incomplete, incorrect, or ambiguous metadata.
This is especially common with AVI captures, lossless captures, DVD/VOB/MPEG sources,
and files produced by older capture workflows or USB video capture devices.

For `cnr2_bm3d()`, the most critical source properties are:    
- whether the source is progressive or interlaced;
- if interlaced, whether it is bottom-field-first (BFF) or top-field-first (TFF);
- whether the source is actually telecine / 2:3 pulldown rather than normal interlaced video;
- colour matrix, range, primaries, transfer, chroma location, and aspect-ratio signalling.

Wrong metadata or wrong clip frame properties will ALMOST CERTAINLY produce wrong output.

In particular, deinterlacing with the wrong field order can damage motion,
create judder, or produce visually incorrect output.
Telecine / 2:3 pulldown material should normally be handled with
inverse telecine / field matching, not with `bwdif` deinterlacing here.

---

### RECOMMENDED SAFE WORKFLOW

The recommended workflow is now:

Identify and ensure correct clip properties BEFORE calling `cnr2_bm3d()`.

First, run only the diagnostic precheck phase:    
1. Create a normal `.vpy` script containing a call to `cnr2_bm3d_precheck_video_file(source_filename)`.
2. Run the `.vpy` script with `vspipe`, eg `vspipe.bat test_precheck.vpy NUL >stdout.txt 2>stderr.txt`.
3. Review the diagnostic report, eg in stderr.txt.
4. Follow any suggestions to edit the call to `cnr2_bm3d_precheck_video_file()` with new `override_*` parameter settings.
5. Repeat steps 2 to 4 until `cnr2_bm3d_precheck_video_file()` reports PASS.

When `cnr2_bm3d_precheck_video_file()` reports PASS:    

6. Copy/review the suggested `core.std.SetFrameProps()` block.
7. Comment out or remove the `cnr2_bm3d_precheck_video_file()` call; put whatever else you need into the script.
8. Ensure the script opens the source clip normally, eg with `BestSource` or whichever source filter you prefer.
9. Paste/check/edit the suggested `SetFrameProps()` block immediately after opening the source clip.
10. Call `cnr2_bm3d()` on the clip after the correct frame properties have been applied, before `set_output()`.

The precheck helper is a **diagnostic-only helper**.
It deliberately stops the VapourSynth script after printing its report.
This is intentional.
It prevents the script from continuing into real processing while the precheck is still active.

Example `.vpy` script:

```python
import vapoursynth as vs
core = vs.core

#import ... your other things
from vscnr2_bm3d import cnr2_bm3d_precheck_video_file, cnr2_bm3d
source_filename = r"D:\TEST\my_vhs_capture.avi"

# -------------------------------------------------------------------------
# Phase 1: diagnostic precheck.
# Run this first. It prints a diagnostic report and deliberately stops the
# script. If the report suggests override_* values, edit this call and run
# it again. Repeat until the precheck reports PASS.
# When the precheck reports PASS, comment out or remove this call before
# running the real processing phase below.
# -------------------------------------------------------------------------
cnr2_bm3d_precheck_video_file(source_filename)

# -------------------------------------------------------------------------
# Phase 2: real processing.
# Do not run this phase while the precheck call above is still active,
# because the precheck deliberately stops the script.
# -------------------------------------------------------------------------
# Open the source however you prefer. BestSource is shown here only as an example.
clip = core.bs.VideoSource(source_filename)

# Paste/check/edit the SetFrameProps() block recommended by the precheck.
# Example only. Use the values printed for your actual source.
clip = core.std.SetFrameProps(
    clip,
    _FieldBased=1,       # BFF
    _Matrix=5,           # BT470_BG
    _Range=0,            # LIMITED
    _Primaries=5,        # BT470_BG
    _Transfer=5,         # BT470_BG
    _ChromaLocation=0,   # LEFT
    _SARNum=16,
    _SARDen=15,
)

# Now run the real cnr2_bm3d processing.
# Refer to the README.md for help and examples of light, medium, and heavy settings.
clip = cnr2_bm3d(
    clip,
    sigma_uv=3.5,
    sigma_luma=0.0,
    radius=1,
    full_quality_denoise=True,
    deinterlace=True,
    deinterlace_rate="same",
    deinterlace_quality="standard",
)

# Other processing here, possibly including anamorphic SAR settings if required.

clip.set_output()
```

---

### What the precheck helper `cnr2_bm3d_precheck_video_file()` does

```python
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
```

The precheck helper:    
- inspects source-file metadata using `pymediainfo`;
- opens the file briefly with BestSource and reads the first frame's VapourSynth properties;
- blends available MediaInfo, BestSource, and user override values;
- runs `vstools.video_heuristics()` after applying known blended preliminary properties;
- prints a suggested `core.std.SetFrameProps()` block;
- prints relevant VapourSynth property value references;
- deliberately stops the script when finished.

The precheck helper `cnr2_bm3d_precheck_video_file()` is **not** a part of the
runtime filtering chain to process any video. It is simply a means to determine
what your video clip properties really are and what must be applied to maximize
chances of success.  Fell free to skip it at your own risk.

---

### If the precheck cannot determine a mandatory property

If the precheck `cnr2_bm3d_precheck_video_file()` finds missing or indeterminate mandatory information,
it will print `FAIL` and suggest an updated call parameters using one or more `override_*` values.
This process iterates until success or you give up on your video.

For example, many AVI captures are reported as interlaced but do not report field order.
In that case, the precheck may ask you to inspect the source and rerun with the correct `_FieldBased` value:
```python
cnr2_bm3d_precheck_video_file(
    source_filename,
    # _FieldBased is missing or indeterminate.
    # Inspect the source video and choose the correct value.
    # Valid override_FieldBased values:
    #   0 = PROGRESSIVE
    #   1 = BFF
    #   2 = TFF
    override_FieldBased=1,   # replace with the correct value for this video
)

```

The usual methods of inpecting video clips to determinf characteristics applies.
Perhaps google it, or ask on https://www.videohelp.com/ or https://forum.doom9.org/

Use an override parameter only after you have determined the correct value. Do not guess field order.

Once the precheck passes, copy/review its suggested `SetFrameProps()` block, 
follow the process above , then run the real vpy script.

---

### Less safe workflow

You may skip `cnr2_bm3d_precheck_video_file()` at your own risk,
assuming the input clip itself already has  correct VapourSynth frame properties
set by the source filter or by your script before calling `cnr2_bm3d()`.

At minimum, the following frame properties should be correct where applicable:

```text
_FieldBased       0 = progressive, 1 = BFF interlaced, 2 = TFF interlaced
_Matrix           colour matrix
_Range            0 = limited/TV range, 1 = full/PC range
_Primaries        colour primaries
_Transfer         transfer characteristics
_ChromaLocation   chroma sample location, especially for 4:2:0 sources
_SARNum           sample aspect ratio numerator
_SARDen           sample aspect ratio denominator
```

For interlaced sources, `_FieldBased` is especially important.

If `deinterlace=True` is requested and the script cannot determine a safe field order,
it will fail rather than guessing.

---

### Telecine / 2:3 pulldown sources

If the precheck detects progressive telecine / 2:3 pulldown material, 
`bwdif` deinterlacing is not the correct operation for that clip.

Use an inverse-telecine / field-matching workflow before calling `cnr2_bm3d()`, 
or use `cnr2_bm3d()` only for denoising after the clip has been prepared appropriately.

---

### Main function cnr2_bm3d()

```python
def cnr2_bm3d(
    clip: vs.VideoNode,
    sigma_uv: float = 3.5,
    sigma_luma: float = 0.0,
    radius: int = 1,
    full_quality_denoise: bool = True,
    matrix: Optional[str] = None,
    limited: Optional[bool] = None,
    tff: Optional[bool] = None,
    deinterlace: bool = False,
    deinterlace_rate: str = "same",
    deinterlace_quality: str = "standard",
    show_info: bool = False,
) -> vs.VideoNode:
```

#### Arguments

`clip`  
Input YUV clip. Any bit depth and subsampling.

`sigma_uv`  
Chroma denoising strength. Around `3.5` is a practical CNR2-like starting point.

`sigma_luma`  
Optional luma denoising strength.

- `0.0` preserves luma from the source clip, matching the original chroma-only CNR2 behaviour.
- `0.5` = very light luma denoise.
- `1.0` = light luma denoise.
- `2.0` = moderate luma denoise; use cautiously.

Luma denoising is visually much more obvious than chroma denoising.

`radius`  
Temporal radius. `0` = spatial only, `1+` = temporal. The wrapper allows `0..9`, but for old VHS chroma denoising, practical values are usually `1` or `2`, with `3` and `4` as experimental headroom.

With field-split interlaced sources, each unit of radius spans one same-parity field, equivalent to one full interlaced frame.

`full_quality_denoise`  
If `True`, runs two BM3Dv2 passes with Wiener refinement. Slower but better quality. Recommended for final encodes.

`matrix`, `limited`, `tff`  
Manual overrides for old workflows. The preferred safe workflow is now to set correct frame properties before calling `cnr2_bm3d()` using the precheck-generated `SetFrameProps()` block.

- `matrix=None` = auto-detect from clip properties / heuristics.
- `limited=None` = auto-detect from clip properties / heuristics.
- `tff=None` = auto-detect from `_FieldBased` where possible.

Only specify these if you know the clip properties are wrong and you understand the override.

`deinterlace`  
If `True`, run `bwdif` deinterlacing on the rewoven interlaced output.

`deinterlace_rate`  
Only used when `deinterlace=True`.

- `"same"` = same-rate progressive output, e.g. `25i -> 25p` or `29.97i -> 29.97p`.
- `"double"` = double-rate progressive output, e.g. `25i -> 50p` or `29.97i -> 59.94p`.

Case is ignored.

`deinterlace_quality`  
Only used when `deinterlace=True`.

- `"standard"` = normal `bwdif` deinterlacing.
- `"enhanced"` = `bwdif` with a `znedi3` `edeint` helper for higher-quality spatial prediction.

Case is ignored.

`show_info`  
If `True`, prints the detected `ClipInfo` before processing. Useful for checking what `cnr2_bm3d()` sees at runtime.

---

### Notes

The function handles progressive and interlaced PAL/NTSC sources.

For interlaced sources:

- fields are separated by parity;
- each same-parity stream is denoised independently;
- temporal comparisons are always between same-parity fields;
- fields are rewoven back to interlaced;
- optional deinterlacing with `bwdif` can then be applied.

For normal interlaced PAL VHS, TFF is common, but do not blindly assume it for every capture chain. Some DVD/VOB/MPEG material may be BFF, as the precheck can show.

---

### Dependencies

```text
VapourSynth R76+
pymediainfo                   pip install pymediainfo
vsjetpack / vstools           pip install vsjetpack
BestSource                    pip install BestSource
vapoursynth-fmtconv           pip install vapoursynth-fmtconv
vapoursynth-bm3dcpu           pip install vapoursynth-bm3dcpu
vapoursynth-bwdif             pip install vapoursynth-bwdif
vapoursynth-znedi3            pip install vapoursynth-znedi3
```

The precheck helper `cnr2_bm3d_precheck_video_file()` requires `pymediainfo`, `BestSource`, and `vstools`.

The main denoising function requires `fmtconv` and `bm3dcpu`.

Deinterlacing requires `vapoursynth-bwdif`.

Enhanced deinterlacing requires `vapoursynth-znedi3`.

---

### Plugin autoload assumptions

The relevant DLL/plugin files are expected to be auto-loaded by VapourSynth, for example:

```text
vapoursynth\plugins\bwdif.dll
vapoursynth\plugins\fmtconv.dll
vapoursynth\plugins\znedi3.dll
vapoursynth\plugins\bm3dcpu\manifest.vs
vapoursynth\plugins\bm3dcpu\bm3dcpu.dll
vapoursynth\plugins\bm3dcpu\bm3dcpu.zn4.dll
```

Exact paths may differ depending on your (possibly portable) VapourSynth layout.

---

### Usage examples

#### Example 1 - Run precheck first

```python
import vapoursynth as vs
core = vs.core

from vscnr2_bm3d import cnr2_bm3d_precheck_video_file

source_filename = r"D:\TEST\my_vhs_capture.avi"

cnr2_bm3d_precheck_video_file(source_filename)

# The precheck deliberately stops the script.
# Review the printed report, copy/review the suggested SetFrameProps() block,
# comment out this precheck call, then run the real processing script.
```

#### Example 2 - Light chroma-only denoise, keep interlaced

```python
light = cnr2_bm3d(
    clip,
    sigma_uv=1.5,
    sigma_luma=0.0,
    radius=1,
    full_quality_denoise=False,
    deinterlace=False,
    show_info=True,
)
```

#### Example 3 - Light chroma plus very light luma denoise, keep interlaced

```python
light = cnr2_bm3d(
    clip,
    sigma_uv=1.5,
    sigma_luma=0.5,
    radius=1,
    full_quality_denoise=False,
    deinterlace=False,
    show_info=True,
)
```

#### Example 4 - Medium CNR2-like chroma denoise, same-rate progressive output

```python
medium = cnr2_bm3d(
    clip,
    sigma_uv=3.5,
    sigma_luma=0.0,
    radius=1,
    full_quality_denoise=True,
    deinterlace=True,
    deinterlace_rate="same",
    deinterlace_quality="standard",
)
```

#### Example 5 - Medium chroma plus light luma denoise, enhanced deinterlacing

```python
medium = cnr2_bm3d(
    clip,
    sigma_uv=3.5,
    sigma_luma=1.0,
    radius=1,
    full_quality_denoise=True,
    deinterlace=True,
    deinterlace_rate="same",
    deinterlace_quality="enhanced",
)
```

#### Example 6 - Heavy chroma denoise, double-rate progressive output

```python
heavy = cnr2_bm3d(
    clip,
    sigma_uv=8.0,
    sigma_luma=0.0,
    radius=2,
    full_quality_denoise=True,
    deinterlace=True,
    deinterlace_rate="double",
    deinterlace_quality="standard",
)
```

#### Example 7 - Heavy chroma plus moderate luma denoise, enhanced double-rate deinterlacing

```python
heavy = cnr2_bm3d(
    clip,
    sigma_uv=8.0,
    sigma_luma=2.0,
    radius=2,
    full_quality_denoise=True,
    deinterlace=True,
    deinterlace_rate="double",
    deinterlace_quality="enhanced",
)
```

#### Example 8 - wild heavy chroma issues

```python
heavy = cnr2_bm3d(
    clip,
    sigma_uv=16.0,
    sigma_luma=0.0,
    radius=2,
    full_quality_denoise=True,
    deinterlace=True,
    deinterlace_rate="double",
    deinterlace_quality="enhanced",
)
```

#### Example 9 - wild heavy chroma issues and wild heavy luma issues for real shockers of VHS captures 

```python
heavy = cnr2_bm3d(
    clip,
    sigma_uv=16.0,
    sigma_luma=16.0,
    radius=2,
    full_quality_denoise=True,
    deinterlace=True,
    deinterlace_rate="double",
    deinterlace_quality="enhanced",
)
```
