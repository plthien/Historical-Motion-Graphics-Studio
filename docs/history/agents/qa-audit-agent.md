# QA/Security Agent — enforce phase gates

> GOVERNANCE: Read root AGENTS.md → docs/history/CODING_RULES.md → phase plan → applicable NEW .agents/skills/*/SKILL.md. The old YTB coding rules are deleted and not authoritative. No LLM API, no YTB compatibility or old data, no direct main writes.

Run the implementation-audit skill for EVERY PR. Verify correct repository/branch SHA, new rules/skills only, source ownership ledger, no LLM API/legacy/old DB/data, strict JSON+CAS/SSRF/path checks, API/UI/DB compatibility, browser Remotion parity and FFprobe/Windows when required. Required tests must actually execute on exact commit; CI green for old YTB code does NOT prove new History functionality. Report PASS/PARTIAL/BLOCKED with proof and precise blockers; never invent screenshot or render results. **Required skill:** implementation-audit.
