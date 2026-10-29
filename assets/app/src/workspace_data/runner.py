"""The workspace's Data app: the DACTAL query page over every user-relevant dataset here.

Installed by the all-data-visible skill (.agents/skills/all-data-visible/). The app has no custom surface
of its own: its front door is the stock DACTAL query page, opened on the narrated tour of the
workspace's export. What it serves:

- ``/`` refreshes the export if it is stale and redirects to the query page on the tour's first
  view.
- ``/dactal.html`` is the stock front-end (pulled by the skill's ``fetch-frontend``), with
  injected: the content-hash-gated reload bootstrap (the browser keeps its own explorable copy
  and re-imports only when the data actually changed -- never on every load), cache-busted
  front-end script URLs, the workspace location beacon, and the handler
  that turns the data's "open ... »" links into workspace windows.
- ``/data/workspace.json`` is the one export (the single source of truth), rebuilt from the
  workspace's records by ``export``.
- ``/open`` asks the workspace to open a window (a chat, a file) and answers 204, so the query
  page never navigates away; ``/raw/<kind>/<id>`` renders a raw record (a skill, a task) as the
  markdown it is.

Persistent state lives under ``DATA_DIR`` (``WORKSPACE_DATA_DATA_DIR`` overrides it); the port is
``PORT`` (``WORKSPACE_DATA_PORT``).
"""

import html
import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

import markdown
from flask import Flask, Response, abort, redirect, request, send_from_directory
from werkzeug.serving import run_simple
from werkzeug.wrappers import Response as WerkzeugResponse

from workspace_data import collect, export, machine_files

DATA_DIR = Path(os.environ.get("WORKSPACE_DATA_DATA_DIR", "data/.apps/workspace-data"))
PORT = int(os.environ.get("WORKSPACE_DATA_PORT", "__PORT__"))
DACTAL_ORIGIN = "https://dactal.org"
WORKSPACE_URL = os.environ.get("WORKSPACE_DATA_SHELL_URL", "http://127.0.0.1:8000")

ASSETS = Path(__file__).parent / "assets"
EXPORT_PATH = DATA_DIR / "data" / f"{export.DBNAME}.json"
# A page load rebuilds the export when it is older than this; the background loop keeps it
# current while nobody is looking.
STALE_AFTER_SECONDS = 10.0
BACKGROUND_REFRESH_SECONDS = 120.0

_refresh_lock = threading.Lock()
_last_refresh = {"at": 0.0}
_stop = threading.Event()


def refresh_export(max_age: float = 0.0) -> None:
    """Rebuild the export unless one was built within ``max_age`` seconds. Single-flight."""
    with _refresh_lock:
        if time.monotonic() - _last_refresh["at"] < max_age and EXPORT_PATH.exists():
            return
        export.write_export(EXPORT_PATH)
        _last_refresh["at"] = time.monotonic()


def _background_refresh() -> None:
    while not _stop.wait(BACKGROUND_REFRESH_SECONDS):
        try:
            refresh_export()
        except OSError as error:
            app.logger.warning("background refresh failed: %s", error)


# ---- the served DACTAL front-end -------------------------------------------------

# Connectors (the chart renderer) load through the export's ``connectors`` dataset; preloading
# one here would run it before the page's engine exists.
_FRONTEND_SCRIPTS = ("dactal.js", "dactal_storage_indexeddb.js", "dactal_utils.js")

# Re-import the browser's private copy only when the export's content hash changed -- or when
# STORED_COPY_REVISION moves, which makes every browser re-import once. Bump it when a change
# to how the page treats its stored copy must reach copies stored before it (2: drop what the
# export no longer has, ``_RETIRE_SCRIPT``).
STORED_COPY_REVISION = "2"
_RELOAD_BOOTSTRAP = """<script>(function(){{try{{
  var v="{version}"; var p=new URLSearchParams(location.search);
  var db=p.get('dbname'); if(!db) return; var key='dactal_dv_'+db;
  if(p.get('reset')){{ localStorage.setItem(key,v); return; }}
  if(localStorage.getItem(key)!==v){{ localStorage.setItem(key,v); p.set('reset','1');
    location.replace(location.pathname+'?'+p.toString()+location.hash); }}
}}catch(e){{}}}})();</script>"""

# The workspace contract: report where the page is (minus the one-shot reset flag) so the
# window reopens on the same query, and open "open ... »" links as workspace windows on the
# client this page is shown in.
_WORKSPACE_GLUE = """<script>(function(){
  if (window.parent === window) return;
  var clientId = "";
  function report(){
    var p = new URLSearchParams(location.search); p.delete('reset');
    var q = p.get('query') || p.get('queryname') || '';
    window.parent.postMessage({type: "shell:location", path: location.pathname + '?' + p.toString(),
      title: q ? 'Data: ' + q.slice(0, 60) : 'Data'}, "*");
  }
  ['pushState', 'replaceState'].forEach(function(name){
    var original = history[name];
    history[name] = function(){ var r = original.apply(this, arguments); report(); return r; };
  });
  window.addEventListener('popstate', report);
  window.addEventListener('load', report);
  window.addEventListener('message', function(event){
    if (event.source !== window.parent || !event.data) return;
    if (event.data.type === 'shell:handshake' && event.data.clientId) clientId = event.data.clientId;
  });
  document.addEventListener('click', function(event){
    var link = event.target.closest && event.target.closest('a.ws-open');
    if (!link) return;
    event.preventDefault();
    fetch(link.getAttribute('href') + (clientId ? '&client=' + encodeURIComponent(clientId) : ''), {method: 'POST'});
  }, true);
})();</script>"""

# On a re-import (the export changed), drop from the browser's stored copy what the export no
# longer has -- the page itself only ever adds and overwrites. A dataset is the export's if the
# previous export's catalog (``datasets``) listed it; a saved view is if it carries one of the
# export's tags. Datasets a connector loaded and views the user saved under their own tags stay.
_RETIRE_SCRIPT = """<script>(function(){
  var EXPORT_TAG = /^(analyses|browse|diagnostic|(project|app|folder|skill): .*)$/;
  var importData = window.import_data;
  window.import_data = async function(exported, overwrite){
    if (overwrite && exported && exported.data) {
      var keep = exported.data;
      (dactal.data.datasets || []).forEach(function(row){
        var name = row && row.id;
        if (!name || name in keep || !(name in dactal.data)) return;
        delete dactal.data[name];
        dactaldb.remove(name);
      });
      var names = new Set((exported.queries || []).map(function(q){ return q.name; }));
      var queries = dactal.data.queries || [];
      var kept = queries.filter(function(q){
        var stale = q && EXPORT_TAG.test(q.tag || '') && !names.has(q.name);
        if (stale) dactal.savedquerynames.delete(q.name);
        return !stale;
      });
      if (kept.length !== queries.length) { dactal.data.queries = kept; dsave('queries'); }
    }
    return importData(exported, overwrite);
  };
})();</script>"""


def served_dactal_html() -> str:
    page = (ASSETS / "dactal.html").read_text(encoding="utf-8")
    version = export.content_version(EXPORT_PATH)
    page = page.replace(
        "<head>", "<head>\n" + _RELOAD_BOOTSTRAP.format(version=f"{version}.{STORED_COPY_REVISION}"), 1
    )
    cache_buster = str(int((ASSETS / "dactal.html").stat().st_mtime))
    for script in _FRONTEND_SCRIPTS:
        page = page.replace(
            f'dactal.org/{script}"', f'dactal.org/{script}?cb={cache_buster}"'
        )
    return page.replace(
        "</body>",
        _files_tree_script() + _RETIRE_SCRIPT + _WORKSPACE_GLUE + "</body>",
        1,
    )


def _files_tree_script() -> str:
    """The linker for the ``files`` tree, run on every export the page imports (machine_files)."""
    linker = (ASSETS / "files_tree.cjs").read_text(encoding="utf-8")
    # Link on import, relink after the page restores its stored copy, and register the
    # ``contents`` adapter: ``<file item>.contents`` (or ``|contents``) fetches the file's text
    # from ``/contents``; anything that is not readable text gives nothing.
    return (
        "<script>"
        + linker
        + """
(function(){
  var importData = window.import_data;
  window.import_data = function(exported, overwrite){
    return importData(window.linkFilesExport(exported), overwrite); };
  var dataInit = window.dactal_data_init;
  window.dactal_data_init = async function(){
    var result = await dataInit.apply(this, arguments);
    window.relinkFilesData(dactal.data);
    return result; };
  dactal.connect_annotator('contents', async function(item){
    if (!item || item.kind !== 'file' || !item.path) return null;
    var response = await fetch('contents?path=' + encodeURIComponent(item.path));
    return response.status === 200 ? await response.text() : null;
  }, ['path'], {requires: [{property: 'path', default: '(required)',
      description: 'full path of a file in the file system dataset'}],
    produces: 'the file text, or nothing when it is not readable text (binary, over 1 MB, a login or key file)'});
})();</script>"""
    )


# ---- opening workspace windows ------------------------------------------------------


def open_window(app_name: str, path: str, client: str) -> int:
    """Ask the workspace shell to open (or focus) a window of ``app_name`` at ``path``. Returns its status."""
    args: dict[str, str] = {"app": app_name}
    if path:
        args["path"] = path
    if client:
        args["client"] = client
    body = json.dumps({"op": "open", "args": args, "requester": None}).encode("utf-8")
    call = urllib.request.Request(
        f"{WORKSPACE_URL}/api/layout/broadcast",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(call, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as error:
        app.logger.warning(
            "open %s %s failed: %s %s", app_name, path, error.code, error.read()[:300]
        )
        return error.code
    except urllib.error.URLError as error:
        app.logger.warning("open %s %s failed: %s", app_name, path, error)
        return 502


# ---- raw records ---------------------------------------------------------------------

_RAW_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>
<style>body{{font:15px/1.55 Georgia,serif;max-width:760px;margin:32px auto;padding:0 20px;color:#222}}
pre,code{{font:13px ui-monospace,monospace;background:#f4f4f2;border-radius:4px}}pre{{padding:10px;overflow:auto}}
.meta{{font:12px ui-monospace,monospace;color:#777;white-space:pre-wrap;border-bottom:1px solid #ddd;padding-bottom:12px}}</style>
</head><body><div class="meta">{meta}</div>{body}</body></html>"""


def _raw_source(kind: str, ident: str) -> Path | None:
    if "/" in ident or ident.startswith("."):
        return None
    if kind == "ticket":
        return collect.TICKETS_DIR / f"{ident}.md"
    if kind == "skill":
        return collect.SKILLS_DIR / ident / "SKILL.md"
    return None


# ---- routes -----------------------------------------------------------------------------

app = Flask("workspace_data", static_folder=None)


@app.route("/")
def index() -> WerkzeugResponse:
    refresh_export(STALE_AFTER_SECONDS)
    return redirect(
        f"dactal.html?dbname={export.DBNAME}&query={quote(export.FIRST_TOUR_QUERY)}"
    )


@app.route("/dactal.html")
def dactal_html() -> Response:
    refresh_export(STALE_AFTER_SECONDS)
    response = Response(served_dactal_html(), mimetype="text/html")
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/data/<path:filename>")
def data(filename: str) -> WerkzeugResponse:
    if not EXPORT_PATH.exists():
        refresh_export()
    if not (EXPORT_PATH.parent / filename).is_file():
        # The stock page's gallery shows dactal.org's own pictures by relative path.
        return redirect(f"{DACTAL_ORIGIN}/data/{quote(filename)}")
    response = send_from_directory(EXPORT_PATH.parent.resolve(), filename, max_age=0)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/queries/<path:filename>")
def gallery_picture(filename: str) -> WerkzeugResponse:
    return redirect(f"{DACTAL_ORIGIN}/queries/{quote(filename)}")


@app.route("/catalog.json")
def catalog() -> Response:
    return send_from_directory(ASSETS, "catalog.json", mimetype="application/json")


@app.route("/open", methods=["GET", "POST"])
def open_route() -> Response:
    app_name = request.args.get("app", "")
    if not app_name:
        abort(400)
    status = open_window(
        app_name, request.args.get("path", ""), request.args.get("client", "")
    )
    return Response(status=204 if status < 300 else status)


@app.route("/raw/<kind>/<ident>")
def raw(kind: str, ident: str) -> Response:
    source = _raw_source(kind, ident)
    if source is None or not source.is_file():
        abort(404)
    fields, body = collect.parse_frontmatter(source.read_text(encoding="utf-8"))
    meta = (
        "\n".join(f"{key}: {value}" for key, value in fields.items())
        + f"\nsource: {source}"
    )
    page = _RAW_PAGE.format(
        title=html.escape(ident),
        meta=html.escape(meta),
        body=markdown.markdown(body, extensions=["fenced_code", "tables"]),
    )
    return Response(page, mimetype="text/html")


@app.route("/contents")
def contents() -> Response:
    """The text of one file on this machine (the page's ``contents`` adapter); 204 when it has none."""
    text = machine_files.text_contents(request.args.get("path", ""))
    if text is None:
        return Response(status=204)
    return Response(text, mimetype="text/plain; charset=utf-8")


@app.route("/health")
def health() -> Response:
    return Response('{"status": "ok"}', mimetype="application/json")


def main() -> None:
    refresh_export()
    threading.Thread(
        target=_background_refresh, name="export-refresh", daemon=True
    ).start()
    run_simple(
        "127.0.0.1", PORT, app, threaded=True, use_reloader=False, use_debugger=False
    )


if __name__ == "__main__":
    main()
