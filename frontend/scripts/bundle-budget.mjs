// Checks the built frontend (dist/) against the size budget of D-138, gzip sizes in kB
// (1 kB = 1000 bytes, gzip level 9). Run after `npm run build`; exits 1 when over budget.
// No dependency: Node's own fs and zlib.
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

const BUDGET = { firstScreen: 160, page: 30, echarts: 220, total: 450 };

// fileURLToPath, not URL.pathname: a folder name with spaces or accents stays readable.
const dist = fileURLToPath(new URL("../dist/", import.meta.url));
const assets = join(dist, "assets");
const gzipKb = (file) => gzipSync(readFileSync(join(assets, file)), { level: 9 }).length / 1000;

const chunks = readdirSync(assets).filter((name) => name.endsWith(".js"));
const sizes = new Map(chunks.map((name) => [name, gzipKb(name)]));
// What the first screen loads: the entry script and the chunks index.html preloads.
const html = readFileSync(join(dist, "index.html"), "utf8");
const preloaded = [...html.matchAll(/\/assets\/([^"]+\.js)"/g)].map((m) => m[1]);
// Plus the active language's texts, loaded before the first render (src/i18n.ts): the larger.
const languages = chunks.filter((name) => /^(it|en)-[\w-]+\.js$/.test(name));
const language = languages.sort((a, b) => sizes.get(b) - sizes.get(a)).slice(0, 1);
const first = [...preloaded, ...language];

const sum = (names) => names.reduce((total, name) => total + sizes.get(name), 0);
const problems = [];
const check = (label, kb, limit) => {
  const line = `${label}: ${kb.toFixed(1)} kB gzip (budget ${limit} kB)`;
  console.log(`${kb > limit ? "OVER" : "ok  "} ${line}`);
  if (kb > limit) problems.push(line);
};

check("first screen", sum(first), BUDGET.firstScreen);
for (const name of chunks.filter((n) => !first.includes(n))) {
  const echarts = name.startsWith("echarts");
  check(`chunk ${name}`, sizes.get(name), echarts ? BUDGET.echarts : BUDGET.page);
}
check("all JavaScript", sum(chunks), BUDGET.total);

if (preloaded.length === 0) problems.push("index.html loads no script: is dist/ built?");
// The charts come after the figures: their library never loads with the first screen.
if (preloaded.some((name) => name.startsWith("echarts"))) {
  problems.push("index.html preloads the echarts chunk (vite.config.ts manualChunks)");
}
if (languages.length !== 2) problems.push("expected one chunk per language (src/i18n.ts)");
if (problems.length > 0) {
  console.error(`\nOver the size budget (D-138):\n${problems.join("\n")}`);
  process.exit(1);
}
