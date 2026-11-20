// The remaining screens for the review screenshots (D-195), used by review-screenshots.mjs:
// accounts, settings, About, not found, and the screens without a session. Invented data only.
//
// On the demo server: the demo investor's accounts (with the edit and delete dialogs), Settings
// (top, the Security card, the sessions, the password dialog, the 2FA dialog's password step and
// QR code; the enrolment is started on this throwaway copy, never confirmed), About and its
// release notes, not found. Then a fresh investor per size and theme, made by the demo
// administrator through the API: the login page, the forced password change, the 2FA dialog's
// QR code and recovery codes (confirmed on this copy) and the login's code step.
// On a second throwaway server, empty, with the default 2FA obligation for administrators
// (:8718, frontend/.data-review-empty/): the first run's three steps for each size and theme,
// never submitted; then the first administrator is created through the API, enrols, and
// creates one administrator per size and theme, each taken through the forced password change
// and the forced enrolment (password, QR code, recovery codes).
import { spawn } from "node:child_process";
import { createHmac } from "node:crypto";
import { rmSync } from "node:fs";

import { ADMIN, CSRF, FRONTEND, portFree } from "./demo-server.mjs";

export const EMPTY_DATA = `${FRONTEND}/.data-review-empty`; // ignored by git (.data*/)
export const EMPTY_PORT = 8718;
export const EMPTY_BASE = `http://127.0.0.1:${EMPTY_PORT}`;
// Invented credentials of the empty server's users.
const OWNER = {
  username: "review-owner",
  password: "review owner password 0001",
  first_name: "Olga",
  last_name: "Esempio",
  email: "review-owner@example.org",
  language: "it",
};
const NEW_PASSWORD = "review new password 0001";

/** Starts the app on an empty throwaway folder, with the default 2FA obligation (admins).
 * Stopped by the caller's finally; also when the script is interrupted or exits on a crash. */
export async function startEmpty() {
  await portFree(EMPTY_PORT);
  rmSync(EMPTY_DATA, { recursive: true, force: true });
  const backend = `${FRONTEND}/../backend`;
  const env = { ...process.env };
  delete env.PT_REQUIRE_2FA; // the default: administrators must enrol
  const server = spawn(`${backend}/.venv/bin/python`, ["-m", "portfolio_tracker"], {
    cwd: backend,
    env: {
      ...env,
      PT_DATA_DIR: EMPTY_DATA,
      PT_HOST: "127.0.0.1",
      PT_PORT: String(EMPTY_PORT),
      PT_STATIC_DIR: `${FRONTEND}/dist`,
      PT_MARKET_DATA: "false",
      PT_COOKIE_SECURE: "false",
    },
    stdio: "ignore",
  });
  console.log(`empty server started, PID ${server.pid}`);
  const kill = () => {
    if (server.exitCode === null && server.signalCode === null) server.kill("SIGTERM");
  };
  process.once("exit", () => {
    kill();
    rmSync(EMPTY_DATA, { recursive: true, force: true });
  });
  for (const signal of ["SIGINT", "SIGTERM"]) {
    process.once(signal, () => {
      kill();
      rmSync(EMPTY_DATA, { recursive: true, force: true });
      process.exit(130);
    });
  }
  return server;
}

// --- TOTP (RFC 6238, as e2e/helpers.ts): codes for the invented users' secrets -------------

function base32(text) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  const bits = [...text.replace(/[\s=]/g, "").toUpperCase()]
    .map((ch) => alphabet.indexOf(ch).toString(2).padStart(5, "0"))
    .join("");
  const bytes = Buffer.alloc(Math.floor(bits.length / 8));
  for (let i = 0; i < bytes.length; i++) bytes[i] = parseInt(bits.slice(i * 8, i * 8 + 8), 2);
  return bytes;
}

function totpAt(secret, step) {
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(step));
  const mac = createHmac("sha1", base32(secret)).update(counter).digest();
  const offset = mac[mac.length - 1] & 0x0f;
  return String((mac.readUInt32BE(offset) & 0x7fffffff) % 1_000_000).padStart(6, "0");
}

const usedStep = new Map();

/** A code the server takes now, never of a step already used for this secret; waits for the
 * next 30-second step when the three it accepts are used. */
async function nextCode(secret) {
  for (;;) {
    const now = Math.floor(Date.now() / 30_000);
    const step = Math.max(now - 1, (usedStep.get(secret) ?? -Infinity) + 1);
    if (step <= now + 1) {
      usedStep.set(secret, step);
      return totpAt(secret, step);
    }
    await new Promise((resolve) => setTimeout(resolve, 5_000));
  }
}

async function ok(response, what) {
  if (!response.ok()) throw new Error(`${what} refused (${response.status()})`);
  return response.status() === 204 ? null : response.json();
}

// --- Shots ---------------------------------------------------------------------------------

async function settled(page) {
  await page.waitForLoadState("networkidle");
}

async function heading(page, name) {
  await page.getByRole("heading", { name, level: 1 }).waitFor();
  await settled(page);
}

/** The element at the top of the window. */
async function toTop(locator) {
  await locator.first().evaluate((element) => element.scrollIntoView({ block: "start" }));
}

/** As the demo investor: accounts, settings, About, not found. Nothing is saved; the 2FA
 * enrolment is started on this copy, never confirmed. */
export async function remainingShots(page, tag, shoot) {
  await page.goto("/accounts");
  await heading(page, "Conti");
  await shoot(page, `${tag}-accounts`);
  await shoot(page, `${tag}-full-accounts`, true);
  await page.getByRole("region", { name: "Conto titoli" }).getByRole("button", { name: "Modifica" }).click();
  await page.getByRole("dialog", { name: "Modifica conto" }).waitFor();
  await shoot(page, `${tag}-account-edit`);
  await page.getByRole("button", { name: "Elimina conto…" }).click();
  await page.getByRole("dialog").waitFor();
  await shoot(page, `${tag}-account-delete`);
  await page.getByRole("dialog").getByRole("button", { name: "Annulla" }).click();

  await page.goto("/settings");
  await heading(page, "Impostazioni");
  await shoot(page, `${tag}-settings`);
  await shoot(page, `${tag}-full-settings`, true);
  await toTop(page.locator(".settings-section", { hasText: "Sicurezza" }));
  await shoot(page, `${tag}-settings-security`);
  await toTop(page.locator(".sessions"));
  await shoot(page, `${tag}-settings-sessions`);
  await page.getByRole("button", { name: "Cambia password" }).click();
  await page.getByRole("dialog", { name: "Cambia password" }).waitFor();
  await shoot(page, `${tag}-settings-password`);
  await page.getByRole("dialog").getByRole("button", { name: "Annulla" }).click();
  await page.getByRole("button", { name: "Attiva 2FA" }).click();
  const dialog = page.getByRole("dialog", { name: "Attiva la verifica in due passaggi" });
  await dialog.waitFor();
  await shoot(page, `${tag}-settings-2fa-password`);
  await dialog.getByLabel("Password").fill("dev-demo-password");
  await dialog.getByRole("button", { name: "Continua" }).click();
  await dialog.locator(".qr-frame svg").waitFor();
  await shoot(page, `${tag}-settings-2fa-qr`);
  await dialog.getByRole("button", { name: "Annulla" }).click();

  await page.goto("/about");
  await heading(page, "Informazioni");
  await page.getByText(/^Mostra l'elenco/).click();
  await shoot(page, `${tag}-about`);
  await shoot(page, `${tag}-full-about`, true);
  await page.getByRole("button", { name: /Note di rilascio$/ }).click();
  await page.getByRole("dialog", { name: "Note di rilascio" }).waitFor();
  await shoot(page, `${tag}-about-release-notes`);
  await page.getByRole("dialog").getByRole("button", { name: "Chiudi" }).click();

  await page.goto("/pagina-che-non-esiste");
  await heading(page, "Pagina non trovata");
  await shoot(page, `${tag}-not-found`);
}

async function logInByForm(page, username, password) {
  await page.goto("/login");
  await page.getByLabel("Nome utente").fill(username);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Accedi" }).click();
}

/** A fresh Italian investor, made by the demo administrator: the login page, the forced
 * password change, the 2FA dialog's QR code and recovery codes, then the login's code step. */
export async function investorSignInShots(browser, newContext, tag, serial, shoot) {
  const admin = await browser.newContext({ baseURL: newContext.base });
  await ok(await admin.request.post("/api/v1/auth/login", { data: ADMIN, headers: CSRF }), "admin login");
  const username = `review-investor-${serial}`;
  const created = await ok(
    await admin.request.post("/api/v1/admin/users", {
      data: { username, first_name: "Iva", last_name: "Esempio", email: `${username}@example.org`, language: "it" },
      headers: CSRF,
    }),
    "new investor",
  );
  await admin.close();

  const { context, page } = await newContext.make();
  await page.goto("/login");
  await heading(page, "Accedi");
  await shoot(page, `${tag}-login`);
  await logInByForm(page, username, created.temporary_password);
  await heading(page, "Cambia la password temporanea");
  await shoot(page, `${tag}-forced-password`);
  await page.getByLabel("Password temporanea").fill(created.temporary_password);
  await page.getByLabel("Nuova password", { exact: true }).fill(NEW_PASSWORD);
  await page.getByLabel("Ripeti la nuova password").fill(NEW_PASSWORD);
  await page.getByRole("button", { name: "Cambia password" }).click();
  await page.locator(".bottom-nav").waitFor();

  await page.goto("/settings");
  await heading(page, "Impostazioni");
  await page.getByRole("button", { name: "Attiva 2FA" }).click();
  const dialog = page.getByRole("dialog", { name: "Attiva la verifica in due passaggi" });
  await dialog.getByLabel("Password").fill(NEW_PASSWORD);
  await dialog.getByRole("button", { name: "Continua" }).click();
  await dialog.locator(".qr-frame svg").waitFor();
  const secret = ((await dialog.locator(".two-factor-key code").textContent()) ?? "").replace(/\s/g, "");
  await dialog.getByLabel("Codice a 6 cifre generato dall'app").fill(await nextCode(secret));
  await dialog.getByRole("button", { name: "Conferma e attiva" }).click();
  await dialog.locator(".recovery-codes li").first().waitFor();
  await settled(page);
  await shoot(page, `${tag}-settings-2fa-codes`);
  await dialog.getByRole("button", { name: "Ho salvato i codici" }).click();

  await context.clearCookies();
  await logInByForm(page, username, NEW_PASSWORD);
  await heading(page, "Verifica in due passaggi");
  await shoot(page, `${tag}-login-code`);
  await context.close();
}

/** On the empty server: the first run's three steps, filled with invented values, never
 * submitted. */
export async function firstRunShots(page, tag, shoot) {
  await page.goto("/");
  await heading(page, "Benvenuto");
  await shoot(page, `${tag}-first-run`);
  await page.getByRole("button", { name: "Inizia da zero" }).click();
  await page.getByLabel("Nome utente").fill(OWNER.username);
  await page.getByLabel("Nome", { exact: true }).fill(OWNER.first_name);
  await page.getByLabel("Cognome").fill(OWNER.last_name);
  await page.getByLabel("E-mail").fill(OWNER.email);
  await page.getByLabel("Password", { exact: true }).fill(OWNER.password);
  await page.getByLabel("Ripeti la password").fill(OWNER.password);
  await shoot(page, `${tag}-first-run-admin`);
  await shoot(page, `${tag}-full-first-run-admin`, true);
  await page.getByRole("button", { name: "Continua" }).click();
  await page.getByRole("radio", { name: "Italiano" }).waitFor();
  await shoot(page, `${tag}-first-run-language`);
}

/** On the empty server: the first administrator (through the API) enrols and creates one
 * administrator per tag; returns their usernames and temporary passwords. */
export async function emptyAdministrators(browser, tags) {
  const context = await browser.newContext({ baseURL: EMPTY_BASE });
  const api = context.request;
  await ok(await api.post("/api/v1/setup/admin", { data: OWNER, headers: CSRF }), "first administrator");
  const { secret } = await ok(
    await api.post("/api/v1/me/two-factor/setup", { data: { password: OWNER.password }, headers: CSRF }),
    "2FA setup",
  );
  await ok(
    await api.post("/api/v1/me/two-factor/confirm", { data: { code: await nextCode(secret) }, headers: CSRF }),
    "2FA confirm",
  );
  const made = [];
  for (const tag of tags) {
    const username = `review-${tag}`;
    const created = await ok(
      await api.post("/api/v1/admin/users", {
        data: {
          username, first_name: "Ada", last_name: "Esempio", email: `${username}@example.org`,
          role: "ADMINISTRATOR", language: "it", password: OWNER.password, code: await nextCode(secret),
        }, // prettier-ignore
        headers: CSRF,
      }),
      `administrator ${tag}`,
    );
    made.push({ username, password: created.temporary_password });
  }
  await context.close();
  return made;
}

/** An administrator of the empty server: the forced password change, then the forced
 * enrolment's password step, QR code and recovery codes. */
export async function forcedEnrolmentShots(page, tag, user, shoot) {
  await logInByForm(page, user.username, user.password);
  await heading(page, "Cambia la password temporanea");
  await page.getByLabel("Password temporanea").fill(user.password);
  await page.getByLabel("Nuova password", { exact: true }).fill(NEW_PASSWORD);
  await page.getByLabel("Ripeti la nuova password").fill(NEW_PASSWORD);
  await page.getByRole("button", { name: "Cambia password" }).click();
  await heading(page, "Attiva la verifica in due passaggi");
  await shoot(page, `${tag}-forced-2fa-password`);
  await page.getByLabel("Password").fill(NEW_PASSWORD);
  await page.getByRole("button", { name: "Continua" }).click();
  await page.locator(".qr-frame svg").waitFor();
  await settled(page);
  await shoot(page, `${tag}-forced-2fa-qr`);
  await shoot(page, `${tag}-full-forced-2fa-qr`, true);
  const secret = ((await page.locator(".two-factor-key code").textContent()) ?? "").replace(/\s/g, "");
  await page.getByLabel("Codice a 6 cifre generato dall'app").fill(await nextCode(secret));
  await page.getByRole("button", { name: "Conferma e attiva" }).click();
  await page.locator(".recovery-codes li").first().waitFor();
  await settled(page);
  await shoot(page, `${tag}-forced-2fa-codes`);
  await shoot(page, `${tag}-full-forced-2fa-codes`, true);
}

/** At 320 px as the demo investor: the bottom bar ("Dashboard" on one line), the menu and
 * Settings; then the login page without a session. */
export async function narrowShots(page, context, shoot) {
  await page.goto("/");
  await page.locator(".bottom-nav").waitFor();
  await settled(page);
  await shoot(page, "narrow-light-bar");
  await page.getByRole("button", { name: "Altro" }).click();
  await page.getByRole("dialog", { name: "Altro" }).waitFor();
  await shoot(page, "narrow-light-menu");
  await page.getByRole("dialog").getByRole("link", { name: "Impostazioni" }).click();
  await heading(page, "Impostazioni");
  await shoot(page, "narrow-light-settings");
  await shoot(page, "narrow-light-full-settings", true);
  await context.clearCookies();
  await page.goto("/login");
  await heading(page, "Accedi");
  await shoot(page, "narrow-light-login");
}
