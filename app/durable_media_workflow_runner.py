from __future__ import annotations

import logging
import signal
from threading import Event

from app.db.session import get_session_factory
from app.modules.media.durable_workflow.host import DurableMediaWorkflowHost, RegisteredDurableWorkflow
from app.scene_asset_coordinator_runner import SceneAssetCoordinatorRunner
from app.visual_reference_coordinator_runner import VisualReferenceCoordinatorRunner
from app.voice_coordinator_runner import VoiceCoordinatorRunner


logger = logging.getLogger(__name__)


def build_host() -> DurableMediaWorkflowHost:
    session_factory = get_session_factory()
    return DurableMediaWorkflowHost(
        (
            RegisteredDurableWorkflow("scene-assets-v2", SceneAssetCoordinatorRunner(session_factory)),
            RegisteredDurableWorkflow("visual-references-v2", VisualReferenceCoordinatorRunner(session_factory)),
            RegisteredDurableWorkflow("voice", VoiceCoordinatorRunner(session_factory)),
        )
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    stop = Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, request_stop)

    host = build_host()
    logger.info("Durable media workflow host started domains=%s", ",".join(item.name for item in host.workflows))
    try:
        host.run_forever(stop)
    finally:
        host.close()
    logger.info("Durable media workflow host stopped")


if __name__ == "__main__":
    main()
