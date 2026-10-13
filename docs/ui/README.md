# UI mockups (milestone M0.5)

Static mockups of every V1 screen, used as the reference for the frontend (M2 onwards).
All data is **synthetic** (invented user, accounts, securities and amounts). The images are
the reference; the app, once built, is the source of truth. If an implemented screen
deliberately differs from its mockup, update or mark the mockup as superseded here.

Language of the mockups: Italian (the app is i18n: it/en). Layout: desktop only. Light theme only.

## Screens

| # | Image | What it shows |
|---|-------|---------------|
| 01 | `screens/01-dashboard.png` | Dashboard: total value, invested capital, net gain (highlighted) with gross shown next to it, value vs invested chart, gain breakdown (gross / net), allocation by type and currency, best/worst holdings, income |
| 02 | `screens/02-holdings-list.png` | "Titoli" page: list of holdings in the portfolio, with account(s), quantity, average cost (PMC), last price, value, gross and net gain |
| 03 | `screens/03-holding-detail.png` | Detail of one holding (opened by clicking a row in 02): price chart with average-cost line and buy/sell/dividend markers, gain table, income received, transactions of that holding |
| 04 | `screens/04-transactions-list.png` | "Transazioni" page: all transactions (of the account in view), filters, multi-select with bulk edit/delete, CSV export |
| 05 | `screens/05-transaction-form.png` | New transaction (type selector, fields, live summary: total cost, cash effect, new average cost). The same form is used to edit |
| 06 | `screens/06-fix-dialogs.png` | Correction flows: edit, delete, bulk edit, "delete all transactions of a holding", and the conflict message when a change would make a quantity negative |
| 07 | `screens/07-import-file.png` | Import step 1: choose file, format, account, "do not move cash" |
| 08 | `screens/08-import-preview.png` | Import step 3: preview (to import / already present / unrecognized / warnings) |
| 09 | `screens/09-import-done.png` | Import step 4: summary |
| 10 | `screens/10-add-existing-holdings.png` | "Add holdings I already own": multi-row table, each row becomes a BUY that does not move cash |
| 11 | `screens/11-add-asset-search-create.png` | Add a holding: search by name/symbol/ISIN, or create it by hand (type, currency, quote unit) |
| 12 | `screens/12-manual-price.png` | Manual price entry and recent prices (per user) |
| 13 | `screens/13-accounts.png` | Accounts list; "Apri" sets the account in view and opens the dashboard |
| 14 | `screens/14-settings.png` | Settings: color theme, language, reporting currency, password, 2FA, sessions, CSV export, link to About |
| 15 | `screens/15-dialogs-security-accounts-users.png` | Dialogs: 2FA setup (QR + text key), recovery codes, change password, new user, password reset result, new account, delete account |
| 16 | `screens/16-login-first-run.png` | Login, 2FA code, first-run welcome (start empty / restore from backup), create administrator, restore |
| 17 | `screens/17-administration.png` | Administration (administrator only): users, manual backup (download now; restore only from the first-run screen), price sources (shown generically: the real provider is chosen and verified in M4), installation info |
| 18 | `screens/18-about.png` | About: version, AGPL v3 licence, source code link, third-party licences |
| 19 | `screens/19-empty-and-error-states.png` | Empty states (no accounts, no holdings, no results) and errors (unrecognized import file, prices stale, generic error, not found) |
| 20 | `screens/20-app-icon-options.png` | Icon options explored. **Chosen: option A** ("P and T joined, with a small amber rising line"). Vector source: `icon.svg` |
| v2 | `screens/v2-dashboard-mobile.png` | Mobile dashboard, kept as a **reference for V2** (mobile is not in V1) |

## Navigation and behavior shown by the mockups

- **Global account selector** at the top of the sidebar ("Conto in vista"): "All accounts" (default) or one account. It applies to every data page and is kept when changing page. The dashboard page header repeats the current view.
- Every list that can span accounts (transactions, holdings) shows the **account** of each record.
- "Tutti i conti" totals are in the **reporting currency**, chosen in Settings. A holding present in more than one account appears once in the aggregated view, with the accounts listed.
- Sidebar: Dashboard, Conti, Titoli, Transazioni, Importa, Impostazioni, Amministrazione. **Amministrazione is shown only to administrators** and enforced by the API (a non-admin gets "not found").
- "Informazioni" (About) is reachable from Settings and from the sidebar footer.
- A user who is not logged in always lands on the login screen; there is no public registration. On a fresh installation (no users) the first-run screen appears instead (start empty or restore a backup).
- Net gain is highlighted; gross is shown next to it. Positive/negative values are never conveyed by color alone (arrows/signs are always present).
- Totals flagged as **partial** (a missing FX rate excludes something) must carry a clearly visible "partial" label (decisions D-64, D-73).

## Decisions taken while designing (to be reflected in `decisions.md` / `requirements.md`)

- **Dark theme and mobile layout are V2**, not V1 (V1: light theme only, four color themes, desktop layout). The mobile dashboard is kept here only as a reference.
- Default color theme: **Moderno**. Selectable themes: Sobrio, Moderno, Ambra, Grafite (Settings).
- Deleting an account deletes its transactions (with a typed confirmation); archiving accounts is V2.
- A user's temporary password (new user / admin reset) is shown **once** and must be changed at first login.
- Reporting currency is a per-user setting.
- App icon: option A.
- Not in V1 (moved to V2): nightly automatic backup (V1 has manual backup only), a number/date format setting separate from the language, "delete my data" in Settings.

## Design tokens

Font: **Manrope** (weights 400–800), tabular numbers for all amounts. It must be **bundled with the app** (self-hosted; no external font CDN, since the app is self-hosted and should work offline).

Shape: cards radius 22 px, controls radius 14 px, pills fully rounded; minimum touch/click target 44 px.

Semantic colors (all themes):

| Token | Value |
|-------|-------|
| Positive | `#0B7A4F` (tint bg `#E8F6EF`, strong text `#0B5E3F`) |
| Negative | `#B3261E` (tint bg `#FCEBEC`, strong text `#8F1D17`) |
| Warning | bg `#FFF1CC`, text `#5A4300` |
| Invested-capital line | `#B7791F` (Ambra theme: `#4B5563`) |
| Surface | `#FFFFFF` |

Color themes (CSS variables: `--bg --ink --muted --border --accent --soft --nav --nav-active`, chart palette `--c1..--c4`):

| Theme | bg | ink | muted | border | accent | soft | nav | nav-active | chart c1–c4 |
|-------|----|-----|-------|--------|--------|------|-----|------------|-------------|
| Sobrio | `#F3F2EE` | `#1B1F23` | `#5E646B` | `#E3E1DB` | `#1F5C57` | `#E3EFEC` | `#12312F` | `#2B7A72` | `#1F5C57 #6FA89F #C9B27C #B8BCC2` |
| **Moderno** (default) | `#EEF0FB` | `#1C1B3A` | `#55537A` | `#E1E3F3` | `#4F46E5` | `#ECEBFD` | `#1E1B4B` | `#4338CA` | `#4F46E5 #0E9F9B #F0A81C #9AA0B5` |
| Ambra | `#FAF3E8` | `#2B2118` | `#6B5B4B` | `#EADFCB` | `#B45309` | `#FBEBD3` | `#3A2410` | `#B45309` | `#B45309 #0F766E #7C5CBF #A8A29E` |
| Grafite | `#F1F1EF` | `#1F2328` | `#5B6168` | `#E1E1DD` | `#3D4650` | `#E9EAEC` | `#1F2328` | `#3D4650` | `#3D4650 #8A949E #D9A441 #C4C8CC` |

Charts: area/line for value vs invested capital, donut for allocation by type, stacked bar for allocation by currency, simple bars for income.

## Notes for implementation

- "PMC" in tables is the average cost per unit (the full label is "Prezzo medio di carico").
- The mockups show Italian amounts (`1.234,56`) and `dd/mm/yyyy` dates. In V1 number and date formats follow the interface language (it/en); a separate format setting is V2.
- Accessibility target: WCAG 2.1 AA on the main flows (see `requirements.md`).
- Anything in a mockup that conflicts with `domain-rules.md` or `decisions.md` loses: the rules are the source of truth.
