import type { Project, TopicIdeasReadResponse } from '../../api/client';
import type { TopicIdeaResolution } from './types';

type TopicIdeaResolutionProject = Pick<
  Project,
  | 'topic_ideas_artifact_id'
  | 'selected_topic_ideas_artifact_id'
  | 'selected_topic_idea_id'
>;

export function resolveSelectedTopicIdea(input: {
  project: TopicIdeaResolutionProject | null | undefined;
  topicIdeasResult: TopicIdeasReadResponse | undefined;
}): TopicIdeaResolution;
