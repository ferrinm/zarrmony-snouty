# zarrmony-snouty

The Snouty (Andrew York / Austin Lefebvre single-objective light-sheet) reader plugin for zarrmony. This context glossary pins the vocabulary the codebase and issue tracker use — read it before writing code or issues so terms stay consistent across the plugin, the roadmap issues, and the vendor's own tooling.

## Language

### Instrument

**Snouty**:
Andrew York's family of single-objective light-sheet microscopes. Also the name of the vendor Python control code (`HT_SOLS_microscope/`) and the on-disk output layout this plugin reads.
_Avoid_: SOLS-scope, York scope, the microscope.

**SOLS**:
Single-Objective Light-Sheet — the imaging modality Snouty implements (55° tilted light sheet, scan-shear along Y). Use when talking about the geometry class; use *Snouty* for the specific implementation on disk.
_Avoid_: light sheet (too generic), SOPI, oblique plane microscopy.

### Input hierarchy (what the plugin points at)

**Session**:
A GUI-driven run, on disk as a directory whose name ends in `_ht_sols_gui/`. Contains one or more acquisitions plus the shared `XY_stage_position_list.txt` and `focus_piezo_position_list.txt`. v0.3 accepts a session directory as multi-scene input — one output store per non-empty child acquisition.
_Avoid_: batch, GUI folder, parent dir.

**Acquisition**:
A single snap or continuous scan run, on disk as a subdirectory whose name ends in `_ht_sols_snap/` or `_ht_sols_acquire/`. Contains sibling `data/` and `metadata/` directories. This is v0.1's unit of input.
_Avoid_: subdir, run, capture, folder.

**Position**:
An XY stage location in a **flat** acquisition, addressed by ordinal. On disk, positions are the `MMMMMM` in `NNNNNN_pMMMMMM.tif` filenames. Multi-position acquisitions expose one scene per position (see #3). On a *Plate*, the same physical idea is a *Field* inside a *Well*, addressed by coordinate instead of by ordinal.
_Avoid_: site, point, well, field (a plate addresses its stage locations as *Well* plus *Field*; a position is the flat-shape term only).

**Plate**:
A multiwell carrier, and the input shape it produces: one acquisition directory whose `data/` filenames carry well coordinates. Produced by an operator-edited python script, not by the GUI, so the directory name is free text and carries no reliable plate marker. The reader identifies a plate from its filenames (see #6).
_Avoid_: multiwell, plate map, screen, HCS (HCS names the OME-NGFF output layout, not the input).

**Well**:
One addressable chamber on a *Plate*, named by a row label and a column label. Two filename grammars encode it. `000000_A01r00c00.tif` names the well `A01` — a row letter and a 1-based column. `000000_r00c00.tif` names the well by a 0-based row index and a 0-based column index, and holds exactly one *Field*.
_Avoid_: site, position, spot.

**Field**:
One imaged XY location inside a *Well*. The first grammar above records a field as the `r<NN>c<NN>` suffix after the well label. The second grammar records one field per well and gives it no label. Each field becomes a separate OME-NGFF image, never an extra array axis.
_Avoid_: tile (the vendor says "tile", which promises a stitched mosaic; this reader does not stitch), FOV, site, subposition.

**Timepoint**:
A single volumetric snapshot at one position. On disk, timepoints are the `NNNNNN` in `NNNNNN.tif` or `NNNNNN_pMMMMMM.tif`. Concatenated into the T axis by #2.
_Avoid_: frame, T-slice, volume (volume means the 3D array, not the axis element).

**Scene**:
The unit the reader exposes via `SnoutyReader.scenes`. Each scene is a `(T, C, Z, Y, X)` xarray. Named `<acquisition-dir>` for single-position acquisitions and `<acquisition-dir>__p<zero-padded-index>` for multi-position (double underscore is the boundary separator; the suffix only appears when needed to disambiguate). On a *Plate* one scene is one *Field*, named `<acquisition-dir>__<canonical-well><field-token>` and always suffixed: `<dir>__A01r00c00` under the first grammar, `<dir>__A01` under the second.
_Avoid_: image, series, dataset.

**Plate format**:
A standard well count, and the row-by-column grid it fixes: 6 is 2x3, 96 is 8x12, 384 is 16x24. Nothing on disk records which one an acquisition used, so the reader infers it by snapping the observed well extent up to the smallest format that contains it (ADR-0003). The `plate_format` constructor keyword overrides the inference and takes the well count, not the grid.
_Avoid_: plate size, plate type, well count (the count alone names the format, but *plate format* names the decision).

### Vendor artefacts

**Sidecar**:
The vendor's `metadata/<name>.txt` key=value plaintext file, one `key: value` per line. Parsed by `_metadata.py`. Not OME-XML; a Snouty-specific format. Always refer to it as "the sidecar" in prose, not "the metadata" (ambiguous with OME-Zarr metadata) or "config".
_Avoid_: metadata file, config, params.

**Timestamp strip**:
The top 8 rows of every Y slice, reserved for a PCO camera binary-coded-decimal timestamp burned into pixel values. Cropped off before the array reaches xarray. Constant `TIMESTAMP_STRIP_PX = 8`.
_Avoid_: header, timestamp header, PCO strip.

**Burned-in stamp**:
The decoded contents of the first 14 pixels of row 0 of the timestamp strip: a camera frame counter and a capture time to the microsecond. The camera hardware writes it before any software sees the frame, so it is the source of truth for acquisition time and it orders the T axis (#23). Decoded by `_pco_timestamp.py`. Distinguish it from the *timestamp strip*, which is the 8-row region that gets cropped.
_Avoid_: PCO timestamp (ambiguous with the strip), BCD, frame time.

**Scan step**:
The physical Y displacement of the scan mirror between successive Z slices during acquisition. Recorded in the sidecar as `scan_step_size_um` (physical) and `scan_step_size_px` (in sample-plane pixels). Determines the raw Z spacing and the deshear shift.
_Avoid_: Z step, slice spacing, stride.

**Voxel aspect ratio**:
Sidecar field `voxel_aspect_ratio` relating Z spacing to XY spacing in the desheared/rotated frame. Used to compute traditional-view Z spacing as `sample_px_um * voxel_aspect_ratio` and the traditional-view rotation angle.
_Avoid_: Z/XY ratio, anisotropy, pixel aspect.

### Output modes (v0.2 #1)

**Raw**:
The vendor's skewed `(Z, Y, X)` volume as read from the TIFF, with the timestamp strip cropped off. Z spacing is the scan step, not the orthogonal Z. Default through v0.2, and a permanent backward-compat mode.
_Avoid_: skewed, as-acquired, native (vendor calls it "native" but that overloads with `DataNative`).

**Desheared**:
Per-slice Y shift by `int(round(scan_step_size_px * z))`, aligning axes. Same physical pixel spacing as raw. Output shape `(Z, Y + max_shift, X)`. **The default since v0.3** (#11): it is lossless, it costs 0.15 s per timepoint, and the padding compresses to nothing.
_Avoid_: shifted, unskewed, aligned, deskewed.

**Traditional**:
Desheared plus an affine rotation of `arctan(scan_step_size_px / voxel_aspect_ratio)` around X, then a Y/Z swap and Z flip. Produces the top-down orthogonal view with Z spacing `sample_px_um * voxel_aspect_ratio`.
_Avoid_: orthogonal, rotated, top-down (use "traditional" to match the vendor's `_load_traditional_dims`), deskewed.

**Deskew**:
Not a term in this context. The vendor's `_affine_rotate` docstring calls the
whole shift-plus-rotate pipeline a "deskew", which collapses two modes with very
different costs into one word. The shift step is *desheared*. The rotate step is
*traditional*.
_Avoid_: deskew, deskewed, deskewing — say *desheared* or *traditional*.

### Execution (v0.3 #7)

**Engine**:
The compute device a transform runs on — `cpu` or `gpu`. Chosen once when a reader opens an acquisition, never per timepoint. Only *traditional* has a GPU path.
_Avoid_: backend, device, accelerator, mode (mode is the output geometry, engine is where it is computed).

**Auto**:
The default engine setting. Means "use the GPU if this host has one and the volume fits on it, otherwise the CPU". Not a third engine — it resolves to `cpu` or `gpu` before any pixel work starts.
_Avoid_: default, best, hybrid.
