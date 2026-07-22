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
A GUI-driven run, on disk as a directory whose name ends in `_ht_sols_gui/`. Contains one or more acquisitions plus the shared `XY_stage_position_list.txt` and `focus_piezo_position_list.txt`. Tracked as the v0.3 input shape.
_Avoid_: batch, GUI folder, parent dir.

**Acquisition**:
A single snap or continuous scan run, on disk as a subdirectory whose name ends in `_ht_sols_snap/` or `_ht_sols_acquire/`. Contains sibling `data/` and `metadata/` directories. This is v0.1's unit of input.
_Avoid_: subdir, run, capture, folder.

**Position**:
An XY stage location within an acquisition. On disk, positions are the `MMMMMM` in `NNNNNN_pMMMMMM.tif` filenames. Multi-position acquisitions expose one scene per position (see #3).
_Avoid_: field, site, well (well is reserved for HCS-plate #6), point.

**Timepoint**:
A single volumetric snapshot at one position. On disk, timepoints are the `NNNNNN` in `NNNNNN.tif` or `NNNNNN_pMMMMMM.tif`. Concatenated into the T axis by #2.
_Avoid_: frame, T-slice, volume (volume means the 3D array, not the axis element).

**Scene**:
The unit the reader exposes via `SnoutyReader.scenes`. Each scene is a `(T, C, Z, Y, X)` xarray. Named `<acquisition-dir>` for single-position acquisitions and `<acquisition-dir>__p<zero-padded-index>` for multi-position (double underscore is the boundary separator; the suffix only appears when needed to disambiguate).
_Avoid_: image, series, dataset.

### Vendor artefacts

**Sidecar**:
The vendor's `metadata/<name>.txt` key=value plaintext file, one `key: value` per line. Parsed by `_metadata.py`. Not OME-XML; a Snouty-specific format. Always refer to it as "the sidecar" in prose, not "the metadata" (ambiguous with OME-Zarr metadata) or "config".
_Avoid_: metadata file, config, params.

**Timestamp strip**:
The top 8 rows of every Y slice, reserved for a PCO camera binary-coded-decimal timestamp burned into pixel values. Cropped off before the array reaches xarray. Constant `TIMESTAMP_STRIP_PX = 8`.
_Avoid_: header, timestamp header, PCO strip.

**Scan step**:
The physical Y displacement of the scan mirror between successive Z slices during acquisition. Recorded in the sidecar as `scan_step_size_um` (physical) and `scan_step_size_px` (in sample-plane pixels). Determines the raw Z spacing and the deshear shift.
_Avoid_: Z step, slice spacing, stride.

**Voxel aspect ratio**:
Sidecar field `voxel_aspect_ratio` relating Z spacing to XY spacing in the desheared/rotated frame. Used to compute traditional-view Z spacing as `sample_px_um * voxel_aspect_ratio` and the traditional-view rotation angle.
_Avoid_: Z/XY ratio, anisotropy, pixel aspect.

### Output modes (v0.2 #1)

**Raw**:
The vendor's skewed `(Z, Y, X)` volume as read from the TIFF, with the timestamp strip cropped off. Z spacing is the scan step, not the orthogonal Z. v0.1 default and permanent backward-compat mode.
_Avoid_: skewed, as-acquired, native (vendor calls it "native" but that overloads with `DataNative`).

**Desheared**:
Per-slice Y shift by `int(round(scan_step_size_px * z))`, aligning axes. Same physical pixel spacing as raw. Output shape `(Z, Y + max_shift, X)`.
_Avoid_: shifted, unskewed, aligned.

**Traditional**:
Desheared plus an affine rotation of `arctan(scan_step_size_px / voxel_aspect_ratio)` around X, then a Y/Z swap and Z flip. Produces the top-down orthogonal view with Z spacing `sample_px_um * voxel_aspect_ratio`.
_Avoid_: orthogonal, rotated, top-down (use "traditional" to match the vendor's `_load_traditional_dims`).
