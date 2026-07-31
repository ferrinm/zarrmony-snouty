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
are ported (CPU-only) from Austin Lefebvre's
[`snouty-folder`](https://github.com/aelefebv/snouty-folder); GPU paths are
intentionally out of scope.

## Supported acquisitions

- **Single- or multi-position, single- or multi-timepoint, single- or
  multi-channel snap and acquire runs.** Multi-position acquisitions expose
  one scene per position; multi-timepoint concatenates along T; multi-channel
  places the vendor's channel labels verbatim along C.
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
- **No GPU deshear/rotate.** Only the CPU paths from
  [`snouty-folder`](https://github.com/aelefebv/snouty-folder) are ported.
  `traditional` mode uses `scipy.ndimage.affine_transform`; cupy is
  intentionally not a dependency.
- **No HCS-plate output.** Sessions whose `XY_stage_position_list.txt`
  describes a well-plate scan-order still surface as a flat scene list.
  Plate-shape detection and OME-NGFF HCS output are tracked for a later
  release.

## Roadmap

- **v0.2** — ✅ deshear and traditional-view output modes (opt-in),
  multi-timepoint T-concat, multi-position (one scene per position),
  multi-channel wiring.
- **v0.3** — ✅ top-level `*_ht_sols_gui/` directory as multi-scene input,
  one output store per non-empty subdir.

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
