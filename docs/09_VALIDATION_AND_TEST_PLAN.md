# Validation and Test Plan

Required gates include:

- strict raw JSON parsing and duplicate-key rejection;
- reserved Artifact ID issuance, reload-safe lookup, one-time consumption, and true upstream-source rejection;
- JSON Schema and Pydantic validation;
- Topic selected-title avoidance tests;
- combined Script/Analysis section mapping, quote resolution, and atomic CAS tests;
- full-Script section coverage, unified image-field validation, and paired draft Scene/Visual publication tests;
- automatic all-beat Prompt Bundle compilation during approval;
- atomic Scene Plan + Visual Plan + Prompt Bundle publication, staged-file cleanup, and stale-pointer conflict tests;
- Prompt Bundle exact beat coverage, order, hash, lineage, generation options, visible-text, and reference-binding validation;
- Scene Assets one-provider-path tests for generate-all, generate-missing, regenerate, worker failure, candidate state transitions, manifest approval, and removed-route closure;
- Assembly/frame-quantization/render-graph tests proving classification-free image-only downstream behavior;
- artifact integrity, lineage, tamper, losing-CAS cleanup, and downstream invalidation;
- clean-database migration and SQLite isolation;
- frontend tests proving Prompt Bundle is hidden, aspect ratio is sent/hydrated, the JSON editor emits only raw editable fields, and Scene Assets has no local/existing/transition UI;
- static/runtime OpenAPI parity;
- closure scans proving internal LLM/research runtime and removed asset-classification runtime are absent;
- deterministic manifest and archive audit.
