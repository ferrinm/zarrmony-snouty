# ADR-0003: Detect a Snouty plate from filenames, and infer the grid by snapping to a standard format

Date: 2026-10-05
Status: Accepted. Amended 2026-10-06 — see
[Amendment (2026-10-06)](#amendment-2026-10-06).

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

## Amendment (2026-10-06)

### A1. The name problem is not specific to plates (#34)

This ADR's Context says that `match.py` identifies every Snouty input by
directory name, and treats the plate matcher as the one exception. A survey of
the whole share refutes the premise behind that exception. The name problem
belongs to every acquisition kind, not to plates.

Counting every directory on the share that holds both `data/` and `metadata/`:

| count | shape of the name a name-based matcher rejects |
| ----- | ---------------------------------------------- |
| 148 | a `snap` / `acquire` token, but not as the exact suffix |
| 24 | `..._ht_sols_acquisition_<freetext>` |
| 15 | no token at all — `..._ht_sols_psf_<freetext>`, `..._ht_sols_grid` |
| 7 | `..._ht_sols_tile`, a third acquisition kind |

That is 195 of 321 directories, or 61%. A further 39 directories that hold
acquisition children fail `match_session`, 25 of them because the GUI wrote
`_ht_sols_gui_session` and the matcher tested for `_ht_sols_gui` as the exact
suffix.

The "Match on the directory name" alternative below was rejected for plates on
10 acquisitions of evidence. The same argument now carries on 195.

### A2. All three matchers test contents (#34)

Decision 1 above is widened to the whole module. No matcher tests a directory
name.

`match` fires when `data/` and `metadata/` both exist, `data/` holds a `.tif`,
and the first `metadata/*.txt` carries a quorum of the key set that
`_metadata.parse_metadata_file` requires. `match_session` fires when an
immediate child satisfies `match`. The scores are unchanged, so a plate still
outranks both at 200.

### A3. The matcher tests identity, the reader tests validity (#34)

The quorum is `SIDECAR_KEY_QUORUM` of nine keys, not all nine. This is
deliberate and it is the subtle part.

A matcher answers *is this Snouty*. It must not answer *is this Snouty and
undamaged*. An acquisition whose sidecar lost a key is still a Snouty
acquisition, and it must match, so that `SnoutyReader` opens it and raises
`SnoutyMetadataError` naming the key that is absent. A matcher that demanded
all nine would reject it, zarrmony would fall through to bioio, and the user
would get `UnsupportedFileFormatError` — the error that names nothing and that
#34 exists to remove. Nine keys of Snouty vocabulary make a false positive
against another vendor unlikely at a quorum of six.

### A4. A matcher now reads one file

This ADR and the `match.py` docstring both said that a matcher parses no
metadata. That rule is withdrawn. `match` reads one sidecar of about 1 KB, and
it reads at most one however many the directory holds.

The rule was a cost rule, and the cost is paid elsewhere already: `match_plate`
lists up to 3456 directory entries on a 384-well plate, on the same network
mount. One small read is cheaper than that. A hard size cap keeps the read
bounded when a directory holds a large `.txt`.

### A5. A session is whatever holds acquisitions

`match_session` no longer tests the parent name. Any directory holding a valid
acquisition child converts as a session: N children in, N stores out.

This claims operator-made grouping folders that hold unrelated runs. The share
has a few, named for the sample rather than for a session. Converting one emits
one store per child, which is the right output, so the behaviour is accepted
rather than worked around.

The session reader's candidate rule changes with it. A candidate is any child
holding a `data/` or a `metadata/` directory. Selecting on that shape rather
than on full validity is what preserves the per-child skip warnings: a
half-written child stays a candidate, so it is named in a
`SnoutySubdirSkippedWarning` instead of disappearing. A new reason token,
`not_a_snouty_sidecar`, covers a child that is not Snouty at all.

### A6. Consequences

The whole module is now content-addressed, so the hard-to-reverse note in
Consequences above applies to every input kind and not only to plates. What a
user can convert is now fixed by the sidecar key set and by
`SIDECAR_KEY_QUORUM`. Raising the quorum later drops damaged acquisitions that
convert today.

False positives are measured on a Snouty share only. No test exercises another
vendor's `data/` plus `metadata/` layout against the quorum. This limit is
known and accepted.

## References

- #6 — the build ticket this ADR specifies.
- #19 — vendor template research. Reported grammar A. Did not see grammar B,
  because no real plate data was reachable at the time.
- #20 — the zarrmony HCS contract.
- #21 — the decision ticket that produced this ADR.
- Amendment source: #34 — the share survey, and the decision to widen
  content-based detection to every matcher.
