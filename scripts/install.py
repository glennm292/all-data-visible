# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Install (or update) the Data app in a Minds workspace, and report on it.

Subcommands, each one mechanical step of the flow in SKILL.md:

- ``install``   copy the app into ``system/apps/workspace_data/``, register its supervised
                program, install it, start it, and wait until it answers healthy. Re-running it
                updates an existing install in place, keeping its port.
- ``verify``    rebuild the export and run every saved query through the real DACTAL engine.
- ``status``    print where the app is, its port, whether it runs, and what the export holds.

Run from anywhere: ``python3 <skill>/scripts/install.py install --workspace <repo root>``
(``--workspace`` defaults to the current directory). Standard library only.
"""

import argparse
import json
import re
import shutil
import socket
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
APP_TEMPLATE = SKILL_ROOT / "assets" / "app"
APP_NAME = "workspace-data"
PACKAGE = "workspace_data"
PORT_PLACEHOLDER = "__PORT__"
FIRST_PORT = 8080
# Ports the workspace's own services hold without declaring them where we scan.
RESERVED_PORTS = {8000, 8010, 8030, 8081}

PROGRAM_CONF = """[program:{name}]
command=python3 system/services/oom_priority/bin/oom_tag_service.py user bash -c "python3 system/scripts/forward_port.py --manifest system/apps/{package}/app.toml --url http://localhost:{port} && {name}"
directory={workspace}
autostart=true
autorestart=true
startsecs=30
startretries=5
stopasgroup=true
killasgroup=true
stdout_logfile=/var/log/supervisor/{name}-stdout.log
stderr_logfile=/var/log/supervisor/{name}-stderr.log
stdout_logfile_maxbytes=10MB
stderr_logfile_maxbytes=10MB
stdout_logfile_backups=3
stderr_logfile_backups=3
"""


class InstallError(Exception):
    """A step of the install could not be completed."""


def _say(line: str) -> None:
    sys.stderr.write(line + "\n")


def _app_dir(workspace: Path) -> Path:
    return workspace / "system" / "apps" / PACKAGE


def _conf_path(workspace: Path) -> Path:
    return workspace / "system" / "supervisord.conf.d" / f"{APP_NAME}.conf"


def check_workspace(workspace: Path) -> None:
    for required in (
        "system/apps",
        "system/supervisord.conf.d",
        "system/scripts/forward_port.py",
    ):
        if not (workspace / required).exists():
            raise InstallError(
                f"{workspace} does not look like a Minds workspace (no {required})"
            )


def existing_port(workspace: Path) -> int | None:
    try:
        match = re.search(
            r"--url http://localhost:(\d+)",
            _conf_path(workspace).read_text(encoding="utf-8"),
        )
    except OSError:
        return None
    return int(match.group(1)) if match else None


def used_ports(workspace: Path) -> set[int]:
    ports = set(RESERVED_PORTS)
    conf_dir = workspace / "system" / "supervisord.conf.d"
    texts = [p.read_text(encoding="utf-8") for p in conf_dir.glob("*.conf")]
    root_conf = workspace / "system" / "supervisord.conf"
    if root_conf.exists():
        texts.append(root_conf.read_text(encoding="utf-8"))
    registry = workspace / "data" / ".state" / "apps.toml"
    if registry.exists():
        texts.append(registry.read_text(encoding="utf-8"))
    for text in texts:
        ports.update(
            int(p) for p in re.findall(r"(?:localhost|127\.0\.0\.1):(\d{4,5})", text)
        )
    return ports


def pick_port(workspace: Path, requested: int | None) -> int:
    current = existing_port(workspace)
    if requested:
        if requested in used_ports(workspace) and requested != current:
            raise InstallError(f"port {requested} is already used in this workspace")
        return requested
    if current:
        return current
    taken = used_ports(workspace)
    return next(p for p in range(FIRST_PORT, 65535) if p not in taken and not _listening(p))


def _listening(port: int) -> bool:
    """Whether something already answers on this loopback port (a program no file declares)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.3)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def copy_app(workspace: Path, port: int) -> Path:
    """Copy the app template into the workspace, filling in its port. Replaces code, never data."""
    target = _app_dir(workspace)
    if target.exists():
        shutil.rmtree(target / "src" / PACKAGE, ignore_errors=True)
    shutil.copytree(
        APP_TEMPLATE,
        target,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    runner = target / "src" / PACKAGE / "runner.py"
    runner.write_text(
        runner.read_text(encoding="utf-8").replace(PORT_PLACEHOLDER, str(port)),
        encoding="utf-8",
    )
    return target


def refresh_frontend(app_dir: Path) -> None:
    """Pull the current stock DACTAL query page and connector catalog; keep the bundled copy offline."""
    proc = subprocess.run(
        [
            sys.executable,
            str(HERE / "run.py"),
            "fetch-frontend",
            "--assets-dir",
            str(app_dir / "src" / PACKAGE / "assets"),
            "--no-refresh-engine",
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        _say(
            "could not reach dactal.org; keeping the bundled query page: "
            + proc.stderr.strip()[-300:]
        )


# The path every other program's ``directory=`` names; the repo root is often reached through it
# by a symlink, so prefer it whenever it points at the same place.
CANONICAL_WORKSPACE = Path("/home/user/workspace")


def write_conf(workspace: Path, port: int) -> None:
    directory = (
        CANONICAL_WORKSPACE
        if CANONICAL_WORKSPACE.resolve() == workspace.resolve()
        else workspace
    )
    _conf_path(workspace).write_text(
        PROGRAM_CONF.format(
            name=APP_NAME, package=PACKAGE, port=port, workspace=directory
        ),
        encoding="utf-8",
    )


def _run(cmd: list[str], workspace: Path) -> None:
    proc = subprocess.run(cmd, cwd=workspace, capture_output=True, text=True)
    if proc.returncode != 0:
        raise InstallError(
            f"{' '.join(cmd)} failed:\n{(proc.stderr or proc.stdout).strip()[-1500:]}"
        )


def install_package(workspace: Path) -> None:
    _run(
        ["uv", "tool", "install", "-e", f"system/apps/{PACKAGE}", "--reinstall"],
        workspace,
    )
    _run(["uv", "sync", "--all-packages"], workspace)


def start(workspace: Path, port: int, timeout: float = 90.0) -> None:
    _run(["supervisorctl", "reread"], workspace)
    _run(["supervisorctl", "update"], workspace)
    _run(["supervisorctl", "restart", APP_NAME], workspace)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/health", timeout=3
            ) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(1)
    raise InstallError(
        f"the app did not answer on port {port}; see /var/log/supervisor/{APP_NAME}-stderr.log"
    )


def install(
    workspace: Path, requested_port: int | None, offline: bool, skip_start: bool
) -> dict:
    check_workspace(workspace)
    port = pick_port(workspace, requested_port)
    was_installed = _app_dir(workspace).exists()
    app_dir = copy_app(workspace, port)
    if not offline:
        refresh_frontend(app_dir)
    write_conf(workspace, port)
    if not skip_start:
        install_package(workspace)
        start(workspace, port)
    return {
        "app": APP_NAME,
        "dir": str(app_dir.relative_to(workspace)),
        "port": port,
        "updated": was_installed,
        "started": not skip_start,
    }


def export_path(workspace: Path) -> Path:
    return workspace / "data" / ".apps" / APP_NAME / "data" / "workspace.json"


def verify(workspace: Path) -> int:
    """Rebuild the export from the workspace's records, then check every saved query."""
    build = subprocess.run(
        [
            # The export is built from the standard library alone.
            sys.executable,
            "-c",
            "import sys; from pathlib import Path; sys.path.insert(0, 'system/apps/workspace_data/src'); "
            "from workspace_data import export; "
            "export.write_export(Path('data/.apps/workspace-data/data/workspace.json'))",
        ],
        cwd=workspace,
        capture_output=True,
        text=True,
    )
    if build.returncode != 0:
        raise InstallError(
            "building the export failed:\n" + build.stderr.strip()[-1500:]
        )
    return subprocess.run(
        [
            sys.executable,
            str(HERE / "run.py"),
            "verify-queries",
            "--export",
            str(export_path(workspace)),
            "--prepare",
            str(_app_dir(workspace) / "src" / "workspace_data" / "assets" / "files_tree.cjs"),
        ]
    ).returncode


def status(workspace: Path) -> dict:
    port = existing_port(workspace)
    state = subprocess.run(
        ["supervisorctl", "status", APP_NAME], capture_output=True, text=True
    ).stdout.strip()
    report: dict = {
        "installed": _app_dir(workspace).exists(),
        "port": port,
        "program": state,
    }
    try:
        export = json.loads(export_path(workspace).read_text(encoding="utf-8"))
        report["datasets"] = {
            r["id"]: r["rows"] for r in export["data"].get("datasets", [])
        }
        report["saved_views"] = len(export.get("queries", []))
    except (OSError, ValueError, KeyError):
        report["datasets"] = None
    manifest = _app_dir(workspace) / "app.toml"
    if manifest.exists():
        report["launcher_rank"] = tomllib.loads(
            manifest.read_text(encoding="utf-8")
        ).get("launcher_rank")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("install", "verify", "status"):
        p = sub.add_parser(name)
        p.add_argument(
            "--workspace", type=Path, default=Path.cwd(), help="the workspace repo root"
        )
        if name == "install":
            p.add_argument(
                "--port",
                type=int,
                help="serve on this port (default: keep, else the lowest free >= 8080)",
            )
            p.add_argument(
                "--offline",
                action="store_true",
                help="use the bundled query page instead of pulling dactal.org's",
            )
            p.add_argument(
                "--no-start",
                action="store_true",
                help="copy and configure only; do not install or start",
            )
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    try:
        if args.command == "install":
            result = install(workspace, args.port, args.offline, args.no_start)
            sys.stdout.write(json.dumps(result, indent=2) + "\n")
        elif args.command == "verify":
            raise SystemExit(verify(workspace))
        else:
            sys.stdout.write(json.dumps(status(workspace), indent=2) + "\n")
    except InstallError as error:
        _say(f"error: {error}")
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
