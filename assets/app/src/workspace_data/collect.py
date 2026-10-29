"""Gather every user-relevant record in this workspace into DACTAL datasets.

Each collector reads one kind of record straight from where the workspace keeps it and
returns a list of plain row objects. The rows are cross-linked on stable ids, never display
names: a message's ``chat`` is the chat id (so DACTAL's same-name auto-join resolves it into
the ``chats`` dataset), a step's ``chat`` likewise. Every row also carries ``source`` (the file
it was read from) and, where there is somewhere to go, an ``open`` link back to the record in
the workspace -- the raw record stays where it lives; this module only reads it.

Paths are relative to the repo root (the cwd every supervised program runs in).
"""

import csv
import json
import os
import re
import subprocess
import sys
import tomllib
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

WORKSPACE_DATA = Path("data")
SKILLS_DIR = Path(".agents/skills")
APP_REGISTRY = WORKSPACE_DATA / ".state" / "apps.toml"
TICKETS_DIR = Path(os.environ.get("TICKETS_DIR", str(WORKSPACE_DATA / ".tickets")))
CHAT_STORE = WORKSPACE_DATA / ".apps" / "chat" / "chats"
JOBS_DIR = WORKSPACE_DATA / ".state" / "jobs"
MNGR_HOST_DIR = Path(os.environ.get("MNGR_HOST_DIR", "/home/user/.mngr"))

# Visible top-level folders of data/ are the user's own (data/README.md); memories sit there too.
FILE_ROOTS_HIDDEN_OK = ("memories",)
# Apps whose stored data is workspace machinery or already covered by a curated dataset.
APP_DATA_SKIPPED = frozenset({"chat", "browser", "terminal", "workspace-data"})
APPS_DIR = Path("system/apps")
SELF_APP = "workspace-data"
# The interpreter a project's dataset module runs under: the root venv, which every app under
# system/apps/ is installed into.
PROJECT_PYTHON = os.environ.get("WORKSPACE_DATA_PROJECT_PYTHON", ".venv/bin/python")
PROVIDER_TIMEOUT_SECONDS = 60
TABLE_SUFFIXES = (".json", ".jsonl", ".csv", ".tsv")
TABLE_MAX_ROWS = 5000
TABLE_MAX_BYTES = 20_000_000
BUILD_CONFIG_FILES = frozenset(
    {
        "package.json",
        "package-lock.json",
        "tsconfig.json",
        "jsconfig.json",
        "composer.json",
        "manifest.json",
        "components.json",
        ".eslintrc.json",
    }
)
MESSAGE_TEXT_LIMIT = 2000
TRANSCRIPT_GLOB = "events/*/common_transcript/events.jsonl"


def _timezone() -> ZoneInfo | None:
    try:
        name = (
            (WORKSPACE_DATA / ".state" / "user_timezone")
            .read_text(encoding="utf-8")
            .strip()
        )
        return ZoneInfo(name) if name else None
    except (OSError, ZoneInfoNotFoundError, ValueError):
        return None


TZ = _timezone()


def _when(ts: float) -> str:
    """A local, sortable, human-readable time for a unix timestamp."""
    return datetime.fromtimestamp(ts, TZ).strftime("%Y-%m-%d %H:%M")


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, TZ).strftime("%Y-%m-%d")


def _iso_ts(value: object) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def open_link(app: str, path: str, label: str) -> str:
    """A DACTAL cell link (raw-anchor render form) that opens ``path`` of ``app`` in a workspace window.

    The href is this app's ``/open`` route, which asks the workspace to open the window and answers
    204, so a click never navigates the query page away.
    """
    href = "/open?" + urlencode({"app": app, "path": path})
    return f'<a href="{href}" class="ws-open" target="_self">{label}</a>'


def raw_link(kind: str, ident: str, label: str = "view raw »") -> str:
    href = f"/raw/{kind}/{quote(ident, safe='')}"
    return f'<a href="{href}" target="_blank" rel="noopener">{label}</a>'


def _read_jsonl(path: Path) -> Iterator[dict]:
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    yield value
    except OSError:
        return


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split a markdown file's simple ``key: value`` frontmatter from its body.

    Only top-level scalar keys are read (nested blocks such as ``metadata:`` are flattened by
    their leaf names), which is all the workspace's tickets, skills, and memories use.
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    fields: dict[str, str] = {}
    for line in text[3:end].splitlines():
        match = re.match(r"^\s*([A-Za-z_][\w-]*):\s*(.*)$", line)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        fields.setdefault(key, value)
    body = text[end + 4 :].lstrip("\n")
    return fields, body


# ---- chats and messages -----------------------------------------------------


def _agent_dirs() -> Iterator[tuple[Path, dict, bool]]:
    """Every agent this host has run: (state dir, identity, still present)."""
    agents = MNGR_HOST_DIR / "agents"
    if agents.is_dir():
        for state_dir in sorted(agents.iterdir()):
            data = _read_json(state_dir / "data.json")
            if isinstance(data, dict):
                yield (
                    state_dir,
                    {
                        "id": data.get("id", state_dir.name),
                        "name": data.get("name", ""),
                        "type": data.get("type", ""),
                        "created": data.get("create_time", ""),
                        "labels": data.get("labels") or {},
                    },
                    True,
                )
    preserved = MNGR_HOST_DIR / "preserved"
    if preserved.is_dir():
        for state_dir in sorted(preserved.iterdir()):
            manifest = _read_json(state_dir / "manifest.json")
            if not isinstance(manifest, dict):
                continue
            identity = manifest.get("identity") or {}
            yield (
                state_dir,
                {
                    "id": identity.get("agent_id", state_dir.name),
                    "name": identity.get("agent_name", ""),
                    "type": identity.get("agent_type", ""),
                    "created": "",
                    "labels": identity.get("labels") or {},
                },
                False,
            )


def chat_key(chat_id: str) -> str:
    """The stable key a chat is joined on: its id without the ``agent-`` prefix.

    DACTAL reads a bare hyphenated value in a query (``agent-0d72...|chat.messages``, what a
    clicked link runs) as an expression, so the key must carry no hyphen.
    """
    return chat_id.removeprefix("agent-")


def _chat_kind(labels: dict) -> str:
    if labels.get("agent_created") == "true":
        return "background work"
    if labels.get("assist") == "true":
        return "help request"
    return "chat"


def collect_chats_and_messages() -> tuple[list[dict], list[dict]]:
    """One ``chats`` row per conversation and one ``messages`` row per message in it.

    A chat the chat app moved between harnesses spans several agents, all labelled with the
    chat's id (``chat_id``); their messages are gathered under that one chat. Background work an
    agent launched is its own row, kind "background work", with ``launched_by`` naming the chat
    that launched it. Only the words exchanged are kept -- not the tool calls and their output.
    """
    agent_to_chat: dict[str, str] = {}
    chats: dict[str, dict] = {}
    transcripts: list[tuple[str, Path]] = []

    for state_dir, identity, is_present in _agent_dirs():
        labels = identity["labels"]
        agent_transcripts = sorted(state_dir.glob(TRANSCRIPT_GLOB))
        if not agent_transcripts:
            continue
        agent_id = identity["id"]
        chat_app_id = labels.get("chat_id") or agent_id
        chat_id = chat_key(chat_app_id)
        agent_to_chat[agent_id] = chat_id
        title = labels.get("display_name") or identity["name"] or chat_id
        row = chats.setdefault(
            chat_id,
            {
                "id": chat_id,
                "chat_app_id": chat_app_id,
                "title": title,
                "kind": _chat_kind(labels),
                "launched_by": labels.get("lead_agent", ""),
                "still_present": is_present,
                "agents": 0,
            },
        )
        row["agents"] += 1
        if labels.get("display_name"):
            row["title"] = labels["display_name"]
        row["still_present"] = row["still_present"] or is_present
        transcripts.extend((agent_id, path) for path in agent_transcripts)

    # The chat app's own record names each chat's first title (a seeded chat has no agent label).
    for chat_dir in sorted(CHAT_STORE.glob("*")) if CHAT_STORE.is_dir() else []:
        record = _read_json(chat_dir / "record.json")
        if not isinstance(record, dict):
            continue
        chat_app_id = record.get("chat_id") or chat_dir.name
        chat_id = chat_key(chat_app_id)
        for agent in record.get("agents") or []:
            if isinstance(agent, dict) and agent.get("agent_id"):
                agent_to_chat.setdefault(agent["agent_id"], chat_id)
        row = chats.setdefault(
            chat_id,
            {
                "id": chat_id,
                "chat_app_id": chat_app_id,
                "title": record.get("seed_title") or chat_id,
                "kind": "chat",
                "launched_by": "",
                "still_present": True,
                "agents": 0,
            },
        )
        if row["title"] == chat_id and record.get("seed_title"):
            row["title"] = record["seed_title"]

    messages: list[dict] = []
    for chat_dir in sorted(CHAT_STORE.glob("*")) if CHAT_STORE.is_dir() else []:
        seed = chat_dir / "seed.jsonl"
        if seed.exists():
            for index, event in enumerate(_read_jsonl(seed)):
                text = (
                    event.get("content")
                    if event.get("role") == "user"
                    else event.get("text")
                )
                ts = _iso_ts(event.get("timestamp"))
                if isinstance(text, str) and text.strip() and ts is not None:
                    messages.append(
                        _message_row(
                            event.get("event_id") or f"{chat_dir.name}:seed:{index}",
                            chat_key(chat_dir.name),
                            "you" if event.get("role") == "user" else "mind",
                            ts,
                            text,
                            0,
                            seed,
                        )
                    )

    for agent_id, path in transcripts:
        chat_id = agent_to_chat.get(agent_id, chat_key(agent_id))
        for event in _read_jsonl(path):
            if event.get("type") != "step":
                continue
            source = event.get("source")
            text = event.get("message")
            ts = _iso_ts(event.get("timestamp"))
            if (
                source not in ("user", "agent")
                or not isinstance(text, str)
                or not text.strip()
                or ts is None
            ):
                continue
            messages.append(
                _message_row(
                    event.get("event_id") or f"{agent_id}:{ts}",
                    chat_id,
                    "you" if source == "user" else "mind",
                    ts,
                    text,
                    len(event.get("tool_calls") or []),
                    path,
                )
            )

    # Background work launched by an agent points at that agent's chat.
    for row in chats.values():
        if row["launched_by"]:
            row["launched_by"] = agent_to_chat.get(
                row["launched_by"], chat_key(row["launched_by"])
            )

    titles = {chat_id: row["title"] for chat_id, row in chats.items()}
    by_chat: dict[str, list[dict]] = {}
    for message in messages:
        message["chat_title"] = titles.get(message["chat"], message["chat"])
        by_chat.setdefault(message["chat"], []).append(message)

    chat_rows = []
    for chat_id, row in chats.items():
        own = by_chat.get(chat_id, [])
        if not own:
            continue
        first = min(m["ts"] for m in own)
        last = max(m["ts"] for m in own)
        row.update(
            {
                "message_count": len(own),
                "from_you": sum(1 for m in own if m["role"] == "you"),
                "started": _when(first),
                "last_active": _when(last),
                "started_ts": int(first),
                "last_ts": int(last),
                "open": open_link("chat", f"/?chat={row['chat_app_id']}", "open chat »")
                if row["kind"] != "background work"
                else "",
            }
        )
        del row["chat_app_id"]
        chat_rows.append(row)
    chat_rows.sort(key=lambda r: r["last_ts"], reverse=True)
    messages.sort(key=lambda m: m["ts"], reverse=True)
    return chat_rows, messages


def _message_row(
    ident: str, chat_id: str, role: str, ts: float, text: str, tools: int, source: Path
) -> dict:
    flat = text.strip()
    return {
        "id": ident,
        "chat": chat_id,
        "role": role,
        "when": _when(ts),
        "day": _day(ts),
        "ts": int(ts),
        "text": flat[:MESSAGE_TEXT_LIMIT]
        + ("…" if len(flat) > MESSAGE_TEXT_LIMIT else ""),
        "length": len(flat),
        "actions_taken": tools,
        "source": str(source),
    }


# ---- files ------------------------------------------------------------------


def collect_files() -> list[dict]:
    """Every file in the user's own folders of ``data/`` (the visible ones, plus memories)."""
    rows = []
    if not WORKSPACE_DATA.is_dir():
        return rows
    for top in sorted(WORKSPACE_DATA.iterdir()):
        if not top.is_dir() or (
            top.name.startswith(".") and top.name not in FILE_ROOTS_HIDDEN_OK
        ):
            continue
        for path in sorted(top.rglob("*")):
            rel = path.relative_to(WORKSPACE_DATA)
            if not path.is_file() or any(part.startswith(".") for part in rel.parts):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            rows.append(
                {
                    "id": str(rel),
                    "name": path.name,
                    "folder": top.name,
                    "kind": path.suffix.lstrip(".").lower() or "none",
                    "size_kb": round(stat.st_size / 1024, 1),
                    "modified": _when(stat.st_mtime),
                    "ts": int(stat.st_mtime),
                    "open": open_link("files", "/" + quote(str(rel)), "open file »"),
                }
            )
    rows.sort(key=lambda r: r["ts"], reverse=True)
    return rows


# ---- memories -----------------------------------------------------------------


def collect_memories() -> list[dict]:
    rows = []
    folder = WORKSPACE_DATA / "memories"
    for path in sorted(folder.glob("*.md")) if folder.is_dir() else []:
        if path.name in ("MEMORY.md", "README.md"):
            continue
        try:
            fields, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        rows.append(
            {
                "id": fields.get("name") or path.stem,
                "about": fields.get("description", ""),
                "type": fields.get("type", ""),
                "text": body.strip()[:MESSAGE_TEXT_LIMIT],
                "modified": _when(path.stat().st_mtime),
                "ts": int(path.stat().st_mtime),
                "open": open_link(
                    "files",
                    "/" + quote(str(path.relative_to(WORKSPACE_DATA))),
                    "open note »",
                ),
            }
        )
    return rows


# ---- tasks and steps ------------------------------------------------------------


def collect_tasks_and_steps(
    agent_to_chat: dict[str, str],
) -> tuple[list[dict], list[dict]]:
    """Tracked work: ``tasks`` (shared work items) and ``steps`` (each chat's progress timeline)."""
    tasks, steps = [], []
    for path in sorted(TICKETS_DIR.glob("*.md")) if TICKETS_DIR.is_dir() else []:
        if path.name == "README.md":
            continue
        try:
            fields, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        title_match = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        summary_match = re.search(
            r"^## Summary\s*\n+(.+?)(?:\n## |\Z)", body, re.MULTILINE | re.DOTALL
        )
        created = _iso_ts(fields.get("created"))
        closed = _iso_ts(fields.get("closed"))
        agent = fields.get("agent") or fields.get("assignee") or ""
        row = {
            "id": fields.get("id") or path.stem,
            "title": title_match.group(1).strip() if title_match else path.stem,
            "status": fields.get("status", ""),
            "summary": summary_match.group(1).strip() if summary_match else "",
            "created": _when(created) if created else "",
            "finished": _when(closed) if closed else "",
            "ts": int(created) if created else 0,
            "open": raw_link("ticket", path.stem),
        }
        if fields.get("step") == "true":
            chat_id = agent_to_chat.get(agent, "")
            row["chat"] = chat_id
            steps.append(row)
        else:
            row["worked_on_by"] = agent
            tasks.append(row)
    tasks.sort(key=lambda r: r["ts"], reverse=True)
    steps.sort(key=lambda r: r["ts"], reverse=True)
    return tasks, steps


# ---- apps, skills, scheduled jobs ----------------------------------------------------


def collect_apps() -> list[dict]:
    try:
        registry = tomllib.loads(APP_REGISTRY.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []
    rows = []
    for entry in registry.get("apps", []):
        if entry.get("internal") or not entry.get("display_name"):
            continue
        name = entry["name"]
        rows.append(
            {
                "id": name,
                "title": entry["display_name"],
                "built_in": entry.get("priority", "user") != "user",
                "launcher_order": entry.get("launcher_rank") or 999,
                "open": open_link(name, "", f"open {entry['display_name']} »"),
            }
        )
    rows.sort(key=lambda r: (r["launcher_order"], r["title"]))
    return rows


def collect_skills() -> list[dict]:
    rows = []
    for skill_md in (
        sorted(SKILLS_DIR.glob("*/SKILL.md")) if SKILLS_DIR.is_dir() else []
    ):
        try:
            fields, _ = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
        except OSError:
            continue
        name = fields.get("name") or skill_md.parent.name
        rows.append(
            {
                "id": name,
                "about": fields.get("description", ""),
                "made_by": "imbue (built in)"
                if fields.get("author") == "imbue"
                else "you",
                "modified": _when(skill_md.stat().st_mtime),
                "ts": int(skill_md.stat().st_mtime),
                "open": raw_link("skill", skill_md.parent.name),
            }
        )
    return rows


def collect_scheduled_jobs() -> list[dict]:
    rows = []
    for job_dir in sorted(JOBS_DIR.iterdir()) if JOBS_DIR.is_dir() else []:
        if not job_dir.is_dir():
            continue
        last = job_dir / "last_success"
        try:
            last_ts = (
                float(last.read_text(encoding="utf-8").strip())
                if last.exists()
                else None
            )
        except (OSError, ValueError):
            last_ts = None
        rows.append(
            {
                "id": job_dir.name,
                "last_success": _when(last_ts) if last_ts else "never",
                "ts": int(last_ts or 0),
            }
        )
    return rows


# ---- each app's own stored records ---------------------------------------------------


def dataset_name(*parts: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", "_".join(parts).lower()).strip("_")


def _rows_from_json_value(value: object) -> list[dict]:
    """A JSON file's records: a list of objects, a single object, or an object holding one list of them."""
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    if isinstance(value, dict):
        lists = [
            v
            for v in value.values()
            if isinstance(v, list) and v and all(isinstance(x, dict) for x in v)
        ]
        return lists[0] if len(lists) == 1 else [value]
    return []


def _number_or_text(value: str) -> object:
    text = value.strip()
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d*\.\d+", text):
        return float(text)
    return value


# Logins and keys an app keeps beside its records are never data the user browses: a file named
# like one is skipped whole, and a field named like one is dropped from every row.
SECRET_FILE_NAME = re.compile(
    r"token|secret|credential|password|passwd|api[_-]?key|private[_-]?key|cookie|oauth",
    re.IGNORECASE,
)
SECRET_FIELD_NAME = re.compile(
    r"secret|password|passwd|api_?key|private_?key|credential|encrypted|cookie|authorization"
    r"|bearer|token$",
    re.IGNORECASE,
)


def _without_secrets(value: object) -> object:
    if isinstance(value, dict):
        return _record_without_secrets(value)
    if isinstance(value, list):
        return [_without_secrets(v) for v in value]
    return value


def _record_without_secrets(record: dict) -> dict:
    return {
        key: _without_secrets(val)
        for key, val in record.items()
        if not SECRET_FIELD_NAME.search(str(key))
    }


def read_table(path: Path) -> list[dict]:
    """The records in one data file: JSON, JSON-lines, CSV, or TSV, with any login or key fields removed.

    Unreadable or oversized files, and files that hold logins or keys, give none.
    """
    if SECRET_FILE_NAME.search(path.name):
        return []
    try:
        if path.stat().st_size > TABLE_MAX_BYTES:
            return []
    except OSError:
        return []
    if path.suffix == ".jsonl":
        rows = list(_read_jsonl(path))
    elif path.suffix == ".json":
        rows = _rows_from_json_value(_read_json(path))
    else:
        try:
            with path.open(encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(
                    handle, delimiter="\t" if path.suffix == ".tsv" else ","
                )
                rows = [
                    {key: _number_or_text(val or "") for key, val in row.items() if key}
                    for row in reader
                ]
        except (OSError, UnicodeDecodeError, csv.Error):
            return []
    return [_record_without_secrets(row) for row in rows[:TABLE_MAX_ROWS]]


def _source(name: str, group: str, about: str, source: str, rows: list[dict]) -> dict:
    """One dataset beyond the curated ones, with where it came from and any saved views it brings."""
    return {
        "name": name,
        "group": group,
        "about": about,
        "source": source,
        "rows": rows,
        "queries": [],
    }


def _part_name(stem: str) -> str:
    """A packaged part's property name: its file's name, as a name a query can traverse.

    A name starting with a digit, or one the item uses itself (``id``, ``name``), gets ``part_``.
    """
    name = dataset_name(stem)
    if name and not name[0].isdigit() and name not in ("id", "name"):
        return name
    return f"part_{name}"


def _packaged(
    name: str,
    label: str,
    group: str,
    about: str,
    source: str,
    parts: list[tuple[str, list[dict]]],
) -> dict:
    """Related datasets packaged as one dataset of one item, each part a property holding its rows.

    ``x.part`` then reaches a part's rows, so the group reads as one thing (``voice_chat.commands``)
    instead of a scatter of look-alike datasets. The item's ``id`` and ``name`` come first (the
    export keeps them there, see ``export.ordered``).
    """
    item: dict = {"id": name, "name": label}
    for part, rows in parts:
        item[part] = rows
    names = ", ".join(part for part, _ in parts)
    return {
        **_source(
            name,
            group,
            f"{about} One item; each of {names} holds its records.",
            source,
            [item],
        ),
        "packaged": True,
    }


def is_group_item(rows: list[dict]) -> bool:
    """Whether a dataset is one packaged item: a single row holding nested records."""
    return len(rows) == 1 and any(
        isinstance(v, dict)
        or (isinstance(v, list) and any(isinstance(x, dict) for x in v))
        for v in rows[0].values()
    )


def _group_or_single(
    name: str,
    label: str,
    group: str,
    about: str,
    source: str,
    entries: list[tuple[str, Path, list[dict]]],
    single_name,
    single_about,
) -> list[dict]:
    """Two or more related files become one packaged item; a lone file stays a dataset of its own."""
    if len(entries) >= 2:
        return [
            _packaged(
                name,
                label,
                group,
                about,
                source,
                [(_part_name(stem), rows) for stem, _, rows in entries],
            )
        ]
    return [
        _source(single_name(stem), group, single_about(path), str(path), rows)
        for stem, path, rows in entries
    ]


def _record_files(root: Path) -> list[tuple[str, Path, list[dict]]]:
    """(name relative to ``root``, path, records) for every JSON or JSON-lines file under ``root`` with records."""
    entries = []
    for path in sorted(root.rglob("*")):
        if path.suffix not in (".json", ".jsonl") or not path.is_file():
            continue
        rows = read_table(path)
        if rows:
            entries.append((str(path.relative_to(root).with_suffix("")), path, rows))
    return entries


def app_data_mtime(app: str) -> float:
    """When anything an app keeps under ``data/.apps/<app>/`` last changed."""
    app_dir = WORKSPACE_DATA / ".apps" / app
    if not app_dir.is_dir():
        return 0.0
    return max(
        (p.stat().st_mtime for p in app_dir.rglob("*") if p.is_file()), default=0.0
    )


def collect_app_records(claimed: frozenset[str] = frozenset()) -> list[dict]:
    """The records each app keeps under ``data/.apps/<app>/`` (JSON or JSON-lines).

    An app keeping several files is packaged as one item named after the app; one whose project
    hands its stored records over itself (``claimed``) is left to its project.
    """
    out = []
    apps_dir = WORKSPACE_DATA / ".apps"
    for app_dir in sorted(apps_dir.iterdir()) if apps_dir.is_dir() else []:
        app = app_dir.name
        if not app_dir.is_dir() or app in APP_DATA_SKIPPED or app in claimed:
            continue
        out.extend(
            _group_or_single(
                dataset_name(app),
                app,
                f"app: {app}",
                f"Records the {app} app keeps.",
                str(app_dir),
                _record_files(app_dir),
                lambda stem, app=app: dataset_name(app, stem),
                lambda path, app=app: f"Records the {app} app keeps ({path.name}).",
            )
        )
    return out


def collect_skill_records() -> list[dict]:
    """The records each skill keeps for itself under ``data/.skills/<skill>/``, one item per skill when several."""
    out = []
    skills_dir = WORKSPACE_DATA / ".skills"
    for skill_dir in sorted(skills_dir.iterdir()) if skills_dir.is_dir() else []:
        if not skill_dir.is_dir():
            continue
        skill = skill_dir.name
        out.extend(
            _group_or_single(
                dataset_name("skill", skill),
                skill,
                f"skill: {skill}",
                f"Records the {skill} skill keeps.",
                str(skill_dir),
                _record_files(skill_dir),
                lambda stem, skill=skill: dataset_name("skill", skill, stem),
                lambda path,
                skill=skill: f"Records the {skill} skill keeps ({path.name}).",
            )
        )
    return out


_NAME_SPLIT = re.compile(r"[-_. ]+")


def _family(stem: str) -> tuple[str, str]:
    """A file name's (family, rest): its first word, and what follows it (``workspace-skills-2026`` -> workspace, skills-2026)."""
    first, _, rest = _NAME_SPLIT.sub("-", stem.strip("-_. ")).partition("-")
    return first.lower(), rest


def collect_folder_tables() -> list[dict]:
    """Every data file (CSV, TSV, JSON, JSON-lines) in the user's own folders.

    Files in one folder whose names start with the same word (``workspace-skills.json``,
    ``workspace-templates.json``) are one family, packaged as one item named after that word
    (``workspace``, with ``skills`` and ``templates``); any other file is a dataset of its own.
    """
    out: list[dict] = []
    if not WORKSPACE_DATA.is_dir():
        return out
    for top in sorted(WORKSPACE_DATA.iterdir()):
        if not top.is_dir() or top.name.startswith("."):
            continue
        families: dict[tuple[Path, str], list[tuple[str, Path, list[dict]]]] = {}
        for path in sorted(top.rglob("*")):
            rel = path.relative_to(WORKSPACE_DATA)
            if path.suffix not in TABLE_SUFFIXES or not path.is_file():
                continue
            if any(part.startswith(".") for part in rel.parts):
                continue
            rows = read_table(path)
            if rows:
                family, rest = _family(path.stem)
                families.setdefault((path.parent, family), []).append(
                    (rest, path, rows)
                )
        for (folder, family), entries in families.items():
            folder_rel = folder.relative_to(WORKSPACE_DATA)
            if len(entries) >= 2 and family and all(rest for rest, _, _ in entries):
                out.append(
                    _packaged(
                        dataset_name(family),
                        family,
                        f"folder: {top.name}",
                        f"The {family} files in {folder_rel}.",
                        str(folder / f"{family}*"),
                        [(_part_name(rest), rows) for rest, _, rows in entries],
                    )
                )
                continue
            for _, path, rows in entries:
                rel = path.relative_to(WORKSPACE_DATA)
                out.append(
                    _source(
                        dataset_name(str(rel.with_suffix(""))),
                        f"folder: {top.name}",
                        f"The records in {rel}.",
                        str(path),
                        rows,
                    )
                )
    return out


def _user_projects() -> Iterator[tuple[str, Path]]:
    """(app name, app directory) for every app the user had built: priority ``user`` in its manifest."""
    for manifest_path in sorted(APPS_DIR.glob("*/app.toml")):
        try:
            manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            continue
        name = manifest.get("name", "")
        if manifest.get("priority", "user") == "user" and name and name != SELF_APP:
            yield name, manifest_path.parent


def _tree_mtime(root: Path) -> float:
    return max((p.stat().st_mtime for p in root.rglob("*.py")), default=0.0)


_PROVIDER_CACHE: dict[str, tuple[tuple[float, float], list[dict], bool]] = {}


def _run_provider(app: str, package: str, package_dir: Path) -> tuple[list[dict], bool]:
    """Run a project's ``<package>.datasets`` module and read what it writes: (datasets, whether it
    hands over the app's stored records itself).

    Cached until the package's Python or the app's stored records change, so a module that reads
    those records stays current.
    """
    key = (_tree_mtime(package_dir), app_data_mtime(app))
    cached = _PROVIDER_CACHE.get(package)
    if cached and cached[0] == key:
        return cached[1], cached[2]
    source = str(package_dir / "datasets.py")
    try:
        python = PROJECT_PYTHON if Path(PROJECT_PYTHON).exists() else sys.executable
        # The project's own source goes first on the path, so its module runs even where the
        # project is not installed into that interpreter.
        env = {
            **os.environ,
            "PYTHONPATH": os.pathsep.join(
                filter(
                    None,
                    [
                        str(package_dir.parent.resolve()),
                        os.environ.get("PYTHONPATH", ""),
                    ],
                )
            ),
        }
        proc = subprocess.run(
            [python, "-m", f"{package}.datasets"],
            env=env,
            capture_output=True,
            text=True,
            timeout=PROVIDER_TIMEOUT_SECONDS,
        )
        payload = json.loads(proc.stdout) if proc.returncode == 0 else None
        problem = (
            ""
            if proc.returncode == 0
            else (proc.stderr.strip().splitlines() or ["failed"])[-1]
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        payload, problem = None, str(error)
    except json.JSONDecodeError:
        payload, problem = None, "did not write JSON"
    out = []
    claims = isinstance(payload, dict) and payload.get("includes_app_records") is True
    if isinstance(payload, dict) and isinstance(payload.get("datasets"), dict):
        for name, spec in payload["datasets"].items():
            if isinstance(spec, dict) and isinstance(spec.get("rows"), list):
                rows = [r for r in spec["rows"] if isinstance(r, dict)][:TABLE_MAX_ROWS]
                out.append(
                    {
                        **_source(
                            dataset_name(name),
                            f"project: {app}",
                            str(spec.get("about", "")),
                            source,
                            rows,
                        ),
                        "packaged": is_group_item(rows),
                    }
                )
        if out:
            out[0]["queries"] = [
                {
                    "name": str(q["name"]),
                    "query": str(q["query"]),
                    "tag": f"project: {app}",
                    "hide": [str(h) for h in q.get("hide", [])],
                }
                for q in payload.get("queries", [])
                if isinstance(q, dict) and q.get("name") and q.get("query")
            ]
    else:
        out.append(
            _source(
                dataset_name(app, "unreadable"),
                f"project: {app}",
                f"Could not read this project's datasets: {problem[:300]}",
                source,
                [],
            )
        )
    _PROVIDER_CACHE[package] = (key, out, claims)
    return out, claims


def collect_project_datasets() -> tuple[list[dict], frozenset[str]]:
    """The data of every project the user had built, and the apps whose stored records a project hands over itself.

    A project hands its data over in either of two ways: data files shipped with its code
    (JSON, JSON-lines, CSV, TSV, excluding build configuration; several are packaged as one item
    named after the app), or -- for data that lives in code, like a game's roster -- a
    ``<package>/datasets.py`` module whose ``python -m`` run writes
    ``{"datasets": {name: {"about": str, "rows": [object, ...]}}, "queries": [...],
    "includes_app_records": bool}`` to stdout; each optional query is ``{"name", "query",
    "hide"?}``, a saved view of those datasets, and ``includes_app_records`` says the module
    already packages the app's stored records, so they are not listed again.
    """
    out = []
    claimed = set()
    for app, app_dir in _user_projects():
        for package_dir in (
            sorted((app_dir / "src").glob("*/")) if (app_dir / "src").is_dir() else []
        ):
            if (package_dir / "datasets.py").is_file():
                sources, claims = _run_provider(app, package_dir.name, package_dir)
                out.extend(sources)
                if claims:
                    claimed.add(app)
        entries = []
        for path in sorted(app_dir.rglob("*")):
            rel = path.relative_to(app_dir)
            if (
                path.suffix not in TABLE_SUFFIXES
                or not path.is_file()
                or path.name in BUILD_CONFIG_FILES
            ):
                continue
            if any(
                part.startswith(".") or part in ("node_modules", "dist", "__pycache__")
                for part in rel.parts
            ):
                continue
            rows = read_table(path)
            if rows:
                entries.append((str(rel.with_suffix("")), path, rows))
        out.extend(
            _group_or_single(
                dataset_name(app),
                app,
                f"project: {app}",
                f"The data files the {app} project ships.",
                str(app_dir),
                entries,
                lambda stem, app=app: dataset_name(app, stem),
                lambda path,
                app=app,
                app_dir=app_dir: f"The records in the {app} project's {path.relative_to(app_dir)}.",
            )
        )
    return out, frozenset(claimed)


def agent_chat_map() -> dict[str, str]:
    """Agent id -> the chat it belongs to (its own id when it is a chat of its own)."""
    mapping: dict[str, str] = {}
    for _, identity, _ in _agent_dirs():
        mapping[identity["id"]] = chat_key(
            identity["labels"].get("chat_id") or identity["id"]
        )
    return mapping
