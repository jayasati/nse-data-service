"""Human dashboard surface — page shells + static frontend.

Thin: serves HTML shells and the static bundle. All data comes from the api/
surface over HTTP, computed by webcore. As pages are added (signals, profile,
…) add a shell under static/pages/, a module under static/js/pages/, and a
route here — nothing else changes.
"""
