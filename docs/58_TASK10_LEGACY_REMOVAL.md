# Task 10 — Legacy Removal

Status: implemented.

Task 10 physically removes the superseded creative runtime, contracts, prompts, frontend types, tests and active documentation. It also removes the obsolete Project pointer from the current model and makes its historical Alembic revision a no-op marker so a clean database never creates the field.

## Closure guarantees

- `app/modules/v3` does not exist.
- Superseded creative API modules and router are deleted.
- `contracts/schemas/v3` and `contracts/prompts/v3` do not exist.
- Active frontend types and screens use Script Analysis and section identities only.
- Backend tests are under `tests/platform` and `tests/v4`; current frontend source and behavioral contracts remain as `tests/*.test.mjs`.
- Runtime, OpenAPI and worker registration expose one creative/production flow.
- No conversion, fallback, backfill or old-artifact reader was introduced.

Repository closure tests may contain exact removed identifiers solely as negative guard data. Migration revision `004` remains only as an empty historical chain marker; it does not create, restore or transform a removed pointer.
