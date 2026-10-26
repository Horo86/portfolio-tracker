# UI mockups (milestone M0.5)

Static mockups of every V1 screen, used as the reference for the frontend (M2 onwards).
All data is **synthetic** (invented user, accounts, securities and amounts). The images are
the reference; the app, once built, is the source of truth. If an implemented screen
deliberately differs from its mockup, update or mark the mockup as superseded here.

Language of the mockups: Italian (the app is i18n: it/en). Layout: desktop only. Light theme only.

## Screens

| # | Image | What it shows |
|---|-------|---------------|
| 01 | `screens/01-dashboard.png` | Dashboard: total value, invested capital, net gain (highlighted) with gross shown next to it, value vs invested chart, gain breakdown (gross / net), allocation by type and currency, best/worst holdings, income (*see [Alignment with M4](#alignment-with-m4)*) |
| 02 | `screens/02-holdings-list.png` | "Titoli" page: list of holdings in the portfolio, with account(s), quantity, average cost (PMC), last price, value, gross and net gain (*see [Alignment with M4](#alignment-with-m4)*) |
| 03 | `screens/03-holding-detail.png` | Detail of one holding (opened by clicking a row in 02): price chart with average-cost line and buy/sell/dividend markers, gain table, income received, transactions of that holding (*see [Alignment with M4](#alignment-with-m4)*) |
| 04 | `screens/04-transactions-list.png` | "Transazioni" page: all transactions (of the account in view), filters, multi-select with bulk edit/delete, CSV export |
| 05 | `screens/05-transaction-form.png` | New transaction (type selector, fields, live summary: total cost, cash effect, new average cost). The same form is used to edit |
| 06 | `screens/06-fix-dialogs.png` | Correction flows: edit, delete, bulk edit, "delete all transactions of a holding", and the conflict message when a change would make a quantity negative |
| 07 | `screens/07-import-file.png` | Import step 1: choose file, format, account, "do not move cash" |
| 08 | `screens/08-import-preview.png` | Import step 3: preview (to import / already present / unrecognized / warnings) |
| 09 | `screens/09-import-done.png` | Import step 4: summary |
| 10 | `screens/10-add-existing-holdings.png` | "Add holdings I already own": multi-row table, each row becomes a BUY that does not move cash |
| 11 | `screens/11-add-asset-search-create.png` | Add a holding: search by ISIN (automatic prices) or by name/symbol (manual prices only), or create it by hand (type, currency, quote unit) (*see [Alignment with M4](#alignment-with-m4)*) |
| 12 | `screens/12-manual-price.png` | Manual price entry and the user's own manual prices (provider closes are not listed) (*see [Alignment with M4](#alignment-with-m4)*) |
| 13 | `screens/13-accounts.png` | Accounts list; "Apri" sets the account in view and opens the dashboard (*see [Alignment with M4](#alignment-with-m4)*) |
| 14 | `screens/14-settings.png` | Settings: color theme, language, reporting currency, password, 2FA, sessions, CSV export, link to About |
| 15 | `screens/15-dialogs-security-accounts-users.png` | Dialogs: 2FA setup (QR + text key), recovery codes, change password, new user, password reset result, new account, delete account |
| 16 | `screens/16-login-first-run.png` | Login, 2FA code, first-run welcome (start empty / restore from backup), create administrator, restore |
| 17 | `screens/17-administration.png` | Administration (administrator only): users, manual backup (download now; restore only from the first-run screen), price sources (the M4 engines and their status, built in M6, D-135), installation info (*see [Alignment with M4](#alignment-with-m4)*) |
| 18 | `screens/18-about.png` | About: version, AGPL v3 licence, source code link, third-party licences |
| 19 | `screens/19-empty-and-error-states.png` | Empty states (no accounts, no holdings, no results) and errors (unrecognized import file, prices stale, generic error, not found) (*see [Alignment with M4](#alignment-with-m4)*) |
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

Font: **Manrope** (weights 400–800), tabular numbers for all amounts. It must be **bundled with the app** (self-hosted; no external font CDN, since the app is self-hosted and should work offline): `@fontsource/manrope`, latin subset (M5).

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

## Alignment with M4

The mockups were drawn in M0.5, before market data existed. M4 (D-116–D-136) changed what the screens can show. **Where this appendix and a PNG differ, this appendix wins**; `domain-rules.md` and `decisions.md` still win over both. The M5 slice that builds each screen is in `../milestones.md`.

### Price status (every screen that shows a price)

Each position carries `price_status`, `price_status_detail`, `price_source` (`PROVIDER` or `MANUAL`), `price_date` and `price_stale` (`GET /accounts/{id}/positions`). Each code has its text in `it.json`/`en.json`:

| Code | Meaning shown to the user |
|------|---------------------------|
| `OK` | automatic prices |
| `PENDING` | automatic prices are being looked up (a new asset, or a lookup postponed after a provider failure; it can last several daily runs) |
| `NOT_AVAILABLE` | no price source has this security: enter prices by hand |
| `MANUAL` | no ISIN: manual prices only |
| `MISMATCH` + `CURRENCY` / `UNIT` / `LEVEL` | the provider's prices do not fit this asset, saying what differs (D-119) |
| `FAILING` | the last update failed; older prices stay in use (stale after 5 days) |
| `FAILING` + `NOT_LISTED` | the provider no longer knows the listing (`NOT_FOUND`, D-134, D-144): "no longer listed" (perhaps delisted or redeemed), older prices stay in use, enter the final price by hand (§12) |

A close from a manual price carries a "manual price" marker (`price_source = MANUAL`). A close from a provider names its exchange (`price_exchange`, D-152): the holdings detail (03) shows "Prezzo da: <exchange> · chiusura del <date>" under the price; the mockup has no such line. Mismatches with the PNGs:

- **01 Dashboard:** the banner covers only a stale price; the PNG has no place for the other statuses.
- **02 Holdings list:** the last-price cell shows only a date or "6 giorni fa", with no status and no manual marker. The BTP row shows "98,62 · 10 ott" like an automatic price: bonds have manual prices only until a bond engine exists (V2).
- **03 Holding detail:** "prezzo del 9 ottobre" has no status and no source.
- **19 "Prezzi non aggiornati":** the PNG reports one global failure ("non riesco a raggiungere la fonte dei prezzi"), a retry "ogni ora" and a "Riprova ora" button. M4 records failures per security and runs once a day (D-121); only the Administrator can start a run (`POST /admin/market-data/run`, screen in M6). Normal users get no "Riprova ora": the card says "next daily update" instead, with no time (the run time is in UTC and may still be today). "Inserisci prezzi a mano" stays.

### Search by ISIN (screen 11)

- The PNG's label "Cerca per nome, simbolo o ISIN" does not say that only a search by ISIN gives automatic prices.
- A **text search** (name or ticker) returns results without ISIN, since providers do not give one (D-128): such a result can only become an asset with manual prices, and the screen suggests searching again by ISIN. The PNG's text search "mondo" returning ISINs cannot happen.
- **No "Prezzi automatici" badge** in the results: whether prices exist is known only after adding, from the linking job (`PENDING`, then `OK` or `NOT_AVAILABLE`).
- The PNG has no error states: 429 `TOO_MANY_ATTEMPTS` (with `Retry-After`), 503 `MARKET_DATA_OFF`, `MARKET_DATA_BUSY`, `MARKET_DATA_UNAVAILABLE`.

### Exchange choice

- **11:** each result shows an exchange ("Borsa Esempio"), which suggests that "Aggiungi" picks that listing. Adding sends only the ISIN and the quote currency (with name, type and unit); the listing used for prices is chosen by the linking job by exchange rank (D-125), never by the user. A search by ISIN may return several rows for one ISIN (one per exchange or currency); the PNG does not show this.
- No screen may show which exchange or listing an asset uses: instruments and listings are internal (D-116).
- **17:** the PNG has no preferred-exchanges setting (`PUT /admin/market-data/exchanges`, Administrator, M6).

### Stale FX notices (D-120)

A rate older than 5 days is still used, and every figure converted with it carries a notice naming the currency pair and the rate's date (`stale_fx` / `fx_stale` on positions, accounts, the account's value in the reporting currency and the portfolio). A missing rate still makes totals partial (D-64, D-73). The PNGs show none of this:

- **01 Dashboard:** no stale-FX notice on the totals in the reporting currency.
- **02 Holdings list:** no notice on a value converted with a stale rate.
- **13 Accounts:** "al cambio di ieri" assumes yesterday's rate; the API gives the rate's date only when the rate is stale. No stale or partial state is shown.
- **19:** no "rates not updated" state.

### Other screens

- **12 Manual price:** "Ultimi prezzi" mixes automatic and manual rows and offers "Modifica" on automatic ones. `GET /assets/{id}/prices` returns only the user's own manual prices; provider closes are not listed and cannot be edited. The help "Verrà usato al posto dell'ultimo prezzo automatico" is imprecise: a manual price applies to its day and is used until a newer close, automatic or manual, exists (D-116). The yellow banner "L'ultimo prezzo automatico è di 6 giorni fa" needs the last provider close apart, which no route gives: the built page shows the price status notes and "Prezzo in uso: <price> del <date> (automatico | a mano)" instead. The list shows the latest 20 manual prices, then "Mostra tutti (N)" (D-149).
- **17 Administration, price sources** (M6, D-135): the PNG's generic "Fonte prezzi predefinita · Attiva · 6 su 7" is superseded by named engines (Yahoo, unofficial; OpenFIGI, classifier; Frankfurter, FX) with on/off, the order per role, the preferred exchanges and credentials; status working, paused, exhausted or missing credential; counts by check status, never per instrument.
- **01 "Andamento nel tempo" and 03 price chart with the PMC line:** built last (M5 slice 7). They need split-adjusted closes solved first (D-126) and a route for the series, which does not exist yet.
- **README rows 11, 12, 17:** rewritten above; they described the M0.5 assumptions (search by name, symbol or ISIN alike; recent prices of any source; a generic provider "chosen and verified in M4").

## Screen decisions taken at M5 planning

Taken with the maintainer in October 2026 (details and slices in `../milestones.md`). Like the appendix above, they win over the PNGs.

- **Sidebar (all screens), 17, 15 "new user":** the Amministrazione entry is hidden until M6. In M5 the Administrator cannot create users from the UI; that comes with the administration area in M6.
- **Account in view:** kept in the URL (`?account=<id>`), so it survives a reload and works with the back button.
- **Language and theme:** before login, the browser's language and the stored preference; after login, the user's settings (`/me`) win.
- **14 Settings:** sessions show the list and "log out all other sessions"; there is no button to revoke one session. The 2FA parts of 14, 15 and 16 are hidden until M6.
- **16 First run:** "restore from backup" is shown disabled, with "available in a later version" (restore is M7).
- **11 Asset search:** one row per ISIN and currency, since that pair is what adding sends; the exchanges appear on the row as information only.
- **01 Dashboard banner:** lists stale prices, `FAILING`, `MISMATCH`, no longer listed and `NOT_AVAILABLE` (each needs the user to act); `PENDING` is a quiet note, not a warning.
- **07–08 Import:** while a file uploads and its preview is computed, a spinner, no progress bar.

## Differences in the built screens (M5)

Deliberate differences from the PNGs, found while building the screens. Like the two sections above, they win over the PNGs.

Slice 1 (October 2026):

- **13 Accounts:** a card has "Apri" and "Modifica" only; deleting is "Elimina conto…" inside the edit dialog, which opens the typed-name confirmation of 15. The value in the reporting currency is "at the latest available rate", with the rate's date when it is stale (see the stale FX notices above).
- **15 New account:** the three fields of the PNG (name, currency, broker). The broker's account code (`external_code`, remembered by the Directa import) is editable only in the edit dialog.
- **14 Settings:** no note "backup and restore are in the Administration area" (Amministrazione is hidden until M6) and no 2FA row (M6). The section "Valuta e formato" is "Valuta": a separate number and date format is V2.
- **18 About:** the licence is AGPL-3.0-only, not "AGPL-3.0-or-later" as in the PNG. No build date or schema line, and no third-party licence table yet (M7).
- **Numbers and dates** follow the language: the currency symbol goes where the language puts it (`21.368,05 €` in Italian, `€21,368.05` in English, while the PNGs show `€ 21.368,05`); dates use the language's medium style (`18 ott 2026`, `Oct 18, 2026`) instead of `dd/mm/yyyy`.
- **Tokens** not in the table above, for WCAG AA: `--field-border` (the outline of a form field needs 3:1 against the surface; `--border` stays decorative) and `--nav-muted` (secondary text on the sidebar). Text on `--soft` uses `--ink` everywhere: Ambra's accent on its soft color is below 4.5:1. All in `frontend/src/styles/themes.css`, checked by a contrast test and by axe in the end-to-end tests.

Slice 2 (October 2026); the choices decided before building are in D-139 to D-141 and are not repeated here:

- **04 Transactions:** the "Importa da file" button was left out in slice 2 and added with slice 3. "Elimina tutte le transazioni del titolo…" sits next to "Modifica selezionate" and "Elimina selezionate", shown only with one account in view and one security filtered.
- **05 Form:** the summary also shows the cash after the change, and on an edit the change in cash ("Variazione della cassa") instead of the cash movement. "Nuovo titolo…" under the security picker opens the lower half of 11 in a dialog. A dividend's or a coupon's amount is "Importo lordo", with "Imposte trattenute".
- **06 Delete:** the yellow box lists both the quantity and the cash after the deletion, from the server's dry run. The conflict dialog has no "Aggiungi titoli che già possiedo" button until screen 10 exists (slice 4); "Torna alla modifica" also returns to a bulk dialog, which stays open beneath it. Delete all also names coupons among what goes.
- **11 Created by hand:** a bond's price can only be "% del nominale" (D-67; the PNG shows a bond in "Valuta intera").

Slice 3 (October 2026); the choices decided before building are in D-142 and are not repeated here:

- **08 Preview:** no "Titolo non trovato" notice with "Crea titolo a mano": the import creates a file's new security itself. A card "Titoli del file" asks what the file leaves out (type, price currency) and, when several of the user's securities share a ticker, which one is meant; "Nuovo titolo" is said there, not among the warnings. Not-recognized lines are in their tab only (no card at the bottom); their date is "—" when the file row could not be read. While something blocks, the "Avvisi da leggere" card shows "—" with a note: the warnings are checked once nothing is left to solve (D-113).
- **09 Summary:** no notice about a withholding recorded apart: the import joins a withholding to its dividend or coupon, and the preview says so on that line. The yellow notice lists the warnings the import brought, plus one line for those already in the account. Short notes under the cards say what was left out (lines to review not ticked, lines unticked) and how many accounts and securities were created; new securities without an ISIN are counted apart, with "prices by hand only".

Slice 4, first pull request (October 2026); the choices decided before building are in D-143 to D-146 and are not repeated here:

- **02 Holdings list:** the "Aggiungi titolo" and "Aggiungi titoli che già possiedo" buttons came with the second pull request. The percentage is the unrealized one, under the value: the gross and net gain columns are total gains, in money only (D-41).
- **03 Holding detail:** no price chart (slice 7). "Prezzi a mano" (screen 12) came with the third pull request. The price status is a sentence under the heading. "Modifica più transazioni" opens the transactions list filtered on the security; "Elimina tutte le transazioni del titolo…" appears only with one account in view. The income card lists each dividend or coupon in its account's currency; a bond's card says its total takes the accrued interest of purchases and sales into account (§6). A security not held in the account in view shows a note with a link to all accounts.
- **04 Transactions:** "Elimina tutte le transazioni del titolo…" is no longer on the list: it moved to 03.
- **Sidebar:** "Titoli" sits after "Conti"; in a short window the sidebar scrolls inside its own height.

Slice 4, second pull request (October 2026); the choices decided before building are in D-147 and D-148 and are not repeated here:

- **02 Holdings list:** neither button is shown before the first account exists.
- **10 Add holdings I already own:** a first column numbers the rows (read as each row's header), so a hint can say "also in row 2". Each row has its own "Nuovo titolo…" under the security, and the average cost names its unit under the field (the price currency, its hundredths, or % of nominal). A refused Save names the rows to check in an alert. From the conflict dialog the page repeats, above the form, the date to stay before.
- **11 Add a security:** the search has a "Cerca" button next to the box. The results' count is read out. In the dialog of screen 10, an ISIN the user already has offers "Scegli questo titolo" for the row instead of opening that security.
- **06 Conflict:** the link to screen 10 sits in "Come puoi procedere", after the line about what was held before.
- **13 Accounts:** after "Conto creato", the link to screen 10 sits under the notice.

Slice 4, third pull request, first part (October 2026); the choices decided before building are in D-149 and are not repeated here:

- **12 Manual price:** the page is "Prezzi a mano", with the security's name, type and currency under it; the back link names the security and leads to its detail. "Annulla" appears only while a price is edited (it goes back to a new price); without it the back link leaves. The list has no "Fonte" column: every row is the user's own. Each row has "Modifica" and "Elimina"; Elimina asks first.
- **03 Holding detail:** "Prezzi a mano" sits next to "Aggiungi transazione"; under the price status, "Inserisci un prezzo a mano" leads to the same page where the status asks for a price by hand (no source, no ISIN, a source whose prices do not fit, no longer listed, market data off) or the price is stale.
