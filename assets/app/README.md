# workspace-data

The Data app: the DACTAL query page over every user-relevant dataset in this workspace,
installed and updated by the `all-data-visible` skill (`.agents/skills/all-data-visible/`).
Change it there, not here: re-running the skill's installer replaces this folder's code.

- `collect.py` reads each kind of record where the workspace keeps it (chats and their
  messages, progress steps, tasks, files in the user's folders, memories, apps, skills,
  scheduled jobs), plus all the data of the user's own projects: every app's and skill's
  stored JSON/JSON-lines records, every CSV/TSV/JSON file in the user's folders, the data
  files a user-built app ships, and what a user-built app hands over itself (below).
- `export.py` assembles them, with the saved queries and the narrated tour, into the one
  export `data/.apps/workspace-data/data/workspace.json`, written only when its content
  changed.
- `runner.py` serves the stock query page (`assets/dactal.html`, pulled with the skill's
  `run.py fetch-frontend`) on that export, rebuilding it on page load when stale and every
  two minutes in the background.

Check every saved query against the real engine with
`python3 .agents/skills/all-data-visible/scripts/install.py verify`.

## Handing a project's data to the Data app

Data that lives in a project's code (a game's roster, a built-in catalog) reaches the Data app
through a `datasets.py` module in the project's package (`system/apps/<app>/src/<package>/`).
Run as `python -m <package>.datasets` under the root venv, it writes to stdout:

```json
{"datasets": {"<name>": {"about": "<one line>", "rows": [{"id": "...", "...": "..."}]}},
 "queries": [{"name": "<saved view>", "query": "<DACTAL query>", "hide": ["<column>"]}]}
```

Join datasets by naming a column after the dataset it points at (`pokemon_figure` joins
`pokemon_figures`), on keys with no hyphens. Output is cached until the package's Python
changes. `system/apps/pokemon_duel/src/pokemon_duel/datasets.py` is the example.
