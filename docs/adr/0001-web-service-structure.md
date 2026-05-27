# ADR 0001 — Web service structure: shared read-core + thin surfaces

- **Status:** Accepted
- **Date:** 2026-05-27
- **Deciders:** jayasati
- **Supersedes:** the flat `nse_data.ops` web package

## Context

The dashboard and stock API grew up inside a single `nse_data/ops/` package. That
package is *internally* well-layered (`config → db → repository → transforms →
service → routes → static`), but the flat package conflates four unrelated
concerns under one name:

1. **Shared read-core** — `repository.py`, `service.py`, `transforms.py`, `config.py`, `db.py`
2. **A machine JSON API** — `routes/stocks.py` (`/api/stocks/*`)
3. **A human dashboard UI** — `routes/pages.py` + the whole `static/` frontend
4. **Observability** — `health.py`, `metrics.py`, `replay.py`, `verify_endpoints.py`

Separately, a top-level `api/` package already exists, stubbed with exactly the
Layer 7 route names from `FEATURE_CHECKLIST.md` §10 (`signals`, `profile`,
`fundamentals`, `blacklist`, `announcements`, `events`, `reports`, `universe`,
`admin`, `webhooks`) — every file currently empty.

The risk: when Layer 7 is filled in, those routes will re-implement DB reads that
already live in `ops/repository.py`, producing **two parallel web stacks reading
the same tables with two copies of the SQL**. Layers 3–7 will each want to add a
page and an API route; under the flat structure that means repeatedly editing the
same crowded package, and the duplication compounds.

## Decision

Adopt the principle:

> **One shared read-core, two thin surfaces (machine API for the bot, dashboard
> for humans), and `ops/` shrinks to observability/batch only.**

Surfaces hold no SQL and no business logic — they only translate the core into
HTTP/HTML. Adding a feature becomes *additive*: a new repository module + service
module + one API route + one dashboard page, touching nothing that already exists.

Layout: **top-level siblings** (not a single umbrella package), reusing the
existing top-level `api/`. The shared read-core is named `webcore/` (single word,
matching the `collectors`/`scheduler`/`storage` convention).

```
src/nse_data/
  webcore/                      # shared read layer — NO web-framework imports
    config.py                   #   table names, paths, constants   (from ops/config.py)
    db.py                       #   read-only connection             (from ops/db.py)
    transforms.py               #   pure candle math                 (from ops/transforms.py)
    errors.py                   #   BadRequest / NotFound / Unavailable
    introspect.py               #   table / ts-column detection      (generic half of ops/health.py)
    repositories/
      stocks.py                 #   (from ops/repository.py)
      signals.py  profile.py  fundamentals.py  blacklist.py …   # added as layers land
    services/
      stocks.py                 #   (from ops/service.py)
      signals.py  profile.py  …

  api/                          # MACHINE surface (Layer 7 §10) — thin, pydantic, paginated
    server.py                   #   composition root: mounts api + dashboard + monitoring routers
    deps.py                     #   DI: conn → repository → service
    schemas.py
    routes/
      stocks.py                 #   (from ops/routes/stocks.py)
      signals.py  profile.py  announcements.py  blacklist.py
      events.py  fundamentals.py  reports.py  universe.py  admin.py
    webhooks.py

  dashboard/                    # HUMAN surface — thin
    routes.py                   #   page shells (from ops/routes/pages.py)
    static/
      pages/   *.html
      css/
      js/{core,components,pages}/

  ops/                          # SHRINKS to observability + non-web batch
    health.py                   #   collector/endpoint-health domain logic
    routes.py                   #   /api/health, /admin/endpoint-health, /metrics
    metrics.py  replay.py  verify_endpoints.py
```

The composition root lives in `api/server.py` (the planned Layer 7 server), which
mounts the dashboard and monitoring routers alongside the API routes — one app,
three route groups, one shared `webcore`.

## Consequences

**Positive**
- No duplication: the empty `api/` stubs and the live stocks API collapse onto one
  `webcore`. Bot API and dashboard call the *same* services/repositories.
- Growth is additive, not invasive — shipping the signals page touches only new files.
- Compute packages (`signals/`, `profile/`, `indicators/`, …) stay the *write* side
  (nightly jobs); `webcore` is the *read* side. Different access patterns, kept apart.
- Frontend `js/{core,components,pages}` stops the flat `static/js/*.js` junk drawer.
- `ops/` becomes honest — only things that aren't web.

**Negative / cost**
- A one-time mechanical move + import-path churn (the layering already exists, so
  little logic changes). Mitigated by phasing with the test suite green at each step.
- `pyproject.toml` `dashboard` extra comment and any `uvicorn` entrypoint that
  referenced `nse_data.ops.web:app` must be updated to `nse_data.api.server:app`.

## Migration phases (tests green after each)

1. `ops/{config,db,transforms} → webcore/`; split `health.py` into
   `webcore/introspect.py` (generic) + `ops/health.py` (collector-health).
2. `repository.py → webcore/repositories/stocks.py`,
   `service.py → webcore/services/stocks.py`, add `webcore/errors.py`.
3. `ops/web.py → api/server.py`; `ops/routes/stocks.py → api/routes/stocks.py`;
   wire the existing `api/` stubs to import from `webcore`.
4. `ops/routes/pages.py → dashboard/routes.py`; reorganize `static/`.
5. Trim `ops/` to `health.py`, `routes.py`, `metrics.py`, `replay.py`, `verify_endpoints.py`.

## Notes

This is the first entry toward the `FEATURE_CHECKLIST.md` §17 "ADR-style decision
log" item.
