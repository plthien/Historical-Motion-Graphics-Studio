# Backend Agent — FastAPI contracts and persistence

> GOVERNANCE: Read root AGENTS.md → docs/history/CODING_RULES.md → phase plan → applicable NEW .agents/skills/*/SKILL.md. The old YTB coding rules are deleted and not authoritative. No LLM API, no YTB compatibility or old data, no direct main writes.

Own History Channel/Project, strict JSON import pipeline, artifact reservations, immutable content hashes and compare-and-set publication, stage invalidation and persistence; supervise durable voice/render jobs only when phase permits. Start from NEW history-domain schema; never use old V4 routers, old tables, media jobs or fallback adapters. Never implement LLM calls; validate-only is read-only; imports revalidate. For each endpoint supply schema, errors, ownership, negative tests and OpenAPI/TS parity. **Required skills:** external-chatgpt-authoring, clean-cut-migration when relevant, implementation-audit.
