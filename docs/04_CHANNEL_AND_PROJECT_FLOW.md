# Channel and Project Flow

A Channel owns Channel DNA. A Project snapshots channel context and owns current immutable artifact pointers for every active stage.

Topic prompt construction silently fills the existing `avoid_topics` field from topic titles currently selected by projects in the same channel. The UI does not expose that derived list. Every Topic candidate recommends one video pacing mode; selecting a Topic initializes that recommendation in the Script form once, where the user may override it. Pacing mode is per Project/video and is not Channel DNA ownership.

Mutations publish through guarded compare-and-set operations. Topic import invalidates selection and all downstream artifacts. Combined Script & Analysis import publishes both pointers together and clears Style Bible and downstream. Full Scene Breakdown import publishes Scene Plan and Visual Plan together and clears Prompt Bundle and production downstream.
