"""
Matching engine Celery tasks.

Runs the P2P matching cycle on a schedule defined by MATCHING_CYCLE_INTERVAL_SECONDS.
Can also be triggered manually via the admin API.
"""

import asyncio
import logging

from app.tasks.celery_app import celery_app
from app.matching_engine.engine import matching_engine

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.matching_tasks.run_matching_cycle")
def run_matching_cycle():
    """
    Execute a matching cycle.

    Celery tasks are synchronous, so we run the async engine
    in an event loop.
    """
    logger.info("Starting scheduled matching cycle")
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(matching_engine.run_cycle())
        if result.get("skipped"):
            logger.info("Matching cycle skipped — lock held by another process")
            return result
        logger.info(
            "Matching cycle %s completed: %d matches",
            result["cycle_id"],
            result["results"]["total_matches"],
        )
        return result
    except Exception:
        logger.exception("Matching cycle failed")
        raise
    finally:
        loop.close()
