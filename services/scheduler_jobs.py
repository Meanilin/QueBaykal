"""APScheduler integration — job definitions for voting.

Called by APScheduler jobs (persisted in DB).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession

from db.session import async_session_maker

log = logging.getLogger(__name__)


async def _run_job(job_func, **kwargs):
    """Run a job with a fresh session."""
    if async_session_maker is None:
        log.error("scheduler_no_session_factory")
        return
    async with async_session_maker() as session:
        try:
            await job_func(session, **kwargs)
            await session.commit()
        except Exception as e:
            await session.rollback()
            log.error("scheduler_job_failed", job=job_func.__name__, error=str(e), exc_info=True)


async def end_suggest_job(vote_session_id: int):
    """Job: end suggestion phase, run filter or go to final vote."""
    from services.voting import end_suggest, run_filter_stage, transition_state, get_vote_session, VoteSessionState

    async def _inner(session: AsyncSession):
        vs = await end_suggest(session, vote_session_id)
        if vs.state == VoteSessionState.FILTER:
            finalists = await run_filter_stage(session, vote_session_id)
            await transition_state(session, vs, VoteSessionState.FINAL_VOTE)
            # Note: sending message to chat would need bot instance
            # We'll do this in the handler after scheduler calls this
    await _run_job(_inner)


async def end_final_vote_job(vote_session_id: int):
    """Job: end final vote, select winner."""
    from services.voting import check_min_votes, select_winner, transition_state, get_vote_session, VoteSessionState

    async def _inner(session: AsyncSession):
        min_ok = await check_min_votes(session, vote_session_id)
        vs = await get_vote_session(session, vote_session_id)

        if not min_ok:
            await transition_state(session, vs, VoteSessionState.CANCELLED)
            return

        winner = await select_winner(session, vote_session_id)
        await transition_state(session, vs, VoteSessionState.WINNER_SELECTED)
        # DOWNLOADING is handled by Epic 3
        await transition_state(session, vs, VoteSessionState.DOWNLOADING)
    await _run_job(_inner)


# These will be registered as APScheduler callbacks
__all__ = [
    "end_suggest_job",
    "end_final_vote_job",
]