# Getting the current DACTAL files, engine, and docs

DACTAL is actively developed and served from **dactal.org**. Always work from the
*current* files, not only the bundled snapshot -- the engine gains operators and
the catalog gains connectors over time.

## What exists on dactal.org

- `https://dactal.org/dactal.html` -- the stock front-end (the query page). Its
  three `<script src>` tags (`dactal.js`, `dactal_storage_indexeddb.js`,
  `dactal_utils.js`) are relative on dactal.org; to serve the page anywhere else
  they must point at the absolute dactal.org origin so the live engine + renderers
  run. (`fetch-frontend` rewrites them for you.)
- `https://dactal.org/dactal.js` -- the **query engine** (the singleton attached to
  `window.DACTAL`). This is what runs queries, in the browser and -- with a
  `global.window = global` shim -- in Node (for the query harness in
  `scripts/verify_queries.js` and for server-side execution in
  `assets/run_query.js` in the dactal-based skill, for apps with custom surfaces).
- `https://dactal.org/dactal_utils.js` -- the front-end UI layer: `render()`, the
  renderer registry (`add_renderer`), `import_data`, the data-file init, the reset
  semantics, cell-link rendering. Read this when you need to know *how the
  front-end behaves* (e.g. the cell render forms in `linking.md`, the `reset` param
  in `export-and-serving.md`).
- `https://dactal.org/dactal_storage_indexeddb.js` -- the browser-local storage
  (the "your private copy" IndexedDB layer).
- `https://dactal.org/catalog.json` -- the full connector catalog (renderers like
  `dactal_chart.js`, `dactal_map.js`, `dactal_timeline.js`, the built-in heatmap;
  data connectors like `dactal_gmail.js`, `dactal_slack.js`, Spotify, GitHub, …).
- Connector files, e.g. `https://dactal.org/dactal_chart.js` -- read one when you
  need its exact spec.

## How to pull them (bundled script)

This skill bundles `scripts/run.py`. Its `fetch-frontend` subcommand downloads
`dactal.html` (with the script srcs rewritten to the dactal.org origin) +
`catalog.json` into your app's assets dir and refreshes the engine snapshot at this
skill's `assets/dactal.js`:

```
python3 scripts/run.py fetch-frontend --assets-dir <your-app>/assets
```

For **server-side execution** you need a local engine copy the Node runner can
`require`. `fetch-frontend` refreshes `assets/dactal.js`; copy that into your app
(e.g. `<your-app>/assets/dactal_engine.js`) or fetch `https://dactal.org/dactal.js`
directly. Keep it reasonably fresh; it is vendored engine code (exclude from
hand-written-code lint/ratchet scope).

`run.py` needs only the Python standard library for `fetch-frontend` and
`verify-queries`; `verify-visual` additionally needs Playwright + Chromium. The
query harness (`verify-queries`) and `run_query.js` need Node.js.

### Cache-bust the live front-end scripts, or updates run stale

The served `dactal.html` loads `dactal.js` / `dactal_storage_indexeddb.js` /
`dactal_utils.js` (and connector scripts) live from dactal.org **by a stable URL**.
Browsers cache those aggressively, so after dactal.org ships a new `dactal_utils.js`
a returning tab keeps running the *old* one -- even a hard reload may serve it from
disk cache. Append a cache-buster to those `<script src>` URLs, keyed to a token
that changes each time you pull the front-end (e.g. the served `dactal.html`'s
mtime, which `fetch-frontend` rewrites): a fresh pull yields a new URL the browser
has never cached, forcing it to fetch the current scripts. `assets/app/src/workspace_data/runner.py`'s
`dactal_html()` does this. Symptom that you forgot it: a documented new front-end
behavior doesn't happen in the tab even though the file on dactal.org has it.

## Reading the engine when docs don't answer it

DACTAL is deliberately forgiving and its exact behavior is sometimes only in the
code. When you need ground truth (a render form, an operator, the reset/import
logic), grep the fetched `dactal_utils.js` / `dactal.js` rather than guessing --
that is how the behaviors documented in `linking.md` and `export-and-serving.md`
were confirmed. Trust `python3 scripts/run.py verify-queries` (it runs every saved
query through the real engine and classifies ok/empty/literal/error) over "it
didn't throw": DACTAL turns a typo'd dataset name into a literal string and
silently ignores a bad property.

## Docs

The authoritative human docs are on dactal.org itself (linked from the front-end's
intro). This skill's `references/dactal-query-language.md`,
`connectors-and-viz.md`, and `export-and-serving.md` distill the language,
connectors, and export format; start there for mechanics, and go to dactal.org for
anything newer or deeper (e.g. authoring a brand-new connector, which is out of
scope for this skill).
