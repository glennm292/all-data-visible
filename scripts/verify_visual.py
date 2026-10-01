# /// script
# requires-python = ">=3.11"
# dependencies = ["playwright==1.58.0"]
# ///
"""Screenshot each saved-query view of a served DACTAL explorer in a headless
browser. Only a real browser runs the chart/map/timeline/heatmap renderers, so
this is the second tier of verification after the headless query harness.

Navigates ``<url>/dactal.html?dbname=<db>&query=<name>`` for each saved query
in the export (filtered by tag) and saves a PNG per view. Fails loudly with a
clear message if Chromium is not installed.

Run via run.py's ``verify-visual`` subcommand, which owns the flag surface.
"""
import argparse
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

# The Minds workspace's stealth Chromium; Playwright only finds builds it downloaded itself.
WORKSPACE_CHROMIUM = "/opt/fortress/tilion-fortress/tilion"


def _executable() -> str | None:
    explicit = os.environ.get("CHROMIUM_EXECUTABLE")
    if explicit:
        return explicit
    return WORKSPACE_CHROMIUM if Path(WORKSPACE_CHROMIUM).exists() else None


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "view"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="base URL serving the explorer")
    parser.add_argument("--export", required=True, type=Path, help="path to the DACTAL export JSON")
    parser.add_argument("--out", required=True, type=Path, help="screenshot output dir")
    parser.add_argument("--tag", action="append", default=[], dest="tags", help="query tag to screenshot (repeatable)")
    args = parser.parse_args()

    export = json.loads(args.export.read_text(encoding="utf-8"))
    dbname = export["export"]
    tags = set(args.tags) if args.tags else None
    queries = [q for q in export.get("queries", []) if tags is None or q.get("tag") in tags]
    if not queries:
        raise SystemExit(f"no saved queries match tags {sorted(tags) if tags else '(any)'}")

    args.out.mkdir(parents=True, exist_ok=True)
    base = args.url.rstrip("/")

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(executable_path=_executable(), args=["--no-sandbox"])
        except PlaywrightError as exc:
            raise SystemExit(
                "Could not launch Chromium. Install it with `python3 -m playwright "
                "install chromium` (and ensure Playwright is installed). "
                "Underlying error: " + str(exc)
            )
        page = browser.new_page(viewport={"width": 1100, "height": 900})
        written = []
        blank = []
        # The first visit imports the export into the browser (one reload); do it before timing views.
        page.goto(f"{base}/dactal.html?dbname={urllib.parse.quote(dbname)}", wait_until="networkidle", timeout=60000)
        for q in queries:
            name = q["name"]
            url = f"{base}/dactal.html?dbname={urllib.parse.quote(dbname)}&query={urllib.parse.quote(name)}"
            page.goto(url, wait_until="networkidle", timeout=60000)
            # Give client-side renderers (SVG charts, Leaflet maps) a beat to paint.
            page.wait_for_timeout(1500)
            order = q.get("order")
            tag = q.get("tag") or "query"
            prefix = f"{slug(tag)}-{order:02d}" if isinstance(order, int) else slug(tag)
            out_path = args.out / f"{prefix}-{slug(name)}.png"
            page.screenshot(path=str(out_path), full_page=True)
            written.append(str(out_path))
            # A view that ran shows result cells or a chart; one that failed shows the page's index.
            drew = page.locator("td.querydata").count() + page.locator("svg").count()
            if not drew:
                blank.append(name)
            print(f"{'captured' if drew else 'NOTHING DRAWN'} {name} -> {out_path}")
        browser.close()

    print(f"\nwrote {len(written)} screenshot(s) to {args.out}", file=sys.stderr)
    if blank:
        raise SystemExit(f"{len(blank)} view(s) drew nothing: {', '.join(blank)}")


if __name__ == "__main__":
    main()
