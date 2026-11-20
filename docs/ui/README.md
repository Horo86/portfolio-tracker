# UI mockups (milestone M0.5)

Static mockups of every V1 screen, used as the reference for the frontend (M2 onwards).
All data is **synthetic** (invented user, accounts, securities and amounts). The images are
the reference; the app, once built, is the source of truth. If an implemented screen
deliberately differs from its mockup, update or mark the mockup as superseded here.

Language of the mockups: Italian (the app is i18n: it/en). Layout: desktop only. Light theme only.

## Screens

| # | Image | What it shows |
|---|-------|---------------|
| 01 | `screens/01-dashboard.png` | Dashboard: total value, invested capital, net gain (highlighted) with gross shown next to it, value vs invested chart (V2, D-156), gain breakdown (gross / net), allocation by type and currency, best/worst holdings, income (*see [Alignment with M4](#alignment-with-m4)*) |
| 02 | `screens/02-holdings-list.png` | "Titoli" page: list of holdings in the portfolio, with account(s), quantity, average cost (PMC), last price, value, gross and net gain (*see [Alignment with M4](#alignment-with-m4)*) |
| 03 | `screens/03-holding-detail.png` | Detail of one holding (opened by clicking a row in 02): price chart with average-cost line and buy/sell/dividend markers (V2, D-156), gain table, income received, transactions of that holding (*see [Alignment with M4](#alignment-with-m4)*) |
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
| 14 | `screens/14-settings.png` | Settings: color theme, language, reporting currency, password, 2FA (from 1.0.0-rc.3, D-174), sessions, CSV export, link to About |
| 15 | `screens/15-dialogs-security-accounts-users.png` | Dialogs: 2FA setup (QR + text key) and recovery codes (both from 1.0.0-rc.3, D-174), change password, new user, password reset result, new account, delete account |
| 16 | `screens/16-login-first-run.png` | Login, 2FA code (from 1.0.0-rc.3, D-174), first-run welcome (start empty / restore from backup), create administrator, restore (not built: a backup is restored with the `restore` command, D-169; the button is shown disabled) |
| 17 | `screens/17-administration.png` | Administration (administrator only): users (M6), manual backup (not built on this page: backup and restore are commands, D-169), security settings (a Security card between Users and Price sources from 1.1.0, D-182; no mockup), price sources (the M4 engines and their status, D-135: a read-only card from 1.0.0-rc.3, D-175 (4); editing later), installation info (built as the administrators' "Sistema" section of About, D-168) (*see [Alignment with M4](#alignment-with-m4)*) |
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

- **Mobile layout is V2**, not V1 (desktop layout only). The mobile dashboard is kept here only as a reference. The dark theme, first V2, came in 1.1.0 (D-176; palette below).
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

Dark (from 1.1.0, D-176): one palette, `[data-theme="scuro"]`, shown instead of the color theme when the per-browser setting "Light or dark" (default "Same as the device") asks for it. Text on a colored button is `--surface`.

| Token | Dark | Token | Dark |
|-------|------|-------|------|
| bg | `#0F1117` | positive | `#4ADE80` (tint `#12301F`, strong `#86EFAC`) |
| surface | `#1A1D27` | negative | `#FF8A80` (tint `#3A1618`, strong `#FFB4AB`) |
| ink | `#E8EAF2` | warning | bg `#3D3000`, text `#FFE08A` |
| muted, field border | `#A3A8BA` | chart 1 / c1 | `#818CF8` |
| border | `#2C3040` | chart 2 / c2 | `#2DD4BF` |
| accent | `#A5B4FC` | chart 3 / c3, invested | `#FBBF24` |
| soft | `#262A3D` | chart 4 / c4 | `#9AA0B5` |
| nav | `#171A2B` | nav-ink, nav-muted | as in light |
| nav-active | `#4338CA` | | |

Two tokens of the dialogs, in every theme: `--backdrop` (light `rgb(15 15 30 / 45%)`, dark `rgb(0 0 0 / 60%)`) and `--dialog-edge` (light `transparent`, dark `--border`, drawn as a 1 px ring). The QR code of the two-factor enrolment stays black on its white frame in dark.

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

A close from a manual price carries a "manual price" marker (`price_source = MANUAL`). A close from a provider names its exchange (`price_exchange`, D-152): the holdings detail (03) shows "Prezzo da: <exchange> · chiusura del <date>" under the price, a known exchange named in the user's language from `price_exchange_code` (slice 6); the mockup has no such line. Mismatches with the PNGs:

- **01 Dashboard:** the banner covers only a stale price; the PNG has no place for the other statuses.
- **02 Holdings list:** the last-price cell shows only a date or "6 giorni fa", with no status and no manual marker. The BTP row shows "98,62 · 10 ott" like an automatic price: bonds have manual prices only until a bond engine exists (V2).
- **03 Holding detail:** "prezzo del 9 ottobre" has no status and no source.

### Search by ISIN (screen 11)

- The PNG's label "Cerca per nome, simbolo o ISIN" does not say that only a search by ISIN gives automatic prices.
- A **text search** (name or ticker) returns results without ISIN, since providers do not give one (D-128): such a result can only become an asset with manual prices, and the screen suggests searching again by ISIN. The PNG's text search "mondo" returning ISINs cannot happen.
- **No "Prezzi automatici" badge** in the results: whether prices exist is known only after adding, from the linking job (`PENDING`, then `OK` or `NOT_AVAILABLE`).
- The PNG has no error states: 429 `TOO_MANY_ATTEMPTS` (with `Retry-After`), 503 `MARKET_DATA_OFF`, `MARKET_DATA_BUSY`, `MARKET_DATA_UNAVAILABLE`.

### Exchange choice

- **11:** each result shows an exchange ("Borsa Esempio"), which suggests that "Aggiungi" picks that listing. Adding sends only the ISIN and the quote currency (with name, type and unit); the listing used for prices is chosen by the linking job by exchange rank (D-125), never by the user. A search by ISIN may return several rows for one ISIN (one per exchange or currency); the PNG does not show this.
- The user never chooses a listing: instruments and listings are internal (D-116). *Since D-152 the holding detail names the exchange of the price in use ("Prezzo da", see above), and a search result names its exchanges as information only.*
- **17:** the PNG has no preferred-exchanges setting (`PUT /admin/market-data/exchanges`, Administrator, V2, D-161).

### Stale FX notices (D-120)

A rate older than 5 days is still used, and every figure converted with it carries a notice naming the currency pair and the rate's date (`stale_fx` / `fx_stale` on positions, accounts, the account's value in the reporting currency and the portfolio). A missing rate still makes totals partial (D-64, D-73). The PNGs show none of this:

- **01 Dashboard:** no stale-FX notice on the totals in the reporting currency.
- **02 Holdings list:** no notice on a value converted with a stale rate.
- **13 Accounts:** "al cambio di ieri" assumes yesterday's rate; the API gives the rate's date only when the rate is stale. No stale or partial state is shown.
- **19:** no "rates not updated" state.

### Other screens

- **12 Manual price:** "Ultimi prezzi" mixes automatic and manual rows and offers "Modifica" on automatic ones. `GET /assets/{id}/prices` returns only the user's own manual prices; provider closes are not listed and cannot be edited. The help "Verrà usato al posto dell'ultimo prezzo automatico" is imprecise: a manual price applies to its day and is used until a newer close, automatic or manual, exists (D-116). The yellow banner "L'ultimo prezzo automatico è di 6 giorni fa" needs the last provider close apart, which no route gives: the built page shows the price status notes and "Prezzo in uso: <price> del <date> (automatico | a mano)" instead. The list shows the latest 20 manual prices, then "Mostra tutti (N)" (D-149).
- **17 Administration, price sources** (V2, D-135, D-161): the PNG's generic "Fonte prezzi predefinita · Attiva · 6 su 7" is superseded by named engines (Yahoo, unofficial; OpenFIGI, classifier; Frankfurter, FX) with on/off, the order per role, the preferred exchanges and credentials; status working, paused, exhausted or missing credential; counts by check status, never per instrument. *From 1.0.0-rc.3 (D-175 (4)) a read-only card under the Users block shows whether market data is on and, per role, the enabled engines in order with their counts by check status; a role without engines says so (bonds: manual prices). No editing until 1.1.0.*
- **17 Administration, price sources screen** (1.1.0, D-186, D-187; no mockup beyond the card above): the page `/admin/price-sources`, administrators only (404 for anyone else), linked from the Price sources card, which stays as the summary. At the top the status (market data on or off, the running job, the next run) and "Aggiorna ora". Then the engines, each with its roles, official or not with a link to its terms, a switch, its status (working, paused, exhausted, missing credential), the time of its last success and last error code (since the start of the process), and its counts by check status, never per instrument. Then, per role, the order of the enabled engines with move up and move down and add and remove buttons (no drag and drop), and the preferred exchanges as ordered text entries. Leaving `ETF_STOCK` or `FX` without an enabled engine asks a confirmation first; a note says that changes apply at the next update. For an engine that needs a key, the credential form is write only: "Salvata il <data>", Replace and Remove, and the value is never shown. The credential and the purge of a disabled engine (price roles only) go through a dialog that asks the administrator's password and, with 2FA, a code (D-187); the other changes ask nothing.
- **01 "Andamento nel tempo" and 03 price chart with the PMC line:** V2 (M5 slice 7, D-156). They need split-adjusted closes solved first (D-126) and a route for the series, which does not exist yet.
- **17 Administration, Security card** (1.1.0, D-182; no mockup): between the Users block and the price sources, built from the Settings rows' pattern as a form: under the heading an intro and where the values come from ("Valori iniziali, dalla configurazione del server…" until the first save, then "Salvate il <data>."); two fieldsets with legends, "Sessioni" (days without use, days after login) and "Tentativi di accesso falliti" (attempts per user and per address, first and longest block), each a short numeric text field with its range and meaning as hint. "Salva" is inactive (`aria-disabled`, so the focus can come back to it) until a value differs; every field is checked first, the errors show under the fields and the focus goes to the first wrong one; then a dialog without the red band lists "<campo>: da X a Y" and asks the administrator's password and, with 2FA, a code. After the save the focus returns to "Salva" and the status line says so, with the number of open sessions shortened when there are some. From 1.1.0 also D-183: a third fieldset "Verifica in due passaggi" with the checkbox "Richiedi la verifica in due passaggi a tutti gli utenti"; its hint counts the active users without 2FA (from the users list) and says what happens; the dialog lists it as "…: da no a sì".
- **README rows 11, 12, 17:** rewritten above; they described the M0.5 assumptions (search by name, symbol or ISIN alike; recent prices of any source; a generic provider "chosen and verified in M4").

## Screen decisions taken at M5 planning

Taken with the maintainer in October 2026 (details and slices in `../milestones.md`). Like the appendix above, they win over the PNGs.

- **Sidebar (all screens), 17, 15 "new user":** the Amministrazione entry is hidden until M6. In M5 the Administrator cannot create users from the UI; that comes with the administration area in M6. *M6 (D-165): the entry is shown to administrators only and holds the Users block alone, built from 17's table: no action on the Administrator's own row (neither "Reimposta password" nor "Disattiva"; the row says "Sei tu"), and the temporary password of a new user shown in a result dialog after creation (like the reset result), not inside the create form. The backup block was not built: M7 delivered backup and restore as commands (D-169). Price sources are a read-only card from 1.0.0-rc.3 (D-175 (4)); the installation info is the "Sistema" section of About, for administrators (D-168).*
- **Account in view:** kept in the URL (`?account=<id>`), so it survives a reload and works with the back button.
- **Language and theme:** before login, the browser's language and the stored preference; after login, the user's settings (`/me`) win.
- **14 Settings:** sessions show the list and "log out all other sessions"; there is no button to revoke one session. **15 and 17, Users (D-174, after the security review):** the dialogs "Reimpostare la password" and "Disattivare la 2FA" of a user ask the administrator's own password and, when the administrator has 2FA, a code or a recovery code; the API refuses the action without them. The "Nuovo utente" dialog asks for the same, under the role, only when the role is Administrator (D-175 (6)). The 2FA parts of 14, 15 and 16 are built in 1.0.0-rc.3; where they differ, D-174 wins (the code step keeps the password step's page, disabling asks for the password and a code, recovery codes can be made again, an administrator must enrol).
- **16 First run:** "restore from backup" is shown disabled; the text under it says that in this version a backup is restored with the `restore` command on the server, in a new installation (D-169, `docs/backup-format.md`). The restore card of the PNG was not built.
- **11 Asset search:** one row per ISIN and currency, since that pair is what adding sends; the exchanges appear on the row as information only.
- **01 Dashboard banner:** lists stale prices, `FAILING`, `MISMATCH`, no longer listed and `NOT_AVAILABLE` (each needs the user to act); `PENDING` is a quiet note, not a warning.
- **07–08 Import:** while a file uploads and its preview is computed, a spinner, no progress bar.

## Manual prices of all securities (1.1.0)

No mockup. `/holdings/prices` ("Aggiorna i prezzi a mano") lists the open holdings whose prices no source will ever fill: `NOT_AVAILABLE`, `MANUAL` (no ISIN) or `OFF` (market data off), not a mismatch or a failing source. Wider than the holdings filter `source=none` and the dashboard line, which stay on `NOT_AVAILABLE` only (D-175 (1)): the security with a link, its last manual price and date, and one price field; a bond's field is "% del nominale, corso secco". One date for all rows (default today, never in the future); "Salva i prezzi" sends each typed price to `PUT /assets/{id}/prices` one by one, a failed row keeps its value and shows its error under the row, a saved row says "Salvato". Built from the pattern of screen 12's form and the list pages. It is reached only from the button "Aggiorna i prezzi a mano" in the head of the holdings list, shown while an open holding needs prices by hand (same rule), with or without the filter; the dashboard line "Nessuna fonte prezzi per N titoli" still opens the filtered list. No menu entry and no link in Settings.

## Live price check (1.1.0)

No mockup (D-178, D-179). The form that creates a security by hand carries the check, so it appears in its three places: the lower half of screen 11 (`/holdings/add`), the "Nuovo titolo…" dialog of screen 10 and the "Nuovo titolo…" dialog of the transaction form (05). `components/PriceCheck.tsx` inside `components/AssetCreateForm.tsx`.

- **Where:** a secondary button "Controlla la fonte prezzi" under "Il prezzo è espresso in", the last field that feeds it, and above "Annulla" and "Crea titolo"; its answer sits right under it in a polite `role="status"` region. Both come after the fields in the tab order; the focus never moves.
- **When:** only on a click, never while typing (D-128, D-148). The button is enabled with a valid ISIN (the form's own check) and a type; currency and unit always have a value. It is hidden when market data is off (`market_data` `OFF` on `/me`).
- **While it runs:** the button is disabled and reads "Controllo in corso…", the region announces it; no second click.
- **Answers:** `FOUND`: "Fonte prevista: {fonte} · {borsa} · ultimo prezzo {prezzo} del {giorno}" (the price in the security's unit, as on the holdings list; the exchange named from its code where known; the engine by the name in `format/engine.ts`), or "(nessun prezzo disponibile)" without a close, always followed by the muted line "La fonte definitiva viene scelta dopo il salvataggio." `NO_SOURCE`: "Nessuna fonte: i prezzi si inseriscono a mano." with the reason (no provider knows the code; no active source covers the type). `MISMATCH`: currency or unit, each pointing at the field to check. Refusals: too many checks or other checks running, with the seconds of `Retry-After` (without it, the app's generic text); providers not answering.
- **Information only:** it fills no field and never holds the save back. The answer is cleared as soon as the ISIN, the type, the currency or the unit change, and a late answer for values changed meanwhile is not shown; a dialog closed and opened again starts empty.

## Small screens (1.1.0)

D-189. Below 1024 px the app uses the **compact shell**; below 768 px the **phone** layout. Desktop (1024 px and wider) is unchanged. Every rule for it is in `frontend/src/styles/responsive.css`, inside one of the two media queries.

- **Top strip** (not sticky, the sidebar's colours): the app icon and "Conto in vista"; the account note stays for screen readers only.
- **Bottom bar** (fixed, at least 56 px high per entry, the device's safe area below it): Dashboard, Titoli, Transazioni, Importa, Altro, each a line icon over its label; the current page has the `--nav-active` background, and "Altro" looks current on the pages it holds. The keyboard focus is a 3 px `--nav-ink` ring inside the entry.
- **"Altro"**: a dialog, full screen on phones: the user and role, Conti, Impostazioni, Amministrazione (administrators only), Informazioni with the version, then Esci and Chiudi. Choosing a page closes it.
- **Content:** padding 28/24 px on tablets, 20/16 px on phones; cards 18/16 px on phones; the page leaves room at the bottom for the bar, and the import's confirmation bar sits above it; a page header stacks on phones; `h1` is 1.5 rem on phones.
- **The window never scrolls sideways** (at 320 px too), or the fixed bar could leave the screen: a wide table scrolls inside its own `.table-scroll` (made the containing block of its screen-reader header texts, which escaped its overflow), the dashboard's columns take 100 % when narrower than 420 px, and on phones a label-and-value row wraps, the colour swatches wrap and the dashboard's share tables have tighter cells. No overflow is set on `main`, `.content` or `body`, so `position: sticky` keeps working.
- **Tablets** (768–1023 px) show the desktop pages with this shell. A table wider than its card scrolls inside it, with a shadow on the edge that has more to show.
- **Tables become cards on phones** (D-190), through `components/Table.tsx`: the same table with its roles written out; each row a card of "label above value" cells, two per line, the wide ones on a line of their own; the column headers hidden for the eye only. A table of at most four short columns stays a table. Done: Administration's users (the user wide, "Sei tu" beside the name on one's own row; e-mail wide; role and status, 2FA and last access in pairs; actions wide); the holdings (D-191: the name wide, the only link, stretched over the whole card, with the keyboard focus ring around the card; the accounts wide; then quantity and average cost, last price with its status pills and value, gross and net gain); a holding's transactions (date and type, the account wide, quantity and price, amount and fees, taxes and cash effect, then "Modifica"); the Transactions list (D-192); the import preview's lines (D-193). The other tables keep scrolling inside their card until their screen is done.
- **Dashboard, holdings and a holding's page on phones** (D-191): one column of cards; the gains, allocation and income tables stay tables with tighter cells; the donut centred above its table; the holdings list's head buttons full width, its filters two per row; a holding's buttons two per row. Tablets keep the desktop pages.
- **Transactions on phones** (D-192): the head's "Aggiungi transazione" full width, "Importa da file" and "Esporta CSV" side by side; the filters two per row, "Cancella filtri" on its own line. Each transaction a card: the tick, the date and the type on the first line; the security, then the account (all accounts only), each on a line of its own; Quantità | Importo, Effetto sulla cassa | "Modifica", the right column lined up under the type. The header row keeps only "select all", with the visible text "Seleziona tutte". The selection bar stays in place while nothing is selected; with something selected it sticks to the top of the window ("N selezionate", then its two buttons side by side) until the end of the list's card. The form in one column, the summary under it and not sticky, the buttons full width with "Elimina…" last on screen. Tablets keep the desktop pages.
- **Import on phones** (D-193): "scegli dal computer" a full-width button inside the drop zone, its text kept. The preview's count cards two per row, its cards stacked, the tabs wrapping (all in sight), "Seleziona tutte" and "Deseleziona tutte" side by side. Each line a card: the tick, the date and the type; the security; the account (several accounts only); Quantità | Importo, Cassa | Righe del file; Note. The confirmation bar sticks 12 px above the bottom bar, its text then Annulla and the confirmation full width; at the end of the page the last card is above it, and Tab never leaves a control under it. The summary's counts two per row, its table of accounts a table, its buttons full width. Tablets keep the desktop pages, the bar sticking above the bottom bar.
- **Dialogs fill the screen on phones** (D-190): no radius, the content scrolls, the buttons full width at the end of the content, never sticky; a danger dialog keeps its red band. Tablets keep the desktop dialog. Every dialog has a visible button that closes it (a phone has no Escape).
- **Touch targets** of at least 44 px below 1024 px: a table's tick in a 44 px area around a 24 px box, a checkbox's label, the calendar button, `summary`, the actions on a card. Links inside sentences are exempt.
- **Administration on phones:** the Security card's Save is full width; the price sources show each role, then its engines under it.

## Differences in the built screens (M5)

Deliberate differences from the PNGs, found while building the screens. Like the two sections above, they win over the PNGs.

Slice 1 (October 2026):

- **13 Accounts:** a card has "Apri" and "Modifica" only; deleting is "Elimina conto…" inside the edit dialog, which opens the typed-name confirmation of 15. The value in the reporting currency is "at the latest available rate", with the rate's date when it is stale (see the stale FX notices above).
- **15 New account:** the three fields of the PNG (name, currency, broker). The broker's account code (`external_code`, remembered by the Directa import) is editable only in the edit dialog.
- **14 Settings:** no note "backup and restore are in the Administration area" (Amministrazione is hidden until M6; backup arrives in M7) and no 2FA row until 1.0.0-rc.3 (D-174). The section "Valuta e formato" is "Valuta": a separate number and date format is V2.
- **18 About:** the licence is AGPL-3.0-only, not "AGPL-3.0-or-later" as in the PNG. The build date is the hint under the version ("Compilata il …", or "Versione di sviluppo" in a development run, D-167); the schema is in the administrators' System section (D-168); the third-party licences of the web app and the server are two tables, each in a native `<details>` ("Show the list"), closed at first, with a link to the full texts (D-167).
- **Numbers and dates** follow the language: the currency symbol goes where the language puts it (`21.368,05 €` in Italian, `€21,368.05` in English, while the PNGs show `€ 21.368,05`); dates use the language's medium style (`18 ott 2026`, `Oct 18, 2026`) instead of `dd/mm/yyyy`. **Date fields** (M5 slice 6) are typed as text in the app's language, `gg/mm/aaaa` in Italian and `dd/mm/yyyy` in English, whatever the browser's language (its own date field follows the browser): "/", "-" or "." between the parts, a one-digit day or month, a four-digit year; a two-digit year or a day that does not exist is told once the field is left, or at once after Save ("Scrivi la data come gg/mm/aaaa.", "Questa data non esiste."). A calendar button beside the field opens the browser's own calendar. Used by the transaction form (date, value date), the transaction filters, "add holdings I already own" and the manual prices.
- **Tokens** not in the table above, for WCAG AA: `--field-border` (the outline of a form field needs 3:1 against the surface; `--border` stays decorative) and `--nav-muted` (secondary text on the sidebar). Text on `--soft` uses `--ink` everywhere: Ambra's accent on its soft color is below 4.5:1. All in `frontend/src/styles/themes.css`, checked by a contrast test and by axe in the end-to-end tests.

Slice 2 (October 2026); the choices decided before building are in D-139 to D-141 and are not repeated here:

- **04 Transactions:** the "Importa da file" button was left out in slice 2 and added with slice 3. "Elimina tutte le transazioni del titolo…" sits next to "Modifica selezionate" and "Elimina selezionate", shown only with one account in view and one security filtered.
- **05 Form:** the summary also shows the cash after the change, and on an edit the change in cash ("Variazione della cassa") instead of the cash movement. "Nuovo titolo…" under the security picker opens the lower half of 11 in a dialog. A dividend's or a coupon's amount is "Importo lordo", with "Imposte trattenute". A sale shows "Imposte trattenute" directly under quantity, price and fees, not among the other amounts, which keep the gross and the bond-only fields (D-157); the PNG shows only a purchase. A sale with "Non muovere la cassa" (how a holding moves to another account, `domain-rules.md` §3.1, E10) also shows "Costo tolto dalla posizione", the exact cost its quantity removes on its date with every decimal (never quantity × the average cost shown), a hint on the whole move (that amount as the gross, fees and taxes at zero; on the destination account a purchase of the same quantity that does not move cash, with the same gross) and a button "Usa questo importo" that types it in the gross field (the other amounts then open); the button waits while the figures are being updated.
- **06 Delete:** the yellow box lists both the quantity and the cash after the deletion, from the server's dry run. The conflict dialog has no "Aggiungi titoli che già possiedo" button until screen 10 exists (slice 4); "Torna alla modifica" also returns to a bulk dialog, which stays open beneath it. Delete all also names coupons among what goes.
- **11 Created by hand:** a bond's price can only be "% del nominale" (D-67; the PNG shows a bond in "Valuta intera").

Slice 3 (October 2026); the choices decided before building are in D-142 and are not repeated here:

- **08 Preview:** no "Titolo non trovato" notice with "Crea titolo a mano": the import creates a file's new security itself. A card "Titoli del file" asks what the file leaves out (type, price currency) and, when several of the user's securities share a ticker, which one is meant; "Nuovo titolo" is said there, not among the warnings. Not-recognized lines are in their tab only (no card at the bottom); their date is "—" when the file row could not be read. While something blocks, the "Avvisi da leggere" card shows "—" with a note: the warnings are checked once nothing is left to solve (D-113). Two cards not in the PNG, under the blocking card, in the screen 19 card style (D-158): "Imposta sulla plusvalenza da aggiungere a mano" lists each capital gains tax the file could not link to a sale (date, amount, row), inside a status region announced once, and is not repeated among the warnings; "Vendite con imposta collegata per data" shows each sale to review because its tax was linked by date (security, date, quantity, gross amount), the note with the tax's date and amount, and the sale's own tick, the same as in its tab.
- **09 Summary:** no notice about a withholding recorded apart: the import joins a withholding to its dividend or coupon, and the preview says so on that line. The yellow notice lists the warnings the import brought, plus one line for those already in the account. Short notes under the cards say what was left out (lines to review not ticked, lines unticked) and how many accounts and securities were created; new securities without an ISIN are counted apart, with "prices by hand only".

Slice 4, first pull request (October 2026); the choices decided before building are in D-143 to D-146 and are not repeated here:

- **02 Holdings list:** the "Aggiungi titolo" and "Aggiungi titoli che già possiedo" buttons came with the second pull request. The percentage is the unrealized one, under the value: the gross and net gain columns are total gains, in money only (D-41).
- **03 Holding detail:** no price chart (slice 7, V2). "Prezzi a mano" (screen 12) came with the third pull request. The price status is a sentence under the heading. "Modifica più transazioni" opens the transactions list filtered on the security; "Elimina tutte le transazioni del titolo…" appears only with one account in view. The income card lists each dividend or coupon in its account's currency; a bond's card says its total takes the accrued interest of purchases and sales into account (§6). A security not held in the account in view shows a note with a link to all accounts.
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

Slice 4, third pull request, second part (October 2026); the choices decided before building are in D-150 and are not repeated here:

- **03 Holding detail:** "Modifica titolo" sits after "Prezzi a mano" in the head. The edit dialog has no mockup: it takes the fields of the lower half of 11. A refusal for a transaction saved meanwhile names the fields in words ("il tipo e la valuta"). Only the fields changed are sent, so a change made in another tab to another field is kept. "Elimina titolo" sits on the left of the dialog's buttons, as "Elimina conto…" in the account dialog; for a security in use, the hint that replaces it sits above them.
- **02 Holdings list:** "Mostra" opens the list of securities with no transactions in place, with no way to close it again, and moves the focus to it; each row shows the name, type and currency.

Slice 5, second pull request (October 2026); the choices decided before building are in D-153:

- **01 Dashboard, confirmed at the slice 5 plan:** no theme dots at the top right (the theme stays in Settings); the view line says "valori al <day>", the valuation day, instead of "aggiornato al"; "Cassa" instead of "Liquidità", the word of the whole app since slice 6; no "Andamento nel tempo" card (slice 7, V2).
- **01 Dashboard, built:** the greeting is the page's heading; the browser tab says "Dashboard". Each banner line names a security with its short price status (or its stale date) and leads to its detail, where "Inserisci un prezzo a mano" is. The allocation and the income are also tables, beside their charts (see below). The table of securities shows the 10 largest, and "Altri (N)" shows the rest in place instead of one summed row: amounts are never added up in the browser. For the same reason the income card has no total of the last 12 months; it says the total since the start. Best and worst is one list, best first, with the % in a colored pill; the rows themselves are not colored. The taxes line under "Da dove viene il guadagno" is the holding detail's ("sui proventi"). The table of the last 12 months is gross only, as its caption says; "Per anno" and "Dall'inizio" give the net too. When accounts are left out for a missing rate (D-73), the allocation and the income cards each repeat "Esclusi i conti in …", and the income card never says "nessun dividendo" then; the banner's lines for single securities come from the holdings of the accounts that are not left out.

Slice 5, third pull request (October 2026), the charts:

- **01 Dashboard:** the donut by type has no text in its centre ("per tipo 100%"), and the bars of the months carry no figures above them: every value is in the table beside each chart, whose rows carry the chart's colors as the legend; the charts themselves are hidden from screen readers and react to neither pointer nor keyboard. The donut sits beside its table, or above it when the card is too narrow for both (two columns of cards). The bar by currency sits between "Per valuta" and its table. Months with nothing show no bar, and with nothing in any of the twelve months there is no chart, only the table. A chart that cannot load or draw shows nothing; the tables stay. Each slice and stretch also has a pattern besides its color. The chart colors are the tokens `--chart-1..4`: the palette `--c1..4` of the table above, darkened where it was below 3:1 against the card (WCAG 1.4.11), and a darker fourth grey in Grafite; checked by the contrast test in the four themes and in dark (D-176).


Slice 6 (October 2026), screen 19: every empty, not found and load error state is one card (icon tile, heading, text, actions). Where the app differs from the PNG:

- **No accounts:** the greeting stays the page's heading (D-153), so the card has none; its three steps are the PNG's, and "Crea il primo conto" opens the new account dialog on Conti.
- **No securities:** over all accounts the heading says "I tuoi conti non hanno ancora titoli"; "Questo conto…" is for one account.
- **No results:** the transactions name their filters; the holdings say only that nothing matches.
- **File not recognized:** also for a file that cannot be read, with the column or row when the server names one. The box lists the twelve verified columns of a Directa export (`import/directa.md`), not the PNG's eight, and the columns a Portfolio Tracker CSV needs at least.
- **Generic error:** the code is the server's own (e.g. `INTERNAL_ERROR`), not "E-503". Only the Administrator is told to check the container's logs; anyone else is told to tell the administrator.
