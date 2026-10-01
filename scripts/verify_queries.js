// Headless DACTAL query harness. Loads a self-contained DACTAL export, runs
// every saved query through the real dactal.js engine in Node, classifies each
// result, and prints a JSON report to stdout.
//
// DACTAL is deliberately forgiving: a mistyped dataset name becomes a literal
// string, a mistyped property is silently ignored, and most malformed queries
// return *something* rather than throwing. So this harness cannot rely on
// exceptions -- it classifies each result instead:
//
//   error   - the query threw (rare; genuine syntax/engine error)
//   empty   - 0 rows (over-strict filter, or a traversal that resolved nothing;
//             note an orphan/diagnostic query returning 0 is the GOOD state)
//   literal - >0 rows but every row is a bare primitive, not a record. This is
//             the signature of a typo'd dataset name that DACTAL turned into a
//             literal -- but also of a legitimate "list the distinct values of
//             one property" query (e.g. `teams.confed`), so it is a WARNING.
//   ok      - >=1 record (object) row
//
// Usage: node verify_queries.js --engine <dactal.js> --export <export.json>
// Emits JSON: {queries:[{name,tag,order,query,status,rows,sample}], summary:{...}}

const fs = require("fs");
const path = require("path");

function arg(name, fallback) {
  const i = process.argv.indexOf(name);
  return i >= 0 && i + 1 < process.argv.length ? process.argv[i + 1] : fallback;
}

const enginePath = arg("--engine");
const exportPath = arg("--export");
if (!enginePath || !exportPath) {
  console.error("usage: node verify_queries.js --engine <dactal.js> --export <export.json>");
  process.exit(2);
}

// The engine attaches its singleton to window.DACTAL when loaded in a browser;
// shim window to global so the same file loads in Node.
global.window = global;
require(path.resolve(enginePath));
const DACTAL = global.window.DACTAL.constructor;

const exp = JSON.parse(fs.readFileSync(exportPath, "utf8"));
const exportDir = path.dirname(path.resolve(exportPath));
const data = exp.data || {};
const queries = exp.queries || [];

// Resolve a dataset value the way the front-end's import_data does: an inline
// array/object is used directly; a bare string is a filename loaded from the
// export's own directory (a self-contained export inlines everything, so this
// is only for exports that split data into side files).
function resolveDataset(value) {
  if (typeof value !== "string") return value;
  for (const candidate of [path.join(exportDir, value), path.join(exportDir, "data", value)]) {
    if (fs.existsSync(candidate)) {
      const parsed = JSON.parse(fs.readFileSync(candidate, "utf8"));
      return Array.isArray(parsed) ? parsed : Object.values(parsed.data || {})[0];
    }
  }
  return null; // missing side file -- surfaced as an empty dataset
}

function classify(result) {
  const rows = Array.isArray(result) ? result : result == null ? [] : [result];
  if (rows.length === 0) return { status: "empty", rows: 0, sample: null };
  const allPrimitive = rows.every((r) => r === null || typeof r !== "object");
  const sample = rows[0];
  return {
    status: allPrimitive ? "literal" : "ok",
    rows: rows.length,
    sample: typeof sample === "object" ? Object.keys(sample || {}).slice(0, 8) : sample,
  };
}

async function run() {
  const report = [];
  for (const q of queries) {
    // A fresh engine per query so labels (`??`) and "current results" from one
    // query cannot bleed into the next.
    const d = new DACTAL();
    for (const [name, value] of Object.entries(data)) {
      const rows = resolveDataset(value);
      if (rows != null) d.load(rows, name);
    }
    const entry = { name: q.name, tag: q.tag ?? null, order: q.order ?? null, query: q.query };
    try {
      const result = await d.query(q.query);
      Object.assign(entry, classify(result));
    } catch (e) {
      Object.assign(entry, { status: "error", rows: 0, sample: null, error: String(e && e.message ? e.message : e) });
    }
    report.push(entry);
  }
  const summary = { ok: 0, empty: 0, literal: 0, error: 0 };
  for (const r of report) summary[r.status] += 1;
  process.stdout.write(JSON.stringify({ export: exp.export ?? null, queries: report, summary }, null, 2));
}

run().catch((e) => {
  console.error("harness failed:", e);
  process.exit(2);
});
