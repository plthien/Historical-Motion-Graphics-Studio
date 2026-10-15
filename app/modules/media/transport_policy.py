from __future__ import annotations

from dataclasses import dataclass
from math import ceil


@dataclass(frozen=True, slots=True)
class MediaTransportPolicy:
    control_plane_total_deadline_seconds: float = 20.0
    control_plane_io_timeout_seconds: float = 5.0
    control_plane_max_request_bytes: int = 5_242_880
    control_plane_max_response_bytes: int = 262_144
    download_total_deadline_seconds: float = 300.0
    download_io_timeout_seconds: float = 15.0
    poll_claim_min_lease_seconds: int = 40
    sqlite_operation_budget_seconds: int = 5
    claim_release_budget_seconds: int = 5
    claim_safety_margin_seconds: int = 5

    def __post_init__(self) -> None:
        positive_floats = {
            "control_plane_total_deadline_seconds": self.control_plane_total_deadline_seconds,
            "control_plane_io_timeout_seconds": self.control_plane_io_timeout_seconds,
            "download_total_deadline_seconds": self.download_total_deadline_seconds,
            "download_io_timeout_seconds": self.download_io_timeout_seconds,
        }
        for name, value in positive_floats.items():
            if type(value) not in {int, float} or isinstance(value, bool) or float(value) <= 0:
                raise ValueError(f"{name} must be a positive number.")
        if self.control_plane_io_timeout_seconds > self.control_plane_total_deadline_seconds:
            raise ValueError("control-plane I/O timeout must not exceed total deadline.")
        if self.download_io_timeout_seconds > self.download_total_deadline_seconds:
            raise ValueError("download I/O timeout must not exceed total deadline.")
        byte_limits = {
            "control_plane_max_request_bytes": self.control_plane_max_request_bytes,
            "control_plane_max_response_bytes": self.control_plane_max_response_bytes,
        }
        for name, value in byte_limits.items():
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer.")
        integer_budgets = {
            "poll_claim_min_lease_seconds": self.poll_claim_min_lease_seconds,
            "sqlite_operation_budget_seconds": self.sqlite_operation_budget_seconds,
            "claim_release_budget_seconds": self.claim_release_budget_seconds,
            "claim_safety_margin_seconds": self.claim_safety_margin_seconds,
        }
        for name, value in integer_budgets.items():
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer.")
        if self.poll_claim_min_lease_seconds <= self.poll_owner_proof_seconds:
            raise ValueError("poll claim minimum lease must be strictly greater than the owner proof total.")

    @property
    def poll_owner_proof_seconds(self) -> int:
        return ceil(
            float(self.control_plane_total_deadline_seconds)
            + self.sqlite_operation_budget_seconds
            + self.claim_release_budget_seconds
            + self.claim_safety_margin_seconds
        )

    def poll_claim_lease_seconds(self, poll_interval_seconds: int) -> int:
        if type(poll_interval_seconds) is not int or poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be a positive integer.")
        return max(
            self.poll_claim_min_lease_seconds,
            poll_interval_seconds * 6,
            self.poll_owner_proof_seconds,
        )


DEFAULT_MEDIA_TRANSPORT_POLICY = MediaTransportPolicy()
