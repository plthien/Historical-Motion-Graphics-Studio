# Topic Ideas External Authoring

The Topic Ideas screen collects only seed topic, topic count, focus, and focus keywords. It does not display an avoid-topic field, selected-topic counter, or selected-topic history.

The backend:

1. loads Channel context;
2. resolves titles of currently selected topics from projects in the same Channel;
3. writes those titles into the existing `avoid_topics` runtime field;
4. supplies the Project-snapshotted Channel DNA `narration_identity` so candidate tone/POV recommendations preserve the Channel personality;
5. renders one copyable ChatGPT prompt with the strict raw output schema;
6. preallocates the Topic Ideas Artifact ID, stores a pending reservation with the frozen prompt inputs, and embeds the exact ID in the prompt and output schema.

Every imported Topic Idea contains the full review contract:

- title;
- core question;
- viewer hook;
- topic angle;
- tone;
- CTR score;
- difficulty;
- content tags;
- risk notes;
- recommended `pov_style`;
- recommended Script `avoid` list;
- recommended video `pacing_mode` (`fast | medium | slow`);
- a concise reason for that pacing recommendation.

`pov_style`, `avoid`, and `recommended_pacing_mode` are candidate-specific Script handoff recommendations. The Topic Ideas runtime input includes the complete Project-snapshotted Channel DNA `pacing_profiles`, so the model evaluates the candidate against that Channel's actual FAST/MEDIUM/SLOW calibration rather than a global interpretation of the labels. Pacing recommendation is based on the idea's structural potential for genuine story-state progression plus independently drawable explanatory/editorial facets and the supplied Channel profile, not a simple niche/title mapping and not a guaranteed final beat cadence. Candidate `tone` is a project-specific modulation inside the supplied Channel narration identity, and `pov_style` may recommend POV/story engine/evidence delivery without redefining that identity. Selecting a Topic copies those defaults into the Script & Analysis form once. The user may edit POV/avoid and may override pacing mode before building the Script prompt. Topic Ideas never invents or retunes numeric pacing ranges.

The user copies the prompt to ChatGPT. The prompt requires ChatGPT to attach/export `topic_ideas.json`; pure JSON in the response is accepted as a fallback. Validate-only performs no write. Import reruns strict parsing, schema and semantic validation, assigns canonical `idea_NNN` IDs, publishes one immutable Topic Ideas artifact, and invalidates stale selection/downstream state.

Selected topics added after a Topic prompt was built do not invalidate that already-issued prompt: the prompt's hidden `avoid_topics` list is frozen in the reservation. A change to the target project's own Topic Ideas pointer blocks import. Prompt wording or schema hash is not used as an import identity; the reserved Artifact ID and current validation contract are authoritative.

The application never calls an LLM for Topic Ideas.
