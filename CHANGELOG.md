# Changelog

What changed in each version of Portfolio Tracker, newest first. "Unreleased" collects what pull requests merged since the last version; the version bump moves it under the new version's number. The app shows these notes on its About page, and each GitHub release uses its version's section.

## Unreleased

### Added
- Two-factor sign-in on the server: a code from an authenticator app after the password, ten single-use recovery codes, and a limit on wrong codes.
- The login asks for the code of the authenticator app (or a recovery code) after the password, says how many attempts are left, and goes back to the password when the time for the code runs out.
- An administrator is taken to set up two-factor sign-in before anything else: scan the QR code (or type the key), confirm a code, then save the ten recovery codes, shown once.
- Settings has a row for two-factor sign-in: turn it on, turn it off, or make new recovery codes (each asks for the password and a code), with the number of recovery codes left.
- The Users screen shows who has two-factor sign-in and lets an administrator turn it off for another user; that user then sees a notice with the date until they dismiss it.
- An administrator can turn off another user's two-factor sign-in (for a lost phone); the user is told at the next login. Resetting a user's password does not turn it off.
- `reset-password <username> --disable-2fa` in the container also removes that user's two-factor sign-in: the way back in for an administrator who lost the phone and the recovery codes.
- `PT_REQUIRE_2FA`: `admins` (the default) obliges every administrator to set up two-factor sign-in before anything else; `off` obliges nobody.
- Resetting a user's password, and turning off a user's two-factor sign-in, ask the administrator's own password again (and a code, when the administrator has two-factor sign-in).
- The app's answers are never kept by a browser or proxy cache (`Cache-Control: no-store` on the API).
- The Holdings list has a filter "Only without a price source".
- The Administration page shows the price sources in use, read only: for ETFs and stocks, bonds, exchange rates and the security type, the sources switched on, in order, with how many securities each found.
- The log warns once per start when a reverse proxy's forwarded headers come from an address not in `PT_TRUSTED_PROXIES`; the README explains why the variable is needed behind a proxy.

### Changed
- The dashboard says the securities with no price source in one line, "No price source for N securities", which opens the Holdings list with only those.
- The About page's cards are spaced like those of the other pages.
- "Add" in the security search also fills in the symbol as the ticker for a result with an ISIN, so a CSV row without ISIN finds that security by its ticker; "Create in <currency>" still fills in only the ISIN.
- Backups now hold each user's two-factor secret, so two-factor sign-in keeps working after a restore. The archive is not encrypted: whoever has it can produce the sign-in codes. Encrypt it and keep it away from the server (`docs/backup-format.md`).
- The README's Security section tells what to do before the app is reachable from the Internet: set up two-factor sign-in for every administrator, save the recovery codes, use HTTPS only, and how a locked-out administrator gets back in.
- The database is upgraded at the first start of this version (two new tables for two-factor sign-in), after the usual copy in `backups/pre-migration/`.

## 1.0.0-rc.2

### Changed
- Data folders of the previews 0.9.0 and 1.0.0-rc.1 are not upgraded: the app stops with "this data folder comes from a preview version; start from an empty folder" and changes nothing in them. Export your transactions as CSV in the preview and import them into a new installation.
- You still sign in with your username.
- Usernames may use letters of any alphabet, digits and `. _ - @ +` (3 to 64 characters), so an e-mail address can be a username; `Àlex` and `àlex` are the same user.
- A failed login records only the username of an existing user, never the text typed for an unknown one.
- The database schema starts again from one baseline revision, which future versions upgrade.

### Added
- First name, last name and e-mail address for every user, asked when the administrator and each new user are created. The e-mail is kept for later: nothing is sent to it.
- The dashboard greets you by name, and the Users screen shows each user's name and e-mail.
- The release notes, on the About page: select the version.

### Fixed
- Long names and e-mail addresses wrap instead of overflowing the page.

## 1.0.0-rc.1

### Added
- Backup by command (`python -m portfolio_tracker backup`): one checked ZIP file with the whole database, without the price-source credentials and the sessions.
- Restore by command into a new, empty installation, checked before anything is written.
- A System section on About for administrators: the database schema, the last upgrade, the copies taken before upgrades and the latest backup.
- The licences of the third-party software, on About.
- A security without an ISIN can get one later, also while transactions use it: it then gets automatic prices.

### Changed
- Upgrades are safer: free space is checked first, a verified copy of the database is kept before any change, a failed upgrade is not retried in a loop, and the three newest copies are kept.
- A holding's price status names the exchange, the symbol and the date of the latest close.
- Security search lists each result with its symbol and exchange, warns when the source did not confirm the ISIN, and says when a result has no ISIN.
- The README explains installing and using the app step by step.

### Fixed
- A listing found by ISIN on an exchange must match the ticker confirmed for that exchange, so a holding is never priced from another security.
- Long security names wrap on the dashboard, the holdings list and the holding's page.

## 0.9.0

### Added
- First preview: accounts in their currency; purchases, sales, dividends, coupons, deposits, withdrawals and splits, with average cost and gains gross and net of taxes; bonds by nominal.
- Import of Directa Excel exports and of the app's own CSV, always with a preview to check before anything is saved; export of all transactions as CSV.
- Automatic daily closing prices for ETFs and stocks found by ISIN, and ECB exchange rates; prices entered by hand for anything else.
- Dashboard with allocation, income and best and worst securities; holdings list and holding pages.
- Italian and English, four colour themes, and several users, each seeing only their own portfolio.
- One container image with its health check, a database copy before each upgrade, and a security baseline: Argon2id passwords, revocable sessions, login limits and a strict content security policy.
