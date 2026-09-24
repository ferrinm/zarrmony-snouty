# ADR-0002: In-process GPU for `traditional` only, with an up-front engine decision

Date: 2026-09-24
Status: Accepted

## Context

v0.2 ships CPU-only `desheared` and `traditional` transforms in `_deshear.py`,
ported from the CPU paths of Austin Lefebvre's
[`snouty-folder`](https://github.com/aelefebv/snouty-folder). That package also
carries cupy paths. This ADR decides whether to port them, where the GPU code
runs, and what happens when the GPU is absent or broken.

The original plan assumed that the reader submits an `sbatch` job per transform
and blocks on the result. That plan let `zarrmony convert` run from a login node
without local CUDA. Measurement rejected it.

### Measurements

Two real acquisitions supply every figure below. Paths are omitted because this
repository is public. Geometry is what reproduces the numbers.

**Acquisition A** — `slices_per_volume: 411`, `height_px: 600`, `width_px: 1500`,
`scan_step_size_px: 2`, `voxel_aspect_ratio: 2.856`, 210 timepoints, one channel.
After the timestamp strip is cropped, each volume is `(411, 592, 1500)`, or
0.73 GB.

**Acquisition B** — `slices_per_volume: 820`, `height_px: 200`, `width_px: 1500`,
`scan_step_size_px: 1`, `voxel_aspect_ratio: 1.428`. Cropped, each volume is
`(820, 192, 1500)`, or 0.47 GB. Its smaller scan step produces far more deshear
padding, which is why section 6 quotes both.

Unless stated otherwise, figures refer to acquisition A.

CPU cost per timepoint, measured in memory on an Apple Silicon laptop:

| transform | CPU time | output shape | output size |
| --------- | -------- | ------------ | ----------- |
| `deshear` | **0.15 s** | `(411, 1412, 1500)` | 1.74 GB |
| `traditional` | **13.56 s** | `(530, 1526, 1500)` | 2.43 GB |

The two transforms differ by a factor of 91. `deshear` is a strided memory copy
and is bandwidth-bound. `traditional` performs real arithmetic.

An earlier conversion of acquisition A ran `snouty-folder` on a TITAN RTX with
cupy active. It took 9712 s, or 46.2 s per timepoint. That run wrote an
intermediate original OME-TIFF, zero-filled both output files page by page, and
read the intermediate back through a memmap. The total traffic was about
1.48 TB, which over 9712 s is 152 MB/s. The run was storage-bound. The GPU
reduced the arithmetic to near zero and the wall-clock time did not move.

This matters because our CPU `traditional` costs 13.56 s per timepoint, which is
3.4x faster than that GPU run end to end. Storage bandwidth, not FLOPs, set the
wall.

### Why per-timepoint job submission loses

`adapter.py` builds one `dask.delayed` node per timepoint TIFF. A job-submission
model therefore means 210 blocking jobs for this acquisition. Each round trip
adds 6.32 GB of traffic: 0.73 GB out, 0.73 GB read in the job, 2.43 GB written
by the job, and 2.43 GB read back. At the 152 MB/s this cluster delivered, that
is 41.6 s of added I/O to save 13.56 s of CPU. The model loses 28 s per
timepoint, or 98 minutes across the acquisition.

Fast scratch storage at 2 GB/s turns this into a win of about 10 s per
timepoint, but only if 210 consecutive submissions cost no queue time. Real
queue latency removes that margin.

## Decision

### 1. Execution model: in-process cupy, no job submission

`_deshear_gpu.py` soft-imports cupy and exposes `cupy_available: bool`. The
reader calls it directly. The reader contains no SLURM code and no
`_slurm.py`. The caller decides where the process runs.

Two caller-side deployments work without further reader code:

- Run `zarrmony convert` inside a GPU allocation, through `srun` or a small
  `sbatch` wrapper script.
- Wire a `dask_jobqueue.SLURMCluster` before the convert. zarrmony's
  `da.compute` picks up an ambient `distributed.Client`, and `da.store` writes
  from the worker straight into the Zarr store, so arrays never pass through
  the client.

### 2. Scope: `traditional` only

The GPU accelerates `traditional`. `deshear` stays on the CPU permanently.
0.15 s per timepoint is 32 s across the whole acquisition, and a PCIe upload of
the input volume alone costs more than that. `snouty-folder` reaches the same
conclusion: its `write_desheared_ome_tif` calls `_per_slice_cpu_deshear`
unconditionally, and cupy appears only inside `_affine_rotate`.

### 3. Concurrency: one GPU transform at a time

A module-level lock serializes GPU work. Dask's default scheduler runs
`os.cpu_count()` threads, and each `traditional` task holds a 1.74 GB desheared
intermediate plus a 2.43 GB output on the device, or 4.17 GB. A 24 GB card fits
about five. Sixteen threads demand 67 GB. cupy's default memory pool retains
freed blocks, so unbounded concurrency exhausts the device.

The lock fixes device demand at 4.17 GB whatever the thread count, and lets the
memory pool reuse the same blocks on every iteration.

### 4. Fallback semantics: decided once, in `__init__`

`SnoutyReader.__init__` selects the engine before any pixel work starts. Three
of the four GPU failure modes are known at that point:

| failure | detectable in `__init__` |
| ------- | ------------------------ |
| cupy not installed | yes, at import |
| no CUDA device visible | yes, through `getDeviceCount()` |
| volume exceeds device memory | yes, from the sidecar through `traditional_shape` |
| transient CUDA fault mid-run | no |

`engine_used` and `engine_fallback_reason` therefore become plain attributes set
at construction. A transient CUDA fault during compute raises and fails the
convert. The reader does not fall back per task.

A per-task fallback cannot report itself. The fallback runs inside a
`dask.delayed` function. Under the default threaded scheduler a mutation of
`self` is visible but racy. Under a distributed scheduler it happens on a worker
process and never reaches the client's reader object. zarrmony reads the audit
after the write, so the attributes get read at the right moment and contain
nothing. That defect passes in CI and returns wrong data on a cluster.

An error is also the more useful signal inside a paid GPU allocation. A run that
quietly drops to 13.56 s per timepoint at timepoint 90 wastes the allocation and
hides a sick node.

### 5. Engine kwarg surface

`SnoutyReader(path, mode=..., engine=...)` accepts `"auto" | "cpu" | "gpu"` and
defaults to `"auto"`. `ZARRMONY_SNOUTY_ENGINE` mirrors it, in the same shape as
the existing `ZARRMONY_SNOUTY_MODE` plumbing. `engine_used` returns
`"cpu" | "gpu"`.

`engine="gpu"` is strict about capability failures and silent about by-design
absences:

- `mode="traditional"` with no cupy raises `SnoutyEngineError`.
- `mode="raw"` or `mode="desheared"` runs on the CPU, reports
  `engine_used="cpu"`, and records the reason `mode '<mode>' has no GPU path`.

The second rule exists because `ZARRMONY_SNOUTY_ENGINE` is set once for a whole
session-level convert. `SnoutySessionReader` forwards the setting to every child
reader, and after the default flip most of those children run `desheared`. A
strict reading fails every correct default-mode convert.

Different children can also reach different answers. Device memory demand
depends on geometry, so one acquisition uses the GPU while its sibling does not.
Per-scene `engine_used` and `engine_fallback_reason` make that self-explanatory
in the audit.

### 6. Default mode flips from `raw` to `desheared`

This ADR ratifies the flip in issue #11, but not its stated reason. The issue
argues that GPU acceleration makes deshearing cheap enough to default to. That
argument is void, because `deshear` has no GPU path. The flip stands on three
other grounds:

- **Cost.** 0.15 s per timepoint, 32 s for the whole acquisition.
- **Losslessness.** `deshear_zyx` writes each input plane into a zeroed output
  at an integer offset. Every input voxel survives exactly once, and `raw` is
  recoverable given the sidecar. `traditional` resamples with `order=0` nearest
  neighbor and a Z zoom of 2.856, which duplicates and drops voxels and does
  not invert.
- **Storage.** The padding is free on disk. Measured with zstd level 3 on a real
  60-plane sub-volume, signal costs 0.941 bytes per voxel and zero padding costs
  0.000061 bytes per voxel.

| dataset | uncompressed growth | padding | predicted on-disk growth |
| ------- | ------------------- | ------- | ------------------------ |
| A (`scan_step_size_px: 2`) | 2.39x | 58% | 1.00x |
| B (`scan_step_size_px: 1`) | 5.27x | 81% | 1.00x |

`traditional` is not the default. A converter's default output must not destroy
information, and `traditional` is a lossy rendering. Lossy renderings stay
behind an explicit flag.

### 7. Configuration surface

There is none beyond `ZARRMONY_SNOUTY_ENGINE`. The earlier plan called for env
vars that hold a SLURM partition, account, GPU count, walltime, and `sbatch`
binary path. The reader does not submit jobs, so none of those settings belong
to it. Partition and account are properties of the caller's `srun`, `sbatch`
script, or `SLURMCluster` configuration.

## Consequences

### Positive

- The reader stays a library. It has no opinion about schedulers, queues, or
  clusters, and it needs no cluster to test.
- `zarrmony convert` under `dask_jobqueue.SLURMCluster` works with no extra
  reader code, because the GPU decision is local to each worker process.
- `engine_used` and `engine_fallback_reason` are honest under every scheduler,
  because nothing sets them during compute.
- Device memory demand is a fixed 4.17 GB at this geometry, independent of host
  core count. The GPU path behaves the same on a 4-core and a 64-core node.
- The default flip costs nothing on disk.

### Negative

- A user on a login node without CUDA gets the CPU path and no warning that a
  faster route exists. The README must document the `sbatch` wrapper.
- A transient CUDA fault fails the whole convert. The user re-runs. We accept
  restart cost in exchange for a reportable engine.
- The GPU advantage over a many-core CPU node is modest. Serialized GPU work is
  PCIe-bound at an estimated 0.3 to 0.5 s per timepoint, and CPU work is
  13.56 s divided by the thread count. The GPU wins clearly on a 16-core node
  and loses on a 64-core node. This estimate is not yet measured on real
  hardware.
- The default flip raises host memory per task from 0.73 GB to 2.47 GB for
  `desheared`, and to 4.90 GB for `traditional`. Sixteen threads want 40 GB and
  78 GB. The reference allocation provided 62 GB. Host-side concurrency needs
  its own bound, tracked in
  [#15](https://github.com/ferrinm/zarrmony-snouty/issues/15) rather than here.
  It must land with #11, because the default flip is what makes it reachable.

### Reversibility

- **Execution model: low cost to revisit.** `_deshear_gpu` has the same function
  surface as `_deshear`. A future `_slurm.py` can wrap it without a change to
  the Reader Protocol surface.
- **Scope: low cost.** Adding a GPU `deshear` later is additive.
- **Default mode flip: high cost.** It is a breaking change to output shape.
  Issue #11 carries the migration note.

## Considered alternatives

| Alternative | Why rejected |
| ----------- | ------------ |
| Reader submits one `sbatch` job per transform and blocks | Adds 6.32 GB of traffic per timepoint to save 13.56 s of CPU. At the 152 MB/s measured on this cluster that is a net loss of 28 s per timepoint. Also leaves every TIFF read, Zarr write, and pyramid mean-pool on the login node, which is the bulk of the work. |
| Reader submits one `sbatch` job for the whole convert | Turns a reader plugin into a job-submission tool, and zarrmony owns the output path. A three-line user-written wrapper does the same thing with no code. |
| Caller-wired `dask_jobqueue.SLURMCluster` | Not rejected. It needs the identical reader code to the chosen option, so it is a supported deployment rather than a competing design. zarrmony's `writers/scene.py` passes `lock=True` to `da.store`, which resolves to `dask.utils.SerializableLock`. That lock is per-process by design and does not block across processes, so a multi-worker cluster parallelizes writes across workers. The single-process threaded default is the configuration it constrains most. |
| GPU `deshear` as well as GPU `traditional` | 0.15 s per timepoint leaves nothing to win, and the PCIe upload costs more than the transform. `snouty-folder` reaches the same conclusion. |
| Silent GPU-to-CPU fallback on any failure, mid-run | The fallback reason cannot travel from a dask worker back to the reader object. The criterion passes under the threaded scheduler and returns wrong data under a distributed one. |
| Unbounded GPU concurrency with an `OutOfMemoryError` catch | Nondeterministic. Failed tasks then contend for host RAM against tasks that succeeded, and cupy's pool thrashes. |
| `traditional` as the new default | Costs 91x `deshear` and discards information irreversibly through `order=0` resampling. |
| `device=` instead of `engine=` for the kwarg | `device` conventionally names a specific device such as `cuda:0`, which invites requests this plugin does not serve. |

## References

- Reference implementation:
  [`snouty-folder`](https://github.com/aelefebv/snouty-folder) — Austin
  Lefebvre's standalone Snouty converter, and the source of every GPU code path
  this ADR scopes. `_affine_rotate` holds the cupy `traditional` path.
  `_per_slice_cpu_deshear` is CPU-only in that package, which corroborates
  decision 2.
- [ADR-0001: read Snouty output with `tifffile` + a hand-parser](./0001-tifffile-over-bioio.md)
  — the same port relationship to `snouty-folder`, and the reason this plugin
  takes algorithms rather than a dependency.
- Timing baseline: an internal project notebook records the 9712 s TITAN RTX run
  of `snouty_folder.SnoutyFolder.write_traditional_ome_tif` on acquisition A,
  inside an interactive single-GPU SLURM allocation. The record is not public.
  The numbers it supplies appear in full under "Measurements" above, so this ADR
  stands on its own.
- [Issue #7](https://github.com/ferrinm/zarrmony-snouty/issues/7) — this ADR.
- Follow-up slices: [#8](https://github.com/ferrinm/zarrmony-snouty/issues/8)
  (`_deshear_gpu` port),
  [#10](https://github.com/ferrinm/zarrmony-snouty/issues/10) (`engine` kwarg),
  [#11](https://github.com/ferrinm/zarrmony-snouty/issues/11) (default flip).
  [#9](https://github.com/ferrinm/zarrmony-snouty/issues/9) (`_slurm.py`) is
  closed by decision 1.
