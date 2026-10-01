# Export format, the analyses tour, serving, and verification

## The DACTAL export / database format

An explorer is one self-contained JSON file:

```json
{
  "export": "<dbname>",
  "data": {
    "<dataset1>": [ {row}, {row}, ... ],
    "<dataset2>": [ ... ],
    "connectors": [ {"connector": "dactal_chart.js", "description": "..."}, ... ]
  },
  "queries": [
    {"name": "...", "query": "...", "tag": "browse|analyses", "order": 1, "hide": ["col"]}
  ]
}
```

- **`export`** — the database name. The stock front-end loads it from
  `/data/<dbname>.json` on the page origin and opens with `dactal.html?dbname=<dbname>`.
- **`data`** — a map of dataset name → array of row objects. Each row is a plain
  object; give every row a stable `id`. The special **`connectors`** dataset lists
  the renderer/data connectors to load (see connectors-and-viz.md).
- A dataset value may also be a **filename string** instead of an inline array;
  the front-end then loads it from `/data/<filename>` on the same origin. A
  self-contained export inlines everything (one file, nothing else to ship).
- **`queries`** — saved queries shown in the front-end's saved-query menu, grouped
  by `tag`. `hide` drops columns from the rendered table.

## Cross-linking (why datasets join)

Because DACTAL auto-joins a property value to a same-named dataset (see
dactal-query-language.md), simply naming a property after a dataset makes it a
link: a match's `team1` resolves to a `teams` row, a goal's `scorer` to a
`players` row. Shape your datasets so the shared keys line up — **on stable keys,
not display names**.

## The `analyses` tour convention

The narrated tour is just saved queries with `tag: "analyses"` and an integer
`order`. The stock front-end groups them and chains each to the next via a
"→ next" link, in `order` sequence.

- **Names are plain titles** ("the group tables", "goals by round"). Do NOT prefix
  them with `analyses:N:` — that was only ever shorthand for setting the tag/order
  in the save dialog, never part of the name.
- **Narrate each** with a trailing note: append `\n???note=[ ... plain english ... ]`
  to the query. The `???` note operator is a no-op that the front-end renders as
  the view's caption.
- A tour view is a normal query, so it can render a table, a chart, a map, a
  timeline, or a heatmap — vary the medium to tell the story.

## Browse (index) queries

One canonical browse view per type, `tag: "browse"`. Each provides a **canonical
display** (a sensible sort + `hide` of noisy columns) and a **canonical qlink**
that links to related records (see the qlink recipes in dactal-query-language.md):
a team's "squad" links to its players; a player's team name links to its team.

## Reproducible pipeline layout

In the Data app this skill installs, the same three stages live in one package
(`assets/app/src/workspace_data/`): `collect.py` reads the sources in place (the workspace's
own records are the preserved raw source), `export.py` shapes them and assembles the export.
The general layout for a pipeline over a fetched snapshot is:

```
pipeline/
  source/            # raw bulk snapshot, kept verbatim (preserve-the-source)
  build_data.py      # source/*.json  -> datasets.json  (shaping + computed fields + STABLE KEYS)
  build_export.py    # datasets.json  -> assets/data/<dbname>.json  (add connectors + queries + tour)
  datasets.json      # intermediate shaped data
```

- `build_data.py` does the topic-specific shaping: parse the source, canonicalize
  join keys, compute derived fields, emit one array per dataset.
- `build_export.py` is mostly boilerplate: load `datasets.json`, add the
  `connectors` list, define the browse queries and the numbered `analyses` tour,
  and write `{export, data, queries}` to the served asset. Copy `export.py`'s
  structure and replace the queries/tour with your own.
- Keep the raw source under `pipeline/source/` so reprocessing never needs a
  refetch. If your environment has code-quality ratchets, scope any that flag CLI
  `print`s (bare-print checks) to your app code, not the pipeline CLIs.

## Serving under stock `dactal.html`

Serve the explorer with the **stock** front-end so the tab shows exactly what a
published dactal.org Gallery example will. Scaffold a web app however your
environment does; the DACTAL-specific runner only serves static things (see
`assets/app/src/workspace_data/runner.py`):

- `GET /` → your custom app surface (or a 302 to `dactal.html?dbname=<dbname>` for
  an explorer-only build).
- `GET /dactal.html` → the stock `dactal.html` (its three `<script src>` rewritten
  to `https://dactal.org/` by `run.py fetch-frontend`), so it uses the live engine +
  renderers.
- `GET /data/<dbname>.json` → the export.

The engine and renderers load from dactal.org, so the tab needs internet access.

## Publishing to the dactal.org Gallery

The single `<dbname>.json` is the entire publishable export:

1. Copy `assets/data/<dbname>.json` to dactal.org's `/data/<dbname>.json`.
2. It is then live at `https://dactal.org/dactal.html?dbname=<dbname>` (land on
   the tour with `&query=<first-tour-name>`).
3. Add a Gallery entry pointing at that URL.

Nothing else ships — datasets, queries, the tour, and the connector list are all
inside the one file.

## Two-tier verification

1. **Headless query check** (`run.py verify-queries --export <path>`): runs every
   saved query through the real engine in Node and classifies each result. Fast;
   catches empty/literal/error queries — including the silent join-drift bug that
   DACTAL otherwise swallows. Fix and re-run until clean.
2. **Visual/behavioral check** (`run.py verify-visual --url <base> --export <path>`):
   screenshots each tour view in headless Chromium, because the chart/map/timeline/
   heatmap renderers only run in a browser. Review the PNGs (or show them to the
   user) to confirm each view actually paints.
