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
Run as `python -m <package>.datasets` under the root venv, it writes to stdout one packaged item
per project (its collections as properties of a single row), its saved views, and whether it
takes in the app's own stored records:

```json
{"datasets": {"<thing>": {"about": "<one line>", "rows": [{"id": "<thing>", "<part>": [{"id": "..."}]}]}},
 "queries": [{"name": "<saved view>", "query": "<thing>.<part>#...", "hide": ["<column>"]}],
 "includes_app_records": true}
```

Related datasets are always packaged this way -- an app's or skill's several stored files, a
family of files in one folder sharing a first word, and a project's own data -- so each group is
one dataset of one item (`pokemon.figures`, `voice_chat.commands`). Output is cached until the
package's Python or the app's stored records change.
`system/apps/pokemon_duel/src/pokemon_duel/datasets.py` is the example; the rules are in the
skill's `references/project-datasets.md` and `references/what-gets-exposed.md`.
