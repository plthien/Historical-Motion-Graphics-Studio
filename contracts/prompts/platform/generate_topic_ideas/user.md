Generate exactly `user_input.topic_count` Topic Idea candidates from the Input JSON.

Use the active channel authoring skill for the Topic Ideas stage to perform the creative evaluation. This task prompt defines the stage-specific inputs, focus policy, required fields, ordering, and hard constraints.

# How to use the input

Use only the fields present in the Input JSON:

- Use only the current task inputs and fresh external research performed during this run when current-market verification is needed. Do not use remembered research, competitor findings, factual conclusions, or project data from prior chats/sessions as authority.
- `channel_context.name`: The channel display name only. Use it only to identify which channel this task belongs to; do not infer style, audience traits, performance history, creative rules, reputation, or channel history from the proper noun.
- `channel_context.language`: Write every viewer-facing output string in this language. Proper nouns may retain their accepted names.
- `channel_context.niche`: Defines the channel's main subject boundaries and relevant adjacent themes. Every candidate must fit naturally within this scope.
- `channel_context.audience`: Describes the supplied viewers' interests and expected context. Use it to judge relevance, vocabulary, accessibility, and likely curiosity/payoff. Do not invent demographics, expertise, preferences, or behavior not stated here.
- `narration_identity`: Defines how narration should sound and behave for this channel. Use its `voice`, `delivery`, `explanation_style`, `tone_boundaries`, and `avoid` values to preserve narration voice, delivery, explanation style, tone limits, and avoidances for every candidate. Candidate-specific tone/POV may modulate this identity but may not replace it.
- `pacing_profiles`: Defines the supplied pacing calibration for `fast`, `medium`, and `slow`. These mode labels are channel-relative: the numeric ranges for `fast` in this input may differ from `fast` in another project. Use the supplied profiles exactly when judging which mode best fits each candidate; do not substitute a global meaning or retune any range.
- Inside each pacing mode, `high`, `medium`, and `low` are visual-density bands, not quality scores, emotional intensity, or narration speed. Each `preferred_hold_seconds.min/max` is the supplied preferred still-image hold range for that density band. Use these values only to judge which overall mode best fits the candidate; do not treat them as Beat quotas, narration-WPM targets, or ranges you may modify.
- `user_input.seed_topic`: The subject from which every candidate must develop. Interpret its intended meaning, but do not copy awkward wording into titles or prose.
- `user_input.topic_count`: The exact number of candidates to return.
- `user_input.focus_keywords`: Relevant concepts to use naturally when they strengthen relevance or clarity. Do not force every keyword into every candidate.
- `user_input.focus`: The primary strategy for selecting and ordering the candidates.
- `user_input.avoid_topics`: Hard exclusions. Do not return these subjects or superficial reframings that preserve the same excluded premise.

# Focus policy

Apply the exact `user_input.focus` value:

- `highest_ctr`: prioritize immediate curiosity, title clarity, viewer stakes, broad accessibility, long-form retention potential, and meaningful differentiation from familiar competing treatments.
- `most_evergreen`: prioritize durable human curiosity, lasting audience value, defensible evidence, and a differentiated current-market promise when current-landscape research is available. When two candidates are similarly evergreen, strongly prefer the less saturated promise.
- `easiest_to_make`: prioritize reliable sources, manageable research, accessible visual material, and a treatment that is not merely a generic version of an already common video.
- `most_unique`: prioritize the most distinctive **channel-aligned audience-facing treatment**: a meaningfully different promise, editorial engine, question, mechanism, or payoff that remains supportable and deep enough for long-form. Let the supplied channel identity and active skill determine what kind of distinctiveness matters. Do not assume uniqueness must come from evidence novelty, humor, human inconvenience, documentary depth, cinematic storytelling, or any other generic mode.
- `balanced`: balance curiosity, originality, credibility, evergreen value, audience fit, production feasibility, and market differentiation.

Do not optimize one dimension by seriously weakening factual accuracy, channel fit, or the ability to produce a satisfying video.

# Candidate discovery and portfolio selection

Silently consider a wider pool of possible ideas before selecting the final candidates; that pool should be substantially wider than the requested final slate. Do not treat broad life domains or familiar topic categories as slots that must each receive one obvious umbrella question.

Before final selection, ensure that the wider pool explores materially different audience-facing treatments. Across the pool, vary central questions, viewer promises, editorial lenses or engines, progression mechanisms, payoffs, and—where useful—evidence or explanation paths. The supplied channel identity and active channel authoring skill determine which dimensions deserve the most weight. Evidence-path diversity alone does not create a meaningfully diverse slate.

When current-landscape research is available, use it before final selection to identify semantically similar existing treatments and common viewer promises. Compare the underlying movie, not just exact title wording.

For any candidate whose broad subject is already heavily covered, ask whether the package gives the viewer a genuinely different reason to click. If not, re-angle the idea toward a more specific and defensible treatment or reject it; the active channel authoring skill owns the creative re-angling method.

The final set must differ materially at the audience-facing treatment level. Across the slate, create meaningful differences in several of:

- central question;
- viewer promise;
- editorial lens or engine;
- story, mechanism, or explanation progression;
- evidence or explanation path when relevant to the channel treatment;
- emotional or intellectual payoff.

Do not require every pair of candidates to differ in every evidence source. Different evidence or explanation paths alone do not satisfy portfolio diversity.

Do not return several variations of the same subtopic merely because their titles are different. When two ideas are similarly strong, prefer the one that expands the portfolio's coverage and is less interchangeable with common current treatments.

Every candidate must contain enough depth for one coherent long-form YouTube video. Do not return ideas that can be completely answered by one fact, a short list, or a brief explanation. Do not assume a specific runtime because no target duration is supplied.

# Required field semantics

- `title`: The strongest concise working title, with a clear subject and curiosity gap. It must be attractive without overstating evidence. If the underlying subject is familiar or saturated, make the differentiating promise visible in the title whenever doing so remains natural and accurate.
- `core_question`: The single central question the video will answer. It should preserve the specific differentiated problem rather than collapsing back into a generic umbrella question.
- `viewer_hook`: A direct viewer-facing sentence expressing curiosity, tension, surprise, contrast, stakes, or payoff. Do not begin with meta-commentary such as “This topic,” “This idea,” “This explores,” or “This asks.” The hook should make the candidate's distinctive viewer promise legible before the click.
- `topic_angle`: The distinct editorial lens and practical scope that make this treatment worth watching, plus the evidence or explanation path needed to support it honestly. Evidence may be central when the supplied channel identity favors an evidence-led treatment; otherwise it should support the channel-appropriate premise rather than becoming the automatic source of novelty. Do not hide all meaningful differentiation only in this field while leaving the title, core question, and viewer hook generic.
- `tone`: A concise video-specific tonal modulation that stays inside the supplied `narration_identity`, rather than defining a replacement channel voice.
- `pov_style`: The editable future-Script POV/story-engine/evidence-delivery recommendation defined by the system contract; it must not override the supplied `narration_identity`.
- `avoid`: The editable future-Script avoid list defined by the system contract.
- `ctr_score`: Use the system-defined editorial score semantics, including saturation and semantic differentiation when current-landscape research is available. High familiarity alone is not evidence of high CTR.
- `difficulty`: Use the system-defined production/research burden semantics.
- `content_tags`: Concise subject and editorial-category tags.
- `risk_notes`: Concrete cautions that later research or scripting must address. Use an empty array only when no material caution exists.
- `recommended_pacing_mode`: Exactly `fast`, `medium`, or `slow`. Compare the candidate's genuine story-state progression and independently drawable explanatory/editorial facet potential against the exact supplied `pacing_profiles` and recommend one of those three supplied modes.
- `pacing_mode_reason`: Explain the recommendation briefly and concretely from the candidate structure and supplied pacing calibration. Do not use niche stereotypes, invent ranges, or assume a global FAST/MEDIUM/SLOW timing table.

# Ordering

Order candidates by:

1. `ctr_score` descending.
2. Stronger title and viewer hook.
3. Better long-form retention potential.
4. Better channel and audience fit.
5. More credible and satisfying evidence path.
6. Clearer single-video scope.
7. Stronger differentiation from the rest of the portfolio; when research is available, also prefer stronger differentiation from common current treatments.

# Final contract review

Before returning, verify:

- the exact requested candidate count;
- no duplicate or near-duplicate ideas;
- every idea can support a complete long-form video;
- natural language in every prose field;
- consistent factual confidence across title, question, hook, angle, and risks;
- realistic research and production feasibility;
- a pacing recommendation whose reason is consistent with the idea's actual structure;
- candidate `tone` and `pov_style` preserve rather than neutralize or redefine the supplied `narration_identity`;
- no excluded topic or misleading claim has slipped through;
- the slate has both internal diversity and meaningful treatment differentiation;
- any familiar or saturated subject has a package-visible reason to exist beyond “we can make a better version”;
- no candidate receives a top CTR score merely because its subject is broadly familiar.

If current-landscape research is unavailable, do not claim or imply that it was performed. Still apply the semantic differentiation checks against the supplied context and the candidate pool.

# Input JSON

```json
{{input_json}}
```
