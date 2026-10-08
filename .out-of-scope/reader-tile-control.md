# Lateral tile control on the Snouty readers

The Snouty readers do not accept a lateral tile size, and they will not. No
`tile_size` keyword, no `--reader-kwarg tile_size=`, no lateral blocking knob
of any other spelling.

Each scene is one dask block per timepoint, covering the whole `(C, Z, Y, X)`
volume. That is the shape the format gives us, and splitting it costs more
than it saves.

## Why this is out of scope

zarrmony plans a write grid, then warns when the reader's blocks do not nest
in it, because every write then splits a source block. The remedy it prints is
a reader tile that divides the grid. For the default `bioio` plugin that is
good advice. For this reader it is not, for three reasons that do not line up.

### The vendor TIFF has no sub-region read

Snouty writes one TIFF per timepoint, one page per z-slice. Checked across
three real acquisitions, covering the flat layout and both plate grammars:

| page shape (uint16) | `is_tiled` | strips per page | `rows_per_strip` | compression |
| --- | --- | --- | --- | --- |
| (150, 1500) | False | 1 | 150 | none |
| (200, 1500) | False | 1 | 200 | none |
| (250, 1500) | False | 1 | 250 | none |

Every page is a single uncompressed contiguous strip spanning the full page.
There are no TIFF tiles to address, so there is no cheap random access below
the page.

### The two lateral axes fail for opposite reasons

X is separable in the transform. The deshear copies each z-plane along Y and
leaves X alone:

```python
# _deshear.deshear_zyx
for z in range(size_z):
    shift = int(np.rint(scan_step_size_px * z))
    out[z, shift : shift + size_y, :] = volume[z, :, :]
```

The `traditional` rotation keeps `size_x` in its `output_shape` for the same
reason. But X is the fastest-varying axis on disk. One row holds 1500 uint16
values, or 3000 bytes. An X sub-range is a strided read that still touches
every row in the file, so the disk traffic does not fall.

Y is the mirror image. Rows are contiguous and uncompressed, so a Y sub-range
is a cheap memmap slice at the storage layer. But the deshear shifts each
z-plane along Y, and the rotation mixes Z and Y again. One output Y tile needs
the full Z-Y extent of the input. Only `raw` mode escapes that coupling, and
#11 moved the default away from `raw`.

So the one axis that the transform can split cannot be split on disk, and the
one axis that disk can split cannot be split by the transform.

### The conversion is storage-bound

ADR-0002 measured this. A GPU run of the reference acquisition moved about
1.48 TB in 9712 s, which is 152 MB/s, and the GPU did not move the wall-clock
time at all. Storage bandwidth sets the wall.

`_pixels._read_and_crop_plane` calls `tifffile.imread(path)` on the whole
file. Put the three facts together and a lateral tile reads the whole
timepoint once per tile. The read amplifies by the tile count in order to
remove a write split. On a storage-bound conversion that is the wrong trade.
It makes the expensive direction worse to improve the cheap one.

## The term is wrong too

`CONTEXT.md` lists *tile* under the terms this project avoids. The vendor says
"tile" to mean a stitched mosaic position, and this reader does not stitch. A
`tile_size` keyword would import that collision straight into the public
surface, where it would be the first thing a user reads.

## What we did instead

Two separate things, neither of which is a tiling knob.

An unsupported reader keyword now fails with a sentence instead of a
traceback. See #42. The reader states what it accepts and names the keyword it
rejected, so a user who follows the warning learns why the advice does not
apply here.

The warning itself is fixed upstream. `_align_reader_tiles` in zarrmony
already decides that a plugin other than the default one does not take a tile
size. The scene writer that builds the remedy text never consults that
decision, so it prints the hint anyway. Tracked as zarrmony issue #159.

## If this comes back

The decision rests on the vendor writing untiled, uncompressed, single-strip
pages, and on the deshear coupling Z and Y. Change either and the arithmetic
is worth redoing.

A vendor change to internally tiled TIFFs would fix the X side. It would not
fix the Y side on the default `desheared` output, because the coupling is in
our transform, not in the file. A conversion that became compute-bound rather
than storage-bound would also be worth a second look.

## Prior requests

- #42 — "Every convert advises `--reader-kwarg tile_size=`, and that flag
  raises TypeError on every Snouty input" (the capability half; the
  error-message half stayed on #42 and shipped)
- #45 — "Lateral block control for the Snouty readers"
