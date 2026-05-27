"""Data-access layer — SQL reads, one module per bounded context.

Services depend on these interfaces, not on SQL. Each repository takes a single
read-only connection at construction. As Layers 4-7 land, add a module here per
context (signals, profile, fundamentals, blacklist, …) rather than growing one
god-repository.
"""
