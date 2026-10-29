# Handing a project's in-code data to the Data app

Data that lives in files is picked up as it is. Data that lives in a project's code -- a
game's roster, a built-in catalog, a rules table -- needs one small module so it can be
exposed too.

## The convention

Add `datasets.py` to the package of a user-built app
(`system/apps/<app>/src/<package>/datasets.py`). The Data app runs it as
`python -m <package>.datasets` (under the workspace's root venv, from the workspace root, with
the package's own `src/` first on the path), caches the result until the package's Python files
or the app's stored records under `data/.apps/<app>/` change, and reads this JSON from its
stdout:

```json
{
  "datasets": {
    "<thing>": {"about": "<one line a person reads>",
                "rows": [{"id": "<thing>", "<part>": [{"id": "...", "...": "..."}], "...": "..."}]}
  },
  "queries": [
    {"name": "<saved view>", "query": "<DACTAL query>", "hide": ["<column>"]}
  ],
  "includes_app_records": true
}
```

- **Package the project's data as one item.** Write one dataset, named for what the project is
  about (`pokemon`), holding a single row whose properties are the project's collections
  (`figures`, `moves`, `finished_duels`, `current_duel`), each a list of rows -- or one nested
  record, when there is only one. Its first two properties are `id` and `name` (a readable
  title, `"Pokemon Duel"`); the Data app keeps them first. Do not write a set of `<thing>_<part>` datasets: the group
  reads as one thing (`pokemon.figures`), and the catalog lists the project once. This is the
  workspace-wide rule (`what-gets-exposed.md`, "Packaging related datasets").
- **Take in the app's own stored records** when they belong to the same picture (a game's saved
  matches beside its roster): read them from the app's data folder -- through the path constants
  the app itself uses, so both always agree -- nest them in the item as the app stored them, and
  set `"includes_app_records": true`. The Data app then stops listing those files as an `app:`
  dataset of their own, and rebuilds the item whenever they change.
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
        "datasets": {"demo": {"about": "All of Demo Game's data in one item.", "rows": [item]}},
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

Check it from the workspace root: `PYTHONPATH=system/apps/<app>/src python -m <package>.datasets
| python3 -m json.tool | head`, then `install.py verify` -- the project's item and views appear
under `project: <app>`. `system/apps/pokemon_duel/src/pokemon_duel/datasets.py` is the worked
example in this workspace.
