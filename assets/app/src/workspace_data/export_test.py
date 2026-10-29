"""The export gathers every kind of record from a workspace laid out like a real one.

Each test builds a small fake workspace under a temporary directory and points the collectors
at it, so what is asserted is what a real install would expose.
"""

import json
import os
from pathlib import Path

import pytest

from workspace_data import collect, export, machine_files

AGENT_ID = "agent-0123456789abcdef0123456789abcdef"
WORKER_ID = "agent-fedcba9876543210fedcba9876543210"

PROVIDER = """
import json, sys
rows = [{"id": "alpha", "power": 3}, {"id": "beta", "power": 5}]
moves = [{"id": "alpha_1", "demo_item": "alpha", "move": "Jab"}]
json.dump({"datasets": {"demo_items": {"about": "Demo items.", "rows": rows},
                        "demo_moves": {"about": "Demo moves.", "rows": moves}},
           "queries": [{"name": "demo items by power", "query": "demo_items#-power"}]}, sys.stdout)
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _events(*events: dict) -> str:
    return "\n".join(json.dumps(e) for e in events) + "\n"


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "data"
    mngr = tmp_path / "mngr"
    apps = tmp_path / "system" / "apps"
    # A chat with one exchange, and background work it launched.
    _write(
        mngr / "agents" / AGENT_ID / "data.json",
        json.dumps(
            {
                "id": AGENT_ID,
                "name": "Chat-1",
                "type": "claude",
                "labels": {"display_name": "Trip planning"},
            }
        ),
    )
    _write(
        mngr / "agents" / AGENT_ID / "events/claude/common_transcript/events.jsonl",
        _events(
            {"type": "header"},
            {
                "type": "step",
                "source": "user",
                "message": "Plan my trip",
                "timestamp": "2026-10-01T10:00:00Z",
                "event_id": "u1",
            },
            {
                "type": "step",
                "source": "agent",
                "message": "Here is a plan.",
                "timestamp": "2026-10-01T10:01:00Z",
                "event_id": "a1",
                "tool_calls": [{"id": "t"}],
            },
            {
                "type": "observation",
                "results": [{"content": "tool output that must not appear"}],
            },
        ),
    )
    _write(
        mngr / "agents" / WORKER_ID / "data.json",
        json.dumps(
            {
                "id": WORKER_ID,
                "name": "research",
                "type": "claude",
                "labels": {"agent_created": "true", "lead_agent": AGENT_ID},
            }
        ),
    )
    _write(
        mngr / "agents" / WORKER_ID / "events/claude/common_transcript/events.jsonl",
        _events(
            {
                "type": "step",
                "source": "user",
                "message": "Research hotels",
                "timestamp": "2026-10-01T10:02:00Z",
                "event_id": "w1",
            },
        ),
    )
    # Tracked work: one step of the chat, one shared task.
    _write(
        data / ".tickets" / "s-1.md",
        f"---\nid: s-1\nstatus: closed\nstep: true\nagent: {AGENT_ID}\n"
        "created: 2026-10-01T10:00:30Z\n---\n# Draft the plan\n\n## Summary\n\nWrote a first plan.\n",
    )
    _write(
        data / ".tickets" / "t-1.md",
        "---\nid: t-1\nstatus: open\ncreated: 2026-10-01T09:00:00Z\n---\n# Book flights\n",
    )
    # The user's folders, an app's stored records, a skill's stored records.
    _write(data / "my-project" / "budget.csv", "item,cost\nhotel,120\nfood,40.5\n")
    _write(data / "documents" / "notes.md", "# notes\n")
    _write(
        data / "memories" / "likes-trains.md",
        "---\nname: likes-trains\ndescription: Prefers trains\n"
        "metadata:\n  type: user\n---\nTakes the train when possible.\n",
    )
    _write(
        data / ".apps" / "planner" / "trips.jsonl",
        _events({"id": "trip1", "city": "Lisbon"}),
    )
    _write(
        data / ".skills" / "digest" / "seen.json",
        json.dumps([{"id": "n1"}, {"id": "n2"}]),
    )
    # A user-built app that hands over in-code data, and a built-in app that must be ignored.
    _write(
        apps / "demo_game" / "app.toml",
        'name = "demo-game"\ndisplay_name = "Demo"\npriority = "user"\n',
    )
    _write(apps / "demo_game" / "src" / "demo_game" / "__init__.py", "")
    _write(apps / "demo_game" / "src" / "demo_game" / "datasets.py", PROVIDER)
    _write(apps / "demo_game" / "package.json", '{"name": "not data"}')
    _write(
        apps / "browser" / "app.toml",
        'name = "browser"\ndisplay_name = "Browser"\npriority = "browser"\n',
    )
    _write(
        apps / "browser" / "src" / "browser" / "datasets.py", "raise SystemExit(1)\n"
    )

    monkeypatch.setattr(collect, "WORKSPACE_DATA", data)
    monkeypatch.setattr(collect, "TICKETS_DIR", data / ".tickets")
    monkeypatch.setattr(collect, "CHAT_STORE", data / ".apps" / "chat" / "chats")
    monkeypatch.setattr(collect, "JOBS_DIR", data / ".state" / "jobs")
    monkeypatch.setattr(collect, "APP_REGISTRY", data / ".state" / "apps.toml")
    monkeypatch.setattr(collect, "SKILLS_DIR", tmp_path / ".agents" / "skills")
    monkeypatch.setattr(collect, "MNGR_HOST_DIR", mngr)
    monkeypatch.setattr(collect, "APPS_DIR", apps)
    monkeypatch.setattr(collect, "PROJECT_PYTHON", "/nonexistent/python")
    monkeypatch.setattr(collect, "_PROVIDER_CACHE", {})
    _write(tmp_path / "machine" / "etc" / "hosts", "127.0.0.1 localhost\n")
    monkeypatch.setattr(machine_files, "ROOT", str(tmp_path / "machine"))
    monkeypatch.setattr(
        export, "MACHINE_FILES_CACHE", tmp_path / "cache" / "machine_files.json"
    )
    return tmp_path


def test_conversations_keep_only_the_words_exchanged(workspace: Path) -> None:
    data = export.build_export()["data"]
    chat = next(c for c in data["chats"] if c["title"] == "Trip planning")
    assert chat["id"] == collect.chat_key(AGENT_ID)
    assert chat["message_count"] == 2 and chat["from_you"] == 1
    texts = {m["text"] for m in data["messages"]}
    assert texts == {"Plan my trip", "Here is a plan.", "Research hotels"}
    assert all(m["chat"] in {c["id"] for c in data["chats"]} for m in data["messages"])


def test_background_work_points_at_the_chat_that_launched_it(workspace: Path) -> None:
    chats = export.build_export()["data"]["chats"]
    worker = next(c for c in chats if c["kind"] == "background work")
    assert worker["launched_by"] == collect.chat_key(AGENT_ID)
    assert worker["open"] == ""


def test_steps_join_their_chat_and_tasks_stay_separate(workspace: Path) -> None:
    data = export.build_export()["data"]
    assert [s["title"] for s in data["steps"]] == ["Draft the plan"]
    assert data["steps"][0]["chat"] == collect.chat_key(AGENT_ID)
    assert data["steps"][0]["summary"] == "Wrote a first plan."
    assert [t["title"] for t in data["tasks"]] == ["Book flights"]


def test_every_kind_of_project_data_becomes_a_dataset(workspace: Path) -> None:
    result = export.build_export()
    data = result["data"]
    assert data["my_project_budget"] == [
        {"item": "hotel", "cost": 120},
        {"item": "food", "cost": 40.5},
    ]
    assert data["planner_trips"] == [{"id": "trip1", "city": "Lisbon"}]
    assert len(data["skill_digest_seen"]) == 2
    assert [r["id"] for r in data["demo_items"]] == ["alpha", "beta"]
    assert data["demo_moves"][0]["demo_item"] == "alpha"
    groups = {r["id"]: r["group"] for r in data["datasets"]}
    assert groups["demo_items"] == "project: demo-game"
    assert groups["my_project_budget"] == "folder: my-project"
    assert not any("browser" in name or "package" in name for name in data)


def test_a_project_brings_its_own_saved_views(workspace: Path) -> None:
    queries = {q["name"]: q for q in export.build_export()["queries"]}
    assert queries["demo items by power"]["query"] == "demo_items#-power"
    assert queries["demo items by power"]["tag"] == "project: demo-game"


def test_every_saved_view_carries_hide_and_order(workspace: Path) -> None:
    for query in export.build_export()["queries"]:
        assert isinstance(query["hide"], list) and "order" in query, query["name"]


def test_tour_starts_with_the_catalog_and_names_have_no_apostrophes(
    workspace: Path,
) -> None:
    queries = export.build_export()["queries"]
    tour = sorted(
        (q for q in queries if q["tag"] == "analyses"), key=lambda q: q["order"]
    )
    assert tour[0]["name"] == export.FIRST_TOUR_QUERY
    assert all("'" not in q["name"] for q in queries)


def test_a_broken_project_is_reported_not_fatal(workspace: Path) -> None:
    _write(
        workspace / "system/apps/demo_game/src/demo_game/datasets.py",
        "raise SystemExit('boom')\n",
    )
    catalog = export.build_export()["data"]["datasets"]
    broken = next(
        r for r in catalog if r["group"] == "project: demo-game" and r["rows"] == 0
    )
    assert "boom" in broken["about"]


def test_export_is_rewritten_only_when_it_changes(workspace: Path) -> None:
    target = workspace / "out" / "workspace.json"
    assert export.write_export(target) is True
    version = export.content_version(target)
    assert export.write_export(target) is False
    _write(workspace / "data" / "my-project" / "more.csv", "a\n1\n")
    assert export.write_export(target) is True
    assert export.content_version(target) != version


def test_read_table_handles_each_format(tmp_path: Path) -> None:
    _write(tmp_path / "a.tsv", "x\ty\n1\tfoo\n")
    _write(tmp_path / "b.json", json.dumps({"items": [{"k": 1}], "meta": "x"}))
    _write(tmp_path / "c.jsonl", '{"k": 1}\nnot json\n{"k": 2}\n')
    assert collect.read_table(tmp_path / "a.tsv") == [{"x": 1, "y": "foo"}]
    assert collect.read_table(tmp_path / "b.json") == [{"k": 1}]
    assert collect.read_table(tmp_path / "c.jsonl") == [{"k": 1}, {"k": 2}]


def test_logins_and_keys_never_reach_the_export(workspace: Path) -> None:
    data_dir = workspace / "data"
    _write(
        data_dir / ".apps" / "music" / "spotify_token.json",
        json.dumps({"access_token": "SECRET_ACCESS", "expires_at": 1}),
    )
    _write(
        data_dir / ".apps" / "music" / "accounts.json",
        json.dumps(
            [
                {
                    "id": "a1",
                    "refresh_token": "SECRET_REFRESH",
                    "encrypted_refresh": "SECRET_ENCRYPTED",
                    "settings": {"api_key": "SECRET_KEY", "theme": "dark"},
                    "input_tokens": 12,
                }
            ]
        ),
    )
    result = export.build_export()
    assert "SECRET_" not in json.dumps(result)
    assert "music_spotify_token" not in result["data"]
    assert result["data"]["music_accounts"] == [
        {"id": "a1", "settings": {"theme": "dark"}, "input_tokens": 12}
    ]


def test_chat_keys_carry_no_hyphen() -> None:
    assert "-" not in collect.chat_key(AGENT_ID)


def test_the_machine_tree_is_file_system_and_the_users_folders_are_files(
    workspace: Path,
) -> None:
    result = export.build_export()
    data = result["data"]
    [root] = data["file system"]
    assert root["id"] == str(workspace / "machine")
    assert root["of"] == [{"name": "etc", "of": [["hosts", 20]]}]
    assert "my-project" in {r["folder"] for r in data["files"]}
    queries = {q["name"]: q["query"] for q in result["queries"]}
    assert queries["your files by folder"].startswith("files/folder")
    assert queries["this machine, from the top"].startswith("file system.of")


def test_an_apps_several_record_files_are_packaged_as_one_item(workspace: Path) -> None:
    _write(
        workspace / "data" / ".apps" / "planner" / "hotels.json",
        json.dumps([{"id": "h1", "name": "Casa"}]),
    )
    data = export.build_export()["data"]
    assert "planner_trips" not in data and "planner_hotels" not in data
    assert data["planner"] == [
        {
            "id": "planner",
            "name": "planner",
            "hotels": [{"id": "h1", "name": "Casa"}],
            "trips": [{"id": "trip1", "city": "Lisbon"}],
        }
    ]
    catalog = {r["id"]: r for r in data["datasets"]}
    assert catalog["planner"]["group"] == "app: planner"
    assert "hotels, trips" in catalog["planner"]["about"]


def test_a_family_of_files_in_one_folder_is_packaged_under_its_first_word(
    workspace: Path,
) -> None:
    docs = workspace / "data" / "documents"
    _write(docs / "workspace-skills-2026-10-16.json", json.dumps([{"id": "s"}]))
    _write(docs / "workspace-templates-2026-10-16.json", json.dumps([{"id": "t"}]))
    _write(docs / "budget-2026.csv", "item,cost\nrent,9\n")
    data = export.build_export()["data"]
    assert data["workspace"] == [
        {
            "id": "workspace",
            "name": "workspace",
            "skills_2026_10_16": [{"id": "s"}],
            "templates_2026_10_16": [{"id": "t"}],
        }
    ]
    assert not any(name.startswith("documents_workspace") for name in data)
    # A file with no family stays a dataset of its own.
    assert data["documents_budget_2026"] == [{"item": "rent", "cost": 9}]


def test_a_project_that_hands_over_its_stored_records_owns_them(
    workspace: Path,
) -> None:
    game = workspace / "system/apps/demo_game/src/demo_game/datasets.py"
    stored = workspace / "data" / ".apps" / "demo-game" / "scores.jsonl"
    _write(stored, _events({"id": "g1", "score": 3}))
    _write(
        game,
        "import json, pathlib, sys\n"
        f"lines = pathlib.Path({str(stored)!r}).read_text().splitlines()\n"
        "scores = [json.loads(l) for l in lines]\n"
        'json.dump({"datasets": {"demo": {"about": "All of it.", "rows": '
        '[{"id": "demo", "scores": scores}]}}, "includes_app_records": True}, sys.stdout)\n',
    )
    data = export.build_export()["data"]
    assert data["demo"][0]["scores"] == [{"id": "g1", "score": 3}]
    assert not any(name.startswith("demo_game") for name in data)
    # A new stored record reaches the item on the next rebuild, with no code change.
    with stored.open("a") as handle:
        handle.write(json.dumps({"id": "g2", "score": 5}) + "\n")
    os.utime(stored, (stored.stat().st_atime, stored.stat().st_mtime + 5))
    data = export.build_export()["data"]
    assert [s["id"] for s in data["demo"][0]["scores"]] == ["g1", "g2"]


def test_a_group_items_id_and_name_are_its_first_two_properties(
    workspace: Path,
) -> None:
    _write(
        workspace / "data" / ".apps" / "planner" / "hotels.json",
        json.dumps([{"id": "h1", "name": "Casa"}]),
    )
    _write(
        workspace / "system/apps/demo_game/src/demo_game/datasets.py",
        "import json, sys\n"
        'json.dump({"datasets": {"demo": {"about": "All of it.", "rows": '
        '[{"moves": [{"id": "m1"}], "name": "Demo", "id": "demo", "figures": [{"id": "f1"}]}]}}}, sys.stdout)\n',
    )
    target = workspace / "out" / "workspace.json"
    export.write_export(target)
    data = json.loads(target.read_text(encoding="utf-8"))["data"]
    assert list(data["planner"][0]) == ["id", "name", "hotels", "trips"]
    assert data["planner"][0]["name"] == "planner"
    assert list(data["demo"][0]) == ["id", "name", "figures", "moves"]
    # Every other row keeps its keys sorted, so the export's bytes stay stable.
    assert list(data["demo"][0]["figures"][0]) == ["id"]
    assert list(data["my_project_budget"][0]) == ["cost", "item"]
