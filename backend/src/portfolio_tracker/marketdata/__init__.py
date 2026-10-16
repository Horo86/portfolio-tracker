"""Market data: provider interfaces, adapters and their registry (D-116 to D-129, ADR-0007).

Pure package: no database or web imports. HTTP goes through an injectable transport
(`http.Transport`), so the adapters can be tested without the network.
"""
