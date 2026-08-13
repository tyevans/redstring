"""The ingestion benchmark: speed, accuracy and stability against a live model.

Not a test suite and not a gate. It runs on demand against a machine that is
not CI's, and its output is a committed JSON record read by a human. See
`docs/superpowers/specs/2026-08-13-ingestion-benchmark-design.md`.
"""
