"""Assemble the workspace's one DACTAL export: ``{export, data, queries}``.

The export is the single source of truth the query page reads. It is rebuilt from the
workspace's own records (``collect``) and written only when its content changed, so its
content hash is a faithful data version: the query page re-imports its private browser copy
only when that hash moves (see ``runner``).

Every saved query below is a view the user can open from the query page's saved-query menu.
The ``analyses`` ones form the narrated tour, chained in ``order``.
"""

import hashlib
import json
from pathlib import Path

from workspace_data import collect

DBNAME = "workspace"
FIRST_TOUR_QUERY = "everything in this workspace"

CONNECTORS = [
    {
        "connector": "dactal_chart.js",
        "description": "Line/bar/area/scatter chart renderer.",
    },
]

# Raw-anchor links and machine fields stay in the data (they are what joins and links run on)
# but are hidden from the tables that would otherwise be cluttered by them.
HIDE_TS = ["ts", "started_ts", "last_ts"]

DATASET_ABOUT = {
    "chats": "Your conversations with your mind, including background work it ran for you.",
    "messages": "Every message in those conversations -- your words and your mind's replies (not its tool output).",
    "steps": "The progress timeline of each conversation: what your mind set out to do and what it did.",
    "tasks": "Shared work items your mind tracked across conversations.",
    "files": "Every file in your own folders (documents, uploads, projects, memories).",
    "memories": "What your mind has noted to remember about you and your work.",
    "apps": "The apps you can open in this workspace.",
    "skills": "The routines your mind knows how to run, built in or made for you.",
    "scheduled_jobs": "Jobs set to run on a schedule.",
}


# What clicking a dataset in the catalog runs after its name: its canonical sort.
BROWSE_SORT = {
    "chats": "#-last_ts",
    "messages": "#-ts",
    "steps": "#-ts",
    "tasks": "#-ts",
    "files": "#-ts",
    "memories": "#-ts",
    "apps": "#launcher_order",
    "skills": "#made_by,id",
    "scheduled_jobs": "#-ts",
}
ALL_ROWS = ":@<=5000"
# Dataset names the export uses itself; a project dataset with one of them is renamed.
RESERVED_NAMES = frozenset(
    {"datasets", "connectors", "queries", "adapters", "annotators"}
)


def note(text: str) -> str:
    return f"\n???note=[ {text} ]"


def _messages_qlink(id_field: str) -> str:
    return f"|messages=(...qlink=(...id=(.{id_field}),query=[|chat.messages#-ts|text;|role;|when;],text=~[messages »]))"


def build_queries(dataset_names: list[str], extra_queries: list[dict]) -> list[dict]:
    queries: list[dict] = [
        # ---- the narrated tour ----
        {
            "name": FIRST_TOUR_QUERY,
            "tag": "analyses",
            "order": 1,
            "hide": ["browse", "source"],
            "query": "datasets#-group,-rows |open=(...qlink=(...id=(.id),query=(.browse),text=~[open »]))"
            + note(
                "Everything this workspace keeps that is yours, one dataset per row. Click 'open »' "
                "to see a dataset. Every view after this one is a different question of this same data, "
                "and the query box above always shows exactly what produced the table."
            ),
        },
        {
            "name": "data from your projects",
            "tag": "analyses",
            "order": 2,
            "hide": ["browse", "source"],
            "query": "datasets:group-=workspace#group,-rows |open=(...qlink=(...id=(.id),query=(.browse),text=~[open »]))"
            + note(
                "The data of the projects you have built: what each app hands over (like a game's "
                "roster), the records each app and skill keeps, and the data files in your folders. "
                "A new project's data appears here on its own."
            ),
        },
        {
            "name": "your conversations",
            "tag": "analyses",
            "order": 3,
            "hide": HIDE_TS + ["id", "launched_by"],
            "query": "chats#-last_ts"
            + _messages_qlink("id")
            + " |title;"
            + note(
                "Each conversation, most recently active first. 'messages »' opens just that "
                "conversation's messages; 'open chat »' takes you to it."
            ),
        },
        {
            "name": "messages per day",
            "tag": "analyses",
            "order": 4,
            "query": "messages/day#name... |chart=(.of...x=~name,y=~count,type=bar,height=320,width=760)"
            + note("How much was said each day, across every conversation."),
        },
        {
            "name": "what you asked for",
            "tag": "analyses",
            "order": 5,
            "hide": HIDE_TS + ["id", "chat", "role", "source", "day"],
            "query": "messages:role=you#-ts|text;|when;"
            + note("Only your own words, newest first."),
        },
        {
            "name": "who does the talking",
            "tag": "analyses",
            "order": 6,
            "query": "messages/chat_title,role |count |characters=~(.of..length....total)#-count"
            + note(
                "Messages and characters written per conversation, by you and by your mind."
            ),
        },
        {
            "name": "recent progress",
            "tag": "analyses",
            "order": 7,
            "hide": ["ts", "id", "status"],
            "query": "steps:status=closed#-ts|summary;|title;|finished;"
            + note(
                "What your mind did, step by step, most recent first, with the conversation each step "
                "belongs to."
            ),
        },
        {
            "name": "your files by folder",
            "tag": "analyses",
            "order": 8,
            "query": "files/folder |count |total_kb=~(.of..size_kb....total)#-count"
            + note(
                "How many files each of your folders holds, and how much space they take."
            ),
        },
        # ---- one browse view per dataset ----
        {
            "name": "all chats",
            "tag": "browse",
            "hide": HIDE_TS,
            "query": "chats#-last_ts" + _messages_qlink("id"),
        },
        {
            "name": "all messages",
            "tag": "browse",
            "hide": HIDE_TS + ["day"],
            "query": "messages#-ts|text;|role;|chat_title;|when;",
        },
        {"name": "all steps", "tag": "browse", "hide": ["ts"], "query": "steps#-ts"},
        {"name": "all tasks", "tag": "browse", "hide": ["ts"], "query": "tasks#-ts"},
        {"name": "all files", "tag": "browse", "hide": ["ts"], "query": "files#-ts"},
        {
            "name": "all memories",
            "tag": "browse",
            "hide": ["ts"],
            "query": "memories#-ts",
        },
        {"name": "all apps", "tag": "browse", "query": "apps#launcher_order"},
        {
            "name": "all skills",
            "tag": "browse",
            "hide": ["ts"],
            "query": "skills#made_by,id",
        },
        {
            "name": "all scheduled jobs",
            "tag": "browse",
            "hide": ["ts"],
            "query": "scheduled_jobs#-ts",
        },
        # ---- diagnostics: each should return nothing ----
        {
            "name": "messages with no conversation",
            "tag": "diagnostic",
            "query": "messages:-(.chat.chats)",
        },
        {
            "name": "steps with an unknown conversation",
            "tag": "diagnostic",
            "query": "steps:chat+:-(.chat.chats)",
        },
    ]
    queries.extend(extra_queries)
    present = set(dataset_names)
    # The stock front-end reads every saved query's ``hide`` and ``order`` and breaks on a query
    # that lacks them, so each one carries both, empty when unused.
    return [
        {"hide": [], "order": None, **q}
        for q in queries
        if _uses_present(q["query"], present)
    ]


def _uses_present(query: str, present: set[str]) -> bool:
    """Drop a browse view whose dataset is empty here (DACTAL would show its name as a literal)."""
    head = query.split("#")[0].split(":")[0].split("/")[0].split("|")[0].strip()
    return head in present or head not in DATASET_ABOUT


def build_export() -> dict:
    chats, messages = collect.collect_chats_and_messages()
    agent_to_chat = collect.agent_chat_map()
    known_chats = {c["id"] for c in chats}
    tasks, steps = collect.collect_tasks_and_steps(agent_to_chat)
    for step in steps:
        if step["chat"] not in known_chats:
            step["chat"] = ""
    curated = {
        "chats": chats,
        "messages": messages,
        "steps": steps,
        "tasks": tasks,
        "files": collect.collect_files(),
        "memories": collect.collect_memories(),
        "apps": collect.collect_apps(),
        "skills": collect.collect_skills(),
        "scheduled_jobs": collect.collect_scheduled_jobs(),
    }
    data: dict[str, list] = {name: rows for name, rows in curated.items() if rows}
    catalog = [
        {
            "id": name,
            "group": "workspace",
            "rows": len(rows),
            "about": DATASET_ABOUT[name],
            "browse": BROWSE_SORT.get(name, ALL_ROWS),
        }
        for name, rows in curated.items()
        if rows
    ]
    extra_queries: list[dict] = []
    sources = (
        collect.collect_project_datasets()
        + collect.collect_folder_tables()
        + collect.collect_app_records()
        + collect.collect_skill_records()
    )
    for source in sources:
        name = source["name"]
        if name in data or name in RESERVED_NAMES:
            name = collect.dataset_name(source["group"].split(": ", 1)[-1], name)
        if source["rows"]:
            data[name] = source["rows"]
            extra_queries.append(
                {
                    "name": f"all {name.replace('_', ' ')}",
                    "tag": source["group"],
                    "query": name,
                }
            )
        extra_queries.extend(source["queries"])
        catalog.append(
            {
                "id": name,
                "group": source["group"],
                "rows": len(source["rows"]),
                "about": source["about"],
                "browse": ALL_ROWS,
                "source": source["source"],
            }
        )
    data["datasets"] = catalog
    data["connectors"] = CONNECTORS
    return {
        "export": DBNAME,
        "data": data,
        "queries": build_queries(list(data), extra_queries),
    }


def write_export(path: Path) -> bool:
    """Rebuild the export and write it to ``path`` only when its content changed. Returns whether it did."""
    body = json.dumps(
        build_export(), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    try:
        if path.read_text(encoding="utf-8") == body:
            return False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(path)
    return True


def content_version(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    except OSError:
        return "none"
