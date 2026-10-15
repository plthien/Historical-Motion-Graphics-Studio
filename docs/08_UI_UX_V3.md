# UI/UX

The Studio presents this linear pipeline:

```text
Topic Ideas → Script & Analysis → Style Bible → Scene Breakdown
→ Scene Assets → Voice → Assembly → Render
```

Prompt Bundle is intentionally absent from navigation. It remains an internal immutable artifact and is compiled automatically when Scene Breakdown is approved.

Topic Ideas, Script & Analysis, and Scene Breakdown use the shared prompt/import workspace:

- build prompt;
- preview and copy a prompt that requests a named `.json` export;
- paste JSON or upload a `.json` file;
- validate with path-specific errors;
- import after a valid result;
- upload JSON after reload without rebuilding the prompt by resolving the reserved Artifact ID from the file.

Topic Ideas does not display selected-topic avoidance history. Its cards show the complete planning contract, including CTR, difficulty, question, angle, hook, tone, tags, risks, suggested POV, Script avoid guidance, suggested pacing mode, and pacing reason. Selecting an idea initializes editable Script defaults once. Script & Analysis is one screen and one import with `llm_decides` or `backend_plan` section planning. The Script form exposes `FAST / MEDIUM / SLOW`, preselects the Topic recommendation, and allows override before prompt build. Imported Script review shows the selected video mode, backend-derived aggregate WPM, per-section planned narration WPM, estimated narration runtime, and requested runtime.

Scene Breakdown accepts one full-Script result, then reviews it with a section selector, scene cards, a detail drawer, and progressively mounted beat cards. Each beat is reviewed as one image design. The approval control owns output aspect ratio and communicates that image prompts are built automatically. Approval errors remain visible. The review workspace surfaces `static_image_v5` post-segmentation timing telemetry, including each section's locked narration WPM, semantic `pacing_density`, preferred hold band, and below/within/above conformance ratios. These values drive review/rescan semantics but do not define or validate a required beat count.

Scene Assets becomes ready only when the backend has atomically published the approved Scene Plan, Visual Plan, and hidden Prompt Bundle. Every beat has the same generate, regenerate, candidate review, approval, and clear-approval workflow. Candidate previews preserve the generated aspect ratio instead of cropping to 16:9.

The UI contains no asset classification, local-render controls, existing-asset binding, LLM settings, research action, creative job polling, or provider diagnostics page.
