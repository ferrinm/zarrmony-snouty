# zarrmony-snouty

Snouty (single-objective light-sheet, "SOLS") reader plugin for
[zarrmony](https://github.com/ferrinm/zarrmony). Detects a single Snouty GUI
acquisition subdirectory (`*_ht_sols_snap` or `*_ht_sols_acquire`) or a
top-level GUI-session directory (`*_ht_sols_gui`) and converts the raw skewed
volumes it contains to OME-NGFF 0.5:

```bash
zarrmony convert /path/to/<ts>_000_ht_sols_snap ./out
zarrmony convert /path/to/<ts>_ht_sols_gui ./out   # one output store per subdir
```

## Install

```bash
pip install zarrmony-snouty
```

_Not yet on PyPI._ Until the first release, install from source:

```bash
pip install git+https://github.com/ferrinm/zarrmony-snouty
```

This pulls `zarrmony` from PyPI as a transitive dependency.

## Verify the plugin registered

The distribution ships **two** `ReaderPlugin` values under `zarrmony.readers`:
`zarrmony-snouty` (subdir-level, v0.1) and `zarrmony-snouty-session`
(session-level, v0.3). Both should appear after `pip install`:

```python
from zarrmony.readers.plugin import list_plugins

print([p.name for p in list_plugins()])
# -> [..., 'zarrmony-snouty', 'zarrmony-snouty-session']
```

For a clean-venv install smoke test (the same shape CI runs):

```bash
uv venv .venv-smoke
source .venv-smoke/bin/activate
uv pip install .
python -c "from zarrmony.readers.plugin import list_plugins; \
           assert 'zarrmony-snouty' in {p.name for p in list_plugins()}"
```

The same assertion runs in CI as `tests/test_install_smoke.py`.

## Use

### Single acquisition subdirectory

```bash
zarrmony inspect /path/to/2026-07-14_10-15-35_000_ht_sols_snap   # dims, channels, pixel sizes
zarrmony convert /path/to/2026-07-14_10-15-35_000_ht_sols_snap ./out
```

Output is a single `<dir-basename>.ome.zarr` store with dims `(T, C, Z, Y, X)`,
physical pixel sizes `(X=sample_px_um, Y=sample_px_um, Z=scan_step_size_um)`
copied from the vendor's `metadata/<name>.txt` sidecar, and channel names from
`channels_per_slice`. The verbatim sidecar text is preserved in the audit at
`<store>/OME/source/raw.snouty.txt`.

### Whole GUI-session directory

Point `zarrmony convert` at the parent `*_ht_sols_gui/` directory to fan out
to one output store per non-empty `_ht_sols_*` subdir in a single command:

```bash
zarrmony convert /path/to/2026-07-14_10-12-21_ht_sols_gui ./out
```

Every child subdir produces one `<subdir-name>.ome.zarr` (or
`<subdir-name>__pNNNNNN.ome.zarr` for multi-position subdirs). Empty or
malformed subdirs are skipped at load time with a `SnoutySubdirSkippedWarning`
naming the subdir path and a machine-parseable reason token
(`missing_data_dir`, `missing_metadata_dir`, `empty_data`, `no_metadata`).
A session with zero surviving subdirs raises `SnoutyDataError`.

If the session dir contains `XY_stage_position_list.txt`, each multi-position
subdir surfaces its stage coordinates as `attrs.zarrmony.stage.xy_mm` on the
returned xarray. Mismatched list lengths emit a `SnoutySessionLayoutWarning`
per affected subdir and omit the attrs for that subdir.

The default Z spacing is the **raw scan step** — the physical distance the
scan mirror moves between successive slices — not the de-sheared/rotated
orthogonal Z. To get orthogonal geometry, pick a non-default output mode
(see below).

### Output modes

`SnoutyReader` takes a `mode` selector with three values:

- `raw` (default) — the vendor's skewed `(Z, Y, X)` volume, only the PCO
  timestamp strip cropped. Z spacing is `scan_step_size_um`. This preserves
  v0.1 output byte-for-byte.
- `desheared` — each z-plane is shifted along Y by
  `int(round(scan_step_size_px * z))` so orthogonal features line up. Output
  shape is `(T, C, Z, Y + max_shift, X)`. Physical pixel sizes are
  unchanged (deshear only aligns axes; it does not change spacing).
- `traditional` — deshear followed by an affine rotation of
  `arctan(scan_step_size_px / voxel_aspect_ratio)` about the X axis, then a
  Y/Z swap and Z flip. Output is a top-down orthogonal view; Z spacing
  becomes `sample_px_um * voxel_aspect_ratio`.

The Python API takes the mode directly:

```python
from zarrmony_snouty import SnoutyReader

reader = SnoutyReader("/path/to/…_ht_sols_snap", mode="desheared")
```

For CLI use, opt in via the `ZARRMONY_SNOUTY_MODE` env var (the plugin's
`open` callable only accepts a path):

```bash
ZARRMONY_SNOUTY_MODE=traditional zarrmony convert /path/to/…_ht_sols_snap ./out
```

Unrecognized values raise a `SnoutyModeError`. Deshear and traditional-view
are ported from Austin Lefebvre's
[`snouty-folder`](https://github.com/aelefebv/snouty-folder). Mode controls
the output geometry only. See **Engine** below for where it computes.

### Engine: where the transform computes

*Mode* is the output geometry. *Engine* is where it computes. The two are
separate kwargs and separate decisions.

`engine` takes three values:

- `auto` (default) — use the GPU when the mode, the host, and the card all
  allow it. Otherwise use the CPU and record the reason.
- `cpu` — always the CPU. This is the v0.2 behaviour, unchanged.
- `gpu` — use the GPU, and raise when the environment cannot.

Only `traditional` has a GPU path, per
[ADR-0002](docs/adr/0002-gpu-deshear-execution-model.md). `raw` runs no
transform at all. A GPU `desheared` costs more in PCIe upload than the whole
CPU deshear costs. `engine="gpu"` on either of those modes is not an error.
The reader runs on the CPU and reports why.

```python
from zarrmony_snouty import SnoutyReader

reader = SnoutyReader("/path/to/…_ht_sols_snap", mode="traditional", engine="gpu")
reader.engine_used  # "gpu"
reader.engine_fallback_reason  # None
```

For CLI use, set `ZARRMONY_SNOUTY_ENGINE`:

```bash
ZARRMONY_SNOUTY_MODE=traditional ZARRMONY_SNOUTY_ENGINE=gpu \
  zarrmony convert /path/to/…_ht_sols_snap ./out
```

An unrecognized value raises a `SnoutyEngineError`.

#### How `auto` and `gpu` differ

The difference is consent. `auto` never fails over hardware, because the
caller expressed no preference. An explicit `gpu` is strict about the
environment and forgiving about capacity.

| condition | `auto` | `gpu` |
| --- | --- | --- |
| the mode has no GPU path | CPU, with a reason | CPU, with a reason |
| cupy is not installed | CPU, with a reason | raises `SnoutyEngineError` |
| no CUDA device answers | CPU, with a reason | raises `SnoutyEngineError` |
| the scene exceeds free device memory | CPU, with a reason | CPU, with a reason |

A missing cupy means that a person must fix the environment. A card that is
too small for one scene is not a broken environment, so that scene moves to
the CPU and the convert continues.

Resolution happens once, in the constructor, before any pixel work. Read
`reader.engine_used` and `reader.engine_fallback_reason` for the result.

#### Sessions

A session convert writes one output store per child. `engine="auto"` picks
one engine for **every** child in the session, sized from the largest child.
A single session never mixes engines under `auto`. An explicit `engine="gpu"`
decides per child, so one oversized child runs on the CPU while the rest run
on the GPU.

#### The two engines do not write identical pixels

**Do not compare a GPU store against a CPU store byte for byte.** `scipy` and
`cupy` disagree.

At the measured geometry 0.0036% of voxels differ: 43,492 of 1,213,170,000,
on 29 lines of the 808,780 in the rotated grid. 28 of those lines are
nearest-neighbour ties, where the two libraries pick opposite equidistant
voxels and both answers are equally valid. **One line is a real loss.** Its
source coordinate is exactly `0.0`. `scipy` reads the first plane there and
`cupy` returns the fill value, so the GPU drops 1500 voxels at the outermost
non-empty edge plane. Measured on real hardware in #18.

Every output store records the engine that wrote it. The reader contributes
this block to the zarrmony acquisition audit:

```json
{"zarrmony_snouty": {
  "engine_used": "gpu",
  "engine_fallback_reason": null,
  "cupy_version": "13.4.1",
  "cuda_runtime_version": "12.4"
}}
```

#### Installing cupy

There is no `gpu` extra. cupy wheels are pinned to a CUDA major version, and
the generic `cupy` source distribution needs a local CUDA toolchain to build.
Install the wheel that matches the CUDA runtime on the host:

```bash
pip install cupy-cuda12x   # for a CUDA 12.x runtime
```

#### Running on a GPU node

Measured throughput is **2.55x the best figure the CPU reaches**. The CPU
transform stops scaling at 8 threads and gets slower above that, so a wider
node does not close the gap. Ask for 8 to 16 cores next to the card. More
cores cost allocation time and return nothing.

The reader submits no jobs of its own (ADR-0002, decision 1). It runs inside
the job that you submit. Wrap the convert in your scheduler's submit script,
and fill in your own partition and account:

```bash
#!/bin/bash
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
# Add your own --partition and --account here.

export ZARRMONY_SNOUTY_MODE=traditional
export ZARRMONY_SNOUTY_ENGINE=auto
zarrmony convert /mnt/readonly/<dataset>/…_ht_sols_gui ./out
```

### Host memory for the transform modes

The reader builds one dask task per timepoint, and each task transforms a
whole `(C, Z, Y, X)` volume. Dask's default scheduler is threaded with
`os.cpu_count()` workers, so without a bound the host holds one whole
transformed volume per core.

Peak host memory per task, at a real acquisition geometry
(`slices_per_volume: 411`, `height_px: 600`, `width_px: 1500`,
`scan_step_size_px: 2`):

| mode | input | intermediate | output | peak per task |
| --- | --- | --- | --- | --- |
| `raw` | 0.73 GB | — | — | **0.73 GB** |
| `desheared` | 0.73 GB | — | 1.74 GB | **2.47 GB** |
| `traditional` | 0.73 GB | 1.74 GB | 2.43 GB | **4.90 GB** |

`desheared` and `traditional` therefore reserve their footprint from a
process-wide byte budget before each transform runs. The budget is **half of
the smaller of the cgroup memory limit and total physical RAM**. Reading the
cgroup limit matters inside a scheduler allocation: a 62 GB SLURM allocation
on a 512 GB node must budget from 62 GB, not from 512 GB.

`raw` reserves nothing. It runs no transform and keeps the scheduler's full
width.

Two environment variables override the bound:

| variable | effect |
| --- | --- |
| `ZARRMONY_SNOUTY_HOST_MEMORY_BYTES` | An absolute budget in bytes. `0` disables the bound. |
| `ZARRMONY_SNOUTY_HOST_MEMORY_FRACTION` | A fraction of the detected limit, greater than 0 and at most 1. Default `0.5`. |

```bash
# Give the transforms 40 GB inside a 64 GB allocation.
ZARRMONY_SNOUTY_HOST_MEMORY_BYTES=40000000000 \
  ZARRMONY_SNOUTY_MODE=traditional \
  zarrmony convert /path/to/…_ht_sols_snap ./out
```

A task whose footprint exceeds the whole budget still runs. It waits for an
idle moment and runs alone, rather than deadlocking. A bad value in either
variable raises a `SnoutyHostMemoryError`.

`SnoutyReader.transform_footprint_bytes` reports what one task will reserve,
before any pixel work starts.

## Supported acquisitions

- **Single- or multi-position, single- or multi-timepoint, single- or
  multi-channel snap and acquire runs.** Multi-position acquisitions expose
  one scene per position; multi-timepoint concatenates along T; multi-channel
  places the vendor's channel labels verbatim along C.
- **The T axis follows the timestamp the camera burns into the pixel data.**
  The PCO hardware writes a frame counter and a capture time into the first
  pixels of every 2D frame, before any software sees it. File mtimes order
  nothing, so a coarse filesystem clock, a `cp -r`, or an `rsync` without
  `-t` cannot scramble the timepoints. If a file carries no stamp the reader
  recognizes, it falls back to the zero-padded filename order and warns with
  `SnoutyTimestampWarning`.
- **Whole GUI-session directories** — one `zarrmony convert` on
  `*_ht_sols_gui/` produces one output store per non-empty subdir. Empty or
  malformed subdirs are skipped with a warning.

Detection requires either a subdir whose name ends in `_ht_sols_snap` or
`_ht_sols_acquire` with sibling `data/` and `metadata/` dirs (at least one
`.tif` and one `.txt`), or a parent GUI-session dir whose name ends in
`_ht_sols_gui` and which contains at least one such subdir. See Limitations
for the remaining unsupported shape.

## Limitations

- **`volumes_per_buffer > 1` is not implemented.** Snouty's
  hardware-limited time sampling packs multiple volumes into a single
  `.tif` (frames laid out as
  `(volumes_per_buffer, slices_per_volume, channels, Y, X)`); no real
  fixture has been staged to verify the buffer-frame layout, so the
  reader raises `SnoutyVolumesPerBufferUnsupportedError` when it sees
  `volumes_per_buffer > 1` in the sidecar. This propagates through
  session-level convert on the first subdir it sees with `vpb > 1`.
- **No GPU deshear.** `desheared` runs on the CPU on every host. The GPU path
  covers `traditional` only (ADR-0002, decision 2). A GPU deshear costs more
  in PCIe upload than the CPU deshear costs outright.
- **cupy is a soft dependency, and there is no `gpu` extra.** `pip install
  zarrmony-snouty` installs no CUDA. Install a matching cupy wheel yourself
  to get a GPU path. See **Engine** above.
- **No HCS-plate output.** Sessions whose `XY_stage_position_list.txt`
  describes a well-plate scan-order still surface as a flat scene list.
  Plate-shape detection and OME-NGFF HCS output are tracked for a later
  release.
  - Plate tile scans also use a third directory suffix and a third filename
    shape: `*_ht_sols_acquisition_*` directories holding
    `NNNNNN_<well>r<row>c<col>.tif`. Detection accepts neither suffix, so the
    reader rejects these directories instead of reading them as a flat T
    axis. The stage visits the tiles in a serpentine order, so their
    burned-in frame counters ascend in tile order and not in filename order.

## Roadmap

- **v0.2** — ✅ deshear and traditional-view output modes (opt-in),
  multi-timepoint T-concat, multi-position (one scene per position),
  multi-channel wiring.
- **v0.3** — ✅ top-level `*_ht_sols_gui/` directory as multi-scene input,
  one output store per non-empty subdir; ✅ in-process GPU `traditional`
  transform behind an `engine` selector, with a CPU fallback.

## Why a separate package?

Snouty is a custom-built microscope with no bioio backend. The vendor
metadata is a bespoke key=value plaintext file, not OME-XML. The raw pixel
data is a plain multi-slice TIFF but the geometry (55° light-sheet tilt,
scan-shear along Y) requires a plugin that understands the sidecar to expose
correct pixel sizes and to deshear into orthogonal views. See
[ADR-0001](docs/adr/0001-tifffile-over-bioio.md) for the rationale and the
[reader-plugin authoring guide](https://github.com/ferrinm/zarrmony/blob/main/docs/writing-a-reader-plugin.md)
for how to build your own plugin.

## License

Apache-2.0. See [LICENSE](LICENSE).
