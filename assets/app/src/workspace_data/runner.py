"""The workspace's Data app: the DACTAL query page over every user-relevant dataset here.

Installed by the all-data-visible skill (.agents/skills/all-data-visible/). The app has no custom surface
of its own: its front door is the stock DACTAL query page, opened on the narrated tour of the
workspace's export. What it serves:

- ``/`` refreshes the export if it is stale and redirects to the query page on the tour's first
  view.
- ``/dactal.html`` is the stock front-end (pulled by the skill's ``fetch-frontend``), with
  injected: the content-hash-gated reload bootstrap (the browser keeps its own explorable copy
  and re-imports only when the data actually changed -- never on every load), cache-busted
  front-end script URLs, the workspace location beacon, the handler
  that turns the data's "open ... »" links into workspace windows, and each user-built app's own
  ``dactal_render.js`` (served from ``/app-assets/<app>/``), which offers a display that draws
  that project's rows as the thing they are rather than as a table.
- ``/data/workspace.json`` is the one export (the single source of truth), rebuilt from the
  workspace's records by ``export``. ``/versions`` says what it holds right now -- a hash of the
  whole of it and one per dataset -- and ``/datasets?names=a,b`` hands back just those. Together
  they are how a page someone left open catches up with a change made in another app without
  being reloaded, pulling only what moved rather than the whole export.
- ``/open`` asks the workspace to open a window (a chat, a file) and answers 204, so the query
  page never navigates away; ``/raw/<kind>/<id>`` renders a raw record (a skill, a task) as the
  markdown it is.

Persistent state lives under ``DATA_DIR`` (``WORKSPACE_DATA_DATA_DIR`` overrides it); the port is
``PORT`` (``WORKSPACE_DATA_PORT``).
"""

import html
import json
import multiprocessing
import os
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
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
# current while someone is using the page, and stops once nobody has for IDLE_AFTER_SECONDS.
STALE_AFTER_SECONDS = 10.0
BACKGROUND_REFRESH_SECONDS = 120.0
IDLE_AFTER_SECONDS = 900.0
# How often the loop asks the cheap question -- has anything the collectors read been touched?
# (``collect.sources_stamp``: a stat walk, a fraction of a second.) A change the person just
# made in another app is picked up within this, instead of waiting out the long refresh above.
# Cheap is relative to the machine and how much it holds, so the gap is also kept to a multiple
# of what the walk itself costs; the page polls on the same beat.
WATCH_SECONDS = 4.0
WATCH_GAP_MULTIPLE = 20.0
# ...but a rebuild costs seconds, and a workspace in use changes constantly (every message of a
# live conversation is a change). So leave the machine this much quiet between rebuilds: a fixed
# floor, and a multiple of what the last rebuild actually cost, which fits itself to the machine.
MIN_REBUILD_GAP_SECONDS = 8.0
REBUILD_GAP_MULTIPLE = 3.0
# ...and however short of memory the machine is, keep trying eventually: a rebuild that keeps
# being killed backs off to here and no further, so the export still catches up once it can.
MAX_REBUILD_GAP_SECONDS = 600.0
# A page load uses the machine's folder tree however old its cache is (walking the machine takes
# seconds); the background loop re-walks it when it is older than the usual ten minutes.
ANY_AGE = float("inf")

_refresh_lock = threading.Lock()
_views_lock = threading.Lock()
_last_refresh = {"at": 0.0}
_last_used = {"at": time.monotonic()}
_last_stamp = {"value": ""}
_waiting = {"change": False}
_failures = {"in_a_row": 0}
_stamp_cost = {"seconds": 0.0}
_rebuild_cost = {"seconds": 0.0}
_stop = threading.Event()



class ExportRebuildError(OSError):
    """The child process that rebuilds the export failed."""


def refresh_export(
    max_age: float = 0.0, machine_max_age: float = machine_files.MAX_AGE_SECONDS
) -> None:
    """Rebuild the export unless one was built within ``max_age`` seconds. Single-flight.

    The rebuild runs in a short-lived child process, so the memory it needs is handed back to
    the machine when it finishes instead of staying with this long-running server.
    """
    with _refresh_lock:
        if time.monotonic() - _last_refresh["at"] < max_age and EXPORT_PATH.exists():
            return
        try:
            _rebuild(EXPORT_PATH, machine_max_age)
        finally:
            # The time of the *attempt*, not of the last success. A rebuild that failed --
            # most often one the out-of-memory killer stopped, which it volunteers for -- must
            # still start the clock, or the next tick finds it overdue and tries again at
            # once, and the two of them take turns at the machine for as long as it is short
            # of memory. The backoff in ``_background_tick`` widens the gap on top of this.
            _last_refresh["at"] = time.monotonic()


def _rebuild(
    path: Path,
    machine_max_age: float,
    job: Callable[[Path, float], None] = export.rebuild_in_own_process,
) -> None:
    """Run ``job`` (the export's rebuild) in a fresh interpreter and wait for it.

    Spawned, not forked: this server runs threads, and a forked child could inherit a lock one of
    them holds.
    """
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=1, mp_context=context) as pool:
        error = pool.submit(job, path, machine_max_age).exception()
    if isinstance(error, BrokenProcessPool):
        # Most often the out-of-memory killer stopping the rebuild, which volunteers for it.
        raise ExportRebuildError(f"export rebuild failed: its process was killed ({error})") from error
    if error is not None:
        raise ExportRebuildError(f"export rebuild failed: {error!r}") from error


def _page_used() -> None:
    _last_used["at"] = time.monotonic()


def _rebuild_gap() -> float:
    """How long to leave the machine alone between rebuilds.

    A multiple of what the last rebuild cost, so a workspace holding more pays more; doubled
    for each rebuild that failed in a row, because the usual reason a rebuild fails is that
    the machine is short of memory, and trying again straight away is what makes that worse.
    """
    steady = max(MIN_REBUILD_GAP_SECONDS, REBUILD_GAP_MULTIPLE * _rebuild_cost["seconds"])
    return min(steady * (2 ** _failures["in_a_row"]), MAX_REBUILD_GAP_SECONDS)


def _due_for_rebuild(waiting: bool, since: float) -> bool:
    """A change is waiting and the quiet gap has passed -- or nothing has been rebuilt in a long while.

    The long way round is still there for what a stamp cannot see: the machine's folder tree
    ages out of its own cache, and a collector may read somewhere nobody listed.
    """
    return (waiting and since >= _rebuild_gap()) or since >= BACKGROUND_REFRESH_SECONDS


def _watch_gap() -> float:
    """How long to wait between stamp walks, given what the last walk cost."""
    return max(WATCH_SECONDS, WATCH_GAP_MULTIPLE * _stamp_cost["seconds"])


def _background_tick() -> bool:
    """One pass of the background loop: rebuild when it is due. Returns whether the page is in use."""
    if time.monotonic() - _last_used["at"] > IDLE_AFTER_SECONDS:
        return False
    started = time.monotonic()
    stamp = collect.sources_stamp()
    _stamp_cost["seconds"] = time.monotonic() - started
    if stamp != _last_stamp["value"]:
        _last_stamp["value"] = stamp
        # Remembered, not acted on: a change seen while the quiet gap still has time to run
        # would otherwise be forgotten by the next walk, which finds the stamp steady again.
        _waiting["change"] = True
    if not _due_for_rebuild(_waiting["change"], time.monotonic() - _last_refresh["at"]):
        return True
    _waiting["change"] = False
    started = time.monotonic()
    try:
        # The gap as the max age too: a tick and a page's "look now" can arrive together, and
        # without it the second would rebuild again the moment the first finished.
        refresh_export(_rebuild_gap(), machine_files.MAX_AGE_SECONDS)
    except OSError as error:
        _failures["in_a_row"] += 1
        app.logger.warning(
            "background refresh failed (%d in a row, next in %.0fs): %s",
            _failures["in_a_row"],
            _rebuild_gap(),
            error,
        )
    else:
        _failures["in_a_row"] = 0
        _rebuild_cost["seconds"] = time.monotonic() - started
    return True


def _check_soon() -> None:
    """Look for a change now rather than on the next beat: the page just came back into view."""
    threading.Thread(target=_background_tick, name="export-check", daemon=True).start()


def _background_refresh() -> None:
    while not _stop.wait(_watch_gap()):
        _background_tick()


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


# Saved views belong to the workspace, not to one browser: the page's own storage is private to
# the browser it runs in, so a view saved in one tab, browser or device would never reach another.
# Every change the page makes to its saved views goes to ``saved-views``, which keeps them in
# ``export.SAVED_VIEWS_PATH`` and rebuilds the export with them; every page load imports the
# export, so every other copy picks them up (a changed export re-imports in full). A view that
# was in the previous export and is gone from this one was deleted elsewhere, and goes here too.
# Views kept only in this browser from before (not in the export, never shared) are sent up once.
_SAVED_VIEWS_SCRIPT = """<script>(function(){
  var db = new URLSearchParams(location.search).get('dbname') || '';
  var namesKey = 'dactal_dv_views_' + db;
  var baseline = null, importing = false;
  function canon(q){ return JSON.stringify([q.name, q.tag || null, q.order == null ? null : q.order,
    q.query, q.hide || [], !!q.relative]); }
  function snapshot(queries){ var m = new Map();
    (queries || []).forEach(function(q){ if (q && q.name) m.set(q.name, canon(q)); }); return m; }
  function send(upsert, remove){
    if (!upsert.length && !remove.length) return;
    fetch('saved-views', {method: 'POST', keepalive: true, headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({upsert: upsert.map(function(q){ return {name: q.name, tag: q.tag,
        order: q.order, query: q.query, hide: q.hide, relative: q.relative}; }), 'delete': remove})});
  }
  var set = dactaldb.set.bind(dactaldb);
  dactaldb.set = function(key, value){
    if (key === 'queries' && baseline && !importing) {
      var now = snapshot(value), upsert = [], remove = [];
      (value || []).forEach(function(q){ if (q && q.name && baseline.get(q.name) !== now.get(q.name)) upsert.push(q); });
      baseline.forEach(function(_, name){ if (!now.has(name)) remove.push(name); });
      baseline = now;
      send(upsert, remove);
    }
    return set(key, value);
  };
  var importData = window.import_data;
  window.import_data = async function(exported, overwrite){
    var names = new Set(((exported && exported.queries) || []).map(function(q){ return q.name; }));
    var before = null;
    try { before = JSON.parse(localStorage.getItem(namesKey) || 'null'); } catch (e) {}
    var previous = new Set(before || []);
    var queries = dactal.data.queries || [];
    if (before) {
      var kept = queries.filter(function(q){ return !(q && previous.has(q.name) && !names.has(q.name)); });
      queries.forEach(function(q){ if (q && previous.has(q.name) && !names.has(q.name)) dactal.savedquerynames.delete(q.name); });
      if (kept.length !== queries.length) { dactal.data.queries = kept; }
    }
    // Built-in views carry the export's tags; one the export dropped is not someone's view.
    var builtIn = /^(analyses|browse|diagnostic|(project|app|folder|skill): .*)$/;
    var localOnly = (dactal.data.queries || []).filter(function(q){
      return q && q.name && q.query && !builtIn.test(q.tag || '') && !names.has(q.name) && !previous.has(q.name); });
    importing = true;
    try { await importData(exported, overwrite); } finally { importing = false; }
    if (dactal.data.queries) await set('queries', dactal.data.queries);
    localStorage.setItem(namesKey, JSON.stringify(Array.from(names)));
    baseline = snapshot(dactal.data.queries);
    send(localOnly, []);
  };
})();</script>"""


# Keep an open page's copy current without making the person reload it. The page imports the
# whole export when it loads and then never looks again, so a change made in another app -- a
# notebook cleared, a note written -- used to be invisible here until the window was refreshed.
# This polls the export's per-dataset hashes and pulls back only the datasets that moved, which
# is why it can run every few seconds: the whole export is tens of megabytes, one dataset is
# usually a few hundred kilobytes. It touches the data, never the person's saved views (those
# are _SAVED_VIEWS_SCRIPT's) and never the query box they may be typing in.
_LIVE_DATA_SCRIPT = """<script>(function(){
  var POLL_MS = %(poll_ms)d;
  var known = null;
  function currentQuery(){ var box = document.getElementById('dactal'); return box ? box.value : ''; }
  function editing(){ return document.activeElement && document.activeElement.id === 'dactal'; }
  async function tick(lookNow){
    if (document.hidden || typeof dactal === 'undefined' || !dactal.data) return;
    try {
      var response = await fetch('versions' + (lookNow ? '?check=1' : ''), {cache: 'no-store'});
      if (!response.ok) return;
      var now = (await response.json()).datasets || {};
      if (known === null) { known = now; return; }
      var changed = Object.keys(now).filter(function(name){ return known[name] !== now[name]; });
      var gone = Object.keys(known).filter(function(name){ return !(name in now); });
      if (!changed.length && !gone.length) return;
      if (changed.length) {
        var pulled = await fetch('datasets?names=' + encodeURIComponent(changed.join(',')),
                                 {cache: 'no-store'});
        if (!pulled.ok) return;
        var fragment = await pulled.json();
        if (window.linkFilesExport) fragment = window.linkFilesExport(fragment);
        Object.keys(fragment.data).forEach(function(name){
          dactal.load(fragment.data[name], name);
          dsave(name);
        });
        if (window.relinkFilesData) window.relinkFilesData(dactal.data);
      }
      // A dataset the export no longer has (an app's records deleted) goes from the copy too --
      // the same pruning _RETIRE_SCRIPT does when a whole export is re-imported.
      gone.forEach(function(name){ delete dactal.data[name]; dactaldb.remove(name); });
      known = now;
      // Show it, when what is on screen is built from something that just moved and the person
      // is not mid-edit. runQuery() with no argument re-runs whatever the query box holds.
      var query = currentQuery();
      if (!editing() && query && changed.concat(gone).some(function(name){ return query.indexOf(name) !== -1; })) {
        runQuery();
      }
    } catch (error) { /* a poll that fails is a poll skipped; the next one catches up */ }
  }
  // Coming back to this window is the moment it most matters: the change was very likely made
  // in whatever window was in front of it, so ask the server to look straight away.
  document.addEventListener('visibilitychange', function(){ if (!document.hidden) tick(true); });
  window.setInterval(function(){ tick(false); }, POLL_MS);
  window.addEventListener('load', function(){ tick(true); });
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
        _files_tree_script()
        + _RETIRE_SCRIPT
        + _SAVED_VIEWS_SCRIPT
        + _LIVE_DATA_SCRIPT % {"poll_ms": int(WATCH_SECONDS * 1000)}
        + _WORKSPACE_GLUE
        + _project_renderer_scripts()
        + "</body>",
        1,
    )


def _project_renderer_scripts() -> str:
    """Load each user-built app's own renderer, so its rows can be drawn as the thing they are.

    A module, not a plain script: it is served from this app's origin (``/app-assets/...``), so a
    relative import in it reaches the sibling files of its own project's assets. Modules are
    deferred, which puts them after the page has built ``dactal`` and before its ``onload`` runs
    the first query -- exactly where a renderer has to register.
    """
    return "".join(
        f'<script type="module" src="app-assets/{quote(app)}/{quote(collect.RENDERER_FILE)}"></script>'
        for app, _ in collect.collect_project_renderers()
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
    _page_used()
    refresh_export(STALE_AFTER_SECONDS, ANY_AGE)
    return redirect(
        f"dactal.html?dbname={export.DBNAME}&query={quote(export.FIRST_TOUR_QUERY)}"
    )


@app.route("/dactal.html")
def dactal_html() -> Response:
    _page_used()
    refresh_export(STALE_AFTER_SECONDS, ANY_AGE)
    response = Response(served_dactal_html(), mimetype="text/html")
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/data/<path:filename>")
def data(filename: str) -> WerkzeugResponse:
    _page_used()
    if not EXPORT_PATH.exists():
        refresh_export(machine_max_age=ANY_AGE)
    if not (EXPORT_PATH.parent / filename).is_file():
        # The stock page's gallery shows dactal.org's own pictures by relative path.
        return redirect(f"{DACTAL_ORIGIN}/data/{quote(filename)}")
    response = send_from_directory(EXPORT_PATH.parent.resolve(), filename, max_age=0)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/queries/<path:filename>")
def gallery_picture(filename: str) -> WerkzeugResponse:
    return redirect(f"{DACTAL_ORIGIN}/queries/{quote(filename)}")


@app.route("/app-assets/<app_name>/<path:filename>")
def project_asset(app_name: str, filename: str) -> WerkzeugResponse:
    """A file from a user-built app's own assets, served from this app's origin.

    This is what lets a project's ``dactal_render.js`` import its siblings: the whole directory
    is reachable under one path here, so the relative URLs inside it resolve. Only the apps the
    user built are served, and only their assets directory.
    """
    assets = collect.project_asset_dirs().get(app_name)
    if assets is None:
        abort(404)
    response = send_from_directory(assets.resolve(), filename, max_age=0)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/versions")
def versions() -> Response:
    """What the export holds right now: a hash of the whole of it, and one per dataset.

    A page that is already open reads this every few seconds to tell what has moved since it
    loaded, so it can fetch back only that and leave everything else alone.
    """
    _page_used()
    path = EXPORT_PATH.with_name(export.VERSIONS_NAME)
    if not path.is_file():
        refresh_export(STALE_AFTER_SECONDS, ANY_AGE)
    elif request.args.get("check"):
        _check_soon()
    try:
        body = path.read_text(encoding="utf-8")
    except OSError:
        body = '{"version": "none", "datasets": {}}'
    response = Response(body, mimetype="application/json")
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/datasets")
def datasets() -> Response:
    """The named datasets as the export now has them, shaped as an export the page can import.

    Takes ``?names=a,b`` -- what the page found changed in ``versions``. A name the export does
    not have is left out rather than refused, so a dataset that vanished between the two calls
    does not cost the page its whole catch-up.
    """
    _page_used()
    wanted = [name for name in request.args.get("names", "").split(",") if name]
    if not wanted:
        abort(400)
    try:
        whole = json.loads(EXPORT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        abort(503)
    held = whole.get("data", {})
    picked = {name: held[name] for name in wanted if name in held}
    response = Response(
        json.dumps({"export": export.DBNAME, "data": picked}, ensure_ascii=False),
        mimetype="application/json",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


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


@app.route("/saved-views", methods=["GET", "POST"])
def saved_views() -> Response:
    """The views saved on the query page, kept for every browser; POST adds, replaces or deletes some.

    POST takes ``{"upsert": [view, ...], "delete": [name, ...]}`` and rebuilds the export so every
    other page picks the change up on its next load.
    """
    if request.method == "POST":
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            abort(400)
        upsert, delete = body.get("upsert", []), body.get("delete", [])
        if not isinstance(upsert, list) or not isinstance(delete, list):
            abort(400)
        with _views_lock:
            export.change_saved_views(upsert, delete)
        refresh_export(machine_max_age=ANY_AGE)
    return Response(
        json.dumps(export.load_saved_views(), ensure_ascii=False),
        mimetype="application/json",
    )


@app.route("/health")
def health() -> Response:
    return Response('{"status": "ok"}', mimetype="application/json")


def main() -> None:
    try:
        refresh_export(machine_max_age=ANY_AGE)
    except OSError as error:
        # Most often the out-of-memory killer, which a rebuild deliberately volunteers for.
        # Refusing to start over it would leave the person with no Data app at all; the loop
        # below and the first page load both try again, and what is already on disk still serves.
        app.logger.warning("the refresh at startup failed, carrying on: %s", error)
    threading.Thread(
        target=_background_refresh, name="export-refresh", daemon=True
    ).start()
    run_simple(
        "127.0.0.1", PORT, app, threaded=True, use_reloader=False, use_debugger=False
    )


if __name__ == "__main__":
    main()
