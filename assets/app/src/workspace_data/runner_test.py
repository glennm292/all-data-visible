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
