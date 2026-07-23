# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Multi-timepoint acquisitions** (#2). `data/` directories with more than
  one `.tif` are concatenated along the T axis, one dask chunk per
  timepoint. Files are ordered by mtime (equivalent to zero-padded filename
  order for correctly-written acquisitions, matching `snouty-folder`'s
  convention). Verified end-to-end against a T=100, single-channel real
  acquisition.
- **Multi-position acquisitions** (#3). Files named `NNNNNN_pMMMMMM.tif`
  are grouped by position index into one scene per position. Composes with
  multi-timepoint: each scene is `(T, C, Z, Y, X)` where T is the number
  of files sharing that position index. Scene names follow a
  *suffix-only-when-needed* rule to preserve v0.1 backward compatibility:
  single-position acquisitions expose `[<acquisition-dir>]` (unchanged);
  multi-position acquisitions expose `[<acquisition-dir>__p000000, …]`
  (double underscore is the intentional boundary separator).
- **Per-scene stage XY coordinates.** When the parent GUI-session
  directory contains `XY_stage_position_list.txt` (one `[x_mm, y_mm]` row
  per position), each multi-position scene surfaces its coordinates as
  `attrs.zarrmony.stage.xy_mm` on the returned xarray. Absent file: attr
  omitted. Malformed file (unparseable line, wrong arity, non-numeric
  values, or fewer rows than positions): `SnoutyXYPositionListError`.

### Changed

- `_read_and_crop_plane` squeezes an optional singleton C axis before
  cropping, so tifffile-tagged `(Z, 1, Y, X)` volumes read the same as
  bare `(Z, Y, X)` volumes.

### Removed

- `SnoutyMultiTimepointUnsupportedError`. The multi-file case is now
  supported; the ``volumes_per_buffer > 1`` case has its own error (below).
- `SnoutyMultipositionUnsupportedError`. Multi-position acquisitions are
  now supported natively; the `_pNNNNNN.tif` pattern no longer raises.

### Guardrails

- New `SnoutyVolumesPerBufferUnsupportedError` for sidecars reporting
  ``volumes_per_buffer > 1`` (Snouty's hardware-limited time sampling
  packs multiple volumes into one `.tif`). The math
  ``size_t = volumes_per_buffer * len(data_files)`` is verified against a
  real fixture at the raw-TIFF level (frame count matches
  ``vpb * channels * slices_per_volume``), but every real ``vpb > 1``
  fixture we have also has multiple channels — so the composition is
  deferred until the multi-channel path (#4) lands and can be tested
  end-to-end.

## [0.2.0] — 2026-07-21

### Added

- **Opt-in `desheared` and `traditional` output modes** on `SnoutyReader`
  via a new `mode` kwarg (`"raw" | "desheared" | "traditional"`, default
  `"raw"` preserves v0.1 byte-for-byte).
  - `desheared` per-slice y-shifts each z-plane by
    `int(round(scan_step_size_px * z))` and pads Y by the maximum shift.
    Physical pixel sizes unchanged (deshear aligns axes; it does not
    change spacing).
  - `traditional` deshears then applies a scipy affine rotation of
    `arctan(scan_step_size_px / voxel_aspect_ratio)` about the X axis,
    swaps Y/Z, and flips. Z spacing becomes
    `sample_px_um * voxel_aspect_ratio`.
  - Ported from the CPU paths of `snouty_folder.SnoutyFolder`
    (`_per_slice_cpu_deshear`, `_affine_rotate`,
    `_load_desheared_dims`, `_load_traditional_dims`) in Austin
    Lefebvre's [`snouty-folder`](https://github.com/aelefebv/snouty-folder).
    cupy/GPU paths intentionally skipped.
- **`ZARRMONY_SNOUTY_MODE` env var** — the plugin's `open` shim reads it
  and forwards to the reader, so `ZARRMONY_SNOUTY_MODE=desheared zarrmony
  convert …` selects a mode from the CLI without code changes.
  Unrecognized values raise `SnoutyModeError`.
- `scipy>=1.13` added as a runtime dependency for the traditional-view
  affine rotate.

## [0.1.0] — 2026-07-14

### Added

- Initial release. `SnoutyReader` adapter satisfies zarrmony's `ReaderProtocol`
  and registers as `zarrmony-snouty` via the `zarrmony.readers` entry point.
- **Directory matcher** that fires on subdirectories whose name ends in
  `_ht_sols_snap` or `_ht_sols_acquire` and which contain sibling `data/` +
  `metadata/` subdirs with at least one `.tif` and one `.txt` file.
- **Metadata sidecar parser** for the vendor's `metadata/<name>.txt`
  key=value plaintext (`_metadata.py`), ported from
  `snouty_folder.SnoutyFolder._load_metadata` (see Austin Lefebvre's
  [`snouty-folder`](https://github.com/aelefebv/snouty-folder)).
  Uses `ast.literal_eval` instead of the reference's `eval` for safe
  tuple parsing.
- **Raw-skewed reader** (`SnoutyReader`) exposing a `(T=1, C=1, Z, Y, X)`
  dask-backed xarray. Physical pixel sizes are `(X=Y=sample_px_um,
  Z=scan_step_size_um)`. The top 8 rows of every Y slice — the PCO BCD
  timestamp strip — are cropped before the array reaches callers.
- **v0.1 scope guardrails.** Multi-position (`*_pNNNNNN.tif`),
  multi-timepoint (>1 data file, or `volumes_per_buffer > 1`), and
  multi-channel (`channels_per_slice` with >1 entry) acquisitions raise
  `NotImplementedError` subclasses with pointers at the v0.2 tracker.
- **Audit propagation.** The verbatim `.txt` sidecar is exposed via
  `SnoutyReader.metadata` so `zarrmony.convert` writes it to
  `OME/source/raw.snouty.txt`; the reader's `name`, `distribution`, and
  `source = "entry_point"` flow into the audit record.
- **Install-smoke test** confirms the plugin surfaces through
  `zarrmony.readers.plugin.list_plugins()` with the expected provenance.

### Known limitations

- Single position, single timepoint, single channel per conversion.
- Raw skewed output only — no deshear or rotation. Reference implementation
  in [`snouty-folder`](https://github.com/aelefebv/snouty-folder) will be
  ported in v0.2.
- Top-level `*_ht_sols_gui/` GUI-session directory is not yet a matchable
  input; users must convert one `*_ht_sols_*` subdir at a time. Tracked for
  v0.3.
