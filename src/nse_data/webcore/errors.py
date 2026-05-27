"""Domain errors raised by services — surface-agnostic.

Route layers map these to HTTP status codes; the dashboard maps them to UI
states. Defined here (not in a service module) so every service and every
surface can share one vocabulary.
"""

from __future__ import annotations


class BadRequest(ValueError):
    """Invalid parameter (-> HTTP 400)."""


class NotFound(Exception):
    """No data for the requested resource (-> HTTP 404)."""


class Unavailable(Exception):
    """A required source isn't ready yet (-> HTTP 503)."""
