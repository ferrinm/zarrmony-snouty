# zarrmony-snouty

Snouty (single-objective light-sheet, "SOLS") reader plugin for
[zarrmony](https://github.com/ferrinm/zarrmony). Detects a single Snouty GUI
acquisition subdirectory (`*_ht_sols_snap` or `*_ht_sols_acquire`) and
converts the raw skewed volume it contains to OME-NGFF 0.5:

```bash
zarrmony convert /path/to/<ts>_000_ht_sols_snap ./out
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

```python
from zarrmony.readers.plugin import list_plugins

print([p.name for p in list_plugins()])
# -> [..., 'zarrmony-snouty']
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

```bash
zarrmony inspect /path/to/2026-07-14_10-15-35_000_ht_sols_snap   # dims, channels, pixel sizes
zarrmony convert /path/to/2026-07-14_10-15-35_000_ht_sols_snap ./out
```

Output is a single `<dir-basename>.ome.zarr` store with dims `(T=1, C=1, Z, Y, X)`,
physical pixel sizes `(X=sample_px_um, Y=sample_px_um, Z=scan_step_size_um)`
copied from the vendor's `metadata/<name>.txt` sidecar, and channel names from
`channels_per_slice`. The verbatim sidecar text is preserved in the audit at
`<store>/OME/source/raw.snouty.txt`.

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

- **Single-position, single-timepoint, single-channel snap or acquire runs.**
  This is the simplest observed Snouty output shape and the target of v0.1.
  Multi-position, multi-timepoint, and multi-channel runs are detected and
  rejected with actionable errors (see Limitations).

Detection requires a directory whose name ends in `_ht_sols_snap` or
`_ht_sols_acquire` and which contains sibling `data/` and `metadata/`
subdirectories with at least one `.tif` and one `.txt` file respectively.

## Limitations

- **No multi-position support.** Data files named `*_pNNNNNN.tif` raise
  `SnoutyMultipositionUnsupportedError` (a `NotImplementedError`). Tracked for
  v0.2.
- **No multi-timepoint support.** More than one `.tif` in `data/` (or
  `volumes_per_buffer > 1`) raises `SnoutyMultiTimepointUnsupportedError`.
  Tracked for v0.2.
- **No multi-channel support.** `channels_per_slice` with more than one
  entry raises `SnoutyMultiChannelUnsupportedError`. Tracked for v0.2.
- **No GPU deshear/rotate.** Only the CPU paths from
  [`snouty-folder`](https://github.com/aelefebv/snouty-folder) are ported.
  `traditional` mode uses `scipy.ndimage.affine_transform`; cupy is
  intentionally not a dependency.
- **No top-level GUI-session-dir support.** Users must point `zarrmony
  convert` at individual `*_ht_sols_*` subdirs, not the parent
  `*_ht_sols_gui/` directory. Multi-scene enumeration across a whole GUI
  session is tracked for v0.3.

## Roadmap

- **v0.2** — ✅ deshear and traditional-view output modes (opt-in).
  Remaining v0.2 tracker items: multi-timepoint via T-concat across
  `data/*.tif`; multi-position (one scene per position); multi-channel
  wiring.
- **v0.3** — top-level `*_ht_sols_gui/` directory as the input, one scene
  per subdir. Consider whether the XY position list surfaces as
  plate-shaped metadata.

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
