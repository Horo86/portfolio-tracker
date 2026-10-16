"""File importers (M3): read an uploaded file into plain rows and map them to transactions.

Pure code: no database and no web imports, like the engine. Every importer recognizes only
what its spec in `docs/import/` lists as verified and reports the rest (CLAUDE.md rule 5).
Uploaded files are untrusted input: the limits of `base` apply before anything is parsed
(D-101).
"""
