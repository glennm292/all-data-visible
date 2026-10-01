# DACTAL query language

DACTAL is a data-agnostic collation/transformation/analysis language that runs
entirely in the browser. A query starts with a datatype/dataset and chains
operators left to right. This is the authoritative reference distilled from the
engine (`dactal_utils.js` `DACTAL_OPERATORS` / `OPERATOR_VARIANTS`) so you do
not have to re-derive it.

## How to read a query

Read left to right; each operator transforms the current *list of items*.

```
teams:confed=UEFA #-count /group |flag=(.of.flag)
└ start  └ filter    └ sort  └ group └ annotate
```

- `teams` — start from the `teams` dataset (the list of all team items).
- `:confed=UEFA` — keep only items whose `confed` property equals `UEFA`.
- `#-count` — sort descending by `count`.
- `/group` — group the items by their `group` property (groups are themselves items).
- `|flag=(.of.flag)` — annotate each group with a `flag` property computed by a subquery.

A subquery in `(...)` runs against each current item; inside it, `.of` refers to
the members of a group, and `.` traverses a property of the current item.

## Operators

| Op | Name | Meaning |
|----|------|---------|
| `?` | start | Start with these items (usually a datatype or dataset). The leading `?` is implicit when a query begins with a dataset name. |
| `.` | traverse | Follow a shared property from each item to the unique resulting items. |
| `..` | traverse w/ duplicates | Traverse without deduplicating. |
| `...` | synthesize | Collect all items into one (to compute an aggregate). |
| `....` | synthesize + extract | Collect into one, compute, then go to that computed value. |
| `:` | filter | Keep only items matching a criterion. |
| `#` | sort | Sort the list. |
| `/` | group | Group by shared properties (groups are items). |
| `//` | merge | Merge items by shared properties. |
| `\|` | annotate | Add a new property to each item. |
| `!` | repeat | Repeat the previous operation. |
| `??` | label | Label a list for back-reference. |
| `???` | note | No-op; used for notes/comments (this is how tour narration is attached). |

### Sub-operators (the ones you will actually use)

Traverse (`.`):
- `.property` — traverse to unique items; `..property` keeps duplicates.
- `.property1,property2` — combine two properties; `.property1;property2` — first, else second.
- `.(subquery)` — traverse a subquery per item. `.~literal` — traverse to a literal.
- **`.=property` — traverse a property's raw value WITHOUT the cross-dataset auto-join** (see Joins).
- `.property@n` / `.property@@n` — first / last n per item.

Filter (`:`):
- `:property=value` `:property-=value` (not-equal) `:property+` (present) `:property-` (absent).
- `:=value` (name exact) `:~value` (name partial) `:-~value` (name non-match) `:[~regex]` (regex).
- `:>n` `:>=n` `:<n` `:<=n` `:=n` — numeric comparisons on the item's value.
- `:@1` (index) `:@<=10` (index range) `:@@1` (reverse index) `:@@<=10`.
- `:(subquery)` — keep items whose subquery is non-empty; `:-(subquery)` — keep items whose subquery is EMPTY (this is how you find orphans — see Joins).
- `:~value1,~value2` — any-of; `:~value1;~value2` — all-of.

Sort (`#`):
- `#property` (by property) `#-property` (reverse) `#+`/`#-` (numeric asc/desc) `#~` (unicode).
- `#property,-(subquery)` — multi-key, tie-broken. `#(subquery)` — sort by subquery result.
- `#@=(val1,val2,...)` — sort in a specified name order. `#rank=property` — sort and annotate rank.

Group / merge (`/`, `//`):
- `/property` `/key=property` `/key=(subquery)` `/property1,property2` (permutations) `/property1;property2` (combinations).
- `//property` merges items sharing a property; `//key=(subquery),+prop1,-prop2` accumulates/discards.

Annotate (`|`):
- `|property=(subquery)` — new property, list value. `|property=~(subquery)` — scalar value. `|property=~value` — literal.
- `|annotator` — apply a built-in annotator (e.g. `count`). `|property=annotator` — annotator under an explicit name.
- `|property=@` — list index; `|property=@@` — reverse index; `|prop=@property` — running rank.
- `|-property` — remove a property. `|newname-oldname` — rename. `|property;` — move to first column.
- `|property` applied to a literal value **converts it to an item** with that value (used in reverse-join qlinks).

Synthesize (`...`):
- `...` bare (collect into one). `...annotator` (aggregate, e.g. `count`). `...prop=annotator` (named).
- `....annotator` — synthesize and extract the scalar. `...property=(subquery)` / `...property=~value` — build fields on the synthesized item.
- Heatmap shape: `...xvals=(...),yvals=(...),vvals=(...)` (see connectors-and-viz.md).

Label / note:
- `??label` labels the current list; `??label=(subquery)`. `??? note` — a note (used for tour narration via `\n???note=[ ... ]`).

## Cross-dataset joins (the most important behavior)

DACTAL **auto-joins a property value to a same-named dataset**: if item `x` has a
property `team` whose value is `"Spain"`, then `x.team` resolves `"Spain"`
against the `teams` dataset (by `id`/`name`, singular/plural tolerant) and
returns the matching team *record*. So `players.team.teams` walks players → their
team string → the team record.

- **`.=property` traverses the RAW value** (no join) — use it when you want the
  string/number itself, not the joined record.
- **The reverse join** uses `|`: a scalar value like `"Spain"` becomes an item
  with `|team.players`, i.e. convert the value to an item carrying a `team`
  property, then join into `players` on the shared `team`. So `Spain|team.players`
  is "Spain's squad".

### USE A STABLE JOIN KEY, NOT DISPLAY NAMES

This is the single biggest silent-failure trap. Different source files drift on
display names: `"USA"` vs `"United States"`, `"Bosnia & Herzegovina"` vs
`"Bosnia and Herzegovina"`, `"Brazil"` vs `"Brasil"`. A join on a drifted name
**silently returns fewer rows** — no error. In the World Cup build this showed up
as "the players dataset only has players from 46 of 48 teams": two squads' team
names did not match the results source, so their players never joined.

Fix it in the **shaping** step (`build_data.py`), not the query: canonicalize
every dataset to one stable key (a FIFA/ISO code, an id) and derive display names
from that key. Join on the key; display the name.

Detect it with an **orphan-check query** and the query harness:

```
players:-(.team.teams)        # players whose team does NOT resolve to a teams row
```

On clean data this returns 0 (the good state). If it returns rows, you have a
drift bug. `run.py verify-queries` runs every saved query and reports this; the
buggy fixture in `tests/fixtures/` reproduces the exact undercount.

## DACTAL swallows errors — verify, do not assume

DACTAL is deliberately forgiving, which means bugs are silent:

- A **mistyped dataset name becomes a literal string**: `teamz` (typo of `teams`)
  returns `["teamz"]`, not an error.
- A **mistyped property is ignored**: `teams#-madeupprop` just does not sort.
- Most **malformed queries return something** rather than throwing.

So you cannot trust "it didn't error". Always run `run.py verify-queries`, which
classifies each query as `ok` (records) / `empty` (0 rows) / `literal` (bare
values — the typo signature) / `error` (threw). Treat `empty` and `literal` on a
browse/analyses query as suspects to investigate.

## Canonical qlink recipes

A cell renders as a clickable link when its value carries a `qlink` sub-property;
clicking runs `id` concatenated with `query`. Build qlinks the idiomatic way —
`id` is the item's value, `query` is a traversal — never by stitching a query
prefix into `id`.

**Forward join** (a player's team name links to that team's record):

```
players#-goals,number,team |team=(....qlink=(....id=~(.team),query=[.teams]))
```

`id` = the team value, `query` = `.teams` → clicking runs `<team>.teams`, the team record.

**Reverse join** (a team's "squad" links to its players):

```
teams#group,name |squad=(...qlink=(...id=(.name),query=[|team.players],text=~[squad »]))
```

`id` = the team name, `query` = `|team.players` → clicking runs `<team>|team.players`
(convert the name to an item, join into players on `team`). `text` sets the link label.

## `hide` and column-naming gotchas

- A saved query may carry `"hide": ["col", ...]` to drop columns from its rendered
  table without changing the query.
- **Do not name a display column the same as an annotator.** `|goals=count`
  misfires because `count` is read as an annotator; keep the native `count`
  column instead.
- **Do not name a column the same as a dataset** — it will auto-join.
