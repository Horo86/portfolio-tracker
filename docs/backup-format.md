# Backup archive format

The backup of a whole instance (D-22, D-169; ADR-0008). In 1.0.0 it is made by a command, with no screen, and it is **not encrypted**.

## Making a backup

```
docker exec portfolio-tracker python -m portfolio_tracker backup
```

Outside a container: `PT_DATA_DIR=<data folder> uv run python -m portfolio_tracker backup` (from `backend/`). `--output <folder>` writes the archive elsewhere; the default is `backups/manual/` in the data folder.

- It runs while the app runs: the database is read through the SQLite online backup API, which gives a consistent copy even while the app writes. It never migrates or changes the live database.
- It refuses, and writes nothing, when there is no database, when the database was written by a newer version (R-18), or when the free space is less than twice the database plus 64 MiB (the working copy and the archive).
- The archive is written as `.partial`, read back and checked against its manifest, then renamed. It has mode `0600`, and the `backups/manual/` folder `0700` when the command creates it.
- The app never deletes a manual backup: keeping, moving and deleting them is up to you.

## Contents

The archive is a ZIP file named `portfolio-<version>-<UTC time>.zip`:

| Entry | Content |
|---|---|
| `manifest.json` | what the archive is (below) |
| `db/app.sqlite` | the database: a single SQLite file (rollback journal, no `-wal`) |
| `attachments/` | reserved for a later version, empty |

`manifest.json`:

| Field | Meaning |
|---|---|
| `format` | `1`: the version of this format |
| `app`, `app_version` | `portfolio-tracker` and the version that wrote the archive |
| `schema_revision` | the database's migration revision (for example `0006`) |
| `created_at` | when the copy was taken, UTC |
| `encrypted` | `false` in 1.0.0 |
| `files` | for each file: `path`, `size` in bytes and `sha256` |
| `excluded` | what is left out: `tables` and `paths` (below) |

## What is left out

Access material never goes into an archive. On the **copy only**, these tables are emptied, then the copy is vacuumed, so their bytes are not left in free pages (R-21):

- `provider_credentials`: the market data providers' keys (encrypted, D-124). After a restore the administrator enters them again.
- `sessions`: the login sessions (token hashes and browser names). After a restore everyone logs in again.

The `secrets/` folder of the data folder (the key that encrypts the provider credentials) is never read.

Everything else is in the archive: users (with their password hashes, so they log in with their passwords after a restore), accounts, securities, transactions, manual prices, stored closes and exchange rates, import history, preferences, the market data settings and the audit log.

The pre-migration copies in `backups/pre-migration/` (D-168) are not backup archives: they keep everything, including the encrypted provider credentials.

## What to keep

- **The archive, away from the server** (another disk, another computer): a backup on the same disk is lost with it.
- **Not needed for a restore:** the `secrets/` folder, because the archive holds no credential. It is needed together with a hand copy of the whole data folder, and with the pre-migration copies. If you set `PT_SECRET_KEY`, keep that value as well.

## Checking an archive by hand

```
unzip -l portfolio-1.0.0-20260101T120000Z.zip
unzip -p portfolio-1.0.0-20260101T120000Z.zip manifest.json
unzip -p portfolio-1.0.0-20260101T120000Z.zip db/app.sqlite | sha256sum
```

The last value must equal the `sha256` of `db/app.sqlite` in the manifest.

## Encrypting an archive by hand

The archive is not encrypted: anyone who has the file can read all the data of the instance. Before you store it anywhere others could reach, encrypt it, for example:

- with 7-Zip: `7z a -t7z -mhe=on -p portfolio-backup.7z portfolio-1.0.0-20260101T120000Z.zip` (AES-256; asks for a passphrase; `-mhe=on` also hides the file names);
- with GnuPG: `gpg --symmetric --cipher-algo AES256 portfolio-1.0.0-20260101T120000Z.zip` (asks for a passphrase, writes a `.gpg` file).

Keep the passphrase somewhere other than the encrypted file. Encryption in the app is planned for a 1.0.x release.

## Restoring

The `restore` command and its steps are described here when they arrive (D-169 (2)).
