# The calibration sheet, its 50 mm stand and the empty-sheet photo (Image → STEP, ImageToSketch 0.10.0 / 0.10.1)

**WORK IN PROGRESS: MANY FEATURES MAY BE BROKEN** (EVERYTHING to solid 0.9.2-wip, 18 October 2026)

*Working notes on bringing ImageToSketch 0.10.0 into the app. The design and the simulated-photo measurements
come from the ImageToSketch project (its own, longer notes are its `docs/CALSHEET.md`, which was not part of the
module zip and is not copied here); the checks on this app's 0.9.2 build are this integration's own.*

## Why a calibration sheet

The scale token gives the scale from one black square: 8 corners. For a flat part that is enough, but a tall part
needs to know how far away the camera was (something nearer the camera looks bigger), and with the token that
distance comes from the photo's lens data, a typed number or a guess. On ImageToSketch's simulated photos of a
140 mm tall part, the token sheet was out by up to 40 % when the distance was guessed.

The calibration sheet replaces the token with a frame of markers, and adds a stand that measures the distance:

- **Page 1**: 40 numbered markers (OpenCV ArUco, 16 mm) round a clear middle where the part goes. Their known
  positions give the scale, the camera's tilt from up to 160 corners, the lens's slight bend (taken out), a
  warning when the paper is not flat, and how the light falls across the sheet. Markers hidden by a shadow or by
  the part are left out. A4 and Letter use different marker numbers, so the page size is read from the photo.
- **The stand** (3D-printed, top face down, no supports; 40 mm square top, 50 mm high) carries a label from page 2
  with one more marker. Stood inside the frame a few cm from the part, its marker is 50 mm nearer the camera than
  the frame's, so it looks larger by distance / (distance − 50): that gives the camera distance with no lens data
  and nothing typed but the part's height. Its height is measured once with calipers (about 50.1 mm with the
  label) and typed into *Stand height, measured*.
- **Page 2's check lines** (150 mm across, 200 mm along): a printer that stretches the page (a laser printer often
  does, more in the feed direction) is put right once their measured lengths are typed in.

## Measured (ImageToSketch, simulated photos)

A 140 mm tall part with a 26 × 12 mm top, nothing typed but its height: within **0.7 %** from 30 - 45 cm and
**0.6 %** from about 1 m. The token sheet on the same photos: out by up to 40 %. ImageToSketch's own checks:
236 tests, its simulated-photo comparison, and its self-test in real FreeCAD 1.0.0 (18 of 18).

## Checked in this app (0.9.2-wip, Linux)

- The 13 library files replaced `app/imagetosketch` (`calsheet.py` is new; `__init__.py`, `service.py` and
  `pipeline.py` changed; the other nine are byte-identical to 0.9.1). The module's `tools/add_calibration_sheet.py`
  was run on this app's page (`app/webui.py`) and `app/image2step.py`, first on a copy, then on the repository: it
  found every piece of text it needed and its changes were the same both times. Run twice, the second time it said
  it was already done. It is a one-off migration, so, like `height_next_to_convert.py` before it, it is not kept in
  `tools/`; `tools/sync_imagetosketch.py` from the module is identical to the one already here.
- One change of this integration's own: the two downloads fall back to the built-in library when a drop-in
  tracer made before ImageToSketch 0.10 is loaded and has no calibration sheet, so the buttons always work.
- **A picture drawn by the test code** (the plate with a round hole lying in the middle of page 1, straight from
  above, 0.2 mm per pixel, no stand): the tab chose *calibration sheet* by itself ("40 frame markers"), the scale
  came out 0.2000 mm per pixel (0.001 % off), the plate and its hole were traced and no marker was, and a 5 mm STEP
  solid had 1.001 times the plate's volume. Kept as `tests/test_image.py`
  `test_calibration_sheet_sets_the_scale`.
- **Downloads** through the real server: the sheet as PDF (A4 and Letter), the stand as a well-formed binary STL.
- **The page in headless Chromium** at 1280 and 390 px wide: choosing *calibration sheet* shows its four fields,
  its two download buttons and the part's height and camera distance next to *Convert to STEP*; the token, marker
  sheet and other choices show and hide their fields as before; no sideways scroll at phone width; no errors in the
  console.

## 0.10.1: the empty-sheet photo

A second photo of the sheet without the part (the stand may stay) is lined up with the first through the frame's
markers (`pairdiff.py`); whatever is the same in both (a printer's banding, dust, the sheet itself) and the part's
shadow are turned into plain paper before the outline is proposed. ImageToSketch's simulated photos of a 140 mm
part with a long hard shadow: within 0.8 % / 3 % instead of 6.6 % / 7.8 %, and the shadow no longer gives a
borderline outline. Limit: a part as grey as its shadow with no texture of its own cannot be told from it.

Checked in this app (0.10.0-wip): the module's `tools/add_empty_sheet_photo.py` found every piece of the 0.9.2
page and added the button, the upload and the server side (run twice: "already done"). A drawn plate with a hard
grey shadow on page 1 and faint banding on both photos: 2 outlines either way, but with the empty-sheet photo the
shadow's outline is no longer in the borderline list (2 → 1), and a 5 mm solid is 1.001 × the expected volume. A
second photo that has gone is reported as an error. Not checked: a real photo pair.

## Not checked

- A real print, a real stand, a real photo; a stand in a test photo of this app (only ImageToSketch's simulated
  ones had one).
- Windows and macOS.
- OpenCV older than 4.7 (the markers need its ArUco module, which `opencv-python-headless` 4.7 and later include;
  without it the tab simply does not offer the sheet as a guess).
