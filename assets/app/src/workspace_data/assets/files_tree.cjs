// (.cjs so Node loads it as a script even under an ES-module package.json.)
// Turns the export's compact ``file system`` tree (see machine_files.py) into linked DACTAL items:
// every folder gets ``of`` (all its contents) plus one property per subfolder, the same object
// in both places, and a symlinked folder shares its target's contents. Every item carries
// ``id``/``path`` (its full path), ``name``, and ``kind`` (folder, file or link); a file also
// carries ``size`` in bytes.
//
// The subfolder properties are non-enumerable, so the page's table does not expand a folder's
// whole subtree into its cells; ``of`` itself shows as a count to click, as for any group. The browser's
// stored copy drops non-enumerable properties, so ``relinkFolders`` puts them back on a tree
// read from storage.
//
// Loaded by the query page (wrapping its data import) and by the headless query harness.
(function (root) {
  // A subfolder named like one of these stays reachable through ``of`` only.
  var RESERVED = { id: 1, name: 1, path: 1, kind: 1, target: 1, note: 1, "of": 1 };

  function join(parent, name) {
    return (parent === "/" ? "" : parent) + "/" + name;
  }

  function addFolderProps(item, kids) {
    for (var i = 0; i < kids.length; i++) {
      var kid = kids[i];
      if (kid.kind !== "file" && !RESERVED[kid.name]) {
        Object.defineProperty(item, kid.name, {
          value: kid, enumerable: false, writable: true, configurable: true,
        });
      }
    }
  }

  // A linked tree read back from storage: restore the subfolder properties.
  function relinkFolders(tree) {
    var seen = new Set();
    var stack = [tree];
    while (stack.length) {
      var item = stack.pop();
      if (!item || seen.has(item) || !Array.isArray(item["of"])) continue;
      seen.add(item);
      addFolderProps(item, item["of"]);
      for (var i = 0; i < item["of"].length; i++) stack.push(item["of"][i]);
    }
    return tree;
  }

  function linkFilesTree(tree) {
    if (!tree) return tree;
    if (tree.kind) return relinkFolders(tree); // already linked, maybe read from storage
    var byPath = new Map();
    var links = [];
    function build(raw, path) {
      if (Array.isArray(raw)) {
        return { id: path, name: raw[0], path: path, kind: "file", size: raw[1] };
      }
      var item = { id: path, name: raw.name, path: path, kind: "folder" };
      if (raw.link !== undefined) {
        item.kind = "link";
        item.target = raw.link;
        links.push(item);
        return item;
      }
      if (raw.loop !== undefined) {
        item.kind = "link";
        item.target = raw.loop;
        item.note = "not followed: it leads back into a folder that contains it";
        item["of"] = [];
        return item;
      }
      if (raw.skipped) item.note = "virtual system folder, contents not listed";
      if (raw.unreadable) item.note = "could not be read";
      var kids = (raw["of"] || []).map(function (kid) {
        return build(kid, join(path, Array.isArray(kid) ? kid[0] : kid.name));
      });
      item["of"] = kids;
      addFolderProps(item, kids);
      byPath.set(path, item);
      return item;
    }
    var linked = build(tree, tree.id || "/");
    for (var i = 0; i < links.length; i++) {
      var target = byPath.get(links[i].target);
      links[i]["of"] = target ? target["of"] : [];
      if (target) addFolderProps(links[i], target["of"]);
      else links[i].note = "target is outside the listed folders";
    }
    return linked;
  }

  function linkExport(exported) {
    var rows = exported && exported.data && exported.data["file system"];
    if (Array.isArray(rows)) exported.data["file system"] = rows.map(linkFilesTree);
    return exported;
  }

  root.linkFilesTree = linkFilesTree;
  root.relinkFilesData = function (data) {
    var rows = data && data["file system"];
    if (Array.isArray(rows)) data["file system"] = rows.map(linkFilesTree);
  };
  root.linkFilesExport = linkExport;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { linkFilesTree: linkFilesTree, linkExport: linkExport };
  }
})(typeof window !== "undefined" ? window : globalThis);
