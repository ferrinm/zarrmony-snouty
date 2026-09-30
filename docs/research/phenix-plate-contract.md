# How `zarrmony-phenix` feeds zarrmony's HCS-plate writer

Research note for issue #20. Facts only — no recommendation for this plugin. The
decision belongs to issue #21.

## Scope and sources

Everything below was read from source in three sibling working copies. Citations
are `<repo>:<repo-relative-path>:<line>`. No URLs — name the file and read it
locally.

| Prefix | Repo | What it owns |
| --- | --- | --- |
| `zarrmony` | core package | the plate writer, the Reader Protocol, the `PlateLayout` dataclasses |
| `phenix` | `zarrmony-phenix` | the reference plate-shaped reader plugin |
| `snouty` | this repo | the flat multi-scene reader being compared against |

Primary sources, in the order they matter:

- `zarrmony:src/zarrmony/readers/plate.py` — the `PlateLayout` / `PlateField` /
  `Acquisition` dataclasses.
- `zarrmony:src/zarrmony/writers/plate.py` — the plate writer.
- `zarrmony:src/zarrmony/readers/plugin.py` — `ReaderProtocol`, `ReaderPlugin`,
  the registry.
- `zarrmony:src/zarrmony/writers/scene.py` — the per-image writer the plate
  writer reuses per FOV.
- `zarrmony:src/zarrmony/transforms.py` — axis normalization.
- `zarrmony:src/zarrmony/api.py` — layout dispatch and the plate conversion path.
- `zarrmony:docs/adr/0004-plate-output-design.md` — the design record.
- `zarrmony:docs/writing-a-reader-plugin.md` §9 — the written contract.
- `phenix:src/zarrmony_phenix/adapter.py` — the reference implementation.
- `snouty:src/zarrmony_snouty/session.py`, `snouty:CONTEXT.md` — the comparison.

---

## The checklist

What a Snouty plate reader must expose. Each item cites the line that reads it.

### Hard requirements — the conversion fails without these

- [ ] **`layout_hint = "plate"`** — a plain class attribute, typed
      `Literal["flat", "plate"]`. Read by `_resolve_layout`; under the default
      `layout="auto"` this is the only thing that routes to the plate writer.
      `zarrmony:src/zarrmony/readers/plugin.py:53`,
      `zarrmony:src/zarrmony/api.py:171-173`.
      Setting it without `plate_layout` is a hard error, and requesting
      `layout="plate"` against a `"flat"` reader raises `LayoutMismatchError`
      (`zarrmony:src/zarrmony/api.py:174-180`).
- [ ] **`plate_layout: PlateLayout`** — populated, not `None`.
      `zarrmony:src/zarrmony/api.py:1905-1909` raises `ZarrmonyError` when it is
      `None`. Declared on the Protocol at
      `zarrmony:src/zarrmony/readers/plugin.py:54`.
- [ ] **`scenes: list[str]`** — non-empty (`zarrmony:src/zarrmony/api.py:950-951`).
      `len(reader.scenes)` is the bound every `PlateField.scene_index` is
      validated against (`zarrmony:src/zarrmony/writers/plate.py:323`).
- [ ] **`set_scene(index: int) -> None`** — called once per FOV by the plate
      writer (`zarrmony:src/zarrmony/writers/plate.py:352`) and again inside
      `write_scene` (`zarrmony:src/zarrmony/writers/scene.py:643`). Must be
      idempotent for the same index.
- [ ] **`xarray_dask_data` property → `xr.DataArray`** — the pixels of the
      current scene. `zarrmony:src/zarrmony/readers/plugin.py:58-59`, consumed at
      `zarrmony:src/zarrmony/writers/scene.py:650`.
- [ ] **`physical_pixel_sizes` property** — any object with `.X` / `.Y` / `.Z`.
      Read per axis via `getattr`, and a missing or `None` axis degrades to
      `1.0` rather than raising
      (`zarrmony:src/zarrmony/writers/scene.py:392-402`).
- [ ] **`dtype` property → `np.dtype`** — **required on the plate path whenever
      `channel_names` is non-empty**, and *not* declared on `ReaderProtocol`.
      `zarrmony:src/zarrmony/writers/plate.py:196` calls
      `_dtype_window(reader.dtype)` unguarded, three lines after the
      `channel_names` early-return. See "Gaps" below — the Phenix reference does
      not define it.

### The `PlateLayout` value — exact types

All three dataclasses are `@dataclass(frozen=True)` and live in
`zarrmony:src/zarrmony/readers/plate.py`. They are also re-exported from
`zarrmony.readers.plugin` (`zarrmony:src/zarrmony/readers/plugin.py:193-206`).

- [ ] **`PlateLayout`** (`zarrmony:src/zarrmony/readers/plate.py:52-73`)
      - `name: str` — becomes `attrs.ome.plate.name`
        (`zarrmony:src/zarrmony/writers/plate.py:245`).
      - `rows: list[str]`
      - `columns: list[str]`
      - `acquisitions: list[Acquisition]` — defaults to `[]`.
      - `fields: list[PlateField]` — defaults to `[]`.
      - `plate_id: str | None` — defaults to `None`. Audit-only; deliberately
        **not** written into `attrs.ome.plate`, because the NGFF 0.5 plate schema
        does not define the key
        (`zarrmony:src/zarrmony/readers/plate.py:62-65`,
        `zarrmony:src/zarrmony/writers/plate.py:456-463`).
- [ ] **`Acquisition`** (`zarrmony:src/zarrmony/readers/plate.py:19-30`)
      - `id: int`, `name: str | None = None`,
        `maximumfieldcount: int | None = None`.
- [ ] **`PlateField`** (`zarrmony:src/zarrmony/readers/plate.py:33-49`)
      - `scene_index: int`, `row: str`, `column: str`,
        `field_name: str | None = None`, `acquisition_id: int | None = None`.

### Structural rules the writer enforces before any pixel is written

`validate_plate_layout` runs first thing in `write_plate` and raises
`PlateLayoutError` (`zarrmony:src/zarrmony/writers/plate.py:117-160`, called at
`:323`).

- [ ] **At most one `Acquisition`.** v1 is single-acquisition; more raises
      (`zarrmony:src/zarrmony/writers/plate.py:126-130`).
- [ ] **Every `PlateField.row` appears in `PlateLayout.rows`**, every
      `.column` in `.columns` — exact string match, no normalization
      (`zarrmony:src/zarrmony/writers/plate.py:137-145`).
- [ ] **`scene_index` in `[0, len(reader.scenes))`**
      (`zarrmony:src/zarrmony/writers/plate.py:146-149`).
- [ ] **`scene_index` unique across all fields** — a duplicate would double-write
      the same source data into two well paths
      (`zarrmony:src/zarrmony/writers/plate.py:150-155`).
- [ ] **`acquisition_id`, when set, names a declared `Acquisition.id`**
      (`zarrmony:src/zarrmony/writers/plate.py:156-160`).
- [ ] **Every scene is referenced by some `PlateField`** — otherwise a
      `LayoutDowngradeWarning` fires and the unreferenced scenes are silently not
      written (`zarrmony:src/zarrmony/writers/plate.py:325-339`).

### Soft-optional surfaces — read through `getattr` or `try`/`except`

Absence degrades; it never fails the run.

- [ ] `channel_names: list[str]` — when empty or absent, the writer hands
      `channels=None` to `write_scene`, which then derives names from the `C`
      coordinate or synthesises `C:0`, `C:1`
      (`zarrmony:src/zarrmony/writers/plate.py:190-193`,
      `zarrmony:src/zarrmony/writers/scene.py:680-688`).
- [ ] `ome_metadata` — fetched per FOV inside a bare `try`; any exception, `None`,
      or an empty `images` list produces a stub `Image` plus an `ExtractorWarning`
      (`zarrmony:src/zarrmony/api.py:235-252`, `:1916-1926`).
- [ ] `metadata` — the raw vendor blob. Serialized
      (`zarrmony:src/zarrmony/api.py:202-211`) and written once at the plate root
      as `OME/source/raw.<ext>.xml`
      (`zarrmony:src/zarrmony/api.py:648-650`,
      `zarrmony:src/zarrmony/writers/plate.py:478-483`).
- [ ] `acquisition_audit` — the zarrmony #76 hook this plugin already exposes
      (`snouty:src/zarrmony_snouty/adapter.py:271-272`).
      Reached through the same `audit_acquisition_for_field` callback the flat
      path uses (`zarrmony:src/zarrmony/api.py:1941-1944`,
      `zarrmony:src/zarrmony/writers/plate.py:433-436`).
- [ ] `used_files` — sequence or callable; feeds the audit's file set
      (`zarrmony:src/zarrmony/_inputs.py:43-63`).
- [ ] `available_plates` — only consulted for the multi-plate ambiguity guard
      (`zarrmony:src/zarrmony/api.py:143`, `:2109-2111`).
- [ ] `close()`.
- [ ] `is_mosaic_reassembly_eligible()` / `tiles_xarray_dask_data` /
      `mosaic_summary` — LIF-only. The plate writer reaches the first through
      `getattr(reader, ..., lambda: False)`, so a reader that does not define it
      skips the whole grid-stitch branch
      (`zarrmony:src/zarrmony/writers/plate.py:362-364`).

### What the writer produces from all this

- `<store>/zarr.json#attrs.ome.plate` — `name`, `rows`, `columns`, `wells`,
  `version`, optional `acquisitions`, `field_count`
  (`zarrmony:src/zarrmony/writers/plate.py:221-255`, written at `:453-454`).
- `<store>/<row>/` — structural group, no attrs by spec
  (`zarrmony:src/zarrmony/writers/plate.py:466`).
- `<store>/<row>/<column>/zarr.json#attrs.ome.well.images` — one entry per field
  (`zarrmony:src/zarrmony/writers/plate.py:258-266`, `:468-472`).
- `<store>/<row>/<column>/<seq>/` — one full OME-Zarr image per FOV
  (`zarrmony:src/zarrmony/writers/plate.py:349-350`).
- `<store>/OME/METADATA.ome.xml` — one combined file for the whole plate; there
  are deliberately no per-FOV sidecars
  (`zarrmony:src/zarrmony/writers/plate.py:474-476`,
  `zarrmony:docs/adr/0004-plate-output-design.md:11`).
- No `bioformats2raw.layout` marker
  (`zarrmony:src/zarrmony/writers/plate.py:9-10`).

---

## Answers to the six questions

### 1. The contract

`plate_layout` is the whole plate-specific surface. Beside it sits exactly one
other plate-specific attribute, `layout_hint`; everything else the plate path
reads is the ordinary flat-reader surface plus one undeclared extra (`dtype`).

The full read set, with exact types, is the checklist above. Condensed:

| Name | Type | Required? | Read at |
| --- | --- | --- | --- |
| `layout_hint` | `Literal["flat", "plate"]` | yes | `api.py:171` |
| `plate_layout` | `PlateLayout` | yes | `api.py:1905` |
| `scenes` | `list[str]` | yes | `api.py:950`, `writers/plate.py:323` |
| `set_scene` | `(int) -> None` | yes | `writers/plate.py:352` |
| `xarray_dask_data` | `xr.DataArray` | yes | `writers/scene.py:650` |
| `physical_pixel_sizes` | object with `.X/.Y/.Z` | yes | `writers/scene.py:392-402` |
| `dtype` | `np.dtype` | yes, if `channel_names` non-empty | `writers/plate.py:196` |
| `channel_names` | `list[str]` | optional | `writers/plate.py:190` |
| `ome_metadata` | `ome_types.OME` | optional | `api.py:239` |
| `metadata` | `str` / `Element` / `OME` | optional | `api.py:1946` |
| `acquisition_audit` | `dict` | optional | `api.py:1941-1944` |
| `used_files` | `Sequence[str]` or callable | optional | `_inputs.py:43` |
| `available_plates` | `list[str]` | optional | `api.py:2109` |

There is no string-parsing fallback anywhere: `plate_layout` is consumed
directly, and ADR-0004 records that parsing plate coordinates out of scene names
was considered and rejected
(`zarrmony:docs/adr/0004-plate-output-design.md:8`,
`zarrmony:docs/writing-a-reader-plugin.md:582-588`).

### 2. Scene naming

**The writer never reads a scene name to learn a plate coordinate.**

Phenix scene names are vendor-native field labels — `F001`, `F002`, … —
formatted by `_field_name` (`phenix:src/zarrmony_phenix/adapter.py:44-45`) and
assigned at `phenix:src/zarrmony_phenix/adapter.py:120`. They are **not unique
across wells**: every well restarts at `F001`
(`phenix:tests/test_plate_layout.py:118-129` shows the same three labels for one
well; the per-scene-fallback test at `:322` exists precisely because the labels
collide). The module docstring states the intent outright — "Scene names are
vendor-native (`F001`, `F002`, ...); plate coordinates live on `plate_layout`,
not the scene name"
(`phenix:src/zarrmony_phenix/adapter.py:4-6`).

On the plate path a scene name is used in exactly two places:

1. As the multiscales image name, and only as the fallback when `field_name` is
   `None`: `image_name = f.field_name or reader.scenes[f.scene_index]`
   (`zarrmony:src/zarrmony/writers/plate.py:354`).
2. As `scene_name` in the audit record
   (`zarrmony:src/zarrmony/writers/scene.py:644`, `:771`).

The on-disk path is `<row>/<column>/<seq>` with `seq` a writer-assigned integer
(`zarrmony:src/zarrmony/writers/plate.py:349`) — never the scene name, and never
`field_name` either (`zarrmony:docs/writing-a-reader-plugin.md:571-580`).

**Comparison with this plugin.** Snouty scenes are named `<acquisition-dir>` for
single-position acquisitions and `<acquisition-dir>__p<index:06d>` for
multi-position (`snouty:src/zarrmony_snouty/session.py:190-194`; the *Scene*
entry at `snouty:CONTEXT.md:35-37`). Two differences of fact:

- Snouty names are globally unique by construction; Phenix names are not.
- Snouty names carry the disambiguating structure in the name; Phenix carries it
  only in `plate_layout`.

Neither difference is load-bearing for the plate writer. Its only uniqueness
requirement is on `scene_index` (`zarrmony:src/zarrmony/writers/plate.py:150-155`),
not on scene names. The existing convention survives a plate reader unchanged; it
would simply become the audit `scene_name`, and the image name too if
`field_name` is left `None`.

### 3. Row and column encoding

Both `row` and `column` are `str`, and both are **opaque to the writer** — it
does membership checks and derives `rowIndex` / `columnIndex` as positions in the
declared lists (`zarrmony:src/zarrmony/writers/plate.py:225-234`). The strings
themselves become directory names.

What Phenix actually emits:

- **Rows are letters.** `_row_to_letter` is `chr(ord("A") + row - 1)` over a
  1-based vendor row index (`phenix:src/zarrmony_phenix/adapter.py:40-41`),
  applied across the full physical range at
  `phenix:src/zarrmony_phenix/adapter.py:122`.
- **Columns are 1-based, zero-padded to width 2, as strings.**
  `[f"{c:02d}" for c in range(1, md.plate_columns + 1)]`
  (`phenix:src/zarrmony_phenix/adapter.py:123`). So `"01"`, not `1` and not
  `"1"`. Asserted at `phenix:tests/test_plate_layout.py:73-76` (`row == "B"`,
  `column == "04"`).

This is not a Phenix quirk — zarrmony's own LIF plate extractor normalizes to the
same shape and names `zarrmony-phenix` as the convention it is matching:
"single uppercase letters, width-2 zero-padded numeric columns — `zarrmony-phenix`
convention" (`zarrmony:src/zarrmony/metadata/lif_plate.py:18-22`, helpers at
`:103-108`, documented shape at `:197-198`).

Casing and padding are preserved verbatim end to end; `parse_well_key` explicitly
does not normalize, and the docstring notes `"b04"` or `"B4"` will fail
downstream membership checks against an upper/zero-padded plate
(`zarrmony:src/zarrmony/writers/plate.py:86-99`).

**Wells that were not acquired are absent, not empty.**

- `rows` and `columns` MUST list every *physical* row and column even when only
  some are imaged. That is the reader's job, stated in the dataclass docstring
  (`zarrmony:src/zarrmony/readers/plate.py:56-58`), in ADR-0004
  (`zarrmony:docs/adr/0004-plate-output-design.md:24`) and in the plugin guide
  (`zarrmony:docs/writing-a-reader-plugin.md:552-556`).
- The `wells` list, and the on-disk groups, are built **only** from wells that
  have at least one `PlateField`: `_group_fields_by_well` groups the fields, and
  both `_build_plate_attr` and the group-creation loop iterate that grouping
  (`zarrmony:src/zarrmony/writers/plate.py:163-175`, `:213-214`, `:227-234`,
  `:465-472`). An unimaged well gets no `wells` entry, no row group and no well
  group — but its row letter and column number still appear in `plate.rows` and
  `plate.columns`.
- Confirmed end to end for a sparse 96-well plate at
  `phenix:tests/test_plate_layout.py:86-101`: `rows == ["A".."H"]`,
  `columns == ["01".."12"]`, six `PlateField`s.

### 4. Fields

**A field is a separate image, not an extra axis.** One `PlateField` per FOV,
each with its own `scene_index` into `reader.scenes`.

- The writer groups fields by `(row, column)` preserving first-occurrence order
  (`zarrmony:src/zarrmony/writers/plate.py:163-175`), then writes each field of a
  well to a sequential integer subpath `0`, `1`, `2`, … in
  `plate_layout.fields` order (`zarrmony:src/zarrmony/writers/plate.py:347-350`).
- The well group's `images` list mirrors that:
  `[{"path": "0", "acquisition": 1}, {"path": "1", ...}]`
  (`zarrmony:src/zarrmony/writers/plate.py:258-266`).
- `plate.field_count` is the maximum field count over all wells
  (`zarrmony:src/zarrmony/writers/plate.py:254`).
- Ordering is the reader's: the writer preserves `plate_layout.fields` order and
  does not sort.

Phenix builds one `PlateField` per `(row, col, field)` triple, flattening
`OperaPhenixReader.well_field_map` in sorted well order and native field order
(`phenix:src/zarrmony_phenix/adapter.py:102-105`, `:124-133`); asserted at
`phenix:tests/test_plate_layout.py:118-129`.

One FOV is exactly one image by spec, which is why plate output rejects LIF
per-tile mode outright (`zarrmony:src/zarrmony/api.py:967-972`) and why explicit
stage-stitch is refused under plate (`zarrmony:src/zarrmony/api.py:981-987`).

### 5. Per-image axes — yes, a full `(T, C, Z, Y, X)` array per field image

This is the answer the `mode` kwarg question turns on, so here is the chain.

- **Phenix hands the writer a full 5-D array per field.**
  `xarray_dask_data` returns
  `xr.DataArray(darr, dims=("T", "C", "Z", "Y", "X"), coords=...)`, built from
  `_read_images_lazy(row, col, [field], md.timepoints, md.channel_ids, md.planes)`
  and chunked `(1, 1, 1, h, w)`
  (`phenix:src/zarrmony_phenix/adapter.py:184-200`, dims at `:200`, chunks at
  `:191`). Every axis is per-field: one field, all timepoints, all channels, all
  planes.
- **The plate writer does no axis handling of its own.** Per FOV it calls the
  very same `write_scene` that flat per-scene output uses
  (`zarrmony:src/zarrmony/writers/plate.py:404-414`). ADR-0004 records that as a
  deliberate rejection of a separate plate pixel path: "a plate FOV is
  structurally identical to a scene in per-scene / bf2raw layouts"
  (`zarrmony:docs/adr/0004-plate-output-design.md:10`).
- **`write_scene` accepts any subset of `T, C, Z, Y, X`, in any order.**
  It calls `normalize_axes`, which transposes to the canonical
  `NGFF_AXIS_ORDER = ("T", "C", "Z", "Y", "X")` and raises `UnsupportedAxesError`
  only for axes outside that set
  (`zarrmony:src/zarrmony/transforms.py:21`, `:88-122`;
  `zarrmony:src/zarrmony/writers/scene.py:650-654`). Axis names, types and units
  are then derived mechanically per dim
  (`zarrmony:src/zarrmony/writers/scene.py:691-693`).

**So T and C compose per image with no plate-specific handling.** The plate
writer's entire per-FOV contribution beyond `write_scene` is:

1. Building the channel list from `reader.channel_names` plus `reader.dtype`
   (`zarrmony:src/zarrmony/writers/plate.py:178-200`) — the same emission-band
   colouring the flat path does.
2. The LIF grid-stitch branch, gated on `is_mosaic_reassembly_eligible` and
   therefore inert for a reader that does not define it
   (`zarrmony:src/zarrmony/writers/plate.py:362-402`).
3. Stamping `row` / `column` / `well_id` / `field_path` / `field_name` /
   `acquisition_id` onto the returned audit record
   (`zarrmony:src/zarrmony/writers/plate.py:415-424`).

**Consequence for this plugin's `mode` kwarg.** `mode` is
`Literal["raw", "desheared", "traditional"]`
(`snouty:src/zarrmony_snouty/adapter.py:48-49`). Everything it changes is inside
`xarray_dask_data` and `physical_pixel_sizes` — the output Z/Y/X shape
(`snouty:src/zarrmony_snouty/adapter.py:218-226`), the delayed per-volume
transform (`:228-237`) and the Z spacing (`:239-256`). It touches neither `scenes`,
`set_scene`, nor any attribute the plate writer reads. Both Snouty readers
already emit `dims=("T", "C", "Z", "Y", "X")`
(`snouty:src/zarrmony_snouty/adapter.py:198-204`, delegated by
`snouty:src/zarrmony_snouty/session.py:269-271`), which is exactly what Phenix
emits. Nothing in the plate path distinguishes them.

Two facts worth carrying into #21:

- The chunk planner pins T and C to `1` unconditionally, on every layout
  (`zarrmony:src/zarrmony/writers/scene.py:135-139`). That is a property of the
  geometry policy, not of plate mode.
- No zarrmony writer reads `DataArray.attrs`. The only `attrs` writes anywhere in
  `src/zarrmony/writers/` are the store's own `ome` keys
  (`zarrmony:src/zarrmony/writers/plate.py:454`, `:469`;
  `zarrmony:src/zarrmony/writers/scene.py:559`, `:575`). So this plugin's
  `{"zarrmony": {"stage": {"xy_mm": [...]}}}` scene attrs
  (`snouty:src/zarrmony_snouty/adapter.py:206-216`) are dropped today on the
  flat path and would be dropped identically on the plate path — unchanged, not
  a plate regression.

### 6. Reader Protocol surface — same protocol, no base class

**No.** There is one Protocol and it covers both paths.

- `ReaderProtocol` is a single `@runtime_checkable` structural `Protocol`
  (`zarrmony:src/zarrmony/readers/plugin.py:40-62`). `layout_hint` and
  `plate_layout` are declared on it directly, at `:53-54`, alongside `scenes` and
  `set_scene`. There is no `PlateReaderProtocol`.
- Nothing inherits from it. `PhenixReader` is a plain class with no bases
  (`phenix:src/zarrmony_phenix/adapter.py:95`), exactly like
  `SnoutySessionReader` (`snouty:src/zarrmony_snouty/session.py:118`) and
  `SnoutyReader` (`snouty:src/zarrmony_snouty/adapter.py:136`). Structural typing
  throughout.
- Registration is identical for both shapes: one
  `ReaderPlugin(name=..., match=..., open=...)` object exported under the
  `zarrmony.readers` entry-point group
  (`zarrmony:src/zarrmony/readers/plugin.py:34`, `:65-86`;
  `phenix:src/zarrmony_phenix/__init__.py:23-29`, entry point at
  `phenix:pyproject.toml:18-19`). The Snouty plugin registers the same way.
- `match()` is unchanged: a cheap, side-effect-free directory predicate returning
  a score (`phenix:src/zarrmony_phenix/match.py:15-25`).
- The tile-alignment reopen in `convert()` is skipped for any plugin other than
  the built-in default, so it is a no-op for both
  (`zarrmony:src/zarrmony/api.py:774-775`).

The delta is two attribute values and one constructed object. This plugin already
declares both attributes at their flat settings —
`layout_hint = "flat"` and `plate_layout = None` at
`snouty:src/zarrmony_snouty/session.py:127-128` and
`snouty:src/zarrmony_snouty/adapter.py:137-138`.

---

## Gaps and v1 limits found while reading

Facts, not recommendations.

1. **`reader.dtype` is required by the plate writer but is not on the Protocol,
   and the Phenix reference does not define it.**
   `zarrmony:src/zarrmony/writers/plate.py:196` reads `reader.dtype` with no
   guard whenever `channel_names` is non-empty.
   `ReaderProtocol` does not list it
   (`zarrmony:src/zarrmony/readers/plugin.py:41-62`), and `PhenixReader` has no
   `dtype` property — its only `dtype` mentions are the FFC `np.float32` cast
   (`phenix:src/zarrmony_phenix/adapter.py:197`) and prose. The reference has not
   been exercised against current zarrmony: `phenix:uv.lock` resolves zarrmony
   `0.10.0`, and its plate tests still pass `permissive=True`
   (`phenix:tests/test_plate_layout.py:267`, `:297`, `:336`), a `convert()` kwarg
   current zarrmony no longer accepts. Both Snouty readers already expose `dtype`
   (`snouty:src/zarrmony_snouty/adapter.py:258-265`,
   `snouty:src/zarrmony_snouty/session.py:281-283`).
2. **v1 is single-acquisition and single-plate.** The writer asserts
   `len(acquisitions) <= 1` (`zarrmony:src/zarrmony/writers/plate.py:126-130`);
   `plate_layout` is one `PlateLayout`, not a list
   (`zarrmony:docs/adr/0004-plate-output-design.md:9`, `:23`). Phenix degrades by
   keeping only the first acquisition's fields and emitting a
   `LayoutDowngradeWarning` (`phenix:src/zarrmony_phenix/adapter.py:107-117`).
   The documented alternative is `layout_hint = "flat"`
   (`zarrmony:docs/writing-a-reader-plugin.md:590-609`).
3. **`field_name` never reaches the filesystem.** It is audit plus multiscales
   name only (`zarrmony:src/zarrmony/readers/plate.py:40-43`,
   `zarrmony:docs/writing-a-reader-plugin.md:571-580`).
4. **`acquisition_id` is written into `well.images[i].acquisition`** whenever it
   is not `None` (`zarrmony:src/zarrmony/writers/plate.py:261-265`), even though
   ADR-0004 describes it as reserved for v2
   (`zarrmony:docs/adr/0004-plate-output-design.md:9`).
5. **`validate=True` checks the store against the v0.5 HCS schema** rather than
   the image schema (`zarrmony:src/zarrmony/_validate.py:54`, `:86-87`).
6. **Two helpers exist for adapters**: `parse_well_key` and
   `summarize_plate_layout` (`zarrmony:src/zarrmony/writers/plate.py:86-99`,
   `:203-218`; documented at `zarrmony:docs/writing-a-reader-plugin.md:611-623`).
   `summarize_plate_layout` is how `inspect()` shows plate structure without
   touching pixels.

## Worked reference: the whole Phenix plate surface in one place

For side-by-side reading, `phenix:src/zarrmony_phenix/adapter.py` exposes exactly
this and nothing else:

| Member | Line | Note |
| --- | --- | --- |
| `layout_hint = "plate"` | `:96` | class attribute |
| `scenes` | `:120` | `["F001", "F002", ...]`, one per FOV |
| `plate_layout` | `:134-140` | built in `__init__`, never lazily |
| `set_scene` | `:180-181` | stores an index, nothing more |
| `xarray_dask_data` | `:183-200` | `(T, C, Z, Y, X)` per field |
| `physical_pixel_sizes` | `:202-210` | µm, `None` where unknown |
| `channel_names` | `:212-215` | |
| `metadata` | `:217-219` | raw index XML text |
| `acquisition_audit` | `:221-237` | zarrmony #76 hook |

No `dtype`, no `close`, no `ome_metadata`, no `used_files`.
