from __future__ import annotations

import argparse
import json
import statistics
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Any, Callable, Iterator, TypeVar

from sqlalchemy import event, func, select
from sqlalchemy.engine import Engine

from app.db.scene_asset_beat_state import SceneAssetBeatState
from app.db.scene_asset_candidate import SceneAssetCandidate
from app.db.scene_asset_operation_beat import SceneAssetOperationBeat
from app.db.scene_asset_operation_task import SceneAssetOperationTask
from app.db.scene_asset_working_operation import SceneAssetWorkingOperation
from app.db.session import build_session_factory
from app.modules.v4.scene_asset_execution_projection import SceneAssetExecutionProjection
from app.modules.v4.scene_asset_workspace_v2_runtime import SceneAssetWorkspaceV2Service


T = TypeVar("T")


@dataclass(frozen=True)
class Sample:
    elapsed_ms: float
    query_count: int


@dataclass(frozen=True)
class Summary:
    iterations: int
    min_ms: float
    p50_ms: float
    p95_ms: float
    max_ms: float
    median_queries: float
    max_queries: int


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        raise ValueError("values must not be empty")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percentile
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


@contextmanager
def _query_counter(engine: Engine) -> Iterator[Callable[[], int]]:
    count = 0

    def before_cursor_execute(*_args: Any, **_kwargs: Any) -> None:
        nonlocal count
        count += 1

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    try:
        yield lambda: count
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)


def _measure(factory, callback: Callable[[Any], T]) -> Sample:
    engine = factory.kw["bind"]
    with factory() as session:
        with _query_counter(engine) as query_count:
            started = time.perf_counter()
            callback(session)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
        session.rollback()
    return Sample(elapsed_ms=elapsed_ms, query_count=query_count())


def _run_samples(factory, callback: Callable[[Any], Any], *, warmup: int, iterations: int) -> list[Sample]:
    for _ in range(warmup):
        _measure(factory, callback)
    return [_measure(factory, callback) for _ in range(iterations)]


def _summarize(samples: list[Sample]) -> Summary:
    timings = [sample.elapsed_ms for sample in samples]
    queries = [sample.query_count for sample in samples]
    return Summary(
        iterations=len(samples),
        min_ms=round(min(timings), 3),
        p50_ms=round(_percentile(timings, 0.50), 3),
        p95_ms=round(_percentile(timings, 0.95), 3),
        max_ms=round(max(timings), 3),
        median_queries=round(float(statistics.median(queries)), 1),
        max_queries=max(queries),
    )


def _workload_metadata(factory, project_id: str) -> dict[str, int]:
    with factory() as session:
        return {
            "beat_states": int(
                session.scalar(
                    select(func.count(SceneAssetBeatState.beat_id)).where(
                        SceneAssetBeatState.project_id == project_id
                    )
                )
                or 0
            ),
            "candidates": int(
                session.scalar(
                    select(func.count(SceneAssetCandidate.candidate_id)).where(
                        SceneAssetCandidate.project_id == project_id
                    )
                )
                or 0
            ),
            "operations": int(
                session.scalar(
                    select(func.count(SceneAssetWorkingOperation.operation_id)).where(
                        SceneAssetWorkingOperation.project_id == project_id
                    )
                )
                or 0
            ),
            "operation_beats": int(
                session.scalar(
                    select(func.count(SceneAssetOperationBeat.operation_beat_id)).where(
                        SceneAssetOperationBeat.project_id == project_id
                    )
                )
                or 0
            ),
            "operation_tasks": int(
                session.scalar(
                    select(func.count(SceneAssetOperationTask.operation_task_id))
                    .join(
                        SceneAssetOperationBeat,
                        SceneAssetOperationBeat.operation_beat_id
                        == SceneAssetOperationTask.operation_beat_id,
                    )
                    .where(SceneAssetOperationBeat.project_id == project_id)
                )
                or 0
            ),
        }


def benchmark(project_id: str, *, beat_id: str | None, warmup: int, iterations: int) -> dict[str, Any]:
    factory = build_session_factory()
    result: dict[str, Any] = {
        "project_id": project_id,
        "warmup": warmup,
        "iterations": iterations,
        "workload": _workload_metadata(factory, project_id),
        "measurements": {},
    }

    workspace_samples = _run_samples(
        factory,
        lambda session: SceneAssetWorkspaceV2Service(session).workspace(project_id),
        warmup=warmup,
        iterations=iterations,
    )
    result["measurements"]["workspace"] = asdict(_summarize(workspace_samples))

    execution_samples = _run_samples(
        factory,
        lambda session: SceneAssetExecutionProjection(session).get_execution_status(project_id),
        warmup=warmup,
        iterations=iterations,
    )
    result["measurements"]["execution_status"] = asdict(_summarize(execution_samples))

    if beat_id:
        beat_samples = _run_samples(
            factory,
            lambda session: SceneAssetWorkspaceV2Service(session).beat(project_id, beat_id),
            warmup=warmup,
            iterations=iterations,
        )
        result["beat_id"] = beat_id
        result["measurements"]["beat"] = asdict(_summarize(beat_samples))

    return result


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Measure Scene Assets V2 read-path latency and SQL query counts against the configured database."
    )
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--beat-id")
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--iterations", type=int, default=5)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.warmup < 0:
        raise SystemExit("--warmup must be >= 0")
    if args.iterations < 1:
        raise SystemExit("--iterations must be >= 1")
    payload = benchmark(
        args.project_id,
        beat_id=args.beat_id,
        warmup=args.warmup,
        iterations=args.iterations,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
