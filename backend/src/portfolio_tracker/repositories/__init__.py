"""Database access. Owner isolation lives here (ADR-0005): every function that reads or
writes a user's domain data (accounts, transactions, private assets, manual prices) takes
`owner_id` and filters by it. A row of another user is indistinguishable from a missing one:
the functions return None, and the API answers 404.

There is deliberately no unscoped getter for domain data.
"""
