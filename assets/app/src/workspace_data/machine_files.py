"""The ``file system`` dataset: the whole folder tree of this machine, as one nested item.

The dataset has one row, the root folder ``/``. Every folder carries an ``of`` property listing
everything in it, and each of its subfolders is also a property of its own, so both of these
reach the same file:

    file system.home.user.workspace.of:=[CLAUDE.md]
    file system.of:=home.of:=user.of:=workspace.of:~CLAUDE

A folder appears twice under its parent (in ``of`` and as a property), which JSON cannot share,
so the export carries a compact tree -- a file is ``[name, size in bytes]``, a folder is
``{"name", "of"}``, a symlinked folder is ``{"name", "link": <target>}`` -- and
``assets/files_tree.cjs`` turns it into the shared, linked items when the query page loads it.
A symlinked folder is followed (``file system.home.user`` reaches ``/mngr-vol/home``) unless
following it would make the tree loop back on itself; such a link is kept as ``loop`` and
shows as an empty folder.

Walking the machine takes seconds, so the tree is cached on disk and rebuilt at most every
``MAX_AGE_SECONDS``.
"""

import bisect
import json
import os
import time
from pathlib import Path

ROOT = "/"
# Virtual kernel folders: thousands of entries that are not files and change by the second.
SKIPPED = frozenset({"/proc", "/sys"})
MAX_AGE_SECONDS = 600.0


def _join(parent: str, name: str) -> str:
    return parent.rstrip("/") + "/" + name


def _file(entry: os.DirEntry) -> list:
    """A file as ``[name, size]``; a symlinked file has its target's size, a broken one none."""
    try:
        return [entry.name, entry.stat().st_size]
    except OSError:
        return [entry.name, None]


def _walk(path: str, name: str, links: list[tuple[str, dict]]) -> dict:
    node: dict = {"name": name, "of": []}
    if path in SKIPPED:
        node["skipped"] = True
        return node
    try:
        with os.scandir(path) as scan:
            entries = sorted(scan, key=lambda entry: entry.name)
    except OSError:
        node["unreadable"] = True
        return node
    kids = node["of"]
    for entry in entries:
        try:
            if entry.is_symlink():
                target = os.path.realpath(entry.path)
                if os.path.isdir(target):
                    link = {"name": entry.name, "link": target}
                    links.append((entry.path, link))
                    kids.append(link)
                else:
                    kids.append(_file(entry))
            elif entry.is_dir(follow_symlinks=False):
                kids.append(_walk(entry.path, entry.name, links))
            else:
                kids.append(_file(entry))
        except OSError:
            kids.append([entry.name, None])
    return node


def _under(path: str, folder: str) -> bool:
    return folder == ROOT or path == folder or path.startswith(folder + "/")


def _break_loops(links: list[tuple[str, dict]]) -> None:
    """Keep only the folder links that leave the tree free of loops; mark the rest ``loop``.

    A link at ``P`` to folder ``T`` loops when ``T`` can reach ``P``: ``P`` is inside ``T``, or
    some link already kept inside ``T`` leads (directly or onward) to a folder that holds ``P``.
    """
    kept_paths: list[str] = []
    kept_target: dict[str, str] = {}

    def reaches(target: str, place: str, seen: set[str]) -> bool:
        if _under(place, target):
            return True
        if target in seen:
            return False
        seen.add(target)
        # Kept links inside ``target``: paths starting "<target>/", which sort below "<target>0".
        prefix = target.rstrip("/") + "/"
        start = bisect.bisect_left(kept_paths, prefix)
        end = bisect.bisect_left(kept_paths, prefix[:-1] + "0")
        for link_path in kept_paths[start:end]:
            if reaches(kept_target[link_path], place, seen):
                return True
        return False

    for path, link in links:
        target = link["link"]
        if reaches(target, path, set()):
            link["loop"] = link.pop("link")
            continue
        bisect.insort(kept_paths, path)
        kept_target[path] = target


def build_tree(root: str | None = None) -> dict:
    """Walk the machine from ``root`` (default ``ROOT``) into the compact tree the export carries."""
    root = root or ROOT
    links: list[tuple[str, dict]] = []
    tree = _walk(root, root, links)
    _break_loops(links)
    tree["id"] = root
    return tree


def cached_tree(cache: Path, max_age: float = MAX_AGE_SECONDS) -> dict:
    """The tree from ``cache`` when it is younger than ``max_age``; otherwise walk and re-cache."""
    try:
        if time.time() - cache.stat().st_mtime < max_age:
            return json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    tree = build_tree()
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(tree, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    tmp.replace(cache)
    return tree
