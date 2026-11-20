# Handing a project's in-code data to the Data app

Data that lives in files is picked up as it is. Data that lives in a project's code -- a
game's roster, a built-in catalog, a rules table -- needs one small module so it can be
exposed too.

## The convention

Add `datasets.py` to the package of a user-built app
(`system/apps/<app>/src/<package>/datasets.py`). The Data app runs it as
`python -m <package>.datasets` (under the project's own venv, `system/apps/<app>/.venv`, which
installs the project and everything it imports; a project with no venv of its own runs under the
workspace's root venv with its `src/` first on the path), from the workspace root, caches the result until the package's Python files
or the app's stored records under `data/.apps/<app>/` change, and reads this JSON from its
stdout:

```json
{
  "datasets": {
    "<thing>": {"about": "<one line a person reads>",
                "item": {"id": "<thing>", "name": "<Title>", "<part>": [{"id": "...", "...": "..."}]}}
  },
  "queries": [
    {"name": "<saved view>", "query": "<DACTAL query>", "hide": ["<column>"]}
  ],
  "includes_app_records": true
}
```

- **Package the project's data as one object.** Write one dataset, named for what the project
  is about (`pokemon`), given as `item`: a single plain object whose properties are the
  project's collections (`figures`, `moves`, `finished_duels`, `current_duel`), each a list of
  rows -- or one nested record, when there is only one. The collections are properties of that
  root object itself; do not wrap the object in a list (`"rows": [item]`), which makes the
  export read as a table of one row. The export writes the object as the dataset's value.
  `rows` (a list) is for a plain table that is not a group. Its first two properties are `id` and `name` (a readable
  title, `"Pokemon Duel"`); the Data app keeps them first. Do not write a set of `<thing>_<part>` datasets: the group
  reads as one thing (`pokemon.figures`), and the catalog lists the project once. This is the
  workspace-wide rule (`what-gets-exposed.md`, "Packaging related datasets").
- **Take in the app's own stored records** when they belong to the same picture (a game's saved
  matches beside its roster): read them from the app's data folder -- through the path constants
  the app itself uses, so both always agree -- nest them in the item as the app stored them, and
  set `"includes_app_records": true`. The Data app then stops listing those files as an `app:`
  dataset of their own, and rebuilds the item whenever they change.
- Build every row in a logical property order: `id`, `name`, then what describes the thing,
  then derived numbers, then links. The Data app keeps the order you write (only `id` and `name`
  are pulled to the front) and never alphabetizes, so do not sort keys yourself.
- Every row gets a stable `id` with no hyphens (a key used in a clickable link must not contain
  `-`; replace it with `_` and keep the original in another column).
- Put derived numbers in the rows (odds, averages, scores) and say in a view's `???note` how
  they are worked out -- the user should be able to read the algorithm, not only the result.
- Inside the item, link one collection's rows to another's by a plain key column (a move's
  `figure` is its figure's `id`). Name the item so it will not collide with another project's.
  Do not name a member property after a top-level dataset: a scalar value under that name would
  auto-join.
- `queries` is optional; write them against the item (`pokemon.figures#-expected_damage`). A
  drill-down link from one member to the rows of another filters that member by the key:
  `|disk=(...qlink=(...id=~[pokemon.moves:figure=],query=(.id),text=~[disk »]))` -- clicking
  runs `pokemon.moves:figure=<this figure's id>`.
- Write the JSON with `json.dump(payload, sys.stdout)`: the workspace's ratchets forbid
  `print` and `sys.stdout.write`.
- If the module fails, the catalog shows a row saying so; the rest of the export is unaffected.
- If the project has an import-layers contract, list `<package>.datasets` as its top layer.

## Example (a game roster and its saved matches)

```python
"""The game's datasets, handed to the workspace's Data app (python -m demo_game.datasets)."""

import json
import sys

from demo_game.roster import SPECIES_BY_NAME
from demo_game.runner import FINISHED_MATCHES_PATH

_FULL_DISK = 360


def figure_key(species) -> str:
    return str(species.name).replace("-", "_")


def _finished_matches() -> list[dict]:
    try:
        lines = FINISHED_MATCHES_PATH.read_text().splitlines()
    except OSError:
        return []
    return [json.loads(line) for line in lines if line.strip()]


def build_datasets() -> dict:
    figures, moves = [], []
    for species in sorted(SPECIES_BY_NAME.values(), key=lambda s: s.display_name):
        expected = sum(w.damage * w.degrees for w in species.wedges if w.damage) / _FULL_DISK
        figures.append({"id": figure_key(species), "name": species.display_name,
                        "expected_damage": round(expected, 1)})
        for position, wedge in enumerate(species.wedges, start=1):
            moves.append({"id": f"{figure_key(species)}_{position}", "figure": figure_key(species),
                          "position": position, "move": str(wedge.name), "degrees": wedge.degrees})
    item = {"id": "demo", "name": "Demo Game", "figures": figures, "moves": moves,
            "finished_matches": _finished_matches()}
    return {
        "datasets": {"demo": {"about": "All of Demo Game's data in one object.", "item": item}},
        "queries": [{
            "name": "demo figures by expected damage",
            "query": "demo.figures#-expected_damage"
                     "|disk=(...qlink=(...id=~[demo.moves:figure=],query=(.id),text=~[disk »]))"
                     "\n???note=[ expected_damage: each wedge's damage times its share of the disk, summed. ]",
        }],
        "includes_app_records": True,
    }


def main() -> None:
    json.dump(build_datasets(), sys.stdout)


if __name__ == "__main__":
    main()
```

Check it from the workspace root with the interpreter the Data app uses:
`system/apps/<app>/.venv/bin/python -m <package>.datasets | python3 -m json.tool | head` (for a
project with no venv of its own, `PYTHONPATH=system/apps/<app>/src python -m <package>.datasets`),
then `install.py verify` -- the project's item and views appear under `project: <app>`. Any
`datasets.py` already in `system/apps/*/src/*/` is a worked example to read before writing a new one.

## Drawing a project's own rows

Data goes in as rows and comes back out as a table. That is right for most data and wrong for
the records that are a *picture* of something -- a board position, a route, a line of writing
and the comment beside it. A project can hand the query page a renderer of its own, so the
result of any query over those rows carries an extra display beside "raw data", drawn the way
the project draws them.

**Where the drawing code should live, when a project does this.** Not required -- a project is
free to keep its page code where it is and ship no renderer at all. But a project that *is*
going to have both its own view and a query-page display is better off with the drawing written
once, in a plain module its pages and its renderer both load:

```
src/<package>/assets/
    rows.js              <- the formatter: a row in, markup out. No fetching, no page state.
    dactal_render.js     <- the renderer: imports rows.js, registers the display.
    index.html           <- the project's own view: loads rows.js (on a route the app serves
                            it from) and calls it, instead of drawing the row itself.
```

The pull the other way -- drawing inline in the page, then writing it out again in the renderer
-- is what the split exists to resist. Two copies of the same drawing drift, and the drift is
quiet: one of them starts leaving out a case and nothing fails. A project with two of its own
views already has this problem without any renderer at all, so the split pays for itself before
the query page is involved.

Keep the formatter free of the page it is on: it takes a row and returns markup (or an element),
reads nothing global, fetches nothing. That is what lets every caller share it. Ship the styles
with it, as a string the caller can add to `document.head`, scoped to your own class -- the query
page knows nothing about them, and a project's own page may already style the same names.

**How the renderer is loaded.** Put a `dactal_render.js` in the project's package assets
directory (`system/apps/<app>/src/<package>/assets/dactal_render.js`). The Data app serves that
whole directory at `/app-assets/<app>/` and loads the file as a module, after the page has built
`dactal` and before it runs its first query. Being a module is what lets it import its siblings
out of the same directory, which is how it reaches the formatter rather than copying it:

```js
import "./rows.js";                // sets window.MyFormat, the same one the project's pages load

add_renderer(["myapp"], {
    // Every one of these fields must be on the first row of a result for the display to be
    // offered. Pick fields the project always writes, and that nothing else here has.
    offerfor: ["row_id", "text", "status"],
    list: (row) => window.MyFormat.element(row),
});
```

The display appears as "myapp list" beside "raw data" in the query result's header, and picking
it re-runs the query with `|list=~myapp` on the end, which is a plain query the user can read
and keep. `list` is handed one row and returns one element; a value read out of a query may
arrive wrapped in a list of one, so unwrap before reading it. Guard the registration (`typeof
add_renderer === "function"`) so a page that failed to reach the engine does not throw.
