# ADR-0008: Backup archive format

Status: accepted (M0, October 2026) — design only, built in M7

## Context
D-22, D-28, D-33, D-34; `../requirements.md` § Backup and Restore. Whole instance, one portable versioned archive with a manifest, optional passphrase encryption, provider credentials excluded, restore only from the setup wizard into an instance without users.

## Options considered
- **ZIP archive with a manifest and a SQLite copy** — portable, inspectable with standard tools, room for attachments later.
- **Raw SQLite file copy** — simplest, but no manifest/version, no place for attachments, credentials would need a separate scrub step anyway.
- **Logical export (JSON/CSV per table)** — version-independent but slow to restore and easy to get subtly wrong (types, ordering, constraints).

## Decision
- Archive: a **ZIP** file containing `manifest.json` (format version, app version, schema revision, creation time, content list with SHA-256 checksums, `encrypted` flag), `db/app.sqlite` (consistent copy via the SQLite online backup API, with provider credentials removed from the copy) and a reserved `attachments/` folder (empty in V1).
- Optional encryption: the whole ZIP is encrypted with a passphrase-derived key (Argon2id KDF + an AEAD cipher; library chosen in M7 under the license policy). Unencrypted backups are flagged in the UI.
- Restore validates the manifest version and checksums, then migrates the restored database forward with the normal Alembic path (pre-migration copy included).
- The format is documented in `docs/` in M7.

## Consequences
- Easier: one file to keep; restore works on any amd64 host.
- Harder: very large instances produce large single files — acceptable at the reference size.
