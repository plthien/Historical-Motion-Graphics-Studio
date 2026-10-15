from __future__ import annotations


ACTIVE_OPERATION_STATUSES = ("queued", "running", "finalizing")
TERMINAL_OPERATION_STATUSES = ("completed", "partial", "failed", "cancelled", "superseded")
ACTIVE_UNIT_STATUSES = ("queued", "running")
TERMINAL_UNIT_STATUSES = ("candidate_ready", "failed", "cancelled")
ACTIVE_TASK_STATUSES = (
    "queued",
    "submitting",
    "waiting_provider",
    "downloading",
    "materializing",
)
TERMINAL_TASK_STATUSES = ("submit_outcome_unknown", "completed", "failed", "cancelled")

OPERATION_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"running", "finalizing", "failed", "cancelled", "superseded"},
    "running": {"queued", "finalizing", "failed", "cancelled", "superseded"},
    "finalizing": {"completed", "partial", "failed", "cancelled", "superseded"},
    "completed": set(),
    "partial": set(),
    "failed": set(),
    "cancelled": set(),
    "superseded": set(),
}

UNIT_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"running", "failed", "cancelled"},
    "running": {"queued", "candidate_ready", "failed", "cancelled"},
    "candidate_ready": set(),
    "failed": set(),
    "cancelled": set(),
}

TASK_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"submitting", "failed", "cancelled"},
    "submitting": {
        "queued",
        "waiting_provider",
        "submit_outcome_unknown",
        "failed",
        "cancelled",
    },
    "submit_outcome_unknown": set(),
    "waiting_provider": {"downloading", "failed", "cancelled"},
    "downloading": {"materializing", "failed", "cancelled"},
    "materializing": {"completed", "failed", "cancelled"},
    "completed": set(),
    "failed": set(),
    "cancelled": set(),
}
