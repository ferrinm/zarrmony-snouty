# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **GPU `traditional` transform** (#8). New internal `_deshear_gpu` module,
  the cupy counterpart to `_deshear.traditional_zyx`, ported from the same
  source ([`snouty-folder`](https://github.com/aelefebv/snouty-folder),
  `SnoutyFolder._affine_rotate`). No reader wiring yet — that is #10.
  - `traditional` only, per ADR-0002 decision 2. The deshear stays on the
    CPU, so only the desheared array crosses the PCIe bus.
  - cupy is a **soft import**. The module loads on a CPU-only install and
    reports `cupy_available`. There is no `gpu` extra, because cupy wheels
    are pinned to a CUDA major version (`cupy-cuda12x` against CUDA 12.9 on
    the measured host) and the generic `cupy` sdist needs a local CUDA
    toolchain to build. Install the matching wheel yourself.
  - A module-level lock serializes device work (ADR-0002 decision 3). One
    call holds 4.17 GB on the device, and an unbounded 16-thread scheduler
    would demand 67 GB from a 23.46 GiB card. The CPU deshear runs outside
    the lock, because it allocates host memory rather than device memory.
  - `required_device_bytes` and `free_device_bytes` let #10 decide the
    engine before any pixel work starts.
  - **The GPU result is not byte-identical to the CPU one.** At the measured
    geometry 0.0036% of voxels differ, all on 29 lines of the 808,780 in the
    rotated grid. 28 are nearest-neighbour ties, where `scipy` and `cupy`
    pick opposite equidistant voxels and both answers are equally valid. The
    remaining line is a real loss: its source coordinate is exactly `0.0`,
    where scipy reads the first plane and cupy returns the fill value, so
    the GPU drops one line of voxels at the outermost non-empty edge plane.
    Measured on real hardware in #18.
  - Measured throughput is **2.55x the CPU's best**. ADR-0002 predicted the
    opposite. The CPU transform stops scaling at 8 threads and degrades
    above it, so a wider node does not close the gap.
  - New `SnoutyEngineError`, and a `gpu` pytest marker for the tests that
    need a device.
- **Bounded host memory for the transform modes** (#15). `desheared` and
  `traditional` now reserve their per-task footprint from a process-wide
  byte budget before each transform runs, so peak host memory no longer
  scales with `os.cpu_count()`. At a real acquisition geometry a
  `traditional` task holds 4.90 GB, which an unbounded 32-thread scheduler
  turns into 157 GB.
  - The budget is half of the smaller of the **cgroup memory limit** and
    total physical RAM. Reading the cgroup limit is what makes the bound
    correct inside a SLURM allocation — a 62 GB allocation on a 512 GB node
    budgets from 62 GB.
  - The footprint is derived from `_deshear.desheared_shape` and
    `_deshear.traditional_shape`, never hardcoded, so it tracks any change
    to the shape formulas.
  - A byte budget rather than a slot count: a session-level convert opens
    child readers whose geometries differ, and a slot count sized from one
    scene misbounds the others.
  - New `ZARRMONY_SNOUTY_HOST_MEMORY_BYTES` (absolute, `0` disables) and
    `ZARRMONY_SNOUTY_HOST_MEMORY_FRACTION` (default `0.5`) env vars. A bad
    value raises the new `SnoutyHostMemoryError`.
  - New `SnoutyReader.transform_footprint_bytes`, readable before compute.
  - `mode="raw"` is unaffected. It runs no transform, reserves nothing, and
    keeps the scheduler's full width.
  - The read and the transform are now **one fused dask task** for the
    non-`raw` modes. Split, the scheduler can materialize many input volumes
    before any transform reserves, so the input term would escape the bound.
- **Top-level GUI-session directory as multi-scene input** (#5). A new
  `SnoutySessionReader` fires on `*_ht_sols_gui/` directories and composes
  one child `SnoutyReader` per non-empty `_ht_sols_*` subdirectory. Its
  `scenes` list is the flat concatenation of every child's scenes
  (verbatim `<subdir>[__pNNNNNN]` names — no session-level prefix; subdir
  names already carry the disambiguating information). `set_scene(i)`
  resolves to the right child + per-child scene; `xarray_dask_data`,
  `physical_pixel_sizes`, `channel_names`, `metadata` delegate to the
  active child. Child readers are instantiated lazily — full metadata
  parsing only happens on first `set_scene()` into a given child.
- **`snouty-session` entry point.** The `zarrmony-snouty` distribution now
  registers **two** `ReaderPlugin` values under `zarrmony.readers`:
  `snouty` (subdir-level, unchanged) and `snouty-session` (session-level,
  new). Both appear in `zarrmony.readers.plugin.list_plugins()` after
  install.
- **`SnoutySubdirSkippedWarning`.** Subdirs that fail a cheap shallow
  validation (missing `data/`, missing `metadata/`, no `.tif` in `data/`,
  no `.txt` in `metadata/`) are dropped from `scenes` with one warning
  per skipped child. Reason tokens (`missing_data_dir`,
  `missing_metadata_dir`, `empty_data`, `no_metadata`) are machine-parseable.
  A session with zero surviving children raises `SnoutyDataError`.
- **`SnoutySessionLayoutWarning`.** When the session-level
  `XY_stage_position_list.txt` length does not match a multi-position
  child's position count, the session reader emits one warning per
  mismatched child and omits `attrs.zarrmony.stage.xy_mm` on that child's
  scenes. Matched-length children pass through the same per-position
  attr code path #3 introduced.
- **Mode env-var propagation across the session.**
  `ZARRMONY_SNOUTY_MODE=desheared zarrmony convert <session-dir>` deshears
  every child in the batch; unknown values still raise `SnoutyModeError`
  at construction time.
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
- **Multi-channel acquisitions** (#4). `channels_per_slice` with more than
  one entry now yields a `(T, C=N, Z, Y, X)` xarray with the vendor's
  channel labels along the `C` coord verbatim. On-disk TIFF layout is
  `(Z, C, Y, X)` (Z outermost, matching the swap in
  `snouty_folder.write_original_ome_tif`) and composes with the
  multi-timepoint and multi-position paths. Verified end-to-end against
  the `('LED', '488')` acquisitions in the exploratory session.

### Fixed

- **The T axis could come out in an arbitrary order** (#23). `SnoutyReader`
  ordered `data/*.tif` by `st_mtime` alone. On a filesystem with a coarse
  mtime granularity every file of one acquisition reports the *same*
  `st_mtime`, `sorted` is stable, so the order fell back to `os.scandir`
  order. That order is arbitrary. The converted store looked fine and its
  timepoints were in the wrong order, with no error and no warning.
  - Reachable on any filesystem. A copy made with `cp -r`, or with `rsync`
    without `-t`, drops the original mtimes the same way. `os.scandir` order
    is not name order anywhere: on APFS, six files created as `000000` to
    `000005` in ascending order list as `000000, 000001, 000003, 000002,
    000005, 000004`.
  - **The T axis now follows the timestamp the camera burns into the pixel
    data.** The PCO hardware writes a binary-coded-decimal frame counter and
    a microsecond-resolution capture time into the first 14 pixels of row 0
    of every 2D frame, before any software sees the frame. That makes it the
    only ordering key that survives a copy and a coarse filesystem clock. New
    internal `_pco_timestamp` module.
  - Verified against every acquisition on the internal share that the reader
    accepts: 115 acquisitions spanning 2023 to 2026, from ten operators, 111
    single-position and 4 multi-position. Every sampled frame decoded, every
    decoded time landed within 3.5 seconds of its own sidecar's `Date` and
    `Time`, and the frame-counter order matched the filename order every
    time.
  - The stamp is read from the first frame only, and from a 28-byte seek
    rather than a full page read, so ordering an acquisition costs one TIFF
    header parse per file.
  - Falls back to the zero-padded filename order, with a new
    `SnoutyTimestampWarning`, when a file carries no stamp this reader
    recognizes, when two files share a frame counter, or when the first
    file's stamp disagrees with its own sidecar by more than an hour. Unlike
    the `st_mtime` sort it replaces, the fallback is deterministic.
  - The reader trusts the stamp over the filename when the two disagree, and
    warns.
  - `parse_metadata_dir` picks the first sidecar **first by mtime, then by
    name**. The same tie made the pick of the geometry sidecar arbitrary.
  - `SnoutySessionReader` enumerates scene names in filename order. That sort
    only decides scene *names*, so the session reader stays lazy and the
    child reader pays for the authoritative time order when a caller opens
    the scene.
  - The synthetic test fixture now burns a real PCO stamp into row 0 of every
    frame, and its default `size_x` grows from 8 to 16 to hold it.

### Changed

- `_read_and_crop_plane` now always returns `(C, Z, Y, X)`: bare
  `(Z, Y, X)` and `(Z, 1, Y, X)` single-channel volumes get a C axis
  prepended; `(Z, C, Y, X)` multi-channel volumes get their leading Z↔C
  axes swapped. `xarray_dask_data` stops inserting a synthetic singleton
  C — the C dim comes from the read path.
- `SnoutyReader` now exposes a `dtype` property (always `np.dtype("uint16")`,
  matching the vendor's PCO output and the existing
  `da.from_delayed(..., dtype="uint16")` construction). Required by
  `zarrmony>=0.9`'s `_channels_for_scene` when computing the OME-NGFF
  display window; without it, `zarrmony convert` errored at the first
  scene. `SnoutySessionReader.dtype` delegates to the active child.

### Removed

- `SnoutyMultiTimepointUnsupportedError`. The multi-file case is now
  supported; the ``volumes_per_buffer > 1`` case has its own error (below).
- `SnoutyMultipositionUnsupportedError`. Multi-position acquisitions are
  now supported natively; the `_pNNNNNN.tif` pattern no longer raises.
- `SnoutyMultiChannelUnsupportedError`. `channels_per_slice` with more
  than one entry is now supported natively.

### Guardrails

- `SnoutyVolumesPerBufferUnsupportedError` still fires on sidecars
  reporting ``volumes_per_buffer > 1`` (Snouty's hardware-limited time
  sampling packs multiple volumes into one `.tif`). No real ``vpb > 1``
  fixture has been staged, so the buffer-frame layout inside a single
  `.tif` — expected to be
  ``(volumes_per_buffer, slices_per_volume, channels, Y, X)`` — remains
  unverified.

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
