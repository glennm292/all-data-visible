# What gets exposed, and where it comes from

Every dataset is read in place from where the workspace keeps it (`collect.py`), shaped into
rows with stable ids (`export.py`), and listed in the `datasets` catalog -- the first view of
the tour -- with its row count, a one-line description, and an `open »` link.

## The workspace's own records (group `workspace`)

| Dataset | One row per | Read from | Links |
|---|---|---|---|
| `chats` | conversation (a chat moved between harnesses is one row; background work is its own row, kind `background work`, `launched_by` its chat) | `~/.mngr/agents/*/data.json` and `~/.mngr/preserved/*/manifest.json` (labels `chat_id`, `display_name`, `agent_created`, `lead_agent`), plus the chat app's `data/.apps/chat/chats/*/record.json` | `open chat »` opens it as a window; `messages »` pivots to its messages |
| `messages` | message (only the words exchanged: the user's and the mind's text, never tool output; first 2000 characters) | each agent's `events/*/common_transcript/events.jsonl` (`type: step`, `source: user/agent`), plus seeded chats' `seed.jsonl` | `chat` joins `chats`; `source` names the transcript |
| `steps` | progress step of a chat | `data/.tickets/*.md` with `step: true` | `chat` joins `chats` (empty for agents that are gone); `view raw »` renders the record |
| `tasks` | shared work item | `data/.tickets/*.md` without `step` | `view raw »` |
| `files` | file in the user's visible folders of `data/`, plus `memories/` | the file tree | `open file »` opens it in the File Viewer |
| `memories` | memory note | `data/memories/*.md` frontmatter + body | `open note »` |
| `apps` | app the user can open | `data/.state/apps.toml` (not `internal`) | `open … »` opens the app |
| `skills` | skill, `made_by` you or built in (`author: imbue`) | `.agents/skills/*/SKILL.md` | `view raw »` renders the SKILL.md |
| `scheduled_jobs` | runner job | `data/.state/jobs/*/last_success` | |

## All the data of the user's own projects

| Group | Dataset name | Read from |
|---|---|---|
| `project: <app>` | as the project names them | a user-built app's `<package>/datasets.py` (see `project-datasets.md`) |
| `project: <app>` | `<app>_<path>` | JSON / JSON-lines / CSV / TSV files shipped in a user-built app's folder (build configuration such as `package.json` excluded) |
| `app: <app>` | `<app>_<file>` | every JSON / JSON-lines file an app keeps under `data/.apps/<app>/` (the chat, browser and terminal stores are machinery and skipped) |
| `skill: <skill>` | `skill_<skill>_<file>` | every JSON / JSON-lines file a skill keeps under `data/.skills/<skill>/` |
| `folder: <folder>` | `<folder>_<path>` | every CSV / TSV / JSON / JSON-lines file in the user's visible folders of `data/` |

A user-built app is one whose manifest says `priority = "user"` (what the `build-app` scaffold
writes); built-in apps are left out. Each file is capped at 20 MB and 5000 rows. A name that
collides with another dataset gets its group's name in front.

Logins and keys are never exposed: a file whose name looks like one (`token`, `secret`,
`credential`, `password`, `api_key`, `cookie`, `oauth`, ...) is skipped whole, and a field named
like one (`access_token`, `client_secret`, `api_key`, `encrypted_*`, ...) is dropped from every
row, nested ones included (`read_table` in `collect.py`).

## The saved views

- **Tour** (`analyses`): everything in this workspace; data from your projects; your
  conversations; messages per day (chart); what you asked for; who does the talking; recent
  progress; your files by folder.
- **Browse**: one `all <dataset>` view per dataset, sorted sensibly.
- **Project views**: whatever each project's `datasets.py` brings, tagged with its group.
- **Diagnostics**: orphan checks that must stay empty (messages with no conversation, steps
  with an unknown conversation, plus any a project adds).

## Extending it

- New kind of workspace record: add a collector to `collect.py` returning rows with an `id`, a
  `source` or `open` link, and join columns named after the dataset they point at; register it
  in `export.py`'s `curated` map, `DATASET_ABOUT`, `BROWSE_SORT`, and give it an `all …` view.
  Add a test to `export_test.py` with a fake record laid out as the real one is.
- New view: add it to `build_queries`. Check it with `install.py verify`, then in a browser.
- Do it in this skill's `assets/app/`, then re-run `install.py install` -- the installed copy
  is replaced on every install.
