# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Internal

- **The sdist ships an allow-list, so untracked state cannot reach a
  distribution** (#58). `uv build` from a working tree wrote `.claude/` into
  the sdist: `settings.local.json` and three complete copies of the
  repository under `.claude/worktrees/`. hatchling keeps every file that a
  VCS does not ignore. `.claude/` is untracked and is not in `.gitignore`,
  and the old deny-list did not name it.
  - PyPI was never affected. `.github/workflows/release.yml` builds from a
    fresh `actions/checkout`, and the published 0.3.2 sdist holds no
    `.claude` member. The content of the published sdist does not change.
  - `[tool.hatch.build.targets.sdist]` now carries `include` instead of
    `exclude`. A new untracked directory is out by default.
  - Every pattern carries a leading `/`. hatchling reads them as gitignore
    patterns, and an unanchored `src` matches a directory of that name at
    any depth. Without the anchor, the worktree copies under `.claude/`
    still shipped.
  - `tests/test_sdist_contents.py` builds an sdist from a copy of the
    tracked tree with `.claude/` and `.envrc` planted back in, then pins the
    member list. The copy keeps the answer the same on a CI host, which has
    no `.claude/`.
  - `hatchling` joins the `dev` extra. The test builds in process, so it
    needs no subprocess and no network. The build backend does not change.
- **CI runs the internal-path checker, and the checker sees two more URL
  shapes** (#59). Either gap let a private-repo link reach a public
  repository.
  - `.github/workflows/ci.yml` gains a `No internal paths` step that runs
    `scripts/check_no_internal_paths.py` over `git ls-files`. Before this,
    the checker ran as a pre-commit hook only. A contributor who clones and
    pushes without `pre-commit install` gets no hook. That contributor can
    land an internal path, and CI stays green.
  - The two GitHub rules match the host more loosely and allow extra path
    segments before the name. `raw.githubusercontent.com/<owner>/zarrmony`
    and `api.github.com/repos/<owner>/zarrmony` passed, because the rules
    demanded the literal `github.com` followed by one segment. The rule for
    the internal org had the same gap. This change fixes both rules.
  - The skip list for the three self-describing files moves from
    `.pre-commit-config.yaml` into the checker, as `SELF_DESCRIBING`. The
    hook and the CI step must agree about it.
  - `tests/test_check_no_internal_paths.py` gains the new URL shapes, the
    matching negative cases for this public repository, and a test that
    scans every tracked file.

## [0.3.2] — 2026-10-08

Point release for #42. An unsupported `--reader-kwarg` raised an uncaught
`TypeError` on 0.3.1 and wrote no store. It now fails with one sentence.

The release also raises the `zarrmony` floor to 0.15.0, fixes two tests that
only failed on a GPU host (#43), and adds two runbook sections for a long
convert on a GPU node (#47, #52).

Pixel data is unchanged. A store written by this release matches one written
by 0.3.1 byte for byte, apart from the conversion timestamps and the output
path in the audit. No reconversion is needed.

### Fixed

- **An unsupported `--reader-kwarg` now fails with one sentence, not a stack
  trace** (#42). zarrmony hands `--reader-kwarg KEY=VALUE` straight to the
  winning plugin's `open`. All three entry points here took a path and
  nothing else, so any reader kwarg raised a `TypeError` that the zarrmony
  CLI does not catch. The user saw a traceback and got no store.
  - The three entry points now accept reader kwargs and reject every one of
    them with the new `SnoutyReaderKwargError`. The message names each
    rejected kwarg and points at the `ZARRMONY_SNOUTY_MODE` and
    `ZARRMONY_SNOUTY_ENGINE` environment variables.
  - The error is a `zarrmony.errors.ReaderKwargError`, which the zarrmony CLI
    converts into a one-line message. It is also a `SnoutyError`, so a
    library caller that catches the package base error still catches it.
  - Rejection happens before the reader is constructed. No directory is
    scanned, and no file under `data/` is opened.
  - `--reader-kwarg path=...` is rejected the same way. zarrmony passes the
    input positionally, so a named `path` used to bind twice and raise
    `got multiple values for argument 'path'`. The three `open` callables
    take `path` positional-only now.
  - A convert that passes no reader kwarg is unchanged. A store written by
    this release matches one written by 0.3.1 byte for byte, apart from the
    conversion timestamps and the output path in the audit.

### Changed

- **The `zarrmony` floor moves from 0.9.0 to 0.15.0** (#42). 0.15.0 added
  `zarrmony.errors.ReaderKwargError`, which this package now imports at
  module scope. Nothing is lost below that floor: `reader_kwargs` forwarding
  arrived in zarrmony 0.13.0, so 0.9.0 through 0.12.0 could never reach a
  plugin `open` with a reader kwarg at all.

### Documentation

- **The GPU-node runbook now covers a node whose device is dead** (#47, from
  the field report in #44). The "Running on a GPU node" section of
  `README.md` gave a submit script with `ZARRMONY_SNOUTY_ENGINE=auto` and
  stopped there. Nothing told the user how to tell a GPU convert from a
  silent CPU fallback. No source changed.
  - The section now gives a device check that calls `getDeviceCount()`
    inside the allocation, before a long job. It states that `nvidia-smi` is
    not a substitute, because `nvidia-smi` passes on a node where every CUDA
    call fails.
  - It warns that `auto` never treats a dead device as an error, and it asks
    the user to read the recorded engine before they trust the timing. The
    text points at the existing `engine_used` attribute and at the existing
    `zarrmony_snouty` audit block. No new field.

- **The GPU-node runbook now sizes a long convert** (#52, from the field
  report in #44). `README.md` said what to ask for in cores, memory and
  device, and nothing about wall time. Two cluster jobs in #44 died on a time
  limit because the plan sized the work from the input bytes. No source
  changed.
  - A new "Sizing a long convert" subsection gives the rule: ask for wall time
    by scene count, never by input bytes. It carries two measured plate rates
    on one TITAN RTX, 0.54 and 0.45 scenes per minute, and the worked example
    that a 3456-scene plate needs 125 to 135 hours.
  - The subsection states that a plate convert is all-or-nothing, because
    zarrmony writes the plate audit after the last field and `convert` has no
    resume flag. It states that a flat or session convert keeps every scene
    that finished, because that path writes each audit inside the scene loop.
  - A matching limitation entry records the all-or-nothing plate convert.

### Internal

- **The engine tests now give the same answer on every host** (#43, from the
  field report in #44). Two tests passed on a CPU-only host and failed on a
  GPU host. The product was right in both cases, so both failures were test
  defects. No file under `src/` changed.
  - The `fake_device` fixture moves to `tests/conftest.py`, because the
    engine tests and the plate tests both need it. It fixes all four
    host-dependent leaves of `_deshear_gpu` now: `cupy_available`,
    `free_device_bytes`, `cupy_version` and `cuda_runtime_version`. It fixed
    the first two only, so the real versions leaked into the audit on a GPU
    host.
  - `test_audit_records_a_fallback_and_the_reason_for_it` runs twice. The
    second parameter makes the leaves answer the way a GPU host answers, and
    the expected audit does not change.
  - `test_the_cpu_engines_run` drops `auto`, which resolves to the GPU on a
    host with a card. `auto` gets its own plate test, and that test fixes
    the device leaves rather than trusting the host.
- **The `CONTEXT.md` glossary separates the two senses of "device"** (#43).
  The Engine entry banned "device" as a synonym and then used the word in
  its own definition. Engine now reads "where a transform computes", and a
  new Device entry defines the hardware sense.
- **`scripts/check_no_internal_paths.py` blocks a private sibling repository
  link in both owner spellings** (#48). The blocklist named one owner only,
  so the other spelling of the same private URL passed.

### Known limitations

- **An interrupted plate convert loses every field it wrote.** zarrmony
  writes the plate audit after the last field, and `zarrmony convert` has no
  resume flag. A plate that runs for days therefore has no safe interruption
  point. Nothing in this repository can change that. The missing resume is
  tracked upstream. A flat or session convert is unaffected.
- zarrmony prints a `TileAlignmentWarning` on every Snouty convert that
  advises `--reader-kwarg tile_size=...`. These readers cannot honour it.
  The hint is not plugin-aware, and nothing in this repository can suppress
  it. Lateral tile control was considered and refused (#45). The warning is
  tracked upstream.

## [0.3.1] — 2026-10-07

Point release for #39. Every convert on 0.3.0 warned once per scene and wrote
that warning into the store. Upgrade from 0.3.0.

The two releases carry the same reader behavior otherwise. Pixel data is
unchanged, and a store written by 0.3.0 needs no reconversion unless you want
the two audit fields that 0.3.0 dropped.

### Added

- **Every Snouty reader now exposes `ome_metadata`** (#39). zarrmony 0.18.1
  reads this surface once per scene. The property returns an
  `ome_types.model.OME` that holds exactly one `Image`, for the active scene.
  - The `Pixels` sizes match the array the writer writes, in all three modes.
    Z, Y and X come from the output shape, never from the sidecar, because
    the default `desheared` mode changes the shape. T is the number of files
    in the scene, never the sidecar `volumes_per_buffer`, which the scope
    validator forces to 1.
  - `physical_size_x/y/z` come from `physical_pixel_sizes`, so the
    `traditional` Z spacing stays correct.
  - `acquisition_date` comes from the sidecar `Date` and `Time`.
  - `objective` and `instruments` stay unset. A Snouty sidecar records no
    objective.
- `ome-types` is now a direct dependency. It was transitive through zarrmony.
- `tests/test_flat_convert.py` is new (#39). No test called `zarrmony.convert`
  on a flat acquisition or on a session before, which is why #39 shipped.
  `tests/test_ome_metadata.py` is new too. It pins the `Pixels` sizes and the
  physical sizes against the written array, in all three modes.

### Fixed

- **A convert no longer warns once per scene, and no longer writes that
  warning into the store** (#39). Before this release every scene of every
  convert raised `ExtractorWarning` and recorded one `metadata_warnings`
  entry, because no reader defined `ome_metadata`. Two audit fields were
  missing as a result: `per_scene[i].channels` and
  `per_scene[i].acquisition.date`. Both are present now.

### Changed

- The test suite turns `zarrmony.errors.ExtractorWarning` into an error
  (#39). The gate names that one category. This plugin raises four warnings
  of its own that tests assert on, so a blanket `UserWarning` gate would
  couple the suite to unrelated behavior.

## [0.3.0] — 2026-10-07

### BREAKING

- **No matcher tests the directory name any more** (#34). Detection reads the
  contents of a directory instead. An acquisition is any directory with
  `data/` and `metadata/`, a `.tif` under `data/`, and a `metadata/*.txt`
  carrying a quorum of the Snouty key set. A session is any directory holding
  such a child.
  - **This widens what converts, and it does not narrow it.** Every directory
    that matched before still matches. A survey of the reference share found
    195 of 321 real acquisitions (61%) that the old name test rejected, plus
    39 session directories. Those now convert.
  - The vendor GUI writes `_ht_sols_snap`, `_ht_sols_acquire` and
    `_ht_sols_gui`, but an operator names every scripted run, so the name was
    never reliable evidence. ADR-0003 reached this conclusion for plates.
    It now covers every input kind.
  - **A directory that is not Snouty can now be claimed if it holds a
    `metadata/*.txt` using six or more of the nine Snouty key names.** This is
    the cost of dropping the name test. No such collision is known.
  - **A session now claims an operator folder that groups unrelated runs.**
    Conversion emits one store per child, which is the intended output.
  - `SnoutySubdirSkippedWarning` gains the reason token
    `not_a_snouty_sidecar`.
  - A matcher now reads one sidecar of about 1 KB. The older rule that a
    matcher parses no metadata is withdrawn.

### Fixed

- **`zarrmony inspect` and `zarrmony convert` no longer fail with
  `UnsupportedFileFormatError` on a readable Snouty acquisition** (#34). The
  name test rejected the input, zarrmony fell through to the default bioio
  reader, and bioio cannot read a directory. The error named nothing useful.
- **A damaged sidecar now reports the key that is absent.** The matcher needs
  a quorum of the key set, not all of it, so an acquisition missing one key
  still reaches `SnoutyReader` and raises `SnoutyMetadataError` naming that
  key.
- **An unreadable `data/` or `metadata/` directory no longer propagates
  `OSError` out of a matcher.** One unreadable child cannot take a whole
  session down.

- **The default `mode` is now `desheared`, not `raw`** (#11). The same
  command writes a different array: Y grows from `Y` to `Y + max_shift`, and
  each z-plane sits at its aligned Y offset. Z spacing does not change.
  - **To keep the v0.2 output, name the mode.** Pass `mode="raw"` to
    `SnoutyReader` or `SnoutySessionReader`, or set
    `ZARRMONY_SNOUTY_MODE=raw` for the plugin `open` shims and the
    `zarrmony convert` CLI.
  - `raw` is a permanent backward-compatible mode. It is not deprecated.
  - Three reasons for the flip. Deshear is lossless, because it writes every
    input voxel exactly once at an integer offset, so `raw` is recoverable
    from a desheared store given the sidecar. It costs 0.15 s per timepoint
    on the CPU. The padding is zeros, and zeros compress to nothing: at
    zstd level 3, signal costs 0.941 bytes per voxel and zero padding costs
    0.000061. An end-to-end convert of a real 48-plane snap confirms it. The
    default array holds 2.71x the voxels of the raw array and occupies
    15,048,616 bytes against 15,061,674, a ratio of 1.00x.
  - **A store can still grow, for an unrelated reason.** A taller array can
    cross zarrmony's threshold for an extra pyramid level. In that convert,
    the raw store stopped at level 0 and the desheared store added a level 1
    of 3.5 MB, which made the whole store 1.23x. Downsampling blends padding
    with signal, so the extra level does not compress like the padding does.
  - `traditional` is not the default, because it resamples with
    nearest-neighbour and does not invert.
  - One `DEFAULT_MODE` constant in `zarrmony_snouty.adapter` now backs both
    readers and both plugin shims, so the four defaults cannot drift apart.

### Added

- **HCS-plate output for multiwell-plate acquisitions** (#6). A third
  reader, `SnoutyPlateReader`, converts a script-driven plate run into one
  OME-NGFF HCS plate store: `<plate>/<row>/<column>/<field>`. One scene per
  imaged field, and every field stays `(T, C, Z, Y, X)`. See
  [ADR-0003](docs/adr/0003-snouty-plate-detection-and-grid-inference.md).
  - **The matcher tests the contents and never the directory name.** That
    name is a literal in a script the operator edits. Six of the ten plate
    acquisitions surveyed end in `ht_sols_acquisition_multiwell_plate` plus
    free text, two carry no plate token at all, and none uses the name the
    vendor template builds. `match_plate` returns 200, above the 100 the
    other two matchers return, so a plate still converts as a plate when an
    operator gives it a GUI suffix.
  - **Two filename grammars, and both patterns are anchored.** Grammar A is
    the vendor generator, `000000_A01r00c00.tif`, where `r00c00` is the
    field inside well `A01`. Grammar B is a hand-rolled operator loop,
    `000000_r00c00.tif`, where `r00c00` is the **well** and the well holds
    one field. The two tokens are byte-identical, so an unanchored search
    reads a 384-well grammar-B plate as 384 fields of one well.
  - **The grid is inferred.** Nothing on disk records the size of the
    physical plate, and both grammars record absolute well coordinates, so
    the observed extent is a lower bound. The reader snaps that extent up to
    the smallest standard format that contains it (6, 12, 24, 48, 96, 384,
    or 1536 wells). Unimaged wells get no well group, and their row and
    column names stay in the plate attributes.
  - **Snapping can under-report a plate.** A 384-well plate imaged only in
    `A1` to `H12` snaps to 96. `plate_format=<well count>` overrides the
    lookup, and it is why that keyword exists. A value outside the table,
    or one too small for the wells on disk, raises the new
    `SnoutyPlateFormatError`. An extent that no standard format contains
    emits the new `SnoutyPlateFormatWarning` and falls back to the observed
    bounding box.
  - **The embedded acquisition script is never read.** Every plate
    directory holds a copy, and it declares `total_rows` and `total_cols`
    directly. One acquisition on the share proves the script and the data
    disagree about which wells were imaged. The filenames record the truth.
  - **Strict parsing.** A `data/` that mixes the two grammars raises
    `SnoutyDataError`, and so does a single `.tif` that matches neither.
    There is no skip-with-warning and no fallback to the flat reader.
  - Scenes are named `<acquisition-dir>__<canonical-well><field-token>` and
    sorted by well, then by field inside the well. Both grammars snake
    across the plate, and the snake carries no meaning.
  - New `snouty-plate` entry point, so `zarrmony convert <plate-dir> <out>`
    needs no flag. `mode` and `engine` behave exactly as they do on the
    other two readers, through the same two environment variables.
  - `SnoutySessionReader` learns nothing about plates. A plate is never a
    child of a GUI session.
  - **Opening a 384-well plate costs one directory listing and one sidecar
    read.** Two reads that a flat acquisition can afford do not scale to
    3456 files on a network share, and both were measured at about 70 ms per
    file there:
    - The reader reads the sidecar of the first data file by name.
      `parse_metadata_dir` picks the oldest `.txt`, which costs one `stat()`
      per sidecar: 246 s on the real 384-well acquisition. A plate whose
      first sidecar is missing still falls back to the directory scan.
    - The reader reads no burned-in stamp when every field holds one
      timepoint, because with one file per field any order is the same
      order. A multi-timepoint plate still orders its T axis by the stamp.
    - Measured on the real 384-well acquisition: 246 s to build the layout
      before, 0.2 s after.
  - **Every plate acquisition found on the share has one timepoint.** The
    multi-timepoint plate path is covered synthetically only.
- **`engine` selector on both readers** (#10). `SnoutyReader` and
  `SnoutySessionReader` take `engine="auto" | "cpu" | "gpu"`, which wires the
  `_deshear_gpu` module from #8 into the reader for the first time. *Mode* is
  the output geometry. *Engine* is where it computes.
  - `auto` is the default, and it picks the GPU only when the mode, the
    host, and the card all allow it. A CPU-only install keeps writing exactly
    the pixels v0.2 wrote.
  - The difference between `auto` and an explicit `gpu` is consent. `auto`
    never fails over hardware. An explicit `gpu` raises `SnoutyEngineError`
    on a wrong environment (no cupy, or no device), and falls back silently
    to the CPU on a capacity shortfall, because one oversized scene is a
    poor reason to abandon a long convert.
  - `traditional` only (ADR-0002, decision 2). `engine="gpu"` on `raw` or
    `desheared` is not an error. The reader runs on the CPU and reports
    `mode '<mode>' has no GPU path`.
  - Resolution happens once, in the constructor, before any pixel work.
    New `reader.engine_used` and `reader.engine_fallback_reason`.
  - **Sessions resolve all-or-nothing under `auto`.** One engine covers every
    child, sized from the largest child, so a single session never mixes
    engines. An explicit `engine="gpu"` resolves per child instead.
  - New `ZARRMONY_SNOUTY_ENGINE` env var on both plugin `open` shims, with
    the same contract as `ZARRMONY_SNOUTY_MODE`. An unknown value raises
    `SnoutyEngineError`.
  - `acquisition_audit` now carries a `zarrmony_snouty` block with
    `engine_used`, `engine_fallback_reason`, `cupy_version` and
    `cuda_runtime_version`, so a store records which engine wrote it. The
    two engines do not write identical pixels (#18), so this is provenance
    and not decoration. `SnoutySessionReader` gained `acquisition_audit`
    delegation, which also fixes a pre-existing gap: session converts
    previously dropped the static `imaging_method` and `microscope` fields.
  - A child whose sidecar does not parse no longer takes down a session at
    construction. Device sizing reads every child's geometry, and a child it
    cannot measure drops out of the sizing and keeps its lazy error.
- **GPU `traditional` transform** (#8). New internal `_deshear_gpu` module,
  the cupy counterpart to `_deshear.traditional_zyx`, ported from the same
  source ([`snouty-folder`](https://github.com/aelefebv/snouty-folder),
  `SnoutyFolder._affine_rotate`). Wired into the reader by #10, above.
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

- **The dependency floor is now `zarrmony>=0.9.0`** (#6). The old `>=0.3.0`
  pin predates the plate API (`PlateLayout`, `PlateField`, `Acquisition`).
- **The per-scene pixel path moved to a new internal `_pixels` module**
  (#6). The read, the timestamp-strip crop, the time ordering, the
  transform, and the pixel sizes now live on one `ScenePixels` helper that
  `SnoutyReader` and `SnoutyPlateReader` both call. The two readers differ
  only in how they group files into scenes. No behavior changed, and
  `zarrmony_snouty.adapter` re-exports every name it exported before.
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
