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

The Z spacing reported here is the **raw scan step** — the physical distance
the scan mirror moves between successive slices — not the de-sheared/rotated
orthogonal Z. Downstream tools that need real orthogonal geometry will need
to deshear the volume (see Roadmap below) or apply the sidecar's
`voxel_aspect_ratio` themselves.

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
- **No deshear or rotation.** The output volume is the raw skewed
  `(Z, Y, X)` from the vendor's TIFF, with the top 8 rows (PCO BCD
  timestamp strip) cropped off. Deshear and traditional-view (rotated)
  modes are tracked for v0.2 — the reference algorithm lives in
  `../snouty-folder` and will be ported.
- **No top-level GUI-session-dir support.** Users must point `zarrmony
  convert` at individual `*_ht_sols_*` subdirs, not the parent
  `*_ht_sols_gui/` directory. Multi-scene enumeration across a whole GUI
  session is tracked for v0.3.

## Roadmap

- **v0.2** — deshear and traditional-view output modes (opt-in), ported from
  the CPU implementation in [`snouty-folder`](https://github.com/ferrinm/snouty-folder);
  multi-timepoint via T-concat across `data/*.tif`; multi-position (one
  scene per position); multi-channel wiring.
- **v0.3** — top-level `*_ht_sols_gui/` directory as the input, one scene
  per subdir. Consider whether the XY position list surfaces as
  plate-shaped metadata.

## Why a separate package?

Snouty is a custom-built microscope with no bioio backend. The vendor
metadata is a bespoke key=value plaintext file, not OME-XML. The raw pixel
data is a plain multi-slice TIFF but the geometry (55° light-sheet tilt,
scan-shear along Y) requires a plugin that understands the sidecar to expose
correct pixel sizes and eventually to deshear. See
[ADR-0001](docs/adr/0001-tifffile-over-bioio.md) for the rationale and the
[reader-plugin authoring guide](https://github.com/ferrinm/zarrmony/blob/main/docs/writing-a-reader-plugin.md)
for how to build your own plugin.

## License

Apache-2.0. See [LICENSE](LICENSE).
