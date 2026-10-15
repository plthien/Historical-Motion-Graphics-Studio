/**
 * Resolve the persisted selected Topic Idea against the exact current artifact.
 * Missing, stale, mismatched, duplicated, or otherwise incomplete identities fail closed.
 */
export function resolveSelectedTopicIdea({ project, topicIdeasResult }) {
  if (!project) return { status: 'none', idea: null };

  const selectedArtifactId = project.selected_topic_ideas_artifact_id;
  const selectedIdeaId = project.selected_topic_idea_id;

  if (selectedArtifactId === null && selectedIdeaId === null) {
    return { status: 'none', idea: null };
  }

  if (selectedArtifactId === null || selectedIdeaId === null) {
    return { status: 'partial_state', idea: null };
  }

  if (selectedArtifactId !== project.topic_ideas_artifact_id) {
    return { status: 'stale_selection', idea: null };
  }

  if (topicIdeasResult === undefined) {
    return { status: 'artifact_not_loaded', idea: null };
  }

  if (
    !topicIdeasResult.generated
    || topicIdeasResult.artifact === null
    || topicIdeasResult.artifact_id !== project.topic_ideas_artifact_id
  ) {
    return { status: 'artifact_mismatch', idea: null };
  }

  const matches = topicIdeasResult.artifact.ideas.filter(
    (idea) => idea.idea_id === selectedIdeaId,
  );
  if (matches.length !== 1) {
    return { status: 'stale_selection', idea: null };
  }

  return { status: 'valid', idea: matches[0] };
}
