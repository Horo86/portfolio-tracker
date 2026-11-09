// Turns the repository's CHANGELOG.md into the release notes the app shows on About (D-173 (3)):
// dist/notes/release-notes.json, read only when the version is selected. The format is the one
// .github/scripts/release-notes.py checks: `## Unreleased`, then `## <version>` sections, newest
// first, each with `### <group>` headings and one-line `- ` items. Items are split into text and
// `code` parts, so the page shows them as text and never needs HTML. The build fails if the
// file breaks the format or has no notes for this version (package.json).
// Usage (after `vite build`): node scripts/release-notes.mjs [CHANGELOG] [OUTPUT]

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));

const GROUPS = new Set(["Added", "Changed", "Fixed", "Removed", "Security"]);
const VERSION = /^(\d+)\.(\d+)\.(\d+)(?:-(alpha|beta|rc)\.(\d+))?$/;

function fail(message) {
  console.error(`release notes: ${message}`);
  process.exit(1);
}

/** Text and `code` parts of one item, in order; an unpaired backtick stays text. */
function parts(item) {
  const out = [];
  const pattern = /`([^`]+)`/g;
  let last = 0;
  for (const match of item.matchAll(pattern)) {
    if (match.index > last) out.push({ text: item.slice(last, match.index) });
    out.push({ code: match[1] });
    last = match.index + match[0].length;
  }
  if (last < item.length) out.push({ text: item.slice(last) });
  return out;
}

function parse(markdown) {
  const lines = markdown.split(/\r?\n/);
  if (lines[0] !== "# Changelog") fail("the first line must be '# Changelog'");
  const sections = [];
  let current = null;
  lines.slice(1).forEach((line, index) => {
    const where = `line ${index + 2}`;
    if (line.startsWith("## ")) {
      const name = line.slice(3);
      if (name !== "Unreleased" && !VERSION.test(name)) fail(`${where}: ${name} is not a version`);
      current = { version: name, groups: [] };
      sections.push(current);
    } else if (current === null || line.trim() === "") {
      if (current === null && (line.startsWith("#") || line.startsWith("- "))) {
        fail(`${where}: only text before the first section`);
      }
    } else if (line.startsWith("### ")) {
      if (!GROUPS.has(line.slice(4))) fail(`${where}: unknown group ${line.slice(4)}`);
      current.groups.push({ title: line.slice(4), items: [] });
    } else if (line.startsWith("- ") && line.slice(2).trim() !== "") {
      if (current.groups.length === 0) fail(`${where}: an item needs a ### group above it`);
      current.groups.at(-1).items.push(parts(line.slice(2)));
    } else {
      fail(`${where}: only ### groups and one-line '- ' items in a section`);
    }
  });
  if (sections[0]?.version !== "Unreleased") fail("the first section must be '## Unreleased'");
  return sections;
}

const [
  source = join(root, "..", "CHANGELOG.md"),
  output = join(root, "dist", "notes", "release-notes.json"),
] = process.argv.slice(2);
const version = JSON.parse(readFileSync(join(root, "package.json"), "utf8")).version;
const sections = parse(readFileSync(source, "utf8"))
  .slice(1) // "Unreleased" is not in a built app: the version bump moves it under its number
  .map((section) => ({
    ...section,
    groups: section.groups.filter((group) => group.items.length > 0),
  }));
const own = sections.find((section) => section.version === version);
if (!own || own.groups.length === 0) fail(`CHANGELOG.md has no notes for ${version}`);
mkdirSync(dirname(output), { recursive: true });
writeFileSync(output, `${JSON.stringify({ version, sections }, null, 1)}\n`);
console.log(`release notes: ${sections.length} versions in ${output}`);
