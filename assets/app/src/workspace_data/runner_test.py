"""The server rebuilds its data in a separate, short-lived process, and only while it is in use."""

import os
import time
from pathlib import Path

import pytest

from workspace_data import export, runner


@pytest.fixture
def rebuilds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[tuple[Path, float]]:
    calls: list[tuple[Path, float]] = []
    monkeypatch.setattr(runner, "_rebuild", lambda path, max_age: calls.append((path, max_age)))
    monkeypatch.setattr(runner, "EXPORT_PATH", tmp_path / "workspace.json")
    monkeypatch.setattr(runner, "_last_refresh", {"at": 0.0})
    return calls


# The jobs below run in the spawned child, which imports them from this module.
def _write_pid(path: Path, machine_max_age: float) -> None:
    path.write_text(f"{os.getpid()} {machine_max_age}")


def _die(path: Path, machine_max_age: float) -> None:
    os._exit(137)


def _fail(path: Path, machine_max_age: float) -> None:
    raise ValueError("no room")


def test_a_refresh_rebuilds_the_export_with_the_asked_age(rebuilds: list[tuple[Path, float]]) -> None:
    runner.refresh_export(machine_max_age=runner.ANY_AGE)
    assert rebuilds == [(runner.EXPORT_PATH, runner.ANY_AGE)]


def test_a_rebuild_runs_the_exports_rebuild_by_default() -> None:
    assert runner._rebuild.__defaults__ == (export.rebuild_in_own_process,)


def test_a_rebuild_runs_in_a_process_of_its_own(tmp_path: Path) -> None:
    out = tmp_path / "out"
    runner._rebuild(out, 5.0, job=_write_pid)
    pid, age = out.read_text().split()
    assert int(pid) != os.getpid()
    assert float(age) == 5.0


def test_a_killed_rebuild_is_reported(tmp_path: Path) -> None:
    with pytest.raises(runner.ExportRebuildError, match="killed"):
        runner._rebuild(tmp_path / "out", 5.0, job=_die)


def test_a_failed_rebuild_is_reported(tmp_path: Path) -> None:
    with pytest.raises(OSError, match="no room"):
        runner._rebuild(tmp_path / "out", 5.0, job=_fail)


def test_the_background_loop_rests_while_nobody_uses_the_page(
    rebuilds: list[tuple[Path, float]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        runner, "_last_used", {"at": time.monotonic() - runner.IDLE_AFTER_SECONDS - 1}
    )
    assert runner._background_tick() is False
    assert rebuilds == []
    runner._page_used()
    assert runner._background_tick() is True
    assert len(rebuilds) == 1


def test_a_page_load_uses_the_cached_folder_tree(rebuilds: list[tuple[Path, float]]) -> None:
    response = runner.app.test_client().get("/")
    assert response.status_code == 302
    assert rebuilds[0][1] == runner.ANY_AGE


def test_a_projects_own_renderer_is_loaded_as_a_module(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A renderer has to import its siblings, which only a module served from this origin can do."""
    assets = tmp_path / "src" / "demo" / "assets"
    assets.mkdir(parents=True)
    (assets / runner.collect.RENDERER_FILE).write_text('import "./draw.js";')
    monkeypatch.setattr(runner.collect, "project_asset_dirs", lambda: {"demo-app": assets})

    tags = runner._project_renderer_scripts()

    assert tags == '<script type="module" src="app-assets/demo-app/dactal_render.js"></script>'


def test_an_app_with_no_renderer_loads_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    monkeypatch.setattr(runner.collect, "project_asset_dirs", lambda: {"demo-app": assets})

    assert runner._project_renderer_scripts() == ""


def test_a_projects_assets_are_served_but_nothing_outside_them(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "draw.js").write_text("// drawing")
    (tmp_path / "secret.py").write_text("# not an asset")
    monkeypatch.setattr(runner.collect, "project_asset_dirs", lambda: {"demo-app": assets})
    client = runner.app.test_client()

    assert client.get("/app-assets/demo-app/draw.js").get_data(as_text=True) == "// drawing"
    assert client.get("/app-assets/demo-app/../secret.py").status_code == 404
    assert client.get("/app-assets/not-an-app/draw.js").status_code == 404


class _NoThread:
    def start(self) -> None:
        pass


class _StillStamp:
    """A collector whose stamp never moves after the first walk, as a quiet workspace's does not."""

    def __init__(self) -> None:
        self.walks = 0

    def sources_stamp(self) -> str:
        self.walks += 1
        return "one-change-then-quiet"


# ---- keeping a page that is already open current -----------------------------------------


def test_a_change_is_rebuilt_sooner_than_the_long_way_round() -> None:
    """The whole point: a change made a moment ago does not wait out the slow refresh."""
    assert runner._due_for_rebuild(waiting=True, since=runner._rebuild_gap() + 1) is True
    assert runner._due_for_rebuild(waiting=False, since=runner._rebuild_gap() + 1) is False


def test_a_busy_workspace_is_not_rebuilt_over_and_over(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every message of a live conversation is a change; rebuilding on each would eat the machine."""
    monkeypatch.setattr(runner, "_rebuild_cost", {"seconds": 8.0})

    assert runner._rebuild_gap() == runner.REBUILD_GAP_MULTIPLE * 8.0
    assert runner._due_for_rebuild(waiting=True, since=1.0) is False
    # What a stamp cannot see still gets picked up the long way round.
    assert runner._due_for_rebuild(waiting=False, since=runner.BACKGROUND_REFRESH_SECONDS) is True


def test_the_watch_gap_grows_with_what_the_walk_costs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "_stamp_cost", {"seconds": 0.0})
    assert runner._watch_gap() == runner.WATCH_SECONDS

    monkeypatch.setattr(runner, "_stamp_cost", {"seconds": 2.0})
    assert runner._watch_gap() == runner.WATCH_GAP_MULTIPLE * 2.0


def test_the_page_is_told_what_each_dataset_holds_now(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "EXPORT_PATH", tmp_path / "workspace.json")
    (tmp_path / export.VERSIONS_NAME).write_text('{"version": "abc", "datasets": {"rosette": "d1"}}')

    body = runner.app.test_client().get("/versions").get_json()

    assert body == {"version": "abc", "datasets": {"rosette": "d1"}}


def test_only_the_datasets_that_moved_are_sent_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sending the whole export back for one changed record is tens of megabytes of waste."""
    monkeypatch.setattr(runner, "EXPORT_PATH", tmp_path / "workspace.json")
    (tmp_path / "workspace.json").write_text(
        '{"export": "workspace", "data": {"rosette": [{"id": "r1"}], "messages": [{"id": "m1"}]}}'
    )
    client = runner.app.test_client()

    body = client.get("/datasets?names=rosette,nothing_here").get_json()

    assert body == {"export": "workspace", "data": {"rosette": [{"id": "r1"}]}}
    assert client.get("/datasets").status_code == 400


def test_the_served_page_keeps_itself_current(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without this the page shows what the workspace held when it was opened, until reloaded."""
    monkeypatch.setattr(runner.collect, "collect_project_renderers", list)

    page = runner.served_dactal_html()

    assert "fetch('versions'" in page
    assert "datasets?names=" in page
    assert f"var POLL_MS = {int(runner.WATCH_SECONDS * 1000)};" in page
    # Coming back to the window asks the server to look straight away.
    assert "tick(true)" in page


def test_a_change_seen_during_the_quiet_gap_is_not_forgotten(
    rebuilds: list[tuple[Path, float]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The walk that follows finds the stamp steady again, so the change has to be remembered."""
    monkeypatch.setattr(runner, "_last_stamp", {"value": ""})
    monkeypatch.setattr(runner, "_waiting", {"change": False})
    monkeypatch.setattr(runner, "_rebuild_cost", {"seconds": 0.0})
    monkeypatch.setattr(runner, "collect", _StillStamp())
    # A rebuild just happened, so the quiet gap has not run out yet.
    monkeypatch.setattr(runner, "_last_refresh", {"at": time.monotonic()})
    runner._page_used()

    runner._background_tick()
    assert rebuilds == []
    assert runner._waiting["change"] is True

    monkeypatch.setattr(
        runner, "_last_refresh", {"at": time.monotonic() - runner._rebuild_gap() - 1}
    )
    runner._background_tick()

    assert len(rebuilds) == 1
    assert runner._waiting["change"] is False


def test_the_app_still_starts_when_the_first_rebuild_is_killed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rebuild volunteers to be killed when memory runs short; that must not cost the app."""
    served: list[bool] = []

    def blown(max_age: float = 0.0, machine_max_age: float = 0.0) -> None:
        raise runner.ExportRebuildError("export rebuild failed: its process was killed")

    monkeypatch.setattr(runner, "refresh_export", blown)
    monkeypatch.setattr(runner.threading, "Thread", lambda **kwargs: _NoThread())
    monkeypatch.setattr(runner, "run_simple", lambda *a, **k: served.append(True))

    runner.main()

    assert served == [True]


def test_a_rebuild_that_was_killed_does_not_start_another_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retrying straight away is what turns one shortage of memory into a loop of them."""
    monkeypatch.setattr(runner, "EXPORT_PATH", tmp_path / "workspace.json")
    monkeypatch.setattr(runner, "_last_refresh", {"at": 0.0})
    monkeypatch.setattr(runner, "_rebuild", lambda path, max_age: (_ for _ in ()).throw(
        runner.ExportRebuildError("its process was killed")
    ))

    with pytest.raises(runner.ExportRebuildError):
        runner.refresh_export()

    assert runner._last_refresh["at"] > 0.0


def test_the_gap_widens_while_rebuilds_keep_being_killed(
    rebuilds: list[tuple[Path, float]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "_failures", {"in_a_row": 0})
    monkeypatch.setattr(runner, "_rebuild_cost", {"seconds": 0.0})
    steady = runner._rebuild_gap()

    monkeypatch.setattr(runner, "_failures", {"in_a_row": 3})
    assert runner._rebuild_gap() == steady * 8

    monkeypatch.setattr(runner, "_failures", {"in_a_row": 50})
    assert runner._rebuild_gap() == runner.MAX_REBUILD_GAP_SECONDS


def test_a_failed_rebuild_is_counted_and_a_good_one_clears_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner, "_failures", {"in_a_row": 0})
    monkeypatch.setattr(runner, "_waiting", {"change": True})
    monkeypatch.setattr(runner, "_last_refresh", {"at": 0.0})
    monkeypatch.setattr(runner, "collect", _StillStamp())
    runner._page_used()

    monkeypatch.setattr(runner, "refresh_export", lambda *a, **k: (_ for _ in ()).throw(
        runner.ExportRebuildError("its process was killed")
    ))
    runner._background_tick()
    assert runner._failures["in_a_row"] == 1

    monkeypatch.setattr(runner, "_waiting", {"change": True})
    monkeypatch.setattr(runner, "_last_refresh", {"at": 0.0})
    monkeypatch.setattr(runner, "refresh_export", lambda *a, **k: None)
    runner._background_tick()
    assert runner._failures["in_a_row"] == 0
