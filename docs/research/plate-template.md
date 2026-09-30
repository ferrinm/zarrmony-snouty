# Plate map and tile ordering in the vendor multiwell-plate template

Research note for issue #19. Feeds the design decision in #21 — **this note records
facts only and makes no recommendation.**

**Subject:** `HT_SOLS_microscope`, upstream <https://github.com/amsikking/HT_SOLS_microscope>
**Snapshot read:** commit `32980ec` (2026-06-08), the current `HEAD` of the vendor working copy.
**Files read:** `ht_sols_microscope_acquisition_template_multiwell_plates.py`,
`ht_sols_microscope.py`, `ht_sols_data_to_zarr.py`, `ht_sols_microscope_gui.py`.

Line citations below are `file:line` against that commit.

## Vocabulary note

`CONTEXT.md` reserves **Position** for an XY stage location and **Well** for plate
work, and rules out *field* and *site*. The vendor's own word for a sampling point
inside a well is **tile** (`ht_sols_microscope.py:1256-1259`), so this note says
*tile* where #19 said "field". One **Position** in the reader's sense corresponds
to exactly one *(well, tile)* pair in a plate run.

---

## Headline

The plate template does **not** produce `NNNNNN_pMMMMMM.tif` filenames and does
**not** write `XY_stage_position_list.txt`. It writes
`NNNNNN_<well><tile>.tif`, e.g. `000000_A01r00c00.tif`, with the well and the
tile indices spelled out in the filename. The well and tile structure is therefore
fully recoverable from the filenames alone, with no Python parsing and no position
list. Details in Q6, which is the load-bearing answer.

---

## Q1. Well selection

There is no list of wells. Wells are selected by a **`start`/`stop` rectangle**
over the plate, expanded into a full row x column product.

At the call site the literal arguments are
`start='A1'`, `stop='B2'` (template lines 58-59), inside a
`get_multiwell_plate_positions(...)` call spanning template lines 54-68.

The expansion happens in the library. `start` and `stop` are split into a row
letter and a column number (`ht_sols_microscope.py:1273-1274`), converted to
half-open index ranges (`ht_sols_microscope.py:1279-1282`), and then iterated as a
full cross product (`ht_sols_microscope.py:1305-1309`). So `start='A1', stop='B2'`
means the four wells A1, A2, B1, B2 — **a rectangle, never an arbitrary set.**

Consequence: a sparse or hand-picked well selection cannot be expressed by this
function at all. Whatever ran through this template acquired a full rectangular
block.

## Q2. Well plate format

**The format is never named.** There is no `"96-well"` or `"384-well"` string
anywhere in the code path. The geometry is passed as bare numbers at the call site:

| Argument | Value | Template line |
| --- | --- | --- |
| `total_rows` | `16` | 55 |
| `total_cols` | `24` | 56 |
| `well_spacing_mm` | `4.5` | 57 |

The only mention of a plate product is a **comment**, template line 53:
`# Revvity PhenoPlate 384-well: rows A to P, cols 1 to 24`. A comment is not
machine-readable, and nothing validates that the numbers match it.

Rows and columns are turned into labels in the library: rows become letters from
`A` (`ht_sols_microscope.py:1267`), columns become 1-based integers
(`ht_sols_microscope.py:1269`). Asserts cap the format at
`total_rows <= 16` and `total_cols <= 24` (`ht_sols_microscope.py:1266, 1268`), i.e.
384-well is the largest plate expressible.

**Well diameter is never computed.** The four `A1_*` arguments (template lines
64-67) look like they describe the A1 well footprint, but the library uses them
only to derive the A1 **centre** by midpoint (`ht_sols_microscope.py:1301-1302`):

```python
A1_X_mm = A1_ul_X_mm - 0.5 * (A1_ul_X_mm - A1_lr_X_mm)
A1_Y_mm = A1_ul_Y_mm - 0.5 * (A1_ul_Y_mm - A1_lr_Y_mm)
```

The separation between the corners — the only thing that carries diameter — is
discarded. Tile placement is driven entirely by `tile_spacing_*_mm`, which come
from the camera field of view, not from the well
(template lines 51-52).

Two further cautions on the numbers:

- **The vendor docstring has 96-well and 384-well spacing swapped.**
  `ht_sols_microscope.py:1253` reads `# float (4.5mm for 96-well, 9mm for 384-well)`.
  Physically it is the reverse: 9 mm for 96-well, 4.5 mm for 384-well. The template
  passes `4.5` with a 16x24 plate, which is correct for 384-well, so the *template*
  is right and the *docstring* is wrong. Do not trust that comment.
- The `A1_*` calibration values are per-session stage calibration, not constants.
  They were changed once already in the template's short history (see Q5).

## Q3. Tile-per-well ordering

**Tiles iterate inside wells.** Wells iterate inside plate columns.

The generator is four nested loops, `ht_sols_microscope.py:1305-1324`, outermost
first:

1. `for c in range(col_start, col_stop)` — plate **column** (line 1305)
2. `for r in rows_range` — plate **row**, i.e. the well (line 1309)
3. `for tc in range(tile_cols)` — tile **column** within the well (line 1310)
4. `for tr in tile_rows_range` — tile **row** within the well, innermost (line 1314)

Both inner-most levels comment `# move faster y-axis more frequently`
(lines 1309, 1314), which is the stated intent: minimise stage travel.

The label is built at `ht_sols_microscope.py:1315-1316`:

```python
position_string = '%s%02ir%02ic%02i'%(
    row_labels[r], col_labels[c], tr, tc)
```

so a position string is `<RowLetter><Col:02d>r<TileRow:02d>c<TileCol:02d>`, e.g.
`A01r00c00`. Regex: `^([A-P])(\d{2})r(\d{2})c(\d{2})$`.

### Index arithmetic

Let `N_tiles = tile_rows * tile_cols` and `N_rows = row_stop - row_start`. For a
zero-based sequence index `p`:

```
well_ord  = p // N_tiles
tile_ord  = p %  N_tiles

c = col_start + well_ord // N_rows
r_raw     = well_ord %  N_rows
r = row_start + (r_raw if c % 2 == 0 else N_rows - 1 - r_raw)

tc = tile_ord // tile_rows
tr_raw     = tile_ord %  tile_rows
tr = tr_raw if tc % 2 == 0 else tile_rows - 1 - tr_raw
```

**Subtlety worth flagging:** the serpentine parity test is on the *absolute* plate
column index `c`, not on its offset within the selected sub-range
(`ht_sols_microscope.py:1307`). A selection starting at an odd column therefore
begins with its rows already reversed. The same applies to `tc`, but `tc` always
starts at 0, so there the distinction never bites.

This arithmetic was verified by replaying the loop body outside the vendor package
and comparing against the generated label sequence; it reproduces it exactly for
both cases below.

### Worked example — the template's own arguments

`start='A1'`, `stop='B2'`, `tile_rows=2`, `tile_cols=1` gives 8 positions:

| index | label |
| --- | --- |
| 0 | `A01r00c00` |
| 1 | `A01r01c00` |
| 2 | `B01r00c00` |
| 3 | `B01r01c00` |
| 4 | `B02r00c00` |
| 5 | `B02r01c00` |
| 6 | `A02r00c00` |
| 7 | `A02r01c00` |

Note the well order A1, B1, B2, A2 — down column 1, then back up column 2.

## Q4. Scan pattern

**Column-major serpentine, at two independent levels.**

- Column-major: the outer loop is over plate columns, the inner over rows
  (`ht_sols_microscope.py:1305-1309`). Wells advance down a column, not across a row.
- Serpentine over wells: `if c % 2: rows_range = reversed(rows_range)`
  (`ht_sols_microscope.py:1307-1308`). Odd plate columns are traversed bottom-to-top.
- Serpentine over tiles: `if tc % 2: tile_rows_range = reversed(tile_rows_range)`
  (`ht_sols_microscope.py:1312-1313`). The same boustrophedon inside each well.

Because the label carries `r`/`c` explicitly, **the scan pattern does not have to be
reverse-engineered to interpret a plate dataset.** It only matters if one is
deriving identity from a bare ordinal, which the plate filenames make unnecessary.

### Incidental finding: tile centring bug

`ht_sols_microscope.py:1290-1291` swaps `tile_rows` and `tile_cols` when computing
the centring offsets:

```python
tile_offset_X_mm = 0.5 * (tile_rows - 1) * tile_spacing_X_mm
tile_offset_Y_mm = 0.5 * (tile_cols - 1) * tile_spacing_Y_mm
```

but the offsets are consumed against the *other* loop variable
(`ht_sols_microscope.py:1319-1320`): `tc` (range `tile_cols`) multiplies
`tile_spacing_X_mm`, and `tr` (range `tile_rows`) multiplies `tile_spacing_Y_mm`.
Centring is therefore only correct when `tile_rows == tile_cols`.

With the template's `tile_rows=2, tile_cols=1` the tile block is **not** centred on
the well: X is offset by a constant +0.5 FOV, and the two tiles sit at Y offsets
`0` and `+1` FOV instead of `-0.5` and `+0.5`. This affects recorded **stage
coordinates**, not labels — so it is a caveat for anyone reconstructing geometry
from `XY_stage_position_mm`, and harmless for anyone reading labels.

## Q5. Stability

**Low structural stability for the template; higher for the library function.**

Everything is **inline inside the `if __name__ == '__main__':` block** (template
line 6 through the end of the file). There are no module-level constants, no
function, no `argparse`, no config file. Every parameter is a literal typed into a
call site the operator edits by hand before each run. The template is a
copy-and-edit scaffold, in keeping with the package's stated workflow of copying
modules into a local working directory (`ht_sols_data_to_zarr.py:17`).

Observed drift, from the template's own history (4 commits):

- **The output directory name has already changed.** An earlier revision built it
  as `ht_sols_acquisition_multiwell_plate` from an inline `datetime.strftime`;
  current `HEAD` uses `ht_sols.prepend_datetime('ht_sols_multiwell_plate')`
  (template line 71). Any matching on a fixed directory suffix is fragile across
  vendor versions — the `_acquisition_` segment is gone in the current revision.
- The `A1_*` stage calibration values were revised wholesale in the same commit.
- Imports (`os`, `numpy`, `tifffile`) were dropped as the template was tidied.

What has **not** drifted:

- The acquisition filename format `'%06i_%s.tif'%(t, p[0])` (template line 88) is
  unchanged since the template's first commit.
- `get_multiwell_plate_positions` and its `'%s%02ir%02ic%02i'` label format were
  introduced in a single commit and the label format has not been touched since
  (`ht_sols_microscope.py:1315-1316`).

So the **filename and label contract has been stable**, while the **surrounding
scaffolding and directory naming have not.** The stable part lives in the library;
the volatile part lives in the operator-edited template.

One more caveat, since operator-edited copies are involved: in practice the copy of
an acquisition script left beside a dataset may have been renamed by whoever ran
it, so its filename is not a reliable key either.

## Q6. Relationship to `XY_stage_position_list.txt` — the decisive question

### 6a. The template does not write that file

`XY_stage_position_list.txt` appears **nowhere** in
`ht_sols_microscope_acquisition_template_multiwell_plates.py`, and nowhere in
`ht_sols_microscope.py`. A search across the whole package finds it only in
`ht_sols_microscope_gui.py`.

It is a **GUI session artefact**. The GUI writes it into the session folder, which
it names `ht_sols_gui` (`ht_sols_microscope_gui.py:104`), at these sites:

- appended on load-from-folder — `ht_sols_microscope_gui.py:1908-1911`
- truncated on delete-all — `ht_sols_microscope_gui.py:1937`
- rewritten in full on delete-current — `ht_sols_microscope_gui.py:1969-1971`

The plate template never instantiates the GUI; it drives `ht_sols.Microscope`
directly (template lines 4, 8). **A plate acquisition produced by this template
will not contain an `XY_stage_position_list.txt` at all.**

### 6b. Even when present, that file carries no well identity

Each line is a bare coordinate pair with a trailing comma. Written as
`str([x, y]) + ',\n'` (`ht_sols_microscope_gui.py:1971`) and parsed back by
stripping brackets and splitting on the comma
(`ht_sols_microscope_gui.py:1897-1901`), giving lines shaped like:

```
[49.9315, -29.5372],
[47.1572, -26.5202],
```

Two floats in millimetres, positionally indexed, and nothing else. **No labels, no
well names, no tile indices, no row/column.** Well identity could at best be
*inferred* by clustering coordinates against an assumed pitch — that is inference,
not recovery, and it needs the plate pitch and A1 origin from somewhere else.

The file is also mutable: the GUI truncates and rewrites it in place as the operator
edits the list (`ht_sols_microscope_gui.py:1937, 1969-1971`), so it reflects the list
at save time rather than a guaranteed record of what was acquired.

### 6c. `_pNNNNNN.tif` is the GUI's convention, not the plate template's

The `_p` filenames come from the GUI's position-list loop,
`ht_sols_microscope_gui.py:2685-2686`:

```python
filename='%06i_p%06i.tif'%(
    self.acquire_count, self.acquire_position)
```

where `acquire_position` is an ordinal index into `XY_stage_position_list`
(`ht_sols_microscope_gui.py:2682`). That ordinal is meaningless on its own, which is
exactly why the GUI path needs the sidecar position list.

The plate template uses a **different** format, template line 88:

```python
filename = '%06i_%s.tif'%(t, p[0])
```

`p[0]` is the label from the generator, so files land as
`000000_A01r00c00.tif`. Full regex:
`^(\d{6})_([A-P])(\d{2})r(\d{2})c(\d{2})\.tif$`.

**So the `NNNNNN_pMMMMMM.tif` pattern that `CONTEXT.md` documents for positions will
not match plate data.** The two acquisition paths have genuinely different filename
grammars.

### 6d. Answer

**Yes — the well and tile structure is fully recoverable without parsing Python, and
`XY_stage_position_list.txt` is not involved.**

The plate template bakes the complete identity of every position into the filename.
From `000000_A01r00c00.tif` one reads directly: timepoint `0`, well `A1`, tile row
`0`, tile column `0`. No ordinal-to-well arithmetic, no loop-order assumption, no
scan-pattern knowledge, no position list, no source parsing. The mapping in Q3 is
useful for *understanding* the acquisition order, but is **not required** to
interpret the data.

Two independent corroborating routes exist, both non-Python:

1. **Per-image sidecars.** Every acquisition writes `metadata/<name>.txt` alongside
   `data/<name>.tif` (`ht_sols_microscope.py:392-394, 405-407`), one `key: value`
   per line (`ht_sols_microscope.py:475-477`). The dict includes
   `XY_stage_position_mm` (`ht_sols_microscope.py:435`), giving absolute stage
   coordinates per image. This is the same sidecar format `_metadata.py` already
   parses.
2. **Directory shape.** The plate run writes one folder with the usual
   `data/`, `metadata/`, `preview/` triple (`ht_sols_microscope.py:390-394`), reused
   across all wells, tiles and timepoints because `folder_name` is constant across
   the loop (template lines 71, 90).

### 6e. Correction to a premise in #19

#19 states that "a copy of this script is dropped into every plate acquisition
directory at run time". **No code in the vendor package does this.** Nothing copies
a `.py` file into an output folder; the only "drop this into the acquisition folder"
instruction is human-facing prose in `ht_sols_data_to_zarr.py:4`, and it refers to
the zarr conversion script, not the acquisition template. If such copies exist beside
real datasets, they are an operator habit, not a vendor guarantee — and so cannot be
relied on as an authoritative record.

---

## Verification status

- **Vendor source:** read directly at commit `32980ec`. All claims above are cited to
  it.
- **Loop ordering:** verified by replaying the generator's loop body standalone and
  diffing against the index arithmetic in Q3. Both the template's own arguments and a
  wider `A1`->`C3` 2x2-tile case reproduce exactly.
- **Real plate fixture: NOT verified.** No plate acquisition directory was reachable
  from this machine — the read-only dataset mounts were not attached at the time of
  writing, so no `*_multiwell_plate*` directory could be inspected. The predicted
  filename grammar and the predicted absence of `XY_stage_position_list.txt` are
  therefore **derived from source, not confirmed against data.** Anyone with a real
  plate dataset under `/mnt/readonly/<dataset>/` should confirm two things: that
  `data/` holds `NNNNNN_<Row><Col>r<NN>c<NN>.tif` files, and that no
  `XY_stage_position_list.txt` sits beside them.

## Open questions left for #21

- Whether the reader should recognise the plate filename grammar at all, and how it
  would relate to the existing `NNNNNN_pMMMMMM.tif` position handling.
- Whether the absent-and-unnamed plate format (Q2) needs to be inferred from
  `total_rows`/`total_cols`, which are not recorded anywhere on disk — note that the
  *plate format itself* is one of the few things a filename-only reader cannot
  recover, since labels reveal only the wells actually acquired.
- How to detect a plate run in the first place, given the directory-suffix drift in
  Q5.
