# Handing a project's in-code data to the Data app

Data that lives in files is picked up as it is. Data that lives in a project's code -- a
game's roster, a built-in catalog, a rules table -- needs one small module so it can be
exposed too.

## The convention

Add `datasets.py` to the package of a user-built app
(`system/apps/<app>/src/<package>/datasets.py`). The Data app runs it as
`python -m <package>.datasets` (under the workspace's root venv, with the package's own `src/`
first on the path), caches the result until the package's Python files change, and reads this
JSON from its stdout:

```json
{
  "datasets": {
    "<name>": {"about": "<one line a person reads>", "rows": [{"id": "...", "...": "..."}]}
  },
  "queries": [
    {"name": "<saved view>", "query": "<DACTAL query>", "hide": ["<column>"]}
  ]
}
```

- Every row gets a stable `id` with no hyphens (a key used in a clickable link must not contain
  `-`; replace it with `_` and keep the original in another column).
- Put derived numbers in the rows (odds, averages, scores) and say in a view's `???note` how
  they are worked out -- the user should be able to read the algorithm, not only the result.
- Join datasets by naming a column after the dataset it points at: a `pokemon_figure` column
  joins the `pokemon_figures` dataset. Name your datasets so they will not collide with other
  projects (prefix with the project's subject).
- `queries` is optional. A drill-down link uses a reverse qlink:
  `|disk=(...qlink=(...id=(.id),query=[|pokemon_figure.pokemon_moves],text=~[disk »]))`.
- Write the JSON with `json.dump(payload, sys.stdout)`: the workspace's ratchets forbid
  `print` and `sys.stdout.write`.
- If the module fails, the catalog shows a row saying so; the rest of the export is unaffected.
- If the project has an import-layers contract, list `<package>.datasets` as its top layer.

## Example (a game roster)

```python
"""The game's datasets, handed to the workspace's Data app (python -m demo_game.datasets)."""

import json
import sys

from demo_game.roster import SPECIES_BY_NAME

_FULL_DISK = 360


def figure_key(species) -> str:
    return str(species.name).replace("-", "_")


def build_datasets() -> dict:
    figures, moves = [], []
    for species in sorted(SPECIES_BY_NAME.values(), key=lambda s: s.display_name):
        expected = sum(w.damage * w.degrees for w in species.wedges if w.damage) / _FULL_DISK
        figures.append({"id": figure_key(species), "name": species.display_name,
                        "expected_damage": round(expected, 1)})
        for position, wedge in enumerate(species.wedges, start=1):
            moves.append({"id": f"{figure_key(species)}_{position}", "demo_figure": figure_key(species),
                          "position": position, "move": str(wedge.name), "degrees": wedge.degrees})
    return {
        "datasets": {
            "demo_figures": {"about": "Every figure, with its average damage per spin.", "rows": figures},
            "demo_moves": {"about": "Every wedge on every figure's disk.", "rows": moves},
        },
        "queries": [{
            "name": "figures by expected damage",
            "query": "demo_figures#-expected_damage"
                     "|disk=(...qlink=(...id=(.id),query=[|demo_figure.demo_moves],text=~[disk »]))"
                     "\n???note=[ expected_damage: each wedge's damage times its share of the disk, summed. ]",
        }],
    }


def main() -> None:
    json.dump(build_datasets(), sys.stdout)


if __name__ == "__main__":
    main()
```

Check it: `python -m <package>.datasets | python3 -m json.tool | head`, then
`install.py verify` -- the project's datasets and views appear under `project: <app>`.
