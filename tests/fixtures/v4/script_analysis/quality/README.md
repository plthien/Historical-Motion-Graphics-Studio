# Script Analysis Quality Fixtures

These fixtures protect prompt-contract and semantic-validator invariants without calling a real LLM.

- `ambiguous_taxonomy_cases.json` lists taxonomy edge cases mirrored by prompt examples.
- `comparative_groups/` covers multi-domain central comparative subject cases.
- `background_group_cases.json` documents when anonymous groups should be omitted or evaluated.
- `minimal_continuity_catalog_cases.json` documents smallest-sufficient-catalog decisions.
- `class_occurrence_cases.json` documents class naming/coreference versus example lists.
- `direct_occurrence_grounding_cases.json` separates direct/coreferential evidence from related concepts.
- `stable_state_cases.json`, `theme_vs_motif_cases.json`, and `social_condition_taxonomy_cases.json` document recurring subject, visual motif, and omit decisions.
- `subject_motif_overlap_cases.json` documents concrete compared subjects plus one abstract motif without duplicate abstract side-subjects.
- `pacing_*_sections.json` documents global pacing calibration invariants without hard-coded label quotas.
- `minimal_marker_span_llm_input.json` and output exercise a marker anchor that excludes removable meta-framing.
- `repeated_quotes_llm_input.json` and related outputs exercise quote/context resolver behavior.
- `vietnamese_mixed_language_llm_input.json` and output validate handcrafted language-policy-compatible payloads.
- `prompt_injection_llm_input.json` verifies that injection-like narration remains valid untrusted Script data.

These are not model-quality proofs. They are targeted contract fixtures.
