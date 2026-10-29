"""The machine's folder tree answers path queries through the real DACTAL engine.

Each test lays out a small machine under a temporary directory -- with a symlinked home folder
and a link that leads back into its own folder -- walks it the way the export does, links it
the way the query page does, and runs queries through the real engine.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from workspace_data import machine_files, runner

ASSETS = Path(__file__).parent / "assets"
# Tests run from the installed copy (system/apps/workspace_data/...); the engine snapshot ships
# with the all-data-visible skill.
ENGINE = (
    Path(__file__).resolve().parents[5]
    / ".agents"
    / "skills"
    / "all-data-visible"
    / "assets"
    / "dactal.js"
)

RUN_QUERIES = """
global.window = global;
require(process.argv[1]);
const {linkExport} = require(process.argv[2]);
const DACTAL = global.window.DACTAL.constructor;
const exp = linkExport({data: {"file system": [JSON.parse(require("fs").readFileSync(0, "utf8"))]}});
(async () => {
  const out = {};
  for (const q of JSON.parse(process.argv[3])) {
    const d = new DACTAL();
    d.load(exp.data["file system"], "file system");
    const rows = await d.query(q);
    out[q] = rows.map((r) =>
      r && typeof r === "object" ? r.path + "|" + r.kind + (r.kind === "file" ? "|" + r.size : "") : r);
  }
  process.stdout.write(JSON.stringify(out));
})();
"""


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "m"
    (root / "vol" / "home" / "workspace").mkdir(parents=True)
    (root / "vol" / "home" / "workspace" / "CLAUDE.md").write_text("hello")
    (root / "vol" / "home" / "workspace" / "AGENTS.md").write_text("x")
    (root / "home").mkdir()
    os.symlink(root / "vol" / "home", root / "home" / "user")
    (root / "mngr-vol" / "notes.txt").parent.mkdir()
    (root / "mngr-vol" / "notes.txt").write_text("x")
    (root / "usr" / "bin").mkdir(parents=True)
    (root / "usr" / "bin" / "python3").write_text("x")
    os.symlink(root / "usr" / "bin", root / "usr" / "bin" / "X11")
    monkeypatch.setattr(machine_files, "ROOT", str(root))
    return root


def _query(tree: dict, queries: list[str]) -> dict:
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    result = subprocess.run(
        [
            "node",
            "-e",
            RUN_QUERIES,
            str(ENGINE),
            str(ASSETS / "files_tree.cjs"),
            json.dumps(queries),
        ],
        input=json.dumps(tree),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_a_file_is_reachable_by_property_path_and_by_contents(machine: Path) -> None:
    tree = machine_files.build_tree()
    claude = f"{machine}/vol/home/workspace/CLAUDE.md|file|5"
    found = _query(
        tree,
        [
            "file system.home.user.workspace.of:=[CLAUDE.md]",
            "file system.of:=home.of:=user.of:=workspace.of:~CLAUDE",
            "file system.home.user.workspace.of",
            "file system.[mngr-vol].of",
        ],
    )
    assert found["file system.[mngr-vol].of"] == [f"{machine}/mngr-vol/notes.txt|file|1"]
    assert found["file system.home.user.workspace.of:=[CLAUDE.md]"] == [claude]
    assert found["file system.of:=home.of:=user.of:=workspace.of:~CLAUDE"] == [claude]
    assert found["file system.home.user.workspace.of"] == [
        f"{machine}/vol/home/workspace/AGENTS.md|file|1",
        claude,
    ]


def test_a_link_back_into_its_own_folder_is_not_followed(machine: Path) -> None:
    tree = machine_files.build_tree()
    usr_bin = next(k for k in tree["of"] if k["name"] == "usr")["of"][0]
    x11 = next(k for k in usr_bin["of"] if isinstance(k, dict) and k["name"] == "X11")
    assert x11 == {"name": "X11", "loop": f"{machine}/usr/bin"}
    found = _query(tree, ["file system.usr.bin.X11", "file system.usr.bin.X11.of"])
    assert found["file system.usr.bin.X11"] == [f"{machine}/usr/bin/X11|link"]
    assert found["file system.usr.bin.X11.of"] == []


def test_two_links_into_each_other_keep_only_the_first() -> None:
    # "/x.d/q" sorts between "/x" and "/x/a": the search for links inside /x must not stop at it.
    links = [
        ("/x.d/q", {"name": "q", "link": "/z"}),
        ("/x/a", {"name": "a", "link": "/y"}),
        ("/y/b", {"name": "b", "link": "/x"}),
    ]
    machine_files._break_loops(links)
    assert [link for _, link in links] == [
        {"name": "q", "link": "/z"},
        {"name": "a", "link": "/y"},
        {"name": "b", "loop": "/x"},
    ]


def test_the_tree_is_cached_until_it_is_stale(
    machine: Path, tmp_path: Path
) -> None:
    cache = tmp_path / "cache.json"
    first = machine_files.cached_tree(cache)
    (machine / "new.txt").write_text("x")
    assert machine_files.cached_tree(cache) == first
    assert ["new.txt", 1] in machine_files.cached_tree(cache, max_age=0)["of"]


def test_contents_gives_text_and_nothing_for_anything_else(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("# héllo\n", encoding="utf-8")
    (tmp_path / "image.bin").write_bytes(b"\x89PNG\0\0data")
    (tmp_path / "latin.txt").write_bytes("caf\xe9".encode("latin-1"))
    (tmp_path / "api_token.json").write_text('{"token": "x"}')
    (tmp_path / "big.txt").write_text("a" * (machine_files.CONTENTS_MAX_BYTES + 1))
    os.symlink(tmp_path / "notes.md", tmp_path / "link.md")
    assert machine_files.text_contents(str(tmp_path / "notes.md")) == "# héllo\n"
    assert machine_files.text_contents(str(tmp_path / "link.md")) == "# héllo\n"
    for name in ("image.bin", "latin.txt", "api_token.json", "big.txt", "missing.txt"):
        assert machine_files.text_contents(str(tmp_path / name)) is None, name
    assert machine_files.text_contents(str(tmp_path)) is None
    assert machine_files.text_contents("notes.md") is None


def test_the_page_reads_contents_through_the_runner(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("hello", encoding="utf-8")
    (tmp_path / "image.bin").write_bytes(b"\0\0")
    client = runner.app.test_client()
    found = client.get("/contents", query_string={"path": str(tmp_path / "notes.md")})
    assert found.status_code == 200 and found.get_data(as_text=True) == "hello"
    binary = client.get("/contents", query_string={"path": str(tmp_path / "image.bin")})
    assert binary.status_code == 204
    assert "connect_annotator('contents'" in runner.served_dactal_html()


def test_a_re_import_drops_what_the_export_no_longer_has() -> None:
    page = runner.served_dactal_html()
    # The retire step wraps the import, so it runs before the page's own import adds the new data.
    assert "dactaldb.remove(name)" in page
    assert page.index("dactaldb.remove(name)") > page.index("window.linkFilesExport")
    # A browser that stored its copy before this behavior re-imports once.
    assert f'.{runner.STORED_COPY_REVISION}"' in page
