# Implementer–Reviewer Release Protocol

## Purpose

Every implementation must pass two deliberately separate roles before it is presented as ready. Focused tests run by the implementer are not an independent release review.

## Role A — Implementer

The Implementer must:

1. start from the exact approved source package;
2. record scope, invariants, failure paths, compatibility policy, and acceptance criteria;
3. implement the smallest coherent active-flow change;
4. add focused regression and adversarial tests;
5. after each major task, self-review, fix every blocker, and repeat self-review until clean;
6. run contract validation, focused tests, relevant broader suites, closure scans, frontend checks, and platform-specific checks required by the changed ownership boundary;
7. push the exact candidate HEAD and run the required candidate CI runner;
8. verify that all required runner jobs complete successfully on that exact HEAD:
   - Backend review gates: dependency install, Alembic head upgrade, contract validation, closure audits, and backend platform/V4 regression suite;
   - Frontend and cutover review gates: dependency install, frontend/architecture tests, and type-check/build;
   - Windows Render review gates: Windows Python dependency install, FFmpeg/FFprobe preflight, Redis/RQ closure audit, and the focused durable Render/Windows process lifecycle/launcher suites;
9. list tests not run and external dependencies not verified.

The Implementer must not issue the final release verdict.

A local pass is not sufficient release evidence when the required candidate runner has not completed successfully.

## Role B — Independent Reviewer

The Reviewer starts from the candidate behavior and must not treat the implementation report as proof.

The Reviewer must:

1. compare the candidate with the approved scope;
2. trace valid import, malformed JSON, duplicate keys, reserved-ID failure, true upstream changes, semantic failure, CAS conflict, cleanup, and downstream invalidation;
3. verify the unified image contract, automatic all-beat Prompt Bundle compilation, atomic Scene/Visual/Prompt publication, hidden Prompt Bundle UI, and Scene Assets single-provider boundary;
4. verify Style Bible, Visual References, Voice, Assembly, and Render ownership remains valid;
5. for Render ownership changes, verify SQLite scheduling/lease authority, stale-attempt fencing, exact-lineage Final Video CAS, Redis/RQ removal, Windows launcher ownership, and Windows process-tree termination/recovery behavior;
6. run adversarial tests not used as the primary implementation proof;
7. rerun focused and broad suites from the candidate;
8. inspect the required candidate runner for the exact reviewed HEAD and verify Backend review gates, Frontend and cutover review gates, and Windows Render review gates each have conclusion `success`;
9. verify OpenAPI parity, removed-runtime closure, manifest determinism, and clean archive paths;
10. issue `PASS`, `CHANGES_REQUESTED`, or `BLOCKED_BY_UNVERIFIED_EXTERNAL_DEPENDENCY`.

## Mandatory runner evidence gate

`PASS` is forbidden unless the required candidate CI runner is green for backend, frontend/cutover, and Windows Render on the same exact candidate commit SHA being reviewed.

The following states do **not** satisfy this gate for any required job: `queued`, `pending`, `in_progress`, `skipped`, `cancelled`, `failure`, `timed_out`, `action_required`, `neutral`, or a job that did not run.

Required evidence is:

- **Backend review gates = `success`**, including successful dependency installation, `alembic upgrade head`, `python tools/validate_contracts.py`, the required closure audits, and the required backend regression suite;
- **Frontend and cutover review gates = `success`**, including successful dependency installation, `npm test`, and `npm run build` / type-check-build;
- **Windows Render review gates = `success`**, including successful Windows dependency installation, FFmpeg/FFprobe availability, Redis/RQ closure audit, and focused durable Render/Windows process ownership/launcher regression.

All required jobs must belong to the same runner execution for the same exact candidate HEAD, or to explicitly linked required executions that unambiguously test that same immutable commit SHA. Evidence from an earlier commit cannot be reused after any corrective patch.

If GitHub Actions or another required runner dependency is unavailable and any required job cannot be verified green, the Reviewer must issue `BLOCKED_BY_UNVERIFIED_EXTERNAL_DEPENDENCY`, not `PASS`.

## Rework loop

`CHANGES_REQUESTED` returns the package to the Implementer. Any corrective patch invalidates the previous review **and all previous runner evidence** and requires a new review plus a new required candidate runner execution for that task or phase. The next major task may not begin until the current task receives `PASS`.

## Release gate

A package is implementation-complete only when:

- the Reviewer reports no blocker or high-severity defect;
- the required candidate runner has Backend review gates, Frontend and cutover review gates, and Windows Render review gates at `success` on the exact reviewed HEAD;
- required automated checks pass;
- source and generated contracts agree;
- `MANIFEST.json` verifies deterministically;
- external media/TTS checks not exercised by the required runner are stated plainly.

There is no environment-blocked exception that permits `PASS` without green required runner evidence. If the required runner cannot be completed or verified, use `BLOCKED_BY_UNVERIFIED_EXTERNAL_DEPENDENCY`.

The application does not execute LLM providers, so no internal LLM-provider readiness claim exists.
