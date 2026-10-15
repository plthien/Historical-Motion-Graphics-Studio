from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SequentialLLMExecutionBudget:
    total_item_count: int
    provider_item_count: int
    provider_attempts_per_item: int
    provider_timeout_seconds: int
    max_retries: int
    startup_grace_seconds: int
    local_item_grace_seconds: int
    minimum_job_timeout_seconds: int

    @property
    def provider_call_count(self) -> int:
        return self.provider_item_count * self.provider_attempts_per_item

    @property
    def provider_budget_seconds(self) -> int:
        return self.provider_call_count * self.provider_timeout_seconds * (1 + self.max_retries)

    @property
    def orchestration_budget_seconds(self) -> int:
        return self.startup_grace_seconds + self.total_item_count * self.local_item_grace_seconds

    @property
    def worker_timeout_seconds(self) -> int:
        return max(
            self.minimum_job_timeout_seconds,
            self.provider_budget_seconds + self.orchestration_budget_seconds,
        )


def calculate_sequential_llm_job_timeout_seconds(
    *,
    total_item_count: int,
    provider_item_count: int,
    provider_attempts_per_item: int,
    provider_timeout_seconds: int,
    max_retries: int,
    startup_grace_seconds: int,
    local_item_grace_seconds: int,
    minimum_job_timeout_seconds: int,
) -> int:
    values = {
        "total_item_count": total_item_count,
        "provider_item_count": provider_item_count,
        "provider_attempts_per_item": provider_attempts_per_item,
        "provider_timeout_seconds": provider_timeout_seconds,
        "startup_grace_seconds": startup_grace_seconds,
        "local_item_grace_seconds": local_item_grace_seconds,
        "minimum_job_timeout_seconds": minimum_job_timeout_seconds,
    }
    for name, value in values.items():
        if type(value) is not int or value < 1:
            raise ValueError(f"{name} must be an integer greater than or equal to 1")
    if provider_item_count > total_item_count:
        raise ValueError("provider_item_count must not exceed total_item_count")
    if type(max_retries) is not int or max_retries < 0:
        raise ValueError("max_retries must be a non-negative integer")
    return SequentialLLMExecutionBudget(
        total_item_count=total_item_count,
        provider_item_count=provider_item_count,
        provider_attempts_per_item=provider_attempts_per_item,
        provider_timeout_seconds=provider_timeout_seconds,
        max_retries=max_retries,
        startup_grace_seconds=startup_grace_seconds,
        local_item_grace_seconds=local_item_grace_seconds,
        minimum_job_timeout_seconds=minimum_job_timeout_seconds,
    ).worker_timeout_seconds
