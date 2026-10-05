# ADR-0003: Detect a Snouty plate from filenames, and infer the grid by snapping to a standard format

Date: 2026-10-05
Status: Accepted

Two decisions, recorded together because the second depends on the first. A
Snouty plate acquisition is identified by the shape of its filenames and never
by its directory name. The physical plate grid is not recorded anywhere on
disk, so the reader infers it by snapping the observed well extent up to the
smallest standard plate format that contains it.

## Context

`match.py` identifies every Snouty input by directory name. An acquisition ends
in `_ht_sols_snap` or `_ht_sols_acquire`. A session ends in `_ht_sols_gui`. Both
names come from the vendor GUI, which writes them itself.

Plate acquisitions do not come from the GUI. An operator copies a python
template, edits literals in it, and runs it. The output directory name is one of
those literals:

```python
folder_label = 'ht_sols_acquisition_multiwell_plate_<operator>_<sample>'
```

A survey of the read-only share found 10 plate acquisitions under
`/mnt/readonly/<dataset>/<operator>/`. The evidence against a name-based
matcher is direct:

- Six directories end in `ht_sols_acquisition_multiwell_plate` plus free text.
- Two carry **no plate token at all**. One is named
  `..._000_ht_sols_acquisition_<operator>_<sample>`.
- The vendor template at HEAD builds a third name,
  `ht_sols_multiwell_plate`. No acquisition on the share uses it.

The survey also found **two filename grammars**, not one.

**Grammar A** comes from the vendor function `get_multiwell_plate_positions`.
Seven acquisitions use it.

```
000000_A01r00c00.tif
^      ^  ^  ^  ^
|      |  |  |  +-- field column, 0-based
|      |  |  +----- field row, 0-based
|      |  +-------- well column, 1-based
|      +----------- well row, letter
+------------------ timepoint
```

**Grammar B** comes from a hand-rolled operator loop,
`well = 'r%02ic%02i' % (r, c)`. Three acquisitions use it.

```
000000_r00c00.tif
^      ^  ^
|      |  +-- well column, 0-based
|      +----- well row, 0-based
+------------ timepoint
```

The two collide. Grammar B's `r00c00` is byte-identical to grammar A's field
suffix, but it names a **well**. A parser that searches for `r\d\dc\d\d` reads a
384-well grammar-B plate as 384 fields of one well.

### The embedded script is not a usable source

Every plate directory on the share contains a copy of the acquisition script.
The vendor does not copy it; the operator does. The script is still not
trustworthy as a plate map.

One acquisition proves it. The script loops `c in range(11, 13)` and
`r in range(4, 14)`, which reads as a rectangle of rows 4 to 13. The acquired
wells are rows 2 to 11 in column 11, and rows 4 to 13 in column 12. Both are
correct. The template reverses `col_positions` for odd columns, then indexes
that reversed list by `r`, so for an odd column the label is row `15 - r`. The
operator's apparent rectangle is not what the microscope imaged.

The filenames record the truth. The script records the intent. They differ.

### Nothing on disk records the plate size

Both grammars record **absolute** well coordinates, so the observed maximum is a
lower bound on the grid and never the grid itself. The sparse acquisition above
is 20 wells on a 384-well plate. Its observed extent is 12 rows by 2 columns.

zarrmony's writer requires the full physical grid. `PlateLayout.rows` and
`PlateLayout.columns` "MUST list every physical row/column of the plate even
when only some are imaged" (`zarrmony/readers/plate.py`). Unimaged wells are
absent from `wells`, but their row and column names still appear.

## Decision

**1. The plate matcher tests contents only. It performs no test on the
directory name.**

It fires when the path is a directory, `data/` and `metadata/` both exist, and
every `.tif` in `data/` matches the same one of two grammars:

```python
PLATE_A = r"^(\d{6})_(?P<row>[A-P])(?P<col>\d{2})r(?P<frow>\d{2})c(?P<fcol>\d{2})\.tif$"
PLATE_B = r"^(\d{6})_r(?P<row>\d{2})c(?P<col>\d{2})\.tif$"
```

A directory that mixes the two grammars raises `SnoutyDataError`. So does a
single `.tif` that matches neither. The matcher returns 200, above the 100 that
`match` and `match_session` return, so a plate wins if an operator ever names a
plate directory with a GUI suffix.

**2. The reader infers the grid by snapping the observed well extent up to the
smallest standard plate format that contains it.**

| wells | rows x columns |
| ----- | -------------- |
| 6 | 2 x 3 |
| 12 | 3 x 4 |
| 24 | 4 x 6 |
| 48 | 6 x 8 |
| 96 | 8 x 12 |
| 384 | 16 x 24 |
| 1536 | 32 x 48 |

A `plate_format` constructor keyword overrides the table lookup. It takes a well
count that the table lists, and it is rejected if it is smaller than the
observed extent. If no table entry contains the observed extent, the reader
emits `SnoutyPlateFormatWarning` and falls back to the observed bounding box.

**3. The embedded acquisition script is never read.**

## Consequences

The matcher now claims directories by content rather than by name. A
script-driven acquisition with plate-shaped filenames is converted as a plate
whatever the operator calls it. This is the intended behaviour and it is the
hard-to-reverse part: widening or narrowing the grammars later changes which
directories existing users can convert.

Grammar B costs a label translation. Its 0-based indices map to the OME-NGFF
convention as row `0` to `"A"` and column `0` to `"01"`, which matches
`zarrmony-phenix`. A grammar-B field carries `field_name=None`, because in that
grammar `r00c00` is the well and not the field.

**Snapping can under-report a plate.** A 384-well plate imaged only in `A1` to
`H12` has an extent of 8 by 12, which snaps to 96. The output then declares a
96-well plate. This is a real and accepted failure mode. The `plate_format`
keyword is the escape hatch, and it is the reason that keyword exists.

Snapping writes a fact that is on no disk anywhere. A reader of the output
cannot tell an inferred grid from a measured one. The alternative was to report
the observed bounding box, which is honest about the read and wrong about the
plate, and which would have labelled a 384-well plate as a 12 by 2 plate.

A plate acquisition is never a child of a GUI session, so `SnoutySessionReader`
learns nothing about plates.

## Considered alternatives

**Match on the directory name.** This is what `match.py` does everywhere else,
and it is cheaper. Real data refutes it. Two of 10 plate acquisitions carry no
plate token, and the one token that six acquisitions share is the token the
vendor template no longer writes.

**Match on the name as a fast path, confirmed by contents.** This has two
possible meanings and both fail. If a name miss rejects, the matcher loses the
two unmarked acquisitions. If a name miss only skips an optimization, it buys
nothing, because the content probe is one `iterdir()` and one regex.

**Parse the embedded script for the grid size.** The script is present in all 10
acquisitions, and it declares `total_rows` and `total_cols` directly. It is
rejected because one acquisition on the share proves that the script and the
data disagree about which wells were imaged. A source that is wrong about the
wells is not a source to trust about the grid.

**One permissive regex covering both grammars.** Rejected. Such a regex cannot
tell a well from a field, and that distinction is the entire plate map.

**Report the observed bounding box as the grid.** Rejected. It violates
zarrmony's stated contract for `rows` and `columns`, and it reports a sparsely
imaged 384-well plate as a small dense plate.

## References

- #6 — the build ticket this ADR specifies.
- #19 — vendor template research. Reported grammar A. Did not see grammar B,
  because no real plate data was reachable at the time.
- #20 — the zarrmony HCS contract.
- #21 — the decision ticket that produced this ADR.
