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
- An administrator sees the latest backup (its name in the data folder, size and date), the last upgrade and the kept pre-migration copies on the About page, in the System section (D-168 (6)).

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

A backup is restored with the `restore` command, **with the app stopped**, into an instance **without users**: a new data folder, or the old one after its `db` folder has been moved aside (keep it until the restored instance works). There is no restore screen in 1.0.0 (D-169).

With the compose file of the README (the service is called `portfolio-tracker`):

1. Put the archive in the data folder, for example in `restore/` (on the server: `<data folder>/restore/portfolio-1.0.0-20260101T120000Z.zip`).
2. Stop the app: `docker compose stop portfolio-tracker`.
3. If the data folder already has data, move its `db` folder aside: `mv <data folder>/db <data folder>/db.before-restore`.
4. Restore: `docker compose run --rm portfolio-tracker python -m portfolio_tracker restore /data/restore/portfolio-1.0.0-20260101T120000Z.zip`
5. Start the app: `docker compose start portfolio-tracker`. If the archive comes from an older version, the app upgrades the database first, with a pre-migration copy (D-168).
6. Everyone logs in with their own password. The administrator enters the provider credentials again, if any were set.

Outside a container: `PT_DATA_DIR=<data folder> uv run python -m portfolio_tracker restore <archive>` (from `backend/`).

**Run it as the app's user.** `docker compose run` uses the `user:` of the compose file (`PUID:PGID`, the same as the app), so the restored files belong to the app. Run by hand as root (for example with `sudo`, or `docker run --user 0`), the files would belong to root and the app could not open them: put them back to the app's user with `sudo chown -R <PUID>:<PGID> <data folder>` (the numbers of `user:` in the compose file), then start the app. The restored database gets the same permissions as one the app creates itself.

What the command checks, before it writes anything:

- the app is not running on that data folder (it holds a lock on `db/.server.lock`; on file systems without locks, such as some network shares, nothing can tell, so always stop the app first);
- the instance has no database, or a database without users;
- the file is a backup archive of this app: exactly the three entries above, a manifest of format `1`, not encrypted, a schema revision this version knows (an archive from a newer version needs that version);
- there is free space for twice the database's declared size plus 64 MiB.

Then it writes the database as `db/app.sqlite.partial`, stopping as soon as it grows past the size in the manifest, compares its size and SHA-256 with the manifest, checks its integrity and revision, and only then renames it to `db/app.sqlite`. Stale `-wal`/`-shm` files and a `migration-failed.json` of the old database are removed. If anything is wrong, it stops with a message and leaves the data folder as it was.

The `secrets/` folder is not touched: a new installation keeps its own key, which is fine because the archive holds no credential.
