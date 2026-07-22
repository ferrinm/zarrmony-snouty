# ADR-0001: Read Snouty output with `tifffile` + a hand-parser, not a bioio backend

Date: 2026-07-14
Status: Accepted

## Context

Snouty is a custom-built single-objective light-sheet ("SOLS") microscope from
the AndrewGYork lab. The GUI writes one subdirectory per snap/acquire run,
each containing:

- `data/<NNN>.tif` — a plain multi-slice TIFF holding the raw skewed `(Z, Y, X)`
  volume (16-bit, ~29 MB at 200×1500 with 48 Z slices).
- `metadata/<NNN>.txt` — a bespoke key=value plaintext sidecar exposing
  channel labels, dimensions, sample pixel size, scan step, voxel aspect
  ratio, and light-sheet tilt.
- `preview/<NNN>.tif` — a small 2D preview render; ignored.

There is no bioio backend for Snouty, and no relevant OME-XML anywhere in the
export — the TIFFs have only bare `ImageDescription`/`OME` tags. Every field
zarrmony needs (channels, pixel sizes, dims) lives in the plaintext sidecar.

## Decision

Parse the `.txt` sidecar directly (`_metadata.py`) into a typed
`SnoutyMetadata` dataclass. Read the pixel volume with `tifffile.imread`
wrapped in `dask.delayed` so `open()` stays cheap. Do not depend on any bioio
sub-package, and do not attempt to synthesise OME-XML from the sidecar just to
route through `bioio-ome-tiff`.

This mirrors [zarrmony-blaze's ADR-0001](https://github.com/ferrinm/zarrmony-blaze/blob/main/docs/adr/0001-tifffile-over-bioio-ome-tiff.md)
(also `tifffile` + a hand-parser), but for a different reason: blaze rejects
bioio because the vendor's real OME-XML is malformed; snouty rejects it
because there is no OME-XML at all.

## Consequences

### Positive

- Zero dependency on `bioio-ome-tiff`, `ome-types`, or any bioio backend.
  Install footprint is `zarrmony` + `tifffile` + `dask` + `numpy` + `xarray`.
- The sidecar parser is ~90 lines of stdlib Python. We control every field
  we consume and can extend it incrementally as the Snouty GUI evolves.
- The verbatim sidecar text lands in `OME/source/raw.snouty.txt` via
  `SnoutyReader.metadata`, so downstream consumers can round-trip any field
  we don't currently parse.

### Negative

- We own the parser. If the vendor changes the sidecar format (adds a
  required key, changes a value type), we update `_metadata.py` rather than
  getting the fix for free from an upstream reader.
- The reader doesn't validate against any schema. A malformed sidecar could
  parse but produce subtly wrong output. Mitigation: required-key checks
  raise `SnoutyMetadataError` with the missing key surfaced in the message.

### Reversibility

- **Low cost to revisit.** If a bioio backend for Snouty ever ships (or a
  standard OME-XML block is added to the TIFF), we can switch by changing
  `adapter.py` to delegate. The Reader Protocol surface doesn't change.

## Considered alternatives

| Alternative                                              | Why rejected                                                                                       |
| -------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| Use `bioio-ome-tiff` directly                            | No OME-XML in the TIFFs; nothing for it to parse.                                                  |
| Synthesise OME-XML from the sidecar and route to `bioio` | Round-trip through an unrelated schema adds fragility. The direct path is smaller and testable.   |
| Depend on the reference [`snouty-folder`](https://github.com/aelefebv/snouty-folder) package as a lib | Not on PyPI; hard cupy dependency in its `pyproject.toml`; we only want the algorithms, not deps. |

## References

- Reference implementation: [`snouty-folder`](https://github.com/aelefebv/snouty-folder)
  (`snouty_folder/snouty_folder.py`) — Austin Lefebvre's standalone
  Snouty→OME-TIFF converter. `SnoutyMetadata` is a direct port of its
  `_load_metadata`; the timestamp-strip cropping and the choice of
  `sample_px_um`/`scan_step_size_um` for pixel sizes both originate there.
- [zarrmony ADR-0001: reader plugin architecture](https://github.com/ferrinm/zarrmony/blob/main/docs/adr/0001-reader-plugin-architecture.md)
