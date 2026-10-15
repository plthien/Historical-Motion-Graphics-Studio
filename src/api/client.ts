export interface ErrorResponse { error_code: string; message: string; user_message?: string; retryable: boolean; details?: Record<string, unknown>; }
export interface HealthResponse { status: string; version: string; }
export interface ExternalPromptPackage {
  prompt_text: string;
  artifact_ids: Record<string, string>;
  output_schema_name: string;
  prompt_version: string;
}
export interface ExternalPromptDraftReadResponse {
  generated: boolean;
  rebuild_required: boolean;
  prompt_package: ExternalPromptPackage | null;
  request_input: Record<string, unknown> | null;
}
export interface ExternalImportPayload { result_json: string; }
export interface ExternalValidationIssue { code: string; path: string; message: string; }
export interface ExternalImportValidationResponse { valid: boolean; errors: ExternalValidationIssue[]; warnings: ExternalValidationIssue[]; summary: Record<string, unknown>; }

export interface MediaProviderSettings {
  provider: 'glabs_webhook';
  enabled: boolean;
  base_url: string;
  api_key_configured: boolean;
  api_key?: string | null;
  default_image_model: 'nano_banana_2' | 'nano_banana_pro';
  default_image_aspect_ratio: '1:1' | '3:4' | '4:3' | '9:16' | '16:9';
  default_upscale: Array<'2K' | '4K'>;
  poll_interval_seconds: number;
  max_parallel_tasks: number;
  enabled_media_kinds: Array<'image'>;
}
export interface MediaProviderSettingsResponse {
  settings: MediaProviderSettings;
  readiness: { status: string; issues: Array<Record<string, unknown>>; provider?: string | null; enabled_media_kinds: string[] };
}
export interface UpdateMediaProviderSettingsPayload { settings: MediaProviderSettings; }
export interface TestMediaProviderConnectionResponse {
  status: 'ready';
  message: string;
  details: { provider: 'glabs_webhook'; health_reachable: boolean; authentication_accepted: boolean };
}
export interface Job { job_id: string; job_type: string; status: string; progress: number; artifact_type: string | null; target_type: string | null; target_id: string | null; created_at: string; updated_at: string; error_code: string | null; error_message: string | null; }
export interface JobListResponse { items: Job[]; page: number; page_size: number; total: number; }
export interface Artifact { artifact_id: string; logical_id: string; version_no: number; is_current: boolean; artifact_type: string; artifact_role: string | null; status: string; created_at: string; path: string | null; }
export interface ArtifactListResponse { items: Artifact[]; page: number; page_size: number; total: number; }

export interface ChannelContext { name: string; language: string; niche: string; audience: string; }
export interface VisualIdentity { medium: string[]; linework: string[]; rendering: string[]; palette: string[]; shape_language: string[]; texture: string[]; editorial_language: string[]; }
export interface EntityStyle { appearance: string[]; avoid: string[]; }
export interface EntityStyles { character: EntityStyle; location: EntityStyle; prop: EntityStyle; recurring_visual_subject: EntityStyle; }
export interface VisualStyleSnapshot { visual_identity: VisualIdentity; entity_styles: EntityStyles; global_negative_constraints: string[]; }
export interface NarrationIdentity { voice: string[]; delivery: string[]; explanation_style: string[]; tone_boundaries: string[]; avoid: string[]; }
export type VideoPacingMode = 'fast' | 'medium' | 'slow';
export type PacingDensity = 'high' | 'medium' | 'low';
export interface PacingHoldRange { min: number; max: number; }
export interface NarrationWpmRange { min: number; max: number; }
export type ChannelPacingModeProfile = Record<PacingDensity, { preferred_hold_seconds: PacingHoldRange }>;
export type ChannelPacingProfiles = Record<VideoPacingMode, ChannelPacingModeProfile>;
export interface ChannelDnaV3 extends VisualStyleSnapshot { schema_version: '3.0'; channel: ChannelContext; narration_wpm: NarrationWpmRange; pacing_profiles: ChannelPacingProfiles; narration_identity: NarrationIdentity; }
export interface Channel { channel_id: string; name: string; language: string; niche: string; audience: string; has_channel_dna: boolean; channel_context: ChannelContext | null; created_at: string; updated_at: string; }
export interface ChannelListResponse { items: Channel[]; page: number; page_size: number; total: number; }
export interface CreateChannelPayload { name: string; language: string; niche: string; audience: string; }
export interface ChannelDnaValidationResponse { valid: boolean; channel_dna: ChannelDnaV3; channel_context: ChannelContext; }
export interface ChannelDnaReadResponse { channel_id: string; has_channel_dna: boolean; channel_dna: ChannelDnaV3 | null; updated_at: string | null; }
export interface ChannelVoiceCalibration { stability: number; similarity_boost: number; use_speaker_boost: boolean; }
export interface ChannelVoiceProfile { schema_version: 1; provider: 'elevenlabs'; voice_id: string; voice_kind: 'professional'; default_preset: 'economy' | 'quality_cloned_voice'; calibration: Record<'economy' | 'quality_cloned_voice', ChannelVoiceCalibration>; }
export interface ChannelVoiceProfileReadResponse { channel_id: string; voice_profile: ChannelVoiceProfile | null; updated_at: string | null; }
export interface Project { project_id: string; channel_id: string; name: string; description: string | null; channel_context: ChannelContext; channel_dna_narration_wpm: NarrationWpmRange; selected_topic_ideas_artifact_id: string | null; selected_topic_idea_id: string | null; topic_ideas_artifact_id: string | null; script_artifact_id: string | null; script_analysis_artifact_id: string | null; style_bible_artifact_id: string | null; scene_plan_artifact_id: string | null; visual_plan_artifact_id: string | null; prompt_bundle_artifact_id: string | null; style_bible_visual_reference_set_artifact_id: string | null; style_bible_visual_reference_approval_set_artifact_id: string | null; scene_asset_manifest_artifact_id: string | null; narration_audio_artifact_id: string | null; narration_word_timing_map_artifact_id: string | null; beat_timing_map_artifact_id: string | null; assembly_plan_artifact_id: string | null; subtitle_srt_artifact_id: string | null; subtitle_vtt_artifact_id: string | null; final_video_artifact_id: string | null; created_at: string; updated_at: string; }
export interface ProjectListResponse { items: Project[]; page: number; page_size: number; total: number; }
export interface CreateProjectPayload { channel_id: string; name: string; description?: string | null; }
export type TopicIdeaFocus = 'highest_ctr' | 'most_evergreen' | 'easiest_to_make' | 'balanced' | 'most_unique';
export type TopicDifficulty = 'easy' | 'medium' | 'hard';
export interface VideoPacingProfile { mode: VideoPacingMode; profile_name: VideoPacingMode; policy: string; density_profiles: Record<'high' | 'medium' | 'low', { preferred_hold_seconds: { min: number; max: number } }>; }
export interface TopicIdea { idea_id: string; title: string; core_question: string; viewer_hook: string; topic_angle: string; tone: string; ctr_score: number; difficulty: TopicDifficulty; content_tags: string[]; risk_notes: string[]; pov_style: string; avoid: string[]; recommended_pacing_mode: VideoPacingMode; pacing_mode_reason: string; }
export interface ScriptTopicIdea { idea_id: string; title: string; core_question: string; viewer_hook: string; topic_angle: string; tone: string; content_tags: string[]; risk_notes: string[]; pov_style: string; avoid: string[]; recommended_pacing_mode: VideoPacingMode; pacing_mode_reason: string; }
export interface TopicIdeasArtifact { artifact_type: 'topic_ideas'; schema_version: '1.0'; channel_context: { name: string; language: string }; source_user_input: { seed_topic: string; topic_count: number; focus_keywords: string[]; focus: TopicIdeaFocus; avoid_topics: string[] }; ideas: TopicIdea[]; }
export interface TopicIdeasResponse { artifact: TopicIdeasArtifact; artifact_id: string; }
export interface TopicIdeasReadResponse { generated: boolean; artifact: TopicIdeasArtifact | null; artifact_id: string | null; }
export interface GenerateTopicIdeasPayload { user_input: Omit<TopicIdeasArtifact['source_user_input'], 'avoid_topics'>; }
export interface ScriptRuntimeDiagnostics { target_duration_minutes: number; planned_average_wpm: number; actual_word_count: number; estimated_duration_minutes: number; runtime_delta_minutes: number; runtime_delta_seconds: number; runtime_delta_percent: number; section_runtime_diagnostics: Array<{ section_id: string; planned_narration_wpm: number; actual_word_count: number; estimated_duration_minutes: number }>; }
export interface ScriptArtifact { artifact_type: 'script'; schema_version: '1.0'; channel_context: { name: string; language: string }; source_topic_ideas_artifact_id: string; source_topic_idea: ScriptTopicIdea; source_user_input: { target_duration_minutes: number; pov_style: string; avoid: string[]; section_count_mode: 'llm_decides' | 'backend_plan'; pacing_mode: VideoPacingMode; narration_wpm: NarrationWpmRange }; video_pacing_profile: VideoPacingProfile; planned_average_wpm: number; computed_constraints: { derived_planned_average_wpm: number; planned_narration_wpm_range: NarrationWpmRange; section_count_mode: 'llm_decides' | 'backend_plan'; recommended_section_count: number; minimum_section_count: number; maximum_section_count: number }; title: string; hook: string; script_sections: Array<{ section_id: string; section_title: string; section_role: string; planned_narration_wpm: number; narration: string }>; full_script: string; section_word_counts: Array<{ section_id: string; actual_word_count: number }>; word_count: number; runtime_diagnostics: ScriptRuntimeDiagnostics; }
export interface ScriptResponse { artifact: ScriptArtifact; artifact_id: string; }
export interface ScriptReadResponse { generated: boolean; artifact: ScriptArtifact | null; artifact_id: string | null; }
export interface ScriptAnalysisCombinedRevisionResponse { script_artifact_id: string; script: ScriptArtifact; script_analysis_artifact_id: string; script_analysis: ScriptAnalysisArtifact; }
export interface SelectTopicPayload { topic_ideas_artifact_id: string; idea_id: string; }
export interface GenerateScriptPayload { selected_topic_idea_id: string; user_input: ScriptArtifact['source_user_input']; }
export interface ScriptAnalysisArtifact {
  artifact_type: 'script_analysis';
  schema_version: '1.0';
  project_id: string;
  source_script_revision_id: string;
  section_guidance: Array<{ section_id: string; pacing_density: 'low' | 'medium' | 'high' }>;
  narrative_markers: Array<{ marker_id: string; marker_type: 'turning_point' | 'reveal' | 'contrast' | 'payoff' | 'emphasis'; section_id: string; anchor_quote: string; context_quote: string; resolved_script_span: [number, number]; importance: 'low' | 'medium' | 'high'; purpose: string }>;
  continuity_catalog: ScriptAnalysisContinuityCatalog;
  visual_context_catalog: ScriptAnalysisVisualContext[];
  backend_qc: { section_coverage_status: 'passed' | 'warning' | 'failed'; quote_resolution_status: 'passed' | 'warning' | 'failed'; entity_deduplication_status: 'passed' | 'warning' | 'failed'; marker_validation_status: 'passed' | 'warning' | 'failed'; visual_context_validation_status: 'passed' | 'warning' | 'failed'; warnings: string[] };
}
export interface ScriptAnalysisOccurrence {
  occurrence_id: string;
  section_id: string;
  quote: string;
  context_quote: string;
  resolved_script_span: [number, number];
}
export interface ScriptAnalysisPresenceRange {
  section_id: string;
  start_quote: string;
  end_quote: string;
  resolved_script_span: [number, number];
}
export interface ScriptAnalysisContinuityBase {
  label: string;
  description: string;
  explicit_visual_traits: string[];
  continuity_notes: string[];
  physical_constraints: string[];
  occurrences: ScriptAnalysisOccurrence[];
  presence_ranges: ScriptAnalysisPresenceRange[];
}
export interface ScriptAnalysisVisualContext {
  context_id: string;
  label: string;
  mode: 'story_world' | 'representative_scenario' | 'evidence_view' | 'reconstruction' | 'neutral_abstract' | 'mixed_context';
  timeframe: string | null;
  setting: string | null;
  constraints: string[];
  presence_ranges: ScriptAnalysisPresenceRange[];
}
export interface ScriptAnalysisIdentityContinuityBase extends ScriptAnalysisContinuityBase {
  identity_markers: string[];
}
export interface ScriptAnalysisCharacter extends ScriptAnalysisIdentityContinuityBase {
  char_id: string;
  entity_kind: string;
  narrative_role: string;
}
export interface ScriptAnalysisLocation extends ScriptAnalysisIdentityContinuityBase {
  loc_id: string;
  location_kind: string;
}
export interface ScriptAnalysisRecurringProp extends ScriptAnalysisIdentityContinuityBase {
  prop_id: string;
  prop_kind: string;
}
export interface ScriptAnalysisRecurringVisualSubject extends ScriptAnalysisContinuityBase {
  subject_id: string;
  visual_type: string;
}
export interface ScriptAnalysisVisualMotif {
  motif_id: string;
  label: string;
  description: string;
  occurrences: ScriptAnalysisOccurrence[];
}
export interface ScriptAnalysisContinuityCatalog {
  characters: ScriptAnalysisCharacter[];
  locations: ScriptAnalysisLocation[];
  recurring_props: ScriptAnalysisRecurringProp[];
  recurring_visual_subjects: ScriptAnalysisRecurringVisualSubject[];
  visual_motifs: ScriptAnalysisVisualMotif[];
}
export interface ScriptAnalysisReadResponse { project_id: string; generated: boolean; artifact_id: string | null; artifact: ScriptAnalysisArtifact | null; }
export interface ScriptAnalysisContinuityReviewResponse { project_id: string; script_analysis_artifact_id: string; review_artifact_id: string | null; removed_continuity_refs: string[]; available_continuity_refs: string[]; }
export interface ScriptAnalysisContinuityReviewMutationPayload { expected_script_analysis_artifact_id: string; expected_review_artifact_id: string | null; continuity_ref: string; removed: boolean; }


export type StyleBibleStatus = 'draft' | 'text_approved';
export type StyleBibleRevisionOperation = 'draft_generation' | 'text_update' | 'text_approval';
export interface StyleBibleOccurrenceQuote { quote: string; context: string; resolved_script_position: number; section_id: string; }
export interface StyleBiblePresenceRange { section_id: string; start_quote: string; end_quote: string; resolved_script_span: [number, number]; }
export interface StyleBibleMotifOccurrence { quote: string; resolved_script_position: number; }
export interface StyleBibleCharacter { char_id: string; label: string; description: string; explicit_visual_traits: string[]; identity_markers: string[]; continuity_notes: string[]; physical_constraints: string[]; first_mention_quote: string; resolved_first_mention_position: number; occurrence_quotes: StyleBibleOccurrenceQuote[]; presence_ranges: StyleBiblePresenceRange[]; entity_kind: string; narrative_role: string; approval_status: StyleBibleStatus; }
export interface StyleBibleLocation { loc_id: string; label: string; description: string; explicit_visual_traits: string[]; identity_markers: string[]; continuity_notes: string[]; physical_constraints: string[]; first_mention_quote: string; resolved_first_mention_position: number; occurrence_quotes: StyleBibleOccurrenceQuote[]; presence_ranges: StyleBiblePresenceRange[]; location_kind: string; approval_status: StyleBibleStatus; }
export interface StyleBibleRecurringProp { prop_id: string; label: string; description: string; explicit_visual_traits: string[]; identity_markers: string[]; continuity_notes: string[]; physical_constraints: string[]; first_mention_quote: string; resolved_first_mention_position: number; occurrence_quotes: StyleBibleOccurrenceQuote[]; presence_ranges: StyleBiblePresenceRange[]; prop_kind: string; significance: string; approval_status: StyleBibleStatus; }
export interface StyleBibleRecurringVisualSubject { subject_id: string; label: string; description: string; visual_type: string; explicit_visual_traits: string[]; continuity_notes: string[]; physical_constraints: string[]; first_mention_quote: string; resolved_first_mention_position: number; occurrence_quotes: StyleBibleOccurrenceQuote[]; presence_ranges: StyleBiblePresenceRange[]; approval_status: StyleBibleStatus; }
export interface StyleBibleMotifV4 { motif_id: string; label: string; description: string; occurrences: StyleBibleMotifOccurrence[]; approval_status: StyleBibleStatus; }
export interface StyleBibleV4Lineage { script_analysis_artifact_id: string; source_script_artifact_id: string; channel_dna_snapshot_sha256: string; continuity_review_artifact_id: string | null; removed_continuity_refs: string[]; }
export interface ProjectStyleBibleArtifactV4 { artifact_type: 'project_style_bible'; schema_version: '2.0'; project_id: string; style_bible_revision_id: string; parent_style_bible_revision_id: string | null; revision_operation: StyleBibleRevisionOperation; status: StyleBibleStatus; lineage: StyleBibleV4Lineage; visual_style_snapshot: VisualStyleSnapshot; characters: StyleBibleCharacter[]; locations: StyleBibleLocation[]; recurring_props: StyleBibleRecurringProp[]; recurring_visual_subjects: StyleBibleRecurringVisualSubject[]; motifs: StyleBibleMotifV4[]; }
export type StyleBibleEditableCharacter = Pick<StyleBibleCharacter, 'char_id' | 'label' | 'description' | 'explicit_visual_traits' | 'identity_markers' | 'continuity_notes' | 'physical_constraints' | 'entity_kind' | 'narrative_role'>;
export type StyleBibleEditableLocation = Pick<StyleBibleLocation, 'loc_id' | 'label' | 'description' | 'explicit_visual_traits' | 'identity_markers' | 'continuity_notes' | 'physical_constraints' | 'location_kind'>;
export type StyleBibleEditableRecurringProp = Pick<StyleBibleRecurringProp, 'prop_id' | 'label' | 'description' | 'explicit_visual_traits' | 'identity_markers' | 'continuity_notes' | 'physical_constraints' | 'prop_kind'>;
export type StyleBibleEditableRecurringVisualSubject = Pick<StyleBibleRecurringVisualSubject, 'subject_id' | 'label' | 'description' | 'visual_type' | 'explicit_visual_traits' | 'continuity_notes' | 'physical_constraints'>;
export type StyleBibleEditableMotif = Pick<StyleBibleMotifV4, 'motif_id' | 'label' | 'description'>;
export interface StyleBibleEditableContentV4 { characters: StyleBibleEditableCharacter[]; locations: StyleBibleEditableLocation[]; recurring_props: StyleBibleEditableRecurringProp[]; recurring_visual_subjects: StyleBibleEditableRecurringVisualSubject[]; motifs: StyleBibleEditableMotif[]; }
export interface GenerateStyleBibleDraftPayloadV4 { script_analysis_artifact_id: string; }
export interface StyleBibleTextUpdatePayloadV4 { expected_style_bible_revision_id: string; editable_content: StyleBibleEditableContentV4; }
export interface StyleBibleArtifactResponseV4 { artifact_id: string; artifact: ProjectStyleBibleArtifactV4; }
export interface StyleBibleReadResponseV4 { project_id: string; generated: boolean; artifact_id: string | null; artifact: ProjectStyleBibleArtifactV4 | null; }



export interface SceneBreakdownApprovePayloadV4 { expected_scene_plan_artifact_id: string; expected_visual_plan_artifact_id: string; aspect_ratio: PromptBundleAspectRatio; }
export interface SceneBreakdownEditableBeatV4 { script_text: string; visual: Record<string, unknown>; }
export interface SceneBreakdownEditableSceneV4 { beats: SceneBreakdownEditableBeatV4[]; }
export interface SceneBreakdownEditableSectionV4 { section_id: string; scenes: SceneBreakdownEditableSceneV4[]; }
export interface SceneBreakdownSectionEditValidatePayloadV4 { expected_scene_plan_artifact_id: string; expected_visual_plan_artifact_id: string; section: SceneBreakdownEditableSectionV4; }
export interface SceneBreakdownSectionEditSavePayloadV4 extends SceneBreakdownSectionEditValidatePayloadV4 { validation_artifact_id: string; }
export interface SceneBreakdownSectionEditValidationResponseV4 { valid: boolean; validation_artifact_id: string | null; errors: Array<{ path: string; code: string; message: string }>; warnings: string[]; }
export interface SceneBreakdownTimingMetrics { planned_narration_wpm: number; beat_count: number; scene_count: number; estimated_section_duration_seconds: number; average_beat_duration_seconds: number; average_narration_duration_seconds: number; median_beat_duration_seconds: number; p90_beat_duration_seconds: number; max_beat_duration_seconds: number; average_scene_duration_seconds: number; max_scene_duration_seconds: number; single_beat_scene_count: number; preferred_hold_seconds: { min: number; max: number }; within_preferred_ratio: number; below_preferred_ratio: number; above_preferred_ratio: number; pacing_conformance: 'passed' | 'warning'; }
export interface SceneBreakdownBackendQCV4 { coverage_status: 'passed' | 'warning' | 'failed'; semantic_partition_status: 'passed' | 'warning' | 'failed'; order_status: 'passed' | 'warning' | 'failed'; source_integrity_status: 'passed' | 'warning' | 'failed'; timing_status: 'passed' | 'warning' | 'failed'; forbidden_field_status: 'passed' | 'warning' | 'failed'; reasoning_leakage_status: 'passed' | 'warning' | 'failed'; semantic_errors: Array<{ path: string; code: string; message: string }>; errors?: Array<{ path: string; code: string; message: string }>; coverage_gaps: Array<{ section_id: string; span: [number, number]; text: string }>; coverage_overlaps: Array<{ section_id: string; span: [number, number]; text: string }>; timing_warnings: string[]; warnings: string[]; timing_policy_version?: string | null; timing_metrics?: SceneBreakdownTimingMetrics | null; timing_sections?: Array<{ section_id: string; pacing_density: 'low' | 'medium' | 'high'; timing_status: 'passed' | 'warning' | 'failed'; timing_metrics: SceneBreakdownTimingMetrics }>; }
export interface ScenePlanBeatV4 { beat_id: string; beat_order: number; local_beat_order: number; script_quote: string; resolved_script_span: [number, number]; visual_context_ref: string; word_count: number; estimated_duration_seconds: number; narrative_marker_refs: string[]; is_narrative_marker: boolean; narrative_marker_types: Array<'turning_point' | 'reveal' | 'contrast' | 'payoff' | 'emphasis'>; turning_point: boolean; reveal: boolean; contrast: boolean; visual_priority: 'critical' | 'high' | 'normal'; eligible_character_refs: string[]; eligible_location_refs: string[]; eligible_prop_refs: string[]; eligible_recurring_visual_subject_refs: string[]; eligible_motif_refs: string[]; pacing_density: 'low' | 'medium' | 'high'; }
export interface ScenePlanSceneV4 { scene_id: string; scene_order: number; section_id: string; local_scene_order: number; resolved_script_span: [number, number]; start_quote: string; end_quote: string; beats: ScenePlanBeatV4[]; }
export interface ScenePlanSectionV4 { section_id: string; section_order: number; section_title: string; section_role: string; resolved_script_span: [number, number]; pacing_density: 'low' | 'medium' | 'high'; scene_ids: string[]; beat_ids: string[]; }
export interface ScenePlanArtifactV4 { artifact_type: 'scene_plan'; schema_version: '2.0'; project_id: string; scene_plan_revision_id: string; parent_scene_plan_revision_id: string | null; revision_operation: 'full_generation' | 'section_regeneration' | 'section_edit' | 'approval'; source_script_revision_id: string; source_script_analysis_revision_id: string; source_style_bible_revision_id: string; source_run_artifact_id: string; status: 'draft_ready' | 'approved'; scene_count: number; beat_count: number; total_expected_duration_seconds: number; sections: ScenePlanSectionV4[]; scenes: ScenePlanSceneV4[]; backend_qc: SceneBreakdownBackendQCV4; }
export interface SceneBreakdownReadResponseV4 { project_id: string; generated: boolean; scene_plan_artifact_id: string | null; scene_plan: ScenePlanArtifactV4 | null; visual_plan_artifact_id: string | null; visual_plan: VisualPlanArtifactV4 | null; }
export interface SceneBreakdownRevisionResponseV4 { scene_plan_artifact_id: string; scene_plan: ScenePlanArtifactV4; visual_plan_artifact_id: string; visual_plan: VisualPlanArtifactV4; }
export type MediaTaskStatus =
  | 'queued'
  | 'submitted'
  | 'provider_pending'
  | 'provider_running'
  | 'downloading'
  | 'completed'
  | 'failed'
  | 'canceled'
  | 'download_failed';

export interface MediaImageGeneratePayload {
  purpose: 'style_bible_reference_image';
  project_id: string;
  style_bible_revision_id?: string | null;
  entity_type: 'character' | 'location' | 'prop' | 'recurring_visual_subject';
  entity_id: string;
  candidate_count: number;
  prompt: string;
  negative_prompt: string;
  model: 'nano_banana_2' | 'nano_banana_pro';
  aspect_ratio: '1:1' | '3:4' | '4:3' | '9:16' | '16:9';
  upscale: Array<'2K' | '4K'>;
}
export interface MediaImageGenerateResponse {
  batch_id: string;
  tasks: Array<{ media_task_id: string; status: 'queued' | 'submitted' | 'failed'; entity_type: VisualReferenceSubjectType; entity_id: string; candidate_index: number }>;
  summary_url: string;
}
export interface MediaActiveBatch {
  batch_id: string;
  purpose: string;
  total: number;
  active_task_count: number;
  created_at: string;
  updated_at: string;
}


export type VisualReferenceAnchorTypeV4 = 'character' | 'location' | 'prop' | 'recurring_visual_subject';
export type VisualReferenceSubjectType = VisualReferenceAnchorTypeV4;
export type VisualReferenceAspectRatiosByAnchorTypeV4 = Partial<Record<VisualReferenceAnchorTypeV4, PromptBundleAspectRatio>>;
export type MediaTaskEntityType = VisualReferenceAnchorTypeV4 | 'beat';
export interface VisualReferenceCandidateV4 { reference_artifact_id: string; media_artifact_id: string; media_task_id: string; candidate_index: number; mime_type: string; media_sha256: string; file_url: string; }
export interface VisualReferenceSubjectV4 { anchor_type: VisualReferenceAnchorTypeV4; anchor_id: string; label: string; source_definition_hash: string; prompt: string; negative_prompt: string; candidates: VisualReferenceCandidateV4[]; selected_draft_reference_artifact_id: string | null; }
export interface VisualReferenceSetV4 { artifact_type: 'style_bible_visual_reference_set'; schema_version: '2.0'; project_id: string; visual_reference_set_artifact_id: string; parent_visual_reference_set_artifact_id: string | null; source_script_revision_id: string; source_script_analysis_revision_id: string; source_style_bible_revision_id: string; status: 'draft_ready'; subjects: VisualReferenceSubjectV4[]; created_at: string; }
export interface VisualReferenceSelectionV4 { anchor_type: VisualReferenceAnchorTypeV4; anchor_id: string; reference_artifact_id: string; media_artifact_id: string; source_definition_hash: string; media_sha256: string; mime_type: string; image_artifact_id: string; }
export interface VisualReferenceApprovalSetV4 { artifact_type: 'style_bible_visual_reference_approval_set'; schema_version: '2.0'; project_id: string; approval_set_artifact_id: string; parent_approval_set_artifact_id: string | null; source_script_revision_id: string; source_script_analysis_revision_id: string; source_style_bible_revision_id: string; source_visual_reference_set_artifact_id: string; selections: VisualReferenceSelectionV4[]; created_at: string; }
export interface MediaGenerationProgressV4 {
  batch_id: string; total: number; queued: number; submitted: number; provider_pending: number; provider_running: number;
  downloading: number; completed: number; failed: number; canceled: number; download_failed: number;
  effective_poll_interval_seconds: number; updated_at: string;
}
export interface VisualReferenceWorkspaceV4 { project_id: string; generated: boolean; visual_reference_set_artifact_id: string | null; visual_reference_set: VisualReferenceSetV4 | null; approval_set_artifact_id: string | null; approval_set: VisualReferenceApprovalSetV4 | null; operation: { operation_id: string; job_id: string; status: 'queued' | 'generating' | 'completed' | 'failed'; progress: number; media_progress: MediaGenerationProgressV4; error_code: string | null; error_message: string | null } | null; readiness: { required_count: number; approved_count: number; ready: boolean; missing_anchor_keys: string[] }; }
export interface VisualReferenceGenerateRequestV4 { style_bible_artifact_id: string; expected_visual_reference_set_artifact_id: string | null; candidate_count: number; aspect_ratio?: PromptBundleAspectRatio; aspect_ratios_by_anchor_type?: VisualReferenceAspectRatiosByAnchorTypeV4; }
export interface VisualReferenceJobSubmissionV4 { job_id: string; job_type: 'visual_reference_v4_generate'; project_id: string; operation_id: string; status: 'queued'; progress: 0; }
export interface VisualReferenceJobStatusV4 { job_id: string; job_type: 'visual_reference_v4_generate'; project_id: string; operation_id: string; status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | 'stale_failed'; progress: number; retryable: boolean; error_code: string | null; error_message: string | null; visual_reference_set_artifact_id: string | null; media_batch_id: string; media_progress: MediaGenerationProgressV4; created_at: string; updated_at: string; started_at: string | null; finished_at: string | null; }
export interface VisualReferenceApproveRequestV4 { visual_reference_set_artifact_id: string; expected_approval_set_artifact_id: string | null; reference_artifact_id: string; }
export interface VisualReferenceClearRequestV4 { visual_reference_set_artifact_id: string; expected_approval_set_artifact_id: string | null; }
export interface VisualReferencePromptUpdateRequestV4 { expected_visual_reference_set_artifact_id: string; prompt: string; negative_prompt: string; }
export interface VisualReferenceApprovalRevisionV4 { artifact_id: string; artifact: VisualReferenceApprovalSetV4; }

export interface MediaActiveBatchesResponse {
  project_id: string;
  active_batches: MediaActiveBatch[];
  latest_batch_id: string | null;
}
export interface MediaTaskStatusResponse {
  media_task_id: string;
  batch_id: string;
  external_provider: string;
  external_task_id: string | null;
  status: MediaTaskStatus;
  provider_status: 'pending' | 'running' | 'completed' | 'failed' | null;
  provider_submitted_at: string | null;
  entity_type: MediaTaskEntityType;
  entity_id: string;
  candidate_index: number;
  created_at: string;
  updated_at: string;
  poll_count: number;
  next_poll_after_seconds: number;
}
export interface MediaTaskResultResponse {
  media_task_id: string;
  status: MediaTaskStatus;
  message?: string | null;
  results?: Array<{ artifact_id: string; image_id: string; storage_uri: string; file_url: string; mime_type: string; source_result_url: string; downloaded_at: string }> | null;
  error_code?: string | null;
  provider_error_code?: number | null;
  error?: string | null;
  error_detail?: string | null;
}
export interface MediaTaskCard {
  media_task_id: string;
  batch_id: string;
  entity_type: MediaTaskEntityType;
  entity_id: string;
  candidate_index: number;
  status: MediaTaskStatus;
  provider_status: 'pending' | 'running' | 'completed' | 'failed' | null;
  provider_submitted_at: string | null;
  thumbnail_url: string | null;
  result_url: string;
  prompt_preview: string;
  model: string;
  aspect_ratio: string;
  selected: boolean;
  locked: boolean;
  created_at: string;
  completed_at: string | null;
}
export interface MediaTaskCardListResponse { tasks: MediaTaskCard[]; }
export interface MediaTaskSummaryResponse {
  batch_id: string;
  purpose: 'style_bible_visual_reference' | 'scene_asset_generation';
  total: number;
  queued: number;
  submitted: number;
  provider_pending: number;
  provider_running: number;
  downloading: number;
  completed: number;
  failed: number;
  canceled: number;
  download_failed: number;
  selected: number;
  locked: number;
  updated_at: string;
}

export class ApiError extends Error {
  readonly errorCode: string;
  readonly retryable: boolean;
  readonly details?: Record<string, unknown>;

  constructor(response: ErrorResponse) {
    super(response.user_message ?? response.message);
    this.name = 'ApiError';
    this.errorCode = response.error_code;
    this.retryable = response.retryable;
    this.details = response.details;
  }
}



export interface ValidationIssue { path: string; code: string; message: string; }
export type QcStatus = 'passed' | 'warning' | 'failed';
export interface VisualPlanRepresentationFamilyQC { total_beat_count: number; counts: Record<'character_environment_lived' | 'object_evidence_closeup' | 'text_diagram_editorial', number>; percentages: Record<'character_environment_lived' | 'object_evidence_closeup' | 'text_diagram_editorial', number>; complete_visual_plan: boolean; }
export interface VisualPlanBackendQC { scene_coverage_status: QcStatus; beat_coverage_status: QcStatus; order_status: QcStatus; source_identity_status: QcStatus; referential_integrity_status: QcStatus; treatment_validity_status: QcStatus; continuity_status: QcStatus; forbidden_field_status: QcStatus; reasoning_leakage_status: QcStatus; missing_scene_ids: string[]; duplicate_scene_ids: string[]; missing_beat_ids: string[]; duplicate_beat_ids: string[]; reordered_beat_ids: string[]; unknown_ref_errors: ValidationIssue[]; invalid_treatment_errors: ValidationIssue[]; continuity_warnings: ValidationIssue[]; forbidden_field_errors: ValidationIssue[]; reasoning_leakage_errors: ValidationIssue[]; representation_family_qc: VisualPlanRepresentationFamilyQC; warnings: string[]; }
export interface VisualPlanReferenceBinding { anchor_type: 'character' | 'location' | 'prop' | 'recurring_visual_subject'; anchor_id: string; reference_artifact_id: string; media_artifact_id: string; approval_set_artifact_id: string; source_definition_hash: string; media_sha256: string; mime_type: 'image/png' | 'image/jpeg' | 'image/webp'; image_artifact_id: string; }
export interface VisualPlanBeatV4 { scene_id: string; beat_id: string; beat_order: number; local_beat_order: number; script_quote: string; resolved_script_span: [number, number]; visual_context_ref: string; computed_duration_seconds: number; visual_priority: 'critical' | 'high' | 'normal'; is_narrative_marker: boolean; narrative_marker_refs: string[]; narrative_marker_types: Array<'turning_point' | 'reveal' | 'contrast' | 'payoff' | 'emphasis'>; representation_family: 'character_environment_lived' | 'object_evidence_closeup' | 'text_diagram_editorial'; selected_character_refs: string[]; selected_location_refs: string[]; selected_prop_refs: string[]; selected_recurring_visual_subject_refs: string[]; selected_motif_refs: string[]; selected_native_visual_reference_ids: string[]; visual_concept: string; subject_action: string; environment_emphasis: string; framing: string; camera_angle: string; camera_movement: string; composition: string; lighting_direction: string; color_emphasis: string; motion_guidance: string; continuity_with_previous: string; previous_beat_image_reference_required: boolean; continuity_group_id: string; continuity_notes: string; production_notes: string; negative_requirements: string[]; visible_text: string[]; selected_reference_bindings: VisualPlanReferenceBinding[]; }
export interface VisualPlanSceneV4 { scene_id: string; scene_order: number; section_id: string; scene_continuity_notes: string; beat_plans: VisualPlanBeatV4[]; }
export interface VisualPlanArtifactV4 { artifact_type: 'visual_plan'; schema_version: '3.0'; project_id: string; visual_plan_revision_id: string; parent_visual_plan_revision_id: string | null; revision_operation: 'full_generation' | 'section_regeneration' | 'section_edit' | 'approval'; source_script_revision_id: string; source_script_analysis_revision_id: string; source_style_bible_revision_id: string; source_scene_plan_revision_id: string; source_run_artifact_id: string; status: 'draft_ready' | 'approved'; scene_count: number; beat_count: number; scenes: VisualPlanSceneV4[]; backend_qc: VisualPlanBackendQC; }
export interface VisualPlanReadResponseV4 { project_id: string; generated: boolean; artifact_id: string | null; artifact: VisualPlanArtifactV4 | null; }

export type PromptBundleStatus = 'draft_ready' | 'approved';
export type PromptBundleRevisionOperation = 'automatic_compilation' | 'full_compilation' | 'beat_recompilation' | 'manual_edit' | 'approval';
export type PromptBundleAspectRatio = '1:1' | '3:4' | '4:3' | '9:16' | '16:9';
export interface PromptComposerOptions { schema_version: '1.0'; prompt_language: 'en'; aspect_ratio: PromptBundleAspectRatio; target_asset_role: 'beat_visual'; image_count: 1; }

export type SceneAssetResolutionStatus = 'not_generated' | 'queued' | 'generating' | 'candidate_ready' | 'approved' | 'failed';
export interface SceneAssetTreatment { visual_concept: string; subject_action: string; environment_emphasis: string; framing: string; camera_angle: string; camera_movement: string; composition: string; lighting_direction: string; color_emphasis: string; motion_guidance: string; continuity_with_previous: string; continuity_notes: string; production_notes: string; negative_requirements: string[]; visible_text: string[]; }
export interface SceneAssetManifestEntry {
  scene_id: string; beat_id: string; beat_order: number; local_beat_order: number; continuity_group_id: string;
  computed_duration_seconds: number; resolution_status: SceneAssetResolutionStatus;
  review_status: 'unreviewed' | 'current_draft' | 'approved' | 'rejected';
  selected_asset_artifact_id: string | null; current_draft_asset_artifact_id: string | null;
  candidate_asset_artifact_ids: string[]; source_prompt_item_id: string; source_prompt_hash: string;
  media_task_ids: string[]; width: number | null; height: number | null; mime_type: string | null; sha256: string | null;
  treatment: SceneAssetTreatment;
  reference_bindings: VisualPlanReferenceBinding[];
  generation_lineage: Record<string, unknown> | null;
  continuity_source: Record<string, unknown> | null;
  error: { code: string; message: string; details: Record<string, unknown> } | null;
}
export interface SceneAssetCandidate { artifact_id: string; media_task_id: string | null; mime_type: string; sha256: string; width: number | null; height: number | null; is_current_draft: boolean; is_approved: boolean; is_rejected: boolean; generated_from_current_prompt: boolean; }
export interface SceneAssetContinuityReadiness { continuity_required: boolean; continuity_source_beat_id: string | null; continuity_source_ready: boolean; continuity_source_kind: 'approved' | null; continuity_source_stale: boolean; continuity_block_reason: 'source_not_generated' | 'source_not_approved' | 'source_artifact_missing' | 'source_integrity_failed' | 'source_lineage_stale' | null; dependency_error: { code: string; message: string; details: Record<string, unknown> } | null; }
export interface SceneAssetBeatPrompt { image_prompt: string; negative_prompt: string; prompt_hash: string; prompt_origin: 'compiled' | 'manual_edit'; }
export type SceneAssetOperationStatus = 'queued' | 'running' | 'waiting_for_media' | 'finalizing' | 'completed' | 'partial' | 'failed' | 'superseded' | 'cancelled';
export interface SceneAssetWorkspaceResponse {
  project_id: string; readiness: { ready: boolean; blockers: string[] }; current_prompt_bundle_artifact_id: string | null; current_manifest_artifact_id: string | null;
  manifest_status: 'generating' | 'partial' | 'draft_ready' | 'approved' | 'failed' | 'superseded' | null;
  operation: { operation_id: string; job_id: string; operation_type: 'generate_all' | 'generate_section' | 'generate_beat' | 'generate_missing' | 'retry_failed' | 'retry_beat' | 'regenerate_beat' | 'regenerate_section'; status: SceneAssetOperationStatus; progress: number; media_progress: MediaGenerationProgressV4 | null; error_code: string | null; error_message: string | null } | null;
  scenes: Array<{ scene_id: string; scene_order: number; section_id: string | null; section_order: number | null; section_title: string | null; beats: Array<{ entry: SceneAssetManifestEntry; script_quote: string | null; candidates: SceneAssetCandidate[]; continuity: SceneAssetContinuityReadiness; prompt: SceneAssetBeatPrompt; approval_change_impact_count: number; action_eligibility: { can_generate: boolean; can_regenerate: boolean; can_retry: boolean; can_review: boolean; can_upload: boolean; can_approve: boolean; is_generation_active?: boolean } }> }>;
  action_eligibility: { can_generate_all: boolean; can_generate_section: boolean; can_generate_missing: boolean; can_retry_failed: boolean; can_approve_manifest: boolean; eligible_generate_count: number; eligible_missing_count: number; blocked_generate_count: number };
}
export type SceneAssetBeatWorkspace = SceneAssetWorkspaceResponse['scenes'][number]['beats'][number];
export interface SceneAssetBeatWorkspaceResponse { project_id: string; base_manifest_artifact_id: string | null; published_manifest_artifact_id: string | null; current_manifest_artifact_id: string | null; manifest_status: SceneAssetWorkspaceResponse['manifest_status']; operation: SceneAssetWorkspaceResponse['operation']; beat: SceneAssetBeatWorkspace; action_eligibility: SceneAssetWorkspaceResponse['action_eligibility']; }
export interface SceneAssetBeatMutationResponse { project_id: string; artifact_id: string; base_manifest_artifact_id: string | null; published_manifest_artifact_id: string; current_manifest_artifact_id: string; manifest_status: NonNullable<SceneAssetWorkspaceResponse['manifest_status']>; operation: SceneAssetWorkspaceResponse['operation']; beats: SceneAssetBeatWorkspace[]; action_eligibility: SceneAssetWorkspaceResponse['action_eligibility']; }
export interface SceneAssetJobSubmissionResponse { project_id: string; job_id: string; operation_id: string; status: 'queued'; manifest_artifact_id: string; }
export interface SceneAssetJobStatusResponse { project_id: string; job_id: string; operation_id: string; operation_type: string; status: SceneAssetOperationStatus; progress: number; manifest_artifact_id: string | null; media_batch_id: string; media_progress: MediaGenerationProgressV4; result: Record<string, unknown>; error_code: string | null; error_message: string | null; }
export interface SceneAssetExecutionStatusResponse { project_id: string; beats: Record<string, { state: SceneAssetResolutionStatus; operation_id: string; job_id: string; media_task_ids: string[] }>; active_count: number; terminal_count: number; effective_poll_interval_seconds: number; updated_at: string; }
export interface SceneAssetRevisionResponse { artifact_id: string; artifact: { status: string; scene_asset_manifest_revision_id: string; [key: string]: unknown }; }

export interface TTSVoice { voice_id: string; display_name: string; language: string; }
export interface TTSStoredProfile { profile_id: string; provider: string; enabled: boolean; display_name: string; default_voice_id: string | null; default_speaking_rate: number; sample_rate_hz: number; output_format: string; maximum_segment_characters: number; provider_options: Record<string, unknown>; }
export interface TTSProfile extends TTSStoredProfile { voices: TTSVoice[]; }
export interface TTSStoredSettings { active_profile_id: string; profiles: TTSStoredProfile[]; }
export interface TTSSettings { active_profile_id: string; profiles: TTSProfile[]; }
export interface TTSReadiness { status: 'ready' | 'not_configured'; issues: Array<{ error_code: string; message: string }>; profile_id: string | null; provider: string | null; voice_id: string | null; requires_ffmpeg: boolean; }
export interface TTSSettingsResponse { settings: TTSSettings; readiness: TTSReadiness; }
export interface VoiceSegmentWorkspace { segment_id: string; segment_order: number; section_id: string; source_start: number; source_end: number; exact_text: string; text_sha256: string; status: string; segment_task_id: string | null; segment_audio_artifact_id: string | null; duration_seconds: number | null; sha256: string | null; audio_url: string | null; timing_source: string | null; error: Record<string, unknown> | null; can_regenerate: boolean; }
export interface VoiceSectionWorkspace { section_id: string; section_order: number; segment_ids: string[]; target_wpm: number | null; raw_wpm: number | null; pacing_status: 'pending' | 'pass' | 'regeneration_required'; correction_relative: number | null; current_provider_pacing_value: number | null; recommended_provider_pacing_value: number | null; recommendation_policy_version: string | null; recommendation_reason: string | null; remediation_identity: string | null; remediation_report_sha256: string | null; source_remediation_operation_id: string | null; regeneration_supported: boolean; can_regenerate: boolean; can_accept_pacing_deviation: boolean; remediation_block_reason: string | null; issue: Record<string, unknown> | null; }
export type VoicePreset = 'economy' | 'quality_cloned_voice';
export type VoiceGenerationProvider = 'elevenlabs' | 'kokoro_local';
export interface VoiceReadinessIssue { error_code: string; message: string; cause_error_code?: string | null; }
export interface VoiceGenerationPresetStatus { ready: boolean; issue: VoiceReadinessIssue | null; model_id: string; }
export interface VoiceGenerationProviderStatus { generation_provider: VoiceGenerationProvider; display_name: string; ready: boolean; issue: VoiceReadinessIssue | null; generation_ready: boolean; generation_issue: VoiceReadinessIssue | null; generation_presets?: Record<VoicePreset, VoiceGenerationPresetStatus> | null; alignment_ready: boolean; alignment_issue: VoiceReadinessIssue | null; alignment_provider: 'elevenlabs'; }
export interface VoiceWorkspaceReadiness { status: 'ready' | 'not_ready'; issues: VoiceReadinessIssue[]; profile_id: string | null; provider: string | null; voice_id: string | null; requires_ffmpeg: boolean; }
export interface VoiceWorkspaceResponse { project_id: string; readiness: VoiceWorkspaceReadiness; script_artifact_id: string | null; selected_generation_provider: VoiceGenerationProvider | null; selected_tts_profile_id: string | null; selected_voice_id: string | null; selected_voice_label: string | null; available_generation_providers: VoiceGenerationProviderStatus[]; kokoro_voice_catalog: TTSVoice[]; speaking_rate: number | null; active_operation: { operation_id: string; job_id: string; status: string; operation_type: string; target_section_id: string | null } | null; current_narration_audio_artifact_id: string | null; audio_duration_seconds: number | null; audio_mime_type: string | null; audio_sha256: string | null; audio_url: string | null; current_narration_word_timing_map_artifact_id: string | null; draft_narration_audio_artifact_id: string | null; draft_narration_word_timing_map_artifact_id: string | null; draft_operation_id: string | null; draft_generation_provider: VoiceGenerationProvider | null; draft_generation_voice_id: string | null; draft_voice_preset: VoicePreset | null; draft_candidate_identity_sha256: string | null; draft_audio_url: string | null; alignment_status: 'queued' | 'submitting' | 'running' | 'completed' | 'submit_outcome_unknown' | 'failed' | null; alignment_completed_section_count: number; alignment_total_section_count: number; alignment_submit_outcome_unknown_section_ids: string[]; timing_map_status: string | null; timing_source: string | null; current_output_problem: VoiceReadinessIssue | null; sections: VoiceSectionWorkspace[]; segments: VoiceSegmentWorkspace[]; approval_eligible: boolean; available_actions: string[]; }
export interface VoiceRealignCurrentResponse { status: 'realigned'; narration_audio_artifact_id: string; previous_narration_word_timing_map_artifact_id: string; narration_word_timing_map_artifact_id: string; beat_timing_map_artifact_id: string | null; preserved_scene_plan_artifact_id: string | null; preserved_visual_plan_artifact_id: string | null; preserved_prompt_bundle_artifact_id: string | null; preserved_scene_asset_manifest_artifact_id: string | null; }
export interface GenerateVoiceRequest { operation_type: 'generate_all' | 'generate_missing'; generation_provider: VoiceGenerationProvider; voice_preset?: VoicePreset | null; generation_voice_id?: string | null; expected_narration_audio_artifact_id: string | null; expected_narration_word_timing_map_artifact_id: string | null; }
export interface VoiceJobSubmissionResponse { job_id: string; operation_id: string; status: string; segment_count: number; submitted_count: number; reused_count: number; }
export interface VoiceJobProblem { error_code: string; message: string; details: Record<string, unknown> | unknown[] | string | number | boolean | null; }
export interface VoiceJobStatusResponse { job_id: string; operation_id: string; operation_type: string; status: string; progress: number; segment_count: number; completed_segment_count: number; failed_segment_count: number; error_code: string | null; error_message: string | null; current_segment_id: string | null; provider: string | null; action: string | null; retry_count: number; max_attempts: number; next_retry_at: string | null; cancel_requested: boolean; problem: VoiceJobProblem | null; }

export interface AssemblyBuildPayload { expected_assembly_plan_artifact_id: string | null; expected_subtitle_srt_artifact_id: string | null; expected_subtitle_vtt_artifact_id: string | null; expected_scene_plan_artifact_id: string; expected_scene_asset_manifest_artifact_id: string; expected_narration_audio_artifact_id: string; expected_narration_word_timing_map_artifact_id: string; expected_beat_timing_map_artifact_id: string; background_music: { artifact_id: string; start_time_units: number; end_time_units: number; source_start_time_units: number; gain_db: number; loop_mode: 'none' | 'loop'; fade_in_time_units: number; fade_out_time_units: number } | null; sound_effects: Array<Record<string, unknown>>; }
export interface RenderProfile { profile_id: string; display_name: string; enabled: boolean; width: number; height: number; frame_rate: { numerator: number; denominator: number }; video_codec: 'libx264'; video_crf: number; video_preset: string; pixel_format: 'yuv420p'; audio_codec: 'aac'; audio_bitrate_kbps: number; audio_sample_rate_hz: 48000; audio_channels: 2; subtitle_mode: 'embedded_mov_text' | 'sidecar_only'; faststart: true; deterministic_encoding: boolean; encoder_threads: number; clipping_policy: 'fail_on_detected_clipping'; }
export interface RenderSettings { active_profile_id: string; profiles: RenderProfile[]; }
export interface RenderReadiness { status: 'ready' | 'not_ready' | 'disabled'; profile_id: string | null; ffmpeg_version: string | null; ffprobe_version: string | null; capabilities_sha256: string | null; issues: Array<{ error_code: string; message: string; capability?: string | null }>; }
export interface RenderSettingsResponse { settings: RenderSettings; readiness: RenderReadiness; }
export interface RenderJobRequest { render_profile_id: string; expected_assembly_plan_artifact_id: string; expected_subtitle_srt_artifact_id: string; expected_subtitle_vtt_artifact_id: string; expected_final_video_artifact_id: string | null; operation_type: 'render' | 'rerender' | 'retry'; }
export interface RenderJobSubmissionResponse { project_id: string; render_operation_id: string; job_id: string; status: string; render_profile_id: string; }
export interface RenderJobStatusResponse { project_id: string; render_operation_id: string; job_id: string; operation_type: string; status: string; progress: number; progress_frame: number; progress_time_units: number; attempt_no: number; retryable: boolean; error_code: string | null; error_message: string | null; }
export interface FinalVideoMetadata { artifact_id: string; sha256: string; size_bytes: number; width: number; height: number; duration_seconds: number; video_codec: 'h264'; pixel_format: 'yuv420p'; frame_rate: { numerator: number; denominator: number }; frame_count: number; audio_codec: 'aac'; audio_sample_rate_hz: 48000; audio_channels: 2; subtitle_mode: 'embedded_mov_text' | 'sidecar_only'; subtitle_stream_count: number; faststart: boolean; decode_valid: boolean; }
export interface RenderWorkspaceResponse { project_id: string; readiness: RenderReadiness; current_assembly_plan_artifact_id: string | null; current_subtitle_srt_artifact_id: string | null; current_subtitle_vtt_artifact_id: string | null; selected_render_profile_id: string; active_operation: { render_operation_id: string; job_id: string; operation_type: string; status: string; progress: number; attempt_no: number } | null; current_final_video_artifact_id: string | null; video_url: string | null; vtt_url: string | null; final_video: FinalVideoMetadata | null; validation_summary: Record<string, unknown> | null; available_actions: string[]; errors: Array<Record<string, unknown>>; }

export interface AssemblyWorkspaceResponse { project_id: string; readiness: { status: string; issues: Array<Record<string,string>> }; current_assembly_plan_artifact_id: string | null; assembly_status: 'draft_ready' | 'approved' | null; current_subtitle_srt_artifact_id: string | null; current_subtitle_vtt_artifact_id: string | null; source_identities: { script_artifact_id: string | null; scene_plan_artifact_id: string | null; scene_asset_manifest_artifact_id: string | null; narration_audio_artifact_id: string | null; narration_word_timing_map_artifact_id: string | null; beat_timing_map_artifact_id: string | null }; total_duration_seconds: number | null; scene_count: number; beat_count: number; subtitle_cue_count: number; visual_timeline_summary: Record<string,unknown>; narration_track_summary: Record<string,unknown>; optional_audio_summary: Record<string,unknown>; subtitle_summary: Record<string,unknown>; backend_qc: Record<string,unknown>; scenes: Array<Record<string,unknown>>; beats: Array<Record<string,unknown>>; subtitle_cues: Array<Record<string,unknown>>; build_eligible: boolean; approval_eligible: boolean; available_actions: string[]; errors: Array<Record<string,unknown>>; }



export interface PromptBundleLineagePayloadV4 { style_bible_artifact_id: string; visual_plan_artifact_id: string; expected_prompt_bundle_artifact_id: string | null; }
export interface PromptBundleCompilePayloadV4 extends PromptBundleLineagePayloadV4 { options: PromptComposerOptions; }
export interface PromptBundleManualEditPayloadV4 extends PromptBundleLineagePayloadV4 { image_prompt: string; negative_prompt: string; }
export interface PromptBundleItem { prompt_item_id: string; scene_id: string; beat_id: string; beat_order: number; local_beat_order: number; continuity_group_id: string; visual_priority: 'critical' | 'high' | 'normal'; target_asset_role: 'beat_visual'; image_prompt: string; negative_prompt: string; prompt_hash: string; prompt_origin: 'compiled' | 'manual_edit'; generation_options: PromptComposerOptions; reference_bindings: VisualPlanReferenceBinding[]; continuity_reference_requirement: { reference_tag: 'prev_beat'; source_beat_id: string; relation: 'same_setup' | 'match_subject' | 'match_location' | 'match_composition' | 'graphic_sequence' } | null; source_provenance: { representation_family: 'character_environment_lived' | 'object_evidence_closeup' | 'text_diagram_editorial'; selected_character_refs: string[]; selected_location_refs: string[]; selected_prop_refs: string[]; selected_recurring_visual_subject_refs: string[]; selected_motif_refs: string[]; selected_native_visual_reference_ids: string[] }; }
export interface PromptBundleArtifactV4 { artifact_type: 'prompt_bundle'; schema_version: '3.0'; project_id: string; prompt_bundle_revision_id: string; parent_prompt_bundle_revision_id: string | null; revision_operation: PromptBundleRevisionOperation; operation_target_beat_id: string | null; source_script_revision_id: string; source_script_analysis_revision_id: string; source_style_bible_revision_id: string; source_scene_plan_revision_id: string; source_visual_plan_revision_id: string; source_visual_reference_approval_set_artifact_id: string | null; status: PromptBundleStatus; options: PromptComposerOptions; source_beat_count: number; prompt_item_count: number; prompt_items: PromptBundleItem[]; backend_qc: Record<string, unknown>; }
export interface PromptBundleReadResponseV4 { project_id: string; generated: boolean; artifact_id: string | null; artifact: PromptBundleArtifactV4 | null; }
export interface PromptBundleRevisionResponseV4 { artifact_id: string; artifact: PromptBundleArtifactV4; }


export class ApiClient {
  constructor(private readonly baseUrl: string) {}

  async getHealth(): Promise<HealthResponse> { return this.request<HealthResponse>('/api/health'); }
  async getVersion(): Promise<HealthResponse> { return this.request<HealthResponse>('/api/version'); }
  async getMediaSettings(): Promise<MediaProviderSettingsResponse> { return this.request<MediaProviderSettingsResponse>('/api/settings/media'); }
  async updateMediaSettings(payload: UpdateMediaProviderSettingsPayload): Promise<MediaProviderSettingsResponse> { return this.request<MediaProviderSettingsResponse>('/api/settings/media', { method: 'PUT', body: JSON.stringify(payload) }); }
  async testMediaConnection(): Promise<TestMediaProviderConnectionResponse> { return this.request<TestMediaProviderConnectionResponse>('/api/settings/media/test-connection', { method: 'POST', body: JSON.stringify({}) }); }
  async listJobs(params: { page?: number; status?: string; job_type?: string } = {}): Promise<JobListResponse> { const search = new URLSearchParams(); if (params.page) search.set('page', String(params.page)); if (params.status) search.set('status', params.status); if (params.job_type) search.set('job_type', params.job_type); const qs = search.toString(); return this.request<JobListResponse>(`/api/jobs${qs ? `?${qs}` : ''}`); }
  async getJob(jobId: string): Promise<Job> { return this.request<Job>(`/api/jobs/${jobId}`); }
  async listArtifacts(params: { page?: number; artifact_type?: string; logical_id?: string } = {}): Promise<ArtifactListResponse> { const search = new URLSearchParams(); if (params.page) search.set('page', String(params.page)); if (params.artifact_type) search.set('artifact_type', params.artifact_type); if (params.logical_id) search.set('logical_id', params.logical_id); const qs = search.toString(); return this.request<ArtifactListResponse>(`/api/artifacts${qs ? `?${qs}` : ''}`); }
  async listChannels(): Promise<ChannelListResponse> { return this.request<ChannelListResponse>('/api/v3/channels'); }
  async createChannel(payload: CreateChannelPayload): Promise<Channel> { return this.request<Channel>('/api/v3/channels', { method: 'POST', body: JSON.stringify(payload) }); }
  async getChannel(channelId: string): Promise<Channel> { return this.request<Channel>(`/api/v3/channels/${channelId}`); }
  async getChannelDna(channelId: string): Promise<ChannelDnaReadResponse> { return this.request<ChannelDnaReadResponse>(`/api/v3/channels/${channelId}/channel-dna`); }
  async validateChannelDnaV3(payload: ChannelDnaV3): Promise<ChannelDnaValidationResponse> { return this.request<ChannelDnaValidationResponse>('/api/v3/channel-dna/validate', { method: 'POST', body: JSON.stringify({ channel_dna: payload }) }); }
  async saveChannelDnaV3(channelId: string, payload: ChannelDnaV3): Promise<Channel> { return this.request<Channel>(`/api/v3/channels/${channelId}/channel-dna`, { method: 'POST', body: JSON.stringify({ channel_dna: payload }) }); }
  async getChannelVoiceProfile(channelId: string): Promise<ChannelVoiceProfileReadResponse> { return this.request<ChannelVoiceProfileReadResponse>(`/api/v3/channels/${channelId}/voice-profile`); }
  async updateChannelVoiceProfile(channelId: string, voiceProfile: ChannelVoiceProfile | null): Promise<ChannelVoiceProfileReadResponse> { return this.request<ChannelVoiceProfileReadResponse>(`/api/v3/channels/${channelId}/voice-profile`, { method: 'PUT', body: JSON.stringify({ voice_profile: voiceProfile }) }); }
  async listProjects(params: { channel_id?: string } = {}): Promise<ProjectListResponse> { const search = new URLSearchParams(); if (params.channel_id) search.set('channel_id', params.channel_id); const qs = search.toString(); return this.request<ProjectListResponse>(`/api/v3/projects${qs ? `?${qs}` : ''}`); }
  async createProject(payload: CreateProjectPayload): Promise<Project> { return this.request<Project>('/api/v3/projects', { method: 'POST', body: JSON.stringify(payload) }); }
  async getProject(projectId: string): Promise<Project> { return this.request<Project>(`/api/v3/projects/${projectId}`); }
  async getProjectTopicIdeas(projectId: string): Promise<TopicIdeasReadResponse> { return this.request<TopicIdeasReadResponse>(`/api/v3/projects/${projectId}/topic-ideas`); }
  async getPendingTopicIdeasPrompt(projectId: string): Promise<ExternalPromptDraftReadResponse> { return this.request<ExternalPromptDraftReadResponse>(`/api/v3/projects/${projectId}/topic-ideas/prompt`); }
  async buildTopicIdeasPrompt(projectId: string, payload: GenerateTopicIdeasPayload): Promise<ExternalPromptPackage> { return this.request<ExternalPromptPackage>(`/api/v3/projects/${projectId}/topic-ideas/prompt`, { method: 'POST', body: JSON.stringify(payload) }); }
  async validateTopicIdeasImport(projectId: string, payload: ExternalImportPayload): Promise<ExternalImportValidationResponse> { return this.request<ExternalImportValidationResponse>(`/api/v3/projects/${projectId}/topic-ideas/import/validate`, { method: 'POST', body: JSON.stringify(payload) }); }
  async importTopicIdeas(projectId: string, payload: ExternalImportPayload): Promise<TopicIdeasResponse> { return this.request<TopicIdeasResponse>(`/api/v3/projects/${projectId}/topic-ideas/import`, { method: 'POST', body: JSON.stringify(payload) }); }
  async getProjectScript(projectId: string): Promise<ScriptReadResponse> { return this.request<ScriptReadResponse>(`/api/v3/projects/${projectId}/script`); }
  async getPendingScriptAnalysisPrompt(projectId: string): Promise<ExternalPromptDraftReadResponse> { return this.request<ExternalPromptDraftReadResponse>(`/api/v3/projects/${projectId}/script-analysis/prompt`); }
  async buildScriptAnalysisPrompt(projectId: string, payload: GenerateScriptPayload): Promise<ExternalPromptPackage> { return this.request<ExternalPromptPackage>(`/api/v3/projects/${projectId}/script-analysis/prompt`, { method: 'POST', body: JSON.stringify(payload) }); }
  async validateScriptAnalysisImport(projectId: string, payload: ExternalImportPayload): Promise<ExternalImportValidationResponse> { return this.request<ExternalImportValidationResponse>(`/api/v3/projects/${projectId}/script-analysis/import/validate`, { method: 'POST', body: JSON.stringify(payload) }); }
  async importScriptAnalysis(projectId: string, payload: ExternalImportPayload): Promise<ScriptAnalysisCombinedRevisionResponse> { return this.request<ScriptAnalysisCombinedRevisionResponse>(`/api/v3/projects/${projectId}/script-analysis/import`, { method: 'POST', body: JSON.stringify(payload) }); }
  async getScriptAnalysis(projectId: string): Promise<ScriptAnalysisReadResponse> { return this.request<ScriptAnalysisReadResponse>(`/api/v4/projects/${projectId}/script-analysis`); }
  async getScriptAnalysisContinuityReview(projectId: string): Promise<ScriptAnalysisContinuityReviewResponse> { return this.request<ScriptAnalysisContinuityReviewResponse>(`/api/v4/projects/${projectId}/script-analysis/continuity-review`); }
  async mutateScriptAnalysisContinuityReview(projectId: string, payload: ScriptAnalysisContinuityReviewMutationPayload): Promise<ScriptAnalysisContinuityReviewResponse> { return this.request<ScriptAnalysisContinuityReviewResponse>(`/api/v4/projects/${projectId}/script-analysis/continuity-review`, { method: 'PATCH', body: JSON.stringify(payload) }); }
  async getStyleBibleV4(projectId: string): Promise<StyleBibleReadResponseV4> { return this.request<StyleBibleReadResponseV4>(`/api/v4/projects/${projectId}/style-bible`); }
  async getSceneBreakdownV4(projectId: string): Promise<SceneBreakdownReadResponseV4> { return this.request<SceneBreakdownReadResponseV4>(`/api/v4/projects/${projectId}/scene-breakdown`); }
  async getPendingSceneBreakdownPromptV4(projectId: string): Promise<ExternalPromptDraftReadResponse> { return this.request<ExternalPromptDraftReadResponse>(`/api/v4/projects/${projectId}/scene-breakdown/prompt`); }
  async buildSceneBreakdownPromptV4(projectId: string): Promise<ExternalPromptPackage> { return this.request<ExternalPromptPackage>(`/api/v4/projects/${projectId}/scene-breakdown/prompt`, { method: 'POST' }); }
  async validateSceneBreakdownImportV4(projectId: string, payload: ExternalImportPayload): Promise<ExternalImportValidationResponse> { return this.request<ExternalImportValidationResponse>(`/api/v4/projects/${projectId}/scene-breakdown/import/validate`, { method: 'POST', body: JSON.stringify(payload) }); }
  async importSceneBreakdownV4(projectId: string, payload: ExternalImportPayload): Promise<SceneBreakdownRevisionResponseV4> { return this.request<SceneBreakdownRevisionResponseV4>(`/api/v4/projects/${projectId}/scene-breakdown/import`, { method: 'POST', body: JSON.stringify(payload) }); }
  async validateSceneBreakdownSectionEditV4(projectId: string, sectionId: string, payload: SceneBreakdownSectionEditValidatePayloadV4): Promise<SceneBreakdownSectionEditValidationResponseV4> { return this.request<SceneBreakdownSectionEditValidationResponseV4>(`/api/v4/projects/${projectId}/scene-breakdown/review/sections/${sectionId}/validate`, { method: 'POST', body: JSON.stringify(payload) }); }
  async saveSceneBreakdownSectionEditV4(projectId: string, sectionId: string, payload: SceneBreakdownSectionEditSavePayloadV4): Promise<SceneBreakdownRevisionResponseV4> { return this.request<SceneBreakdownRevisionResponseV4>(`/api/v4/projects/${projectId}/scene-breakdown/review/sections/${sectionId}`, { method: 'PUT', body: JSON.stringify(payload) }); }
  async approveSceneBreakdownV4(projectId: string, payload: SceneBreakdownApprovePayloadV4): Promise<SceneBreakdownRevisionResponseV4> { return this.request<SceneBreakdownRevisionResponseV4>(`/api/v4/projects/${projectId}/scene-breakdown/approve`, { method: 'POST', body: JSON.stringify(payload) }); }
  async getVisualPlanV4(projectId: string, signal?: AbortSignal): Promise<VisualPlanReadResponseV4> { return this.request<VisualPlanReadResponseV4>(`/api/v4/projects/${projectId}/visual-plan`, { signal }); }
  async getPromptBundleV4(projectId: string, signal?: AbortSignal): Promise<PromptBundleReadResponseV4> { return this.request<PromptBundleReadResponseV4>(`/api/v4/projects/${projectId}/prompt-bundle`, { signal }); }
  async compilePromptBundleV4(projectId: string, payload: PromptBundleCompilePayloadV4): Promise<PromptBundleRevisionResponseV4> { return this.request<PromptBundleRevisionResponseV4>(`/api/v4/projects/${projectId}/prompt-bundle/compile`, { method: 'POST', body: JSON.stringify(payload) }); }
  async recompilePromptBundleV4(projectId: string, payload: PromptBundleCompilePayloadV4): Promise<PromptBundleRevisionResponseV4> { return this.request<PromptBundleRevisionResponseV4>(`/api/v4/projects/${projectId}/prompt-bundle/recompile`, { method: 'POST', body: JSON.stringify(payload) }); }
  async recompilePromptBundleBeatV4(projectId: string, beatId: string, payload: PromptBundleLineagePayloadV4): Promise<PromptBundleRevisionResponseV4> { return this.request<PromptBundleRevisionResponseV4>(`/api/v4/projects/${projectId}/prompt-bundle/beats/${beatId}/recompile`, { method: 'POST', body: JSON.stringify(payload) }); }
  async editPromptBundleBeatV4(projectId: string, beatId: string, payload: PromptBundleManualEditPayloadV4): Promise<PromptBundleRevisionResponseV4> { return this.request<PromptBundleRevisionResponseV4>(`/api/v4/projects/${projectId}/prompt-bundle/beats/${beatId}`, { method: 'PUT', body: JSON.stringify(payload) }); }
  async approvePromptBundleV4(projectId: string, payload: PromptBundleLineagePayloadV4): Promise<PromptBundleRevisionResponseV4> { return this.request<PromptBundleRevisionResponseV4>(`/api/v4/projects/${projectId}/prompt-bundle/approve`, { method: 'POST', body: JSON.stringify(payload) }); }
  async rebuildSceneAssetPromptV4(projectId: string): Promise<PromptBundleRevisionResponseV4> {
    const current = await this.getPromptBundleV4(projectId);
    if (!current.generated || !current.artifact || !current.artifact_id) throw new Error('PROMPT_BUNDLE_NOT_FOUND: Current Scene Asset Prompt Bundle is missing.');
    const draft = await this.recompilePromptBundleV4(projectId, {
      style_bible_artifact_id: current.artifact.source_style_bible_revision_id,
      visual_plan_artifact_id: current.artifact.source_visual_plan_revision_id,
      expected_prompt_bundle_artifact_id: current.artifact_id,
      options: current.artifact.options,
    });
    return this.approvePromptBundleV4(projectId, {
      style_bible_artifact_id: draft.artifact.source_style_bible_revision_id,
      visual_plan_artifact_id: draft.artifact.source_visual_plan_revision_id,
      expected_prompt_bundle_artifact_id: draft.artifact_id,
    });
  }
  async generateStyleBibleDraftV4(projectId: string, payload: GenerateStyleBibleDraftPayloadV4): Promise<StyleBibleArtifactResponseV4> { return this.request<StyleBibleArtifactResponseV4>(`/api/v4/projects/${projectId}/style-bible/generate`, { method: 'POST', body: JSON.stringify(payload) }); }
  async updateStyleBibleTextV4(projectId: string, payload: StyleBibleTextUpdatePayloadV4): Promise<StyleBibleArtifactResponseV4> { return this.request<StyleBibleArtifactResponseV4>(`/api/v4/projects/${projectId}/style-bible/text`, { method: 'PUT', body: JSON.stringify(payload) }); }
  async approveStyleBibleTextV4(projectId: string, expectedStyleBibleRevisionId: string): Promise<StyleBibleArtifactResponseV4> { return this.request<StyleBibleArtifactResponseV4>(`/api/v4/projects/${projectId}/style-bible/text/approve`, { method: 'POST', body: JSON.stringify({ expected_style_bible_revision_id: expectedStyleBibleRevisionId }) }); }
  async getVisualReferencesV4(projectId: string, signal?: AbortSignal): Promise<VisualReferenceWorkspaceV4> { return this.request<VisualReferenceWorkspaceV4>(`/api/v4/projects/${projectId}/visual-references`, { signal }); }
  async generateVisualReferencesV4(projectId: string, payload: VisualReferenceGenerateRequestV4): Promise<VisualReferenceJobSubmissionV4> { return this.request<VisualReferenceJobSubmissionV4>(`/api/v4/projects/${projectId}/visual-references/jobs`, { method: 'POST', body: JSON.stringify(payload) }); }
  async regenerateVisualReferenceV4(projectId: string, anchorType: VisualReferenceAnchorTypeV4, anchorId: string, payload: VisualReferenceGenerateRequestV4): Promise<VisualReferenceJobSubmissionV4> { return this.request<VisualReferenceJobSubmissionV4>(`/api/v4/projects/${projectId}/visual-references/${anchorType}/${anchorId}/regenerate/jobs`, { method: 'POST', body: JSON.stringify(payload) }); }
  async uploadVisualReferenceV4(projectId: string, anchorType: VisualReferenceAnchorTypeV4, anchorId: string, expectedVisualReferenceSetArtifactId: string, file: File): Promise<VisualReferenceWorkspaceV4> {
    const body = new FormData();
    body.set('expected_visual_reference_set_artifact_id', expectedVisualReferenceSetArtifactId);
    body.set('file', file);
    return this.request<VisualReferenceWorkspaceV4>(`/api/v4/projects/${projectId}/visual-references/${anchorType}/${anchorId}/upload`, { method: 'POST', body });
  }
  async updateVisualReferencePromptV4(projectId: string, anchorType: VisualReferenceAnchorTypeV4, anchorId: string, payload: VisualReferencePromptUpdateRequestV4): Promise<VisualReferenceWorkspaceV4> {
    return this.request<VisualReferenceWorkspaceV4>(`/api/v4/projects/${projectId}/visual-references/${anchorType}/${anchorId}/prompt`, { method: 'PUT', body: JSON.stringify(payload) });
  }
  async getVisualReferenceJobV4(projectId: string, jobId: string, signal?: AbortSignal): Promise<VisualReferenceJobStatusV4> { return this.request<VisualReferenceJobStatusV4>(`/api/v4/projects/${projectId}/visual-references/jobs/${jobId}`, { signal }); }
  async approveVisualReferenceV4(projectId: string, anchorType: VisualReferenceAnchorTypeV4, anchorId: string, payload: VisualReferenceApproveRequestV4): Promise<VisualReferenceApprovalRevisionV4> { return this.request<VisualReferenceApprovalRevisionV4>(`/api/v4/projects/${projectId}/visual-references/${anchorType}/${anchorId}/approve`, { method: 'POST', body: JSON.stringify(payload) }); }
  async clearVisualReferenceV4(projectId: string, anchorType: VisualReferenceAnchorTypeV4, anchorId: string, payload: VisualReferenceClearRequestV4): Promise<VisualReferenceApprovalRevisionV4> { return this.request<VisualReferenceApprovalRevisionV4>(`/api/v4/projects/${projectId}/visual-references/${anchorType}/${anchorId}/clear`, { method: 'POST', body: JSON.stringify(payload) }); }
  async selectTopic(projectId: string, payload: SelectTopicPayload): Promise<Project> { return this.request<Project>(`/api/v3/projects/${projectId}/selected-topic`, { method: 'PUT', body: JSON.stringify(payload) }); }
  async getTTSSettings(signal?: AbortSignal): Promise<TTSSettingsResponse> { return this.request<TTSSettingsResponse>('/api/settings/tts', { signal }); }
  async updateTTSSettings(settings: TTSStoredSettings): Promise<TTSSettingsResponse> { return this.request<TTSSettingsResponse>('/api/settings/tts', { method: 'PUT', body: JSON.stringify({ settings }) }); }
  async getTTSReadiness(profileId?: string, voiceId?: string, signal?: AbortSignal): Promise<TTSReadiness> { const search = new URLSearchParams(); if (profileId) search.set('profile_id', profileId); if (voiceId) search.set('voice_id', voiceId); return this.request<TTSReadiness>(`/api/settings/tts/readiness${search.size ? `?${search.toString()}` : ''}`, { signal }); }
  async getVoiceWorkspace(projectId: string, signal?: AbortSignal): Promise<VoiceWorkspaceResponse> { return this.request<VoiceWorkspaceResponse>(`/api/v4/projects/${projectId}/voice`, { signal }); }
  async generateVoice(projectId: string, payload: GenerateVoiceRequest): Promise<VoiceJobSubmissionResponse> { return this.request<VoiceJobSubmissionResponse>(`/api/v4/projects/${projectId}/voice/jobs`, { method: 'POST', body: JSON.stringify(payload) }); }
  async getVoiceJob(projectId: string, jobId: string): Promise<VoiceJobStatusResponse> { return this.request<VoiceJobStatusResponse>(`/api/v4/projects/${projectId}/voice/jobs/${jobId}`); }
  async cancelVoiceJob(projectId: string, jobId: string): Promise<VoiceJobStatusResponse> { return this.request<VoiceJobStatusResponse>(`/api/v4/projects/${projectId}/voice/jobs/${jobId}/cancel`, { method: 'POST' }); }
  async regenerateVoiceSection(projectId: string, sectionId: string, expectedAudio: string | null, expectedWordTiming: string | null, sourceOperationId: string, expectedReportSha256: string): Promise<VoiceJobSubmissionResponse> { return this.request<VoiceJobSubmissionResponse>(`/api/v4/projects/${projectId}/voice/sections/${sectionId}/regenerate/jobs`, { method: 'POST', body: JSON.stringify({ expected_narration_audio_artifact_id: expectedAudio, expected_narration_word_timing_map_artifact_id: expectedWordTiming, source_remediation_operation_id: sourceOperationId, expected_remediation_report_sha256: expectedReportSha256 }) }); }
  async approveVoice(projectId: string, candidateAudio: string, candidateWordTiming: string, expectedAudio: string | null, expectedWordTiming: string | null): Promise<{ narration_audio_artifact_id: string; narration_word_timing_map_artifact_id: string; status: string }> { return this.request(`/api/v4/projects/${projectId}/voice/approve`, { method: 'POST', body: JSON.stringify({ candidate_narration_audio_artifact_id: candidateAudio, candidate_narration_word_timing_map_artifact_id: candidateWordTiming, expected_narration_audio_artifact_id: expectedAudio, expected_narration_word_timing_map_artifact_id: expectedWordTiming }) }); }
  async retryVoiceAlignment(projectId: string, jobId: string): Promise<{ status: string; retried_section_count: number }> { return this.request(`/api/v4/projects/${projectId}/voice/jobs/${jobId}/alignment/retry`, { method: 'POST' }); }
  async realignCurrentVoice(projectId: string, expectedAudio: string, expectedWordTiming: string): Promise<VoiceRealignCurrentResponse> { return this.request(`/api/v4/projects/${projectId}/voice/realign-current`, { method: 'POST', body: JSON.stringify({ expected_narration_audio_artifact_id: expectedAudio, expected_narration_word_timing_map_artifact_id: expectedWordTiming }) }); }
  getVoiceAudioUrl(projectId: string, artifactId: string | null = null): string { const suffix = artifactId ? `?artifact_id=${encodeURIComponent(artifactId)}` : ''; return this.resolveUrl(`/api/v4/projects/${projectId}/voice/audio${suffix}`); }
  getVoiceSectionAudioUrl(projectId: string, sectionId: string): string { return this.resolveUrl(`/api/v4/projects/${projectId}/voice/sections/${sectionId}/audio`); }
  getVoiceSegmentAudioUrl(projectId: string, segmentId: string): string { return this.resolveUrl(`/api/v4/projects/${projectId}/voice/segments/${segmentId}/audio`); }
  async getRenderSettings(): Promise<RenderSettingsResponse> { return this.request<RenderSettingsResponse>('/api/settings/render'); }
  async updateRenderSettings(settings: RenderSettings): Promise<RenderSettingsResponse> { return this.request<RenderSettingsResponse>('/api/settings/render', { method: 'PUT', body: JSON.stringify({ settings }) }); }
  async getRenderReadiness(profileId?: string): Promise<RenderReadiness> { const suffix = profileId ? `?profile_id=${encodeURIComponent(profileId)}` : ''; return this.request<RenderReadiness>(`/api/settings/render/readiness${suffix}`); }
  async getRenderWorkspace(projectId: string, signal?: AbortSignal): Promise<RenderWorkspaceResponse> { return this.request<RenderWorkspaceResponse>(`/api/v4/projects/${projectId}/render`, { signal }); }
  async submitRender(projectId: string, payload: RenderJobRequest): Promise<RenderJobSubmissionResponse> { return this.request<RenderJobSubmissionResponse>(`/api/v4/projects/${projectId}/render/jobs`, { method: 'POST', body: JSON.stringify(payload) }); }
  async getRenderJob(projectId: string, jobId: string, signal?: AbortSignal): Promise<RenderJobStatusResponse> { return this.request<RenderJobStatusResponse>(`/api/v4/projects/${projectId}/render/jobs/${jobId}`, { signal }); }
  async cancelRenderJob(projectId: string, jobId: string): Promise<RenderJobStatusResponse> { return this.request<RenderJobStatusResponse>(`/api/v4/projects/${projectId}/render/jobs/${jobId}/cancel`, { method: 'POST' }); }
  async retryRenderJob(projectId: string, jobId: string): Promise<RenderJobSubmissionResponse> { return this.request<RenderJobSubmissionResponse>(`/api/v4/projects/${projectId}/render/jobs/${jobId}/retry`, { method: 'POST' }); }
  getRenderVideoUrl(projectId: string): string { return this.resolveUrl(`/api/v4/projects/${projectId}/render/video`); }
  async getAssemblyWorkspace(projectId: string, signal?: AbortSignal): Promise<AssemblyWorkspaceResponse> { return this.request<AssemblyWorkspaceResponse>(`/api/v4/projects/${projectId}/assembly`, { signal }); }
  async buildAssembly(projectId: string, payload: AssemblyBuildPayload): Promise<AssemblyWorkspaceResponse> { return this.request<AssemblyWorkspaceResponse>(`/api/v4/projects/${projectId}/assembly/build`, { method: 'POST', body: JSON.stringify(payload) }); }
  async approveAssembly(projectId: string, assemblyId: string, srtId: string, vttId: string): Promise<AssemblyWorkspaceResponse> { return this.request<AssemblyWorkspaceResponse>(`/api/v4/projects/${projectId}/assembly/approve`, { method: 'POST', body: JSON.stringify({ expected_assembly_plan_artifact_id: assemblyId, expected_subtitle_srt_artifact_id: srtId, expected_subtitle_vtt_artifact_id: vttId }) }); }
  getAssemblySrtUrl(projectId: string): string { return this.resolveUrl(`/api/v4/projects/${projectId}/assembly/subtitles.srt`); }
  getAssemblyVttUrl(projectId: string): string { return this.resolveUrl(`/api/v4/projects/${projectId}/assembly/subtitles.vtt`); }
  async getSceneAssets(projectId: string, signal?: AbortSignal): Promise<SceneAssetWorkspaceResponse> { return this.request<SceneAssetWorkspaceResponse>(`/api/v4/projects/${projectId}/scene-assets`, { signal }); }
  async getSceneAssetBeat(projectId: string, beatId: string, signal?: AbortSignal): Promise<SceneAssetBeatWorkspaceResponse> { return this.request<SceneAssetBeatWorkspaceResponse>(`/api/v4/projects/${projectId}/scene-assets/beats/${beatId}`, { signal }); }
  async getSceneAssetExecutionStatus(projectId: string, signal?: AbortSignal): Promise<SceneAssetExecutionStatusResponse> { return this.request<SceneAssetExecutionStatusResponse>(`/api/v4/projects/${projectId}/scene-assets/execution-status`, { signal }); }
  async editSceneAssetBeatPrompt(projectId: string, beatId: string, expectedPromptBundleArtifactId: string, imagePrompt: string, negativePrompt: string): Promise<PromptBundleRevisionResponseV4> { return this.request<PromptBundleRevisionResponseV4>(`/api/v4/projects/${projectId}/scene-assets/beats/${beatId}/prompt`, { method: 'PUT', body: JSON.stringify({ expected_prompt_bundle_artifact_id: expectedPromptBundleArtifactId, image_prompt: imagePrompt, negative_prompt: negativePrompt }) }); }
  async restoreSceneAssetBeatPrompt(projectId: string, beatId: string, expectedPromptBundleArtifactId: string): Promise<PromptBundleRevisionResponseV4> { return this.request<PromptBundleRevisionResponseV4>(`/api/v4/projects/${projectId}/scene-assets/beats/${beatId}/prompt/restore-compiled`, { method: 'POST', body: JSON.stringify({ expected_prompt_bundle_artifact_id: expectedPromptBundleArtifactId }) }); }
  getSceneAssetContentUrl(projectId: string, artifactId: string): string { return this.resolveUrl(`/api/v4/projects/${projectId}/scene-assets/assets/${artifactId}/content`); }
  async generateSceneAssets(projectId: string, operationType: 'generate_all' | 'generate_section' | 'generate_missing' | 'retry_failed' | 'regenerate_section', expected: string | null, candidateCount = 1, sectionId: string | null = null): Promise<SceneAssetJobSubmissionResponse> { return this.request<SceneAssetJobSubmissionResponse>(`/api/v4/projects/${projectId}/scene-assets/jobs`, { method: 'POST', body: JSON.stringify({ operation_type: operationType, expected_scene_asset_manifest_artifact_id: expected, candidate_count: candidateCount, ...(sectionId ? { section_id: sectionId } : {}) }) }); }
  async generateSceneAssetBeat(projectId: string, beatId: string, expected: string | null, candidateCount = 1): Promise<SceneAssetJobSubmissionResponse> { return this.request<SceneAssetJobSubmissionResponse>(`/api/v4/projects/${projectId}/scene-assets/beats/${beatId}/generate/jobs`, { method: 'POST', body: JSON.stringify({ expected_scene_asset_manifest_artifact_id: expected, candidate_count: candidateCount }) }); }
  async uploadSceneAssetBeatImage(projectId: string, beatId: string, expected: string | null, file: File): Promise<SceneAssetBeatMutationResponse> { const body = new FormData(); body.append('file', file); if (expected) body.append('expected_scene_asset_manifest_artifact_id', expected); return this.request<SceneAssetBeatMutationResponse>(`/api/v4/projects/${projectId}/scene-assets/beats/${beatId}/upload`, { method: 'POST', body }); }
  async retrySceneAssetBeat(projectId: string, beatId: string, expected: string, candidateCount = 1): Promise<SceneAssetJobSubmissionResponse> { return this.request<SceneAssetJobSubmissionResponse>(`/api/v4/projects/${projectId}/scene-assets/beats/${beatId}/retry/jobs`, { method: 'POST', body: JSON.stringify({ expected_scene_asset_manifest_artifact_id: expected, candidate_count: candidateCount }) }); }
  async getSceneAssetJob(projectId: string, jobId: string): Promise<SceneAssetJobStatusResponse> { return this.request<SceneAssetJobStatusResponse>(`/api/v4/projects/${projectId}/scene-assets/jobs/${jobId}`); }
  async regenerateSceneAssetBeat(projectId: string, beatId: string, expected: string, candidateCount = 1): Promise<SceneAssetJobSubmissionResponse> { return this.request<SceneAssetJobSubmissionResponse>(`/api/v4/projects/${projectId}/scene-assets/beats/${beatId}/regenerate/jobs`, { method: 'POST', body: JSON.stringify({ expected_scene_asset_manifest_artifact_id: expected, candidate_count: candidateCount }) }); }
  async decideSceneAssetCandidate(projectId: string, beatId: string, candidateId: string | null, expected: string, decision: 'set-current-draft' | 'approve' | 'reject' | 'clear-approval'): Promise<SceneAssetBeatMutationResponse> { const suffix = decision === 'clear-approval' ? `beats/${beatId}/clear-approval` : `beats/${beatId}/${candidateId}/${decision}`; return this.request<SceneAssetBeatMutationResponse>(`/api/v4/projects/${projectId}/scene-assets/${suffix}`, { method: 'POST', body: JSON.stringify({ expected_scene_asset_manifest_artifact_id: expected }) }); }
  async approveSceneAssetManifest(projectId: string, expected: string): Promise<SceneAssetRevisionResponse> { return this.request<SceneAssetRevisionResponse>(`/api/v4/projects/${projectId}/scene-assets/approve`, { method: 'POST', body: JSON.stringify({ expected_scene_asset_manifest_artifact_id: expected }) }); }
  async generateMediaImages(payload: MediaImageGeneratePayload): Promise<MediaImageGenerateResponse> { return this.request<MediaImageGenerateResponse>('/api/v3/media/images/generate', { method: 'POST', body: JSON.stringify(payload) }); }
  async getActiveMediaBatches(projectId: string): Promise<MediaActiveBatchesResponse> {
    return this.request<MediaActiveBatchesResponse>(`/api/v3/media/batches/active?project_id=${encodeURIComponent(projectId)}`);
  }
  async listMediaTasks(params: { batch_id?: string; status?: MediaTaskStatus; offset?: number; limit?: number } = {}): Promise<MediaTaskCardListResponse> {
    const search = new URLSearchParams();
    if (params.batch_id) search.set('batch_id', params.batch_id);
    if (params.status) search.set('status', params.status);
    if (params.offset !== undefined) search.set('offset', String(params.offset));
    if (params.limit !== undefined) search.set('limit', String(params.limit));
    search.set('view', 'cards');
    const qs = search.toString();
    return this.request<MediaTaskCardListResponse>(`/api/v3/media/tasks${qs ? `?${qs}` : ''}`);
  }
  async getMediaTaskSummary(batchId: string): Promise<MediaTaskSummaryResponse> { return this.request<MediaTaskSummaryResponse>(`/api/v3/media/tasks/summary?batch_id=${encodeURIComponent(batchId)}`); }
  async getMediaTaskStatus(mediaTaskId: string): Promise<MediaTaskStatusResponse> { return this.request<MediaTaskStatusResponse>(`/api/v3/media/tasks/${mediaTaskId}/status`); }
  async getMediaTaskResult(mediaTaskId: string): Promise<MediaTaskResultResponse> { return this.request<MediaTaskResultResponse>(`/api/v3/media/tasks/${mediaTaskId}/result`); }
  getMediaTaskFileUrl(mediaTaskId: string, resultIndex = 1): string { return this.resolveUrl(`/api/v3/media/tasks/${mediaTaskId}/file?result_index=${encodeURIComponent(String(resultIndex))}`); }
  resolveUrl(path: string): string {
    if (/^https?:\/\//i.test(path)) return path;
    return new URL(path, this.baseUrl).toString();
  }

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    let response: Response;
    try {
      const headers = new Headers(init?.headers);
      const isFormDataBody = typeof FormData !== 'undefined' && init?.body instanceof FormData;
      if (!isFormDataBody && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
      response = await fetch(`${this.baseUrl}${path}`, { ...init, headers });
    } catch (error) {
      if (init?.signal?.aborted || (error instanceof Error && error.name === 'AbortError')) throw error;
      throw new ApiError({ error_code: 'NETWORK_ERROR', message: error instanceof Error ? error.message : 'Network request failed.', retryable: true });
    }
    const body = (await response.json()) as T | ErrorResponse;
    if (!response.ok) throw new ApiError(body as ErrorResponse);
    return body as T;
  }
}

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000';
export const apiClient = new ApiClient(apiBaseUrl);
