// Writes dist/licenses/THIRD-PARTY.txt and dist/licenses/frontend.json (D-167): every
// production dependency of the frontend,
// the code that ships in the built app, with its licence, the full text of its licence file
// and its NOTICE file when it has one (Apache-2.0, ECharts). Optional peer dependencies
// (TypeScript, for react-i18next's types) are not shipped and not listed. Run by `npm run
// build` after Vite. It fails when a package has no licence text, so the notices are never
// shipped incomplete. The JSON (name, version, licence) is the list on About. No network: it
// reads node_modules only.
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { init } from "license-checker-rseidelsohn";

const root = fileURLToPath(new URL("..", import.meta.url));
const target = join(root, "dist", "licenses", "THIRD-PARTY.txt");
const list = join(root, "dist", "licenses", "frontend.json");

const packages = await new Promise((resolve, reject) => {
  init(
    {
      start: root,
      production: true,
      excludePrivatePackages: true,
      direct: Infinity,
      nopeer: true,
      // licenseText is read only with a custom format.
      customFormat: { name: "", version: "", licenses: "", repository: "", licenseText: "" },
    },
    (error, result) => (error ? reject(error) : resolve(result)),
  );
});

const entries = Object.entries(packages).sort(([a], [b]) => a.localeCompare(b));
const missing = entries.filter(([, info]) => !info.licenseText?.trim()).map(([id]) => id);
if (entries.length === 0 || missing.length > 0) {
  console.error(
    entries.length === 0
      ? "third-party notices: no production dependency found"
      : `third-party notices: no licence text for ${missing.join(", ")}`,
  );
  process.exit(1);
}

const rule = "-".repeat(78);
const licence = (info) => [info.licenses].flat().join(", ");
const header = [
  "Portfolio Tracker: third-party software in the web app",
  "",
  "The built web app includes the following packages. Each is listed with its version, its",
  "licence, the text of its licence file and its NOTICE file if it has one, as published by",
  "its authors. Portfolio Tracker itself is licensed under the GNU Affero General Public",
  "License v3 (AGPL-3.0-only), in /app/LICENSE in the image. The server's Python packages",
  "are listed in BACKEND-THIRD-PARTY.txt, next to this file, in the image.",
  "",
];
const body = entries.map(([id, info]) =>
  [
    rule,
    `${id}`,
    `Licence: ${licence(info)}`,
    ...(info.repository ? [`Source: ${info.repository}`] : []),
    "",
    info.licenseText.trim(),
    "",
    ...(info.noticeFile ? ["NOTICE:", "", readFileSync(info.noticeFile, "utf8").trim(), ""] : []),
  ].join("\n"),
);

mkdirSync(dirname(target), { recursive: true });
writeFileSync(target, [...header, ...body, rule, ""].join("\n"));
const rows = entries.map(([, info]) => ({
  name: info.name,
  version: info.version,
  license: licence(info),
}));
writeFileSync(list, `${JSON.stringify(rows, null, 1)}\n`);
console.log(
  `third-party notices: ${entries.length} packages in dist/licenses/THIRD-PARTY.txt and frontend.json`,
);
