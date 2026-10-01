# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Deterministic helpers for building a DACTAL data explorer.

Three subcommands, each a cleanly-separable mechanical step of the flow in
SKILL.md. There is intentionally no ``run all``: the creative middle of the
flow (shaping datasets, writing queries, writing the analyses tour) is
user-steered prose that cannot be chained headless.

- ``fetch-frontend``  download the stock dactal.org front-end + engine + catalog
- ``verify-queries``  run every saved query in an export through the real engine
- ``verify-visual``   screenshot each analyses view in a headless browser

Run: ``python3 scripts/run.py <subcommand> ...`` (from this skill's directory).
fetch-frontend and verify-queries need only the Python stdlib + Node.js;
verify-visual additionally needs Playwright + Chromium.
"""
import argparse
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
BUNDLED_ENGINE = SKILL_ROOT / "assets" / "dactal.js"
DACTAL_ORIGIN = "https://dactal.org"

# The three front-end script tags dactal.html loads. On dactal.org itself these
# are relative; to preview the export anywhere else they must point at the live
# engine so the same code runs as it will in the published Gallery example.
FRONTEND_SCRIPTS = ("dactal.js", "dactal_storage_indexeddb.js", "dactal_utils.js")


def _fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 (fixed dactal.org origin)
        return response.read()


def fetch_frontend(assets_dir: Path, refresh_engine: bool) -> dict:
    """Download stock dactal.html (with rewritten script srcs), catalog.json, and
    optionally refresh the bundled engine snapshot used by the query harness.

    Returns a summary dict of what landed and where.
    """
    assets_dir = assets_dir.resolve()
    (assets_dir / "data").mkdir(parents=True, exist_ok=True)

    html = _fetch(f"{DACTAL_ORIGIN}/dactal.html").decode("utf-8")
    # Rewrite each front-end <script src="X"> (relative on dactal.org) to the
    # absolute dactal.org origin so the preview uses the live engine.
    for script in FRONTEND_SCRIPTS:
        html = html.replace(f'src="{script}"', f'src="{DACTAL_ORIGIN}/{script}"')
    html_path = assets_dir / "dactal.html"
    html_path.write_text(html, encoding="utf-8")

    rewritten = sum(1 for s in FRONTEND_SCRIPTS if f'src="{DACTAL_ORIGIN}/{s}"' in html)

    catalog = _fetch(f"{DACTAL_ORIGIN}/catalog.json")
    catalog_path = assets_dir / "catalog.json"
    catalog_path.write_text(catalog.decode("utf-8"), encoding="utf-8")
    connectors = json.loads(catalog)

    result = {
        "dactal_html": str(html_path),
        "script_srcs_rewritten": rewritten,
        "catalog": str(catalog_path),
        "connectors_available": len(connectors),
    }

    if refresh_engine:
        engine = _fetch(f"{DACTAL_ORIGIN}/dactal.js")
        BUNDLED_ENGINE.write_bytes(engine)
        result["engine_refreshed"] = str(BUNDLED_ENGINE)

    return result


def verify_queries(export_path: Path, engine: Path, strict: bool) -> tuple[dict, int]:
    """Run every saved query in the export through the real DACTAL engine (Node)
    and classify each result. Returns (report, exit_code).

    Exit code is non-zero if any query errored, or -- with ``strict`` -- if any
    query was empty or literal-only. See verify_queries.js for the classifier.
    """
    harness = HERE / "verify_queries.js"
    try:
        proc = subprocess.run(
            ["node", str(harness), "--engine", str(engine), "--export", str(export_path)],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        raise SystemExit("node is not installed or not on PATH -- the query harness needs Node.js to run the DACTAL engine.")
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"query harness failed (exit {proc.returncode})")
    report = json.loads(proc.stdout)
    summary = report["summary"]
    bad = summary["error"] + (summary["empty"] + summary["literal"] if strict else 0)
    return report, (1 if bad else 0)


def _render_report(report: dict) -> None:
    label = {"ok": "ok", "empty": "EMPTY", "literal": "LITERAL", "error": "ERROR"}
    for q in report["queries"]:
        order = q["order"] if q["order"] is not None else "-"
        line = f'{label[q["status"]]:<7} [{q["tag"]}/{order}] {q["name"]}: {q["rows"]} rows'
        if q["status"] == "error":
            line += f' -- {q.get("error", "")}'
        print(line)
    s = report["summary"]
    print(f'\nsummary: {s["ok"]} ok, {s["empty"]} empty, {s["literal"]} literal, {s["error"]} error')


def verify_visual(url: str, export_path: Path, out_dir: Path, tags: list[str]) -> int:
    """Screenshot each saved query view in a headless browser. Delegates to the
    self-contained PEP 723 verify_visual.py (which owns the playwright dep).
    """
    script = HERE / "verify_visual.py"
    cmd = [
        "uv", "run", str(script),
        "--url", url, "--export", str(export_path), "--out", str(out_dir),
    ]
    for tag in tags:
        cmd += ["--tag", tag]
    return subprocess.run(cmd).returncode


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_fetch = sub.add_parser("fetch-frontend", help="download stock dactal.html + catalog + engine")
    p_fetch.add_argument("--assets-dir", required=True, type=Path, help="where dactal.html + catalog.json land")
    p_fetch.add_argument("--no-refresh-engine", action="store_true", help="do not refresh the bundled dactal.js snapshot")

    p_vq = sub.add_parser("verify-queries", help="run every saved query through the engine")
    p_vq.add_argument("--export", required=True, type=Path, help="path to the DACTAL export JSON")
    p_vq.add_argument("--engine", type=Path, default=BUNDLED_ENGINE, help="dactal.js path (default: bundled snapshot)")
    p_vq.add_argument("--strict", action="store_true", help="also fail on empty/literal results")
    p_vq.add_argument("--json", action="store_true", help="emit the raw JSON report")

    p_vv = sub.add_parser("verify-visual", help="screenshot each saved-query view in a browser")
    p_vv.add_argument("--url", required=True, help="base URL serving the explorer (e.g. http://localhost:8080)")
    p_vv.add_argument("--export", required=True, type=Path, help="path to the DACTAL export JSON")
    p_vv.add_argument("--out", type=Path, default=Path("dactal-screenshots"), help="screenshot output dir")
    p_vv.add_argument("--tag", action="append", default=[], dest="tags", help="only screenshot this query tag (repeatable; default: analyses)")

    args = parser.parse_args()

    if args.command == "fetch-frontend":
        result = fetch_frontend(args.assets_dir, refresh_engine=not args.no_refresh_engine)
        print(json.dumps(result, indent=2))
    elif args.command == "verify-queries":
        report, code = verify_queries(args.export, args.engine, args.strict)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            _render_report(report)
        raise SystemExit(code)
    elif args.command == "verify-visual":
        tags = args.tags or ["analyses"]
        raise SystemExit(verify_visual(args.url, args.export, args.out, tags))


if __name__ == "__main__":
    main()
