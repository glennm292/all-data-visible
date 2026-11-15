"""The server rebuilds its data in a separate, short-lived process, and only while it is in use."""

import subprocess
import sys
import time
from pathlib import Path

import pytest

from workspace_data import runner


@pytest.fixture
def rebuilds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setattr(runner, "EXPORT_PATH", tmp_path / "workspace.json")
    monkeypatch.setattr(runner, "_last_refresh", {"at": 0.0})
    return commands


def test_a_rebuild_runs_as_its_own_program(rebuilds: list[list[str]]) -> None:
    runner.refresh_export(machine_max_age=runner.ANY_AGE)
    assert rebuilds == [
        [sys.executable, "-m", "workspace_data.export", str(runner.EXPORT_PATH), "inf"]
    ]


def test_a_failed_rebuild_is_reported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runner, "EXPORT_PATH", tmp_path / "workspace.json")
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda command, **_: subprocess.CompletedProcess(command, -9, "", "Killed"),
    )
    with pytest.raises(OSError, match="Killed"):
        runner.refresh_export()


def test_the_background_loop_rests_while_nobody_uses_the_page(
    rebuilds: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        runner, "_last_used", {"at": time.monotonic() - runner.IDLE_AFTER_SECONDS - 1}
    )
    assert runner._background_tick() is False
    assert rebuilds == []
    runner._page_used()
    assert runner._background_tick() is True
    assert len(rebuilds) == 1


def test_a_page_load_uses_the_cached_folder_tree(rebuilds: list[list[str]]) -> None:
    response = runner.app.test_client().get("/")
    assert response.status_code == 302
    assert rebuilds[0][-1] == "inf"
