---
name: all-data-visible
description: "Make every piece of user-relevant data in a Minds workspace visible and queryable: installs a 'Data' app -- beside Chat, Browser and File Viewer in the launcher and on the desktop -- that opens the DACTAL query page over all of it. Exposed: conversations and their messages, each chat's progress steps, tracked tasks, the whole machine's folder tree, files in the user's folders, memories, apps, skills, scheduled jobs, and ALL the data of the user's own projects (every app's and skill's stored records, CSV/TSV/JSON files in the user's folders, data files shipped with user-built apps, and data a user-built app keeps in its code, handed over through a small datasets.py module). Every row links back to its source; the data rebuilds itself as the workspace changes. Use when the user wants to see, query, or audit everything their workspace holds, wants 'all data visible', or asks for a Data option/app. Self-contained: bundles the app, its installer and tests, the DACTAL engine snapshot, the query-verification harness, and the DACTAL references it needs."
metadata:
  crystallized: true
  self_contained: true
---

# All data visible

Installs the **Data** app into a Minds workspace: the stock [DACTAL](https://dactal.org) query
page, opened on a narrated tour of one export that holds every user-relevant record the
workspace keeps. It is the workspace-level expression of the Maker Ecology ethos
(`references/maker-ecology-ethos.md`): nothing the workspace knows about the user is hidden
from them, every view is a query they can read and change, and every row leads back to where
it came from.

What it exposes, where each piece comes from, and how to extend it:
`references/what-gets-exposed.md`. How a project hands over data that lives in its code:
`references/project-datasets.md`.

## What is in this skill

- `scripts/install.py` -- `install` (copy, register, install, start; re-run to update in
  place, keeping the port), `verify` (rebuild the export, run every saved view through the real
  engine), `status`.
- `assets/app/` -- the whole app (`workspace_data` package): `collect.py` (readers, one per
  kind of record), `export.py` (datasets, saved views, the tour), `runner.py` (serves the query
  page with the workspace glue), `export_test.py`, the manifest, icon, and the stock query page.
- `scripts/run.py` -- DACTAL mechanics: `fetch-frontend`, `verify-queries`, `verify-visual`
  (with `verify_queries.js`, `verify_visual.py`); `assets/dactal.js` is the engine snapshot.
- `references/` -- the ethos, the DACTAL query language, linking, connectors and charts, the
  export format, getting current DACTAL files, what gets exposed, and project datasets.

## The flow

Show the user each step's result as you go; the window is the last thing they see.

### 1. Install the app `[script]`

From the workspace root:

```
python3 .agents/skills/all-data-visible/scripts/install.py install
```

It copies `assets/app/` to `system/apps/workspace_data/` (app name `workspace-data`, shown as
"Data", launcher rank 25: between File Viewer and Browser), pulls the current stock query page
from dactal.org (`--offline` keeps the bundled one), writes
`system/supervisord.conf.d/workspace-data.conf`, installs the package (`uv tool install -e`
plus `uv sync --all-packages`), starts it, and waits for `/health`. Re-running it updates the
code in place and keeps the port; the user's data and explorations are untouched.

If the workspace follows the `build-app` / `update-app` conventions, this is the equivalent of
their scaffold step -- do not also scaffold. Take the `update-app` service lease
(`editing service workspace-data`) before re-installing over a running copy.

### 2. Let the user's projects hand over in-code data `[prose]`

List the user's own apps (`priority = "user"` in `system/apps/*/app.toml`). For each one whose
meaningful data lives in code rather than in files (a game's roster, a built-in catalog, a
rules table), add a `<package>/datasets.py` per `references/project-datasets.md`. Load the
`update-app` skill before touching another app's code. Data already in files (stored records,
CSV/JSON in the user's folders, data files an app ships) needs nothing: it is picked up as-is.

### 3. Verify `[script]`

```
python3 .agents/skills/all-data-visible/scripts/install.py verify
PYTHONPATH=system/apps/workspace_data/src uv run pytest system/apps/workspace_data -q
```

`verify` must report no `error` and no `literal`; the only `empty` views allowed are the
diagnostics (orphan checks, which are empty when the data is clean). Then render the views in
a real browser before showing anyone -- the chart renderer and the page's own quirks only show
there:

```
python3 .agents/skills/all-data-visible/scripts/run.py verify-visual \
    --url http://127.0.0.1:<port> --export data/.apps/workspace-data/data/workspace.json
```

Look at the screenshots: each view should draw a table or chart, not the page's index. In
headless Chromium inside the workspace, pass the stealth build's path if the default browser
is missing (see the workspace's browser notes).

### 4. Put it in front of the user `[script]`

The launcher entry appears on its own (from the manifest). Add the desktop icon and open the
window -- both change what the user sees, so only after step 3 passes:

```
python3 system/scripts/layout.py shortcut set workspace-data open --mode focus --cell <next free cell>
python3 system/scripts/layout.py open workspace-data
```

Read the current shortcuts (`layout.py desktops`) to pick the next free cell in the column
of built-in shortcuts; never move the user's own icons.

## The rules this app keeps (do not break them when extending it)

- **One export, read by everything.** `data/.apps/workspace-data/data/workspace.json`,
  rewritten only when its content changes. The page re-imports its private browser copy only
  when that content hash moves, so the user's own saved queries and explorations survive.
- **Read in place, never copy-and-discard.** The workspace's own files are the raw source;
  every row carries `source` and/or an `open` link back to it.
- **Stable join keys, with no hyphens.** A column named after a dataset auto-joins it
  (`messages.chat` -> `chats`); a key used in a clickable link must not contain `-`, because the
  page reads `agent-0d72...` as an expression. Chats are keyed by their id without `agent-`.
- **Every saved view carries `hide` and `order`** (empty/None when unused). The stock page
  crashes on a saved view without them -- it silently shows its index instead of the result.
- **No apostrophes in saved-view names**: the page's save box shows them as `&squo;`.
- **Connectors load through the export's `connectors` dataset only.** Preloading a renderer
  script in the page runs it before the engine exists.
- **Links that open workspace windows** are raw anchors to the app's `/open` route (204, so
  the page never navigates), clicked through the injected glue that passes the shell's client.

## Gotchas

- DACTAL swallows typos (a misspelled dataset becomes a literal); trust `verify`, not "no
  error". Sums need `..` to keep duplicates: `|total_kb=~(.of..size_kb....total)`.
- The query page loads the engine from dactal.org, so the window needs internet access.
- Updating the app replaces `system/apps/workspace_data/` code; customise through
  `references/project-datasets.md` or change this skill and re-install.
- Messages are kept to their first 2000 characters in the table; the full text stays in the
  chat. Only the words exchanged are exposed, not tool output.
