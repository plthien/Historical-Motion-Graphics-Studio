# Exact JSON shape

Return exactly one JSON object with this shape:

```json
{
  "artifact_id": "Use the exact reserved Topic Ideas artifact ID supplied in the prompt",
  "ideas": [
    {
      "title": "string",
      "core_question": "string",
      "viewer_hook": "string",
      "topic_angle": "string",
      "tone": "string",
      "ctr_score": 1,
      "difficulty": "easy",
      "content_tags": ["string"],
      "risk_notes": ["string"],
      "recommended_pacing_mode": "medium",
      "pacing_mode_reason": "string",
      "pov_style": "string",
      "avoid": ["string"]
    }
  ]
}
```

# Structural rules

- The root fields are exactly `artifact_id` and `ideas`.
- `artifact_id` must exactly match the reserved Topic Ideas artifact ID supplied by the backend in the prompt.
- `ideas` is an array containing exactly `user_input.topic_count` candidates.
- Every candidate must contain all and only these fields:
  - `title`: non-empty string.
  - `core_question`: non-empty string.
  - `viewer_hook`: non-empty string.
  - `topic_angle`: non-empty string.
  - `tone`: non-empty string.
  - `ctr_score`: integer from 1 through 10.
  - `difficulty`: enum string, exactly one of `easy`, `medium`, or `hard`.
  - `content_tags`: array of strings.
  - `risk_notes`: array of strings.
  - `recommended_pacing_mode`: enum string, exactly one of `fast`, `medium`, or `slow`.
  - `pacing_mode_reason`: non-empty concise reason grounded in the idea's story/evidence structure and visual-state potential.
  - `pov_style`: non-empty recommended Script point of view and narration style.
  - `avoid`: array of candidate-specific Script treatments or claims to avoid.
- Additional root fields and additional candidate fields are forbidden.

# Application-owned fields

The application assigns `idea_id`, `artifact_type`, `schema_version`, `channel_context`, `source_user_input`, revision identity, source tracking, and provider or model metadata after import. Do not include any of them. The only application-issued identity that must be echoed is the reserved root `artifact_id`.

Return no scripts, outlines, sections, scenes, beats, shots, thumbnails, visual prompts, image prompts, visual styles, production plans, explanations, comments, or markdown.
