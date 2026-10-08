# Pre-Migration Runbook - Steps 0 to 7

**Project:** MPEG-2 Indexer / VapourSynth Deblocker repository migration
**Filename:** `Pre_Migration_Runbook_Steps_0_to_7_v0_5.md`
**Version:** 0.5
**Date:** 2026-10-08
**Scope:** Safe Git/GitHub/Visual Studio 2026 repository migration preparation only.
**Status:** Ready to run after Claude review `Claude_REVIEW_OF_ChatGPT_Runbook_v0_3_and_D-C_Design_Record_v0_2.md`.

## Decisions already made

- **D-A - DECIDED:** Rename the existing GitHub repository; media stays tracked.
- **D-B - DECIDED:** Fresh clone into the final local location, with the real clone performed through Visual Studio 2026.
- The old local repository remains intact as rollback/reference until the migrated repository passes its acceptance checks.
- **Tag name - DECIDED:** `pre-vapoursynth-mpeg2deblock-restructure`
- **D-C - NOT YET DECIDED:** Final Visual Studio/source layout remains a separate later decision.

## Current old repository

```text
E:\SOFTWARE-Win11\MULTIMEDIA\Mpeg2BlockInspector\Mpeg2BlockInspector
```

## Important execution rules

1. Use an ordinary `cmd.exe`.
2. Close Visual Studio 2026 before Step 0 and keep it closed through Step 7.
3. Run one step at a time.
4. Inspect each result before continuing.
5. Stop on any unexplained difference.
6. Do not make any new repository/document commits after Step 4 until Step 7 is complete.
7. Steps 5 and 6 must each be run start-to-finish in one command window.
8. Do not run the existing `TEST_*.BAT` files during Steps 0 to 7.
9. **Do not execute any section headed `ROLLBACK ONLY` during normal progression.**
10. A `ROLLBACK ONLY` section is reference material to use only when deliberately aborting or undoing the immediately preceding step. When following the runbook normally, skip it completely and continue to the next numbered step.

---

# Step 0 - Confirm starting state

Close Visual Studio 2026 first.

Then:

```bat
cd /d "E:\SOFTWARE-Win11\MULTIMEDIA\Mpeg2BlockInspector\Mpeg2BlockInspector"

git status -sb
git rev-parse --show-toplevel
git rev-parse HEAD
git log --oneline cb6f6a6..HEAD
```

Expected repository root:

```text
E:/SOFTWARE-Win11/MULTIMEDIA/Mpeg2BlockInspector/Mpeg2BlockInspector
```

The audit baseline was commit:

```text
cb6f6a6
```

If HEAD is now later than `cb6f6a6`, inspect:

```bat
git log --oneline cb6f6a6..HEAD
```

Requirement:

- any commits after `cb6f6a6` must be understood and expected;
- ideally they are only migration/review/document additions;
- there must be no unexplained source or test change.

Working tree requirement:

- ideally clean;
- if Visual Studio was used since the audit, unstaged ` M` lines under `.vs/` are tolerated temporarily because Step 2 will remove `.vs` from Git;
- any change outside `.vs/` must be understood before proceeding.

---

# Step 1 - Create and commit `.gitattributes`

Create `.gitattributes` as plain US-ASCII with no byte-order mark:

```bat
(
echo getpic.c text eol=lf
echo mpeg2dec.c text eol=lf
echo Stage1_Inspector_Analyzer_v0_2.py text eol=crlf
) > .gitattributes

type .gitattributes
```

Expected contents:

```text
getpic.c text eol=lf
mpeg2dec.c text eol=lf
Stage1_Inspector_Analyzer_v0_2.py text eol=crlf
```

Stage it:

```bat
git add .gitattributes
```

A Git warning concerning LF being replaced by CRLF for `.gitattributes` itself is harmless.

Verify that the rules are active:

```bat
git check-attr text eol -- src/getpic.c src/mpeg2dec.c
git check-attr text eol -- src/Stage1_Inspector_Analyzer_v0_2.py

git ls-files --eol src/getpic.c src/mpeg2dec.c src/Stage1_Inspector_Analyzer_v0_2.py

git status --short
```

Expected attribute results:

```text
src/getpic.c: text: set
src/getpic.c: eol: lf
src/mpeg2dec.c: text: set
src/mpeg2dec.c: eol: lf
src/Stage1_Inspector_Analyzer_v0_2.py: text: set
src/Stage1_Inspector_Analyzer_v0_2.py: eol: crlf
```

Expected EOL state conceptually:

```text
i/lf w/lf   attr/text eol=lf      src/getpic.c
i/lf w/lf   attr/text eol=lf      src/mpeg2dec.c
i/lf w/crlf attr/text eol=crlf    src/Stage1_Inspector_Analyzer_v0_2.py
```

Expected status normally:

```text
A  .gitattributes
```

Tolerated exception:

```text
 M .vs/...
```

if Visual Studio touched tracked `.vs` files since the audit.

No other unexpected line is acceptable.

Recheck the frozen hashes:

```bat
certutil -hashfile "src\getpic.c" SHA256
certutil -hashfile "src\mpeg2dec.c" SHA256
certutil -hashfile "src\Stage1_Inspector_Analyzer_v0_2.py" SHA256
```

Required hashes:

```text
getpic.c
e80239cfe73a0c04490a9bf131714861254ed1d7d095b052aac616a887ef0eca

mpeg2dec.c
8e6053ccb3a40be8c0d985f2e35124bf728d1f61df9b6d0c01b71e13e13b0947

Stage1_Inspector_Analyzer_v0_2.py
8e0d58305ee4fe518c25b67bdbe74acc4fb392486f23e6137862d916e5e02cda
```

Commit only the staged `.gitattributes` change:

```bat
git commit -m "Define frozen source checkout line endings"
git push origin main
```

Check:

```bat
git log -1 --oneline
git status --short
```

If `.vs` files were already modified locally, they may still appear as unstaged changes here. Step 2 removes them from tracking.

## *** ROLLBACK ONLY - DO NOT EXECUTE DURING NORMAL PROGRESSION - STEP 1 ***

**NORMAL EXECUTION: SKIP THIS ENTIRE SECTION.**

Use the commands below only if you have deliberately decided to undo/abort this step.
Do not paste them merely because they appear next in the document.

Before commit:

```bat
git restore --staged .gitattributes
del .gitattributes
```

After commit/push:

```text
git revert <Step-1-commit-hash>
git push origin main
```

Do not reset or rewrite history.

---

# Step 2 - Separately untrack Visual Studio `.vs` state

This command removes tracked `.vs` files from Git only. It does not delete the old tree's `.vs` files from disk.

```bat
git rm -r --cached .vs
git status --short
```

Expected staged deletions are exactly:

```text
D  .vs/Mpeg2BlockInspector.slnx/v18/.wsuo
D  .vs/Mpeg2BlockInspector.slnx/v18/Browse.VC.db
D  .vs/Mpeg2BlockInspector.slnx/v18/DocumentLayout.json
D  .vs/ProjectSettings.json
D  .vs/slnx.sqlite
```

No other unexpected staged change should appear.

Commit and push:

```bat
git commit -m "Stop tracking Visual Studio workspace state"
git push origin main
```

Verify:

```bat
git status -sb
git ls-files .vs
dir /a .vs
```

Requirements:

- `git status` is clean;
- `git ls-files .vs` prints nothing;
- `.vs` still physically exists in the old working tree.

The existing `.gitignore` already ignores `.vs/`, so Visual Studio workspace state remains local from this point onward.

## *** ROLLBACK ONLY - DO NOT EXECUTE DURING NORMAL PROGRESSION - STEP 2 ***

**NORMAL EXECUTION: SKIP THIS ENTIRE SECTION.**

Use the commands below only if you have deliberately decided to undo/abort this step.
Do not paste them merely because they appear next in the document.

Before commit:

```bat
git restore --staged .vs
```

After commit/push:

```text
git revert <Step-2-commit-hash>
git push origin main
```

That ordinary revert puts the five files back under Git as they were.

---

# Normal progression after Step 2

If Step 2 completed successfully, **do not run the Step 2 rollback section**.

Continue directly to Step 3.

The rollback sections are intentionally placed near the steps they describe for emergency reference, but they are not part of the normal command stream.

---

# Step 3 - Verify repository policy after both commits

Run:

```bat
git check-attr text eol -- src/getpic.c src/mpeg2dec.c
git check-attr text eol -- src/Stage1_Inspector_Analyzer_v0_2.py

git ls-files --eol src/getpic.c src/mpeg2dec.c src/Stage1_Inspector_Analyzer_v0_2.py

git status --short
```

Expected:

```text
getpic.c:
    text: set
    eol: lf
    i/lf w/lf

mpeg2dec.c:
    text: set
    eol: lf
    i/lf w/lf

Stage1_Inspector_Analyzer_v0_2.py:
    text: set
    eol: crlf
    i/lf w/crlf
```

Status must be clean.

Recheck the three hashes:

```bat
certutil -hashfile "src\getpic.c" SHA256
certutil -hashfile "src\mpeg2dec.c" SHA256
certutil -hashfile "src\Stage1_Inspector_Analyzer_v0_2.py" SHA256
```

Required values remain:

```text
e80239cfe73a0c04490a9bf131714861254ed1d7d095b052aac616a887ef0eca
8e6053ccb3a40be8c0d985f2e35124bf728d1f61df9b6d0c01b71e13e13b0947
8e0d58305ee4fe518c25b67bdbe74acc4fb392486f23e6137862d916e5e02cda
```

---

# Step 4 - Confirm GitHub state and record the exact commit to be tested

Run:

```bat
git status -sb
git log --oneline -5
git rev-parse HEAD
git ls-remote origin refs/heads/main
```

Requirements:

- working tree clean;
- local HEAD and remote `main` identify the same commit;
- both preparation commits are visible in recent history.

Record the tested commit in a temporary text file:

```bat
git rev-parse HEAD > "%TEMP%\Mpeg2BlockInspector_tested_commit.txt"
type "%TEMP%\Mpeg2BlockInspector_tested_commit.txt"
```

This is the exact commit that Steps 5 and 6 will test and Step 7 must tag.

**Do not make any further commit between this point and completion of Step 7.**

---

# Step 5 - Disposable command-line scratch clone

Run this whole step in one command window.

Define the scratch folder:

```bat
set "CLONECHECK=%TEMP%\Mpeg2BlockInspector_clonecheck"

if exist "%CLONECHECK%" rmdir /s /q "%CLONECHECK%"
```

Clone the current old local repository as a physically separate local clone:

```bat
git clone --no-hardlinks "E:\SOFTWARE-Win11\MULTIMEDIA\Mpeg2BlockInspector\Mpeg2BlockInspector" "%CLONECHECK%"
```

Enter it and prove where you are:

```bat
cd /d "%CLONECHECK%"
git rev-parse --show-toplevel
```

Required output must be the scratch folder, not the old repository:

```text
.../Mpeg2BlockInspector_clonecheck
```

Hash the three frozen files:

```bat
certutil -hashfile "src\getpic.c" SHA256
certutil -hashfile "src\mpeg2dec.c" SHA256
certutil -hashfile "src\Stage1_Inspector_Analyzer_v0_2.py" SHA256
```

Required:

```text
getpic.c
e80239cfe73a0c04490a9bf131714861254ed1d7d095b052aac616a887ef0eca

mpeg2dec.c
8e6053ccb3a40be8c0d985f2e35124bf728d1f61df9b6d0c01b71e13e13b0947

Stage1_Inspector_Analyzer_v0_2.py
8e0d58305ee4fe518c25b67bdbe74acc4fb392486f23e6137862d916e5e02cda
```

Inspect checkout state:

```bat
git check-attr text eol -- src/getpic.c src/mpeg2dec.c src/Stage1_Inspector_Analyzer_v0_2.py
git ls-files --eol src/getpic.c src/mpeg2dec.c src/Stage1_Inspector_Analyzer_v0_2.py
git status --short
```

Requirements:

- both C files are `w/lf`;
- analyzer is `w/crlf`;
- Git status is clean.

Confirm the scratch clone is on the exact tested commit:

```bat
git rev-parse HEAD
type "%TEMP%\Mpeg2BlockInspector_tested_commit.txt"
```

Those two hashes must be identical.

Return to the old repository:

```bat
cd /d "E:\SOFTWARE-Win11\MULTIMEDIA\Mpeg2BlockInspector\Mpeg2BlockInspector"
git rev-parse --show-toplevel
```

Required: the old repository root.

Delete only the scratch clone:

```bat
rmdir /s /q "%CLONECHECK%"
```

## *** ROLLBACK ONLY - DO NOT EXECUTE DURING NORMAL PROGRESSION - STEP 5 ***

**NORMAL EXECUTION: SKIP THIS ENTIRE SECTION.**

Use the commands below only if you have deliberately decided to undo/abort this step.
Do not paste them merely because they appear next in the document.

Nothing in the old repository was changed.

If interrupted, delete the scratch folder:

```bat
if exist "%CLONECHECK%" rmdir /s /q "%CLONECHECK%"
```

---

# Step 6 - Prove the old executable reproduces the LP baseline index

Run this whole step in one command window.

Do not use `TEST_Mpeg2BlockInspector_LP_2.BAT`. It deletes and rewrites the tracked old-tree `.idx` and `.log`.

Start in the old repository and prove it:

```bat
cd /d "E:\SOFTWARE-Win11\MULTIMEDIA\Mpeg2BlockInspector\Mpeg2BlockInspector"
git rev-parse --show-toplevel
```

Required:

```text
E:/SOFTWARE-Win11/MULTIMEDIA/Mpeg2BlockInspector/Mpeg2BlockInspector
```

Create a separate scratch output directory:

```bat
set "LPREPRO=%TEMP%\Mpeg2BlockInspector_LP_repro"

if exist "%LPREPRO%" rmdir /s /q "%LPREPRO%"
mkdir "%LPREPRO%"
```

Print the exact paths to be used:

```bat
echo EXE    = E:\SOFTWARE-Win11\MULTIMEDIA\Mpeg2BlockInspector\Mpeg2BlockInspector\src\x64\Release\Mpeg2BlockInspector.exe
echo INPUT  = E:\SOFTWARE-Win11\MULTIMEDIA\Mpeg2BlockInspector\Mpeg2BlockInspector\VHSC_samples\LG_576i_3_LP.mpg
echo OUTPUT = %LPREPRO%\LG_576i_3_LP.idx
echo FFMPEG = C:\SOFTWARE\ffmpeg\ffmpeg.exe
```

Use the existing LP BAT's same pipe form, with the index destination in the scratch folder:

```bat
"C:\SOFTWARE\ffmpeg\ffmpeg.exe" -v error -i "E:\SOFTWARE-Win11\MULTIMEDIA\Mpeg2BlockInspector\Mpeg2BlockInspector\VHSC_samples\LG_576i_3_LP.mpg" -c:v copy -an -f mpeg2video - | "E:\SOFTWARE-Win11\MULTIMEDIA\Mpeg2BlockInspector\Mpeg2BlockInspector\src\x64\Release\Mpeg2BlockInspector.exe" -b - -m "%LPREPRO%\LG_576i_3_LP.idx"

echo Inspector pipeline exit code: %ERRORLEVEL%
```

Required:

```text
Inspector pipeline exit code: 0
```

Any non-zero exit code is a STOP. Do not continue to the hash check or Step 7 until the cause is understood.

The scratch directory must exist and be writable because the inspector writes:

```text
<index>.s1tmp
```

beside the requested index before renaming it into place.

Inspect the result:

```bat
dir "%LPREPRO%"
certutil -hashfile "%LPREPRO%\LG_576i_3_LP.idx" SHA256
```

Required LP baseline hash:

```text
849b6a9c411a7db837268af3ac54501a06f5f1158e68d5245ce2b3ec40a0d011
```

Also confirm that the tracked old-tree LP index was not touched:

```bat
certutil -hashfile "VHSC_samples\LG_576i_3_LP.idx" SHA256
```

It must also be:

```text
849b6a9c411a7db837268af3ac54501a06f5f1158e68d5245ce2b3ec40a0d011
```

If either hash differs, stop.

Only after both hashes match:

```bat
rmdir /s /q "%LPREPRO%"
```

Confirm the repository is still clean and still at the tested commit:

```bat
git status -sb
git rev-parse HEAD
type "%TEMP%\Mpeg2BlockInspector_tested_commit.txt"
```

The two commit hashes must be identical.

## *** ROLLBACK ONLY - DO NOT EXECUTE DURING NORMAL PROGRESSION - STEP 6 ***

**NORMAL EXECUTION: SKIP THIS ENTIRE SECTION.**

Use the commands below only if you have deliberately decided to undo/abort this step.
Do not paste them merely because they appear next in the document.

Nothing in the repository should have changed.

If interrupted, delete the scratch output folder:

```bat
if exist "%LPREPRO%" rmdir /s /q "%LPREPRO%"
```

---

# Step 7 - Create and explicitly push the migration tag

First verify the final pre-migration state:

```bat
cd /d "E:\SOFTWARE-Win11\MULTIMEDIA\Mpeg2BlockInspector\Mpeg2BlockInspector"

git rev-parse --show-toplevel
git status -sb
git rev-parse HEAD
type "%TEMP%\Mpeg2BlockInspector_tested_commit.txt"
git ls-remote origin refs/heads/main
git tag -l pre-vapoursynth-mpeg2deblock-restructure
```

Requirements:

- correct old repository root;
- working tree clean;
- local HEAD equals the commit recorded in Step 4;
- remote `main` is the same commit;
- the tag does not already exist.

Create the decided tag:

```bat
git tag pre-vapoursynth-mpeg2deblock-restructure
```

Push the tag explicitly:

```bat
git push origin pre-vapoursynth-mpeg2deblock-restructure
```

Verify all identities:

```bat
git rev-parse HEAD
git rev-list -n 1 pre-vapoursynth-mpeg2deblock-restructure
git ls-remote --tags origin refs/tags/pre-vapoursynth-mpeg2deblock-restructure
type "%TEMP%\Mpeg2BlockInspector_tested_commit.txt"
```

All four must identify the same commit.

After successful verification, the temporary tested-commit record is no longer needed:

```bat
del "%TEMP%\Mpeg2BlockInspector_tested_commit.txt"
```

## *** ROLLBACK ONLY - DO NOT EXECUTE DURING NORMAL PROGRESSION - STEP 7 ***

**NORMAL EXECUTION: SKIP THIS ENTIRE SECTION.**

Use the commands below only if you have deliberately decided to undo/abort this step.
Do not paste them merely because they appear next in the document.

If the tag has been created locally but not pushed:

```bat
git tag -d pre-vapoursynth-mpeg2deblock-restructure
```

If the tag has already been pushed, deleting the remote tag is an externally visible GitHub change and requires Dave's explicit go.

No reset, force push or history rewrite is required.

---

# STOP POINT

After Step 7:

**STOP. Do not rename the GitHub repository yet.**

At this point the reversible preparation is complete.

The next externally visible action is:

```text
Step 8 - Rename the GitHub repository
```

That step requires Dave's explicit go.

After the rename, the real migration clone will be performed through Visual Studio 2026 directly into the final permanent local path, followed by Gate B from a command prompt.

---

# Gate-B facts already recorded for later use

Frozen source hashes:

```text
src\getpic.c
e80239cfe73a0c04490a9bf131714861254ed1d7d095b052aac616a887ef0eca

src\mpeg2dec.c
8e6053ccb3a40be8c0d985f2e35124bf728d1f61df9b6d0c01b71e13e13b0947

src\Stage1_Inspector_Analyzer_v0_2.py
8e0d58305ee4fe518c25b67bdbe74acc4fb392486f23e6137862d916e5e02cda
```

Old reference executable:

```text
src\x64\Release\Mpeg2BlockInspector.exe
SHA-256:
3edb7147001341f669b8d49be7446d79fe2d688fcff66a16f3227f80290aa5de
```

LP baseline index:

```text
VHSC_samples\LG_576i_3_LP.idx
SHA-256:
849b6a9c411a7db837268af3ac54501a06f5f1158e68d5245ce2b3ec40a0d011
```

The rebuilt executable after migration does not need to hash identically to the old executable.

The newly generated LP index does need to hash identically to the baseline.

---

# Notes carried from Claude review v0.4

- The old repository is public.
- Media remains tracked by decision.
- The `.vs` files must be untracked before the VS2026 clone.
- The three frozen text files have repository-controlled EOL policy before cloning.
- The existing test BAT files are not used for the pre-rename reproducibility proof.
- The same `C:\SOFTWARE\ffmpeg\ffmpeg.exe` used by the existing test scripts is used for Step 6 and must also be used later at Gate B.
- The inspector source cold-read supports that the index contains no path/time/random data and that, with `-m` and no `-o`, the scratch index is the only output expected.
- Licence/NOTICE cleanup is a later repository-structure item and is not part of Steps 0 to 7.
- D-C, the final Visual Studio/source layout, remains undecided and is not changed by this runbook.

# D-C / later Visual Studio design cross-reference

The intended later Visual Studio 2026 end state and the mandatory CNR3 configuration-transfer audit are recorded separately in:

```text
Migration_Design_Record_D-C_CNR3_VS2026_Intent_v0_2.md
```

This does not alter Steps 0-7 in this runbook. D-C remains a post-Gate-B layout decision, and the detailed CNR3 settings audit is required before creating the future VapourSynth DLL project.


---

# Change log

## v0.5 - 2026-10-08

- Re-labelled every recovery block as `ROLLBACK ONLY - DO NOT EXECUTE DURING NORMAL PROGRESSION`.
- Added an explicit rule that rollback sections are skipped completely during normal execution.
- Added a direct normal-progression note after Step 2: continue to Step 3; do not run rollback commands.
- No normal execution command or sequencing change.

## v0.4 - 2026-10-08

- Corrected the document identity: filename/version now agree and the title correctly says Steps 0 to 7.
- Added Claude review A1: Step 6 requires inspector pipeline exit code 0; any non-zero value is a STOP.
- Updated the D-C cross-reference to the superseding design record v0.2.
- No migration command or sequencing change beyond the explicit Step 6 exit-code acceptance criterion.

## v0.3 - 2026-10-08

- Added the D-C / CNR3 Visual Studio design-record cross-reference.

## v0.2 - 2026-10-08

- Incorporated Claude pre-migration audit safeguards, rollback instructions, scratch-path guards, tested-commit identity, and reproducibility checks.
