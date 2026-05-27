"""Shared read-core for the HTTP-facing surfaces (api/ and dashboard/).

Framework-free: holds configuration, the read-only DB connection, pure candle
transforms, table introspection, domain errors, and the repository/service
layers that both the machine API and the human dashboard read through. No
FastAPI imports live here — surfaces translate this core into HTTP/HTML.

See docs/adr/0001-web-service-structure.md for the rationale.
"""
