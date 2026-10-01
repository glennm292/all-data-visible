# all-data-visible

A **self-contained skill** for Minds workspaces: it makes every piece of user-relevant data
the workspace holds visible and queryable, by installing a **Data** app -- beside Chat,
Browser and File Viewer -- that opens the [DACTAL](https://dactal.org) query page over all of
it.

Exposed, each row linked back to its source and rebuilt as the workspace changes:

- conversations and every message in them (the words exchanged, not tool output), each
  chat's progress steps, tracked tasks;
- files in the user's folders, memories, apps, skills, scheduled jobs;
- **all the data of the user's own projects**: every app's and skill's stored records,
  CSV/TSV/JSON files in the user's folders, data files shipped with user-built apps, and data a
  user-built app keeps in its code (handed over through a small `datasets.py` module).

It applies the Maker Ecology ethos at the level of the whole workspace: nothing the workspace
knows is hidden from its owner, every view is a query they can read and change, and every
record leads back to where it came from.

## Use

Copy this folder to `.agents/skills/all-data-visible/` in a Minds workspace and ask the mind
for "all data visible" (or follow `SKILL.md`). The mechanical part is:

```
python3 .agents/skills/all-data-visible/scripts/install.py install
python3 .agents/skills/all-data-visible/scripts/install.py verify
```

## What's inside

```
SKILL.md                         the flow and the rules the app keeps
references/
  what-gets-exposed.md           every dataset, where it comes from, how to extend
  project-datasets.md            how a project hands over data that lives in its code
  maker-ecology-ethos.md         the why
  dactal-query-language.md       operators, joins, stable keys, qlink recipes
  linking.md                     links within the data and out to the source
  connectors-and-viz.md          renderers (charts, maps, timelines, heatmaps)
  export-and-serving.md          the {export, data, queries} format, the tour, verification
  getting-dactal.md              pulling the current DACTAL files
scripts/
  install.py                     install | verify | status
  run.py                         fetch-frontend | verify-queries | verify-visual
  verify_queries.js              headless query harness (real engine, in Node)
  verify_visual.py               screenshot each view (Playwright)
assets/
  dactal.js                      DACTAL engine snapshot
  app/                           the Data app (Flask): collect.py, export.py, runner.py, tests
```

## Requirements

A Minds workspace (supervisord, `forward_port.py`, uv), Python 3.11+, Node.js for query
verification, Playwright + Chromium for screenshots, and internet access to dactal.org for the
query page itself.

## Provenance

DACTAL and the Maker Ecology ethos are Glenn McDonald's (dactal.org, furia.com). The DACTAL
references and verification scripts come from the
[dactal-based](https://github.com/glennm292/dactal-based) skill; the engine, front-end, and
connectors are fetched from dactal.org and remain his.
