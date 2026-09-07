"""Voting service layer.

Issues #33-#49: session management, suggestions, reactions, filtering, final vote.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    Booking,
    BookingStatus,
    EventMovie,
    EventMovieStatus,
    TVDevice,
    User,
    Vote,
    VoteSession,
    VoteSessionState,
    VoteType,
)

if TYPE_CHECKING:
    pass


class VotingError(Exception):
    pass


class VotingStateError(VotingError):
    pass


class VotingNotFoundError(VotingError):
    pass


class VotingPermissionError(VotingError):
    pass


# --- State machine helpers ---

VALID_TRANSITIONS: dict[VoteSessionState, list[VoteSessionState]] = {
    VoteSessionState.CREATED: [VoteSessionState.SUGGESTIONS],
    VoteSessionState.SUGGESTIONS: [VoteSessionState.FILTER, VoteSessionState.FINAL_VOTE],
    VoteSessionState.FILTER: [VoteSessionState.FINAL_VOTE],
    VoteSessionState.FINAL_VOTE: [VoteSessionState.WINNER_SELECTED],
    VoteSessionState.WINNER_SELECTED: [VoteSessionState.DOWNLOADING],
    VoteSessionState.DOWNLOADING: [VoteSessionState.READY, VoteSessionState.DOWNLOAD_FAILED],
    VoteSessionState.READY: [VoteSessionState.PLAYING],
    VoteSessionState.PLAYING: [VoteSessionState.COMPLETED],
    VoteSessionState.DOWNLOAD_FAILED: [VoteSessionState.SUGGESTIONS],  # retry_vote
    VoteSessionState.CANCELLED: [],
    VoteSessionState.COMPLETED: [],
}


def can_transition(current: VoteSessionState, target: VoteSessionState) -> bool:
    return target in VALID_TRANSITIONS.get(current, [])


async def transition_state(
    session: AsyncSession,
    vote_session: VoteSession,
    target_state: VoteSessionState,
) -> VoteSession:
    if not can_transition(vote_session.state, target_state):
        raise VotingStateError(
            f"Invalid transition: {vote_session.state.value} -> {target_state.value}"
        )
    vote_session.state = target_state
    await session.flush()
    return vote_session


# --- Session creation ---

async def create_vote_session(
    session: AsyncSession,
    *,
    chat_id: int,
    tv_device_id: int,
    mode: str,
    created_by_user_id: int,
    booking_id: int | None = None,
    movie_duration_estimate: int | None = None,
    suggest_duration: int | None = None,
    filter_duration: int | None = None,
    vote_duration: int | None = None,
    min_votes: int = 0,
    top_n: int = 5,
    scheduled_start: datetime | None = None,
) -> VoteSession:
    """Create a new vote session in CREATED state."""
    vs = VoteSession(
        chat_id=chat_id,
        tv_device_id=tv_device_id,
        booking_id=booking_id,
        mode=mode,
        state=VoteSessionState.CREATED,
        suggest_duration=suggest_duration,
        filter_duration=filter_duration,
        vote_duration=vote_duration,
        movie_duration_estimate=movie_duration_estimate,
        created_by_user_id=created_by_user_id,
        min_votes=min_votes,
        top_n=top_n,
        scheduled_start=scheduled_start,
        started_at=datetime.now(),
    )
    session.add(vs)
    await session.flush()
    return vs


async def get_vote_session(
    session: AsyncSession,
    session_id: int,
) -> VoteSession:
    vs = await session.get(VoteSession, session_id)
    if not vs:
        raise VotingNotFoundError(f"Vote session {session_id} not found")
    return vs


async def get_active_session_for_chat(
    session: AsyncSession,
    chat_id: int,
) -> VoteSession | None:
    """Get the most recent non-finalized session for a chat."""
    q = select(VoteSession).where(
        VoteSession.chat_id == chat_id,
        VoteSession.state.not_in([
            VoteSessionState.COMPLETED,
            VoteSessionState.CANCELLED,
            VoteSessionState.DOWNLOAD_FAILED,
        ]),
    ).order_by(VoteSession.created_at.desc())
    return (await session.execute(q)).scalar_one_or_none()


# --- Suggestions ---

async def suggest_movie(
    session: AsyncSession,
    *,
    vote_session_id: int,
    user_id: int,
    title: str,
) -> EventMovie:
    """Add a movie proposal to the session."""
    vs = await get_vote_session(session, vote_session_id)

    if vs.state != VoteSessionState.SUGGESTIONS:
        raise VotingStateError(f"Suggestions only allowed in SUGGESTIONS state, got {vs.state.value}")

    # Deduplication: same normalized title in this session
    normalized = title.strip().lower()
    existing_q = select(EventMovie).where(
        EventMovie.session_id == vote_session_id,
        EventMovie.normalized_title == normalized,
    )
    existing = (await session.execute(existing_q)).scalar_one_or_none()
    if existing:
        raise VotingError(f"Фильм «{title}» уже предложен (от @{existing.proposer.username or existing.proposer.telegram_id})")

    movie = EventMovie(
        session_id=vote_session_id,
        title=title.strip(),
        normalized_title=normalized,
        submitted_by_user_id=user_id,
        status=EventMovieStatus.PROPOSED,
    )
    session.add(movie)
    await session.flush()

    # Update reaction count to 0 (explicit)
    movie.reaction_count = 0
    await session.flush()

    return movie


async def list_movies(
    session: AsyncSession,
    vote_session_id: int,
    *,
    status_filter: list[EventMovieStatus] | None = None,
    order_by: str = "reaction_count_desc",
) -> list[EventMovie]:
    q = select(EventMovie).where(EventMovie.session_id == vote_session_id)

    if status_filter:
        q = q.where(EventMovie.status.in_(status_filter))

    if order_by == "reaction_count_desc":
        q = q.order_by(desc(EventMovie.reaction_count))
    elif order_by == "reaction_count_asc":
        q = q.order_by(EventMovie.reaction_count)
    elif order_by == "created":
        q = q.order_by(EventMovie.created_at)

    return list((await session.execute(q)).scalars().all())


# --- Fire reactions (🔥) ---

async def add_fire_reaction(
    session: AsyncSession,
    *,
    vote_session_id: int,
    user_id: int,
    movie_id: int,
) -> EventMovie:
    """Record a fire reaction vote (🔥)."""
    vs = await get_vote_session(session, vote_session_id)

    if vs.state not in [VoteSessionState.SUGGESTIONS, VoteSessionState.FILTER]:
        raise VotingStateError("Fire reactions only during SUGGESTIONS/FILTER")

    movie = await session.get(EventMovie, movie_id)
    if not movie or movie.session_id != vote_session_id:
        raise VotingNotFoundError("Movie not found in this session")

    # Check if user already voted fire for this movie
    existing_q = select(Vote).where(
        Vote.session_id == vote_session_id,
        Vote.user_id == user_id,
        Vote.vote_type == VoteType.FIRE,
        Vote.movie_id == movie_id,
    )
    existing = (await session.execute(existing_q)).scalar_one_or_none()
    if existing:
        raise VotingError("Вы уже голосовали 🔥 за этот фильм")

    vote = Vote(
        session_id=vote_session_id,
        user_id=user_id,
        movie_id=movie_id,
        vote_type=VoteType.FIRE,
    )
    session.add(vote)

    # Increment reaction count
    movie.reaction_count += 1
    movie.status = EventMovieStatus.FILTERED_IN  # being in filter is "filtered_in"
    await session.flush()

    return movie


async def remove_fire_reaction(
    session: AsyncSession,
    *,
    vote_session_id: int,
    user_id: int,
    movie_id: int,
) -> EventMovie:
    """Remove a fire reaction (user un-reacted)."""
    vs = await get_vote_session(session, vote_session_id)

    movie = await session.get(EventMovie, movie_id)
    if not movie or movie.session_id != vote_session_id:
        raise VotingNotFoundError("Movie not found")

    vote_q = select(Vote).where(
        Vote.session_id == vote_session_id,
        Vote.user_id == user_id,
        Vote.vote_type == VoteType.FIRE,
        Vote.movie_id == movie_id,
    )
    vote = (await session.execute(vote_q)).scalar_one_or_none()
    if vote:
        await session.delete(vote)
        movie.reaction_count = max(0, movie.reaction_count - 1)
        await session.flush()

    return movie


# --- Filter stage (Top-N) ---

async def run_filter_stage(
    session: AsyncSession,
    vote_session_id: int,
) -> list[EventMovie]:
    """Run the Top-N filter: keep top N by reaction_count, others FILTERED_OUT."""
    vs = await get_vote_session(session, vote_session_id)
    top_n = vs.top_n

    # Get movies with reaction_count > 0, sorted desc
    q = select(EventMovie).where(
        EventMovie.session_id == vote_session_id,
        EventMovie.reaction_count > 0,
    ).order_by(desc(EventMovie.reaction_count))
    all_movies = list((await session.execute(q)).scalars().all())

    finalists: list[EventMovie] = []

    for i, movie in enumerate(all_movies):
        if i < top_n:
            movie.status = EventMovieStatus.FINALIST
            finalists.append(movie)
        else:
            movie.status = EventMovieStatus.FILTERED_OUT

    await session.flush()
    return finalists


# --- Final vote ---

async def cast_final_vote(
    session: AsyncSession,
    *,
    vote_session_id: int,
    user_id: int,
    movie_id: int,
) -> EventMovie:
    """Cast a final inline vote for a finalist."""
    vs = await get_vote_session(session, vote_session_id)

    if vs.state != VoteSessionState.FINAL_VOTE:
        raise VotingStateError(f"Final vote only in FINAL_VOTE state, got {vs.state.value}")

    movie = await session.get(EventMovie, movie_id)
    if not movie or movie.session_id != vote_session_id:
        raise VotingNotFoundError("Movie not found")

    if movie.status != EventMovieStatus.FINALIST:
        raise VotingError("Можно голосовать только за финалистов")

    # Check if user already voted in final (UNIQUE constraint handles it, but give nice error)
    existing_q = select(Vote).where(
        Vote.session_id == vote_session_id,
        Vote.user_id == user_id,
        Vote.vote_type == VoteType.FINAL,
    )
    existing = (await session.execute(existing_q)).scalar_one_or_none()
    if existing:
        raise VotingError("Вы уже голосовали в финальном голосовании")

    vote = Vote(
        session_id=vote_session_id,
        user_id=user_id,
        movie_id=movie_id,
        vote_type=VoteType.FINAL,
    )
    session.add(vote)

    movie.final_votes += 1
    await session.flush()

    return movie


async def get_final_vote_counts(
    session: AsyncSession,
    vote_session_id: int,
) -> list[tuple[EventMovie, int]]:
    q = select(EventMovie).where(
        EventMovie.session_id == vote_session_id,
        EventMovie.status == EventMovieStatus.FINALIST,
    ).order_by(desc(EventMovie.final_votes))
    movies = (await session.execute(q)).scalars().all()
    return [(m, m.final_votes) for m in movies]


# --- Winner selection ---

async def select_winner(
    session: AsyncSession,
    vote_session_id: int,
) -> EventMovie:
    """Select winner: max final_votes, tiebreaker = random.choice."""
    vs = await get_vote_session(session, vote_session_id)

    if vs.state != VoteSessionState.FINAL_VOTE:
        raise VotingStateError(f"Cannot select winner from state {vs.state.value}")

    q = select(EventMovie).where(
        EventMovie.session_id == vote_session_id,
        EventMovie.status == EventMovieStatus.FINALIST,
    ).order_by(desc(EventMovie.final_votes))
    finalists = list((await session.execute(q)).scalars().all())

    if not finalists:
        raise VotingError("Нет финалистов для выбора победителя")

    max_votes = finalists[0].final_votes
    tied = [m for m in finalists if m.final_votes == max_votes]

    winner = random.choice(tied) if len(tied) > 1 else finalists[0]

    # Update statuses
    for m in finalists:
        m.status = EventMovieStatus.FINALIST  # keep as finalist
    winner.status = EventMovieStatus.WINNER
    vs.winner_movie_id = winner.id

    await session.flush()
    return winner


async def get_winner(session: AsyncSession, vote_session_id: int) -> EventMovie | None:
    vs = await get_vote_session(session, vote_session_id)
    if vs.winner_movie_id:
        return await session.get(EventMovie, vs.winner_movie_id)
    return None


# --- Retry vote ---

async def retry_vote(
    session: AsyncSession,
    vote_session_id: int,
) -> VoteSession:
    """Reset session to SUGGESTIONS state for a new round, keeping existing movies."""
    vs = await get_vote_session(session, vote_session_id)

    if vs.state not in [VoteSessionState.DOWNLOAD_FAILED, VoteSessionState.CANCELLED]:
        raise VotingStateError(f"Retry only from DOWNLOAD_FAILED/CANCELLED, got {vs.state.value}")

    # Reset state
    vs.state = VoteSessionState.SUGGESTIONS
    # Reset movie statuses
    q = select(EventMovie).where(EventMovie.session_id == vote_session_id)
    movies = (await session.execute(q)).scalars().all()
    for m in movies:
        m.status = EventMovieStatus.PROPOSED
        m.reaction_count = 0
        m.final_votes = 0
    # Delete existing votes
    from sqlalchemy import delete
    await session.execute(
        delete(Vote).where(Vote.session_id == vote_session_id)
    )
    # Reset winner
    vs.winner_movie_id = None

    await session.flush()
    return vs


# --- Min votes check ---

async def check_min_votes(
    session: AsyncSession,
    vote_session_id: int,
) -> bool:
    """Check if min_votes threshold is met."""
    vs = await get_vote_session(session, vote_session_id)
    if vs.min_votes <= 0:
        return True

    q = select(func.count(Vote.id.distinct())).where(
        Vote.session_id == vote_session_id,
        Vote.vote_type == VoteType.FINAL,
    )
    count = (await session.execute(q)).scalar() or 0
    return count >= vs.min_votes


# --- Session termination ---

async def cancel_session(
    session: AsyncSession,
    vote_session_id: int,
    user_id: int,
    is_admin: bool = False,
) -> VoteSession:
    vs = await get_vote_session(session, vote_session_id)

    # Only creator or admin can cancel
    if vs.created_by_user_id != user_id and not is_admin:
        raise VotingPermissionError("Только создатель или админ может отменить")

    if vs.state in [VoteSessionState.COMPLETED, VoteSessionState.CANCELLED]:
        raise VotingError(f"Сессия уже в финальном состоянии: {vs.state.value}")

    vs.state = VoteSessionState.CANCELLED
    await session.flush()
    return vs


async def end_vote(session: AsyncSession, vote_session_id: int, user_id: int, is_admin: bool = False) -> VoteSession:
    """Admin force-end the vote, transition to CANCELLED."""
    return await cancel_session(session, vote_session_id, user_id, is_admin)


async def end_suggest(
    session: AsyncSession,
    vote_session_id: int,
) -> VoteSession:
    """Admin ends suggestion phase early → FILTER or FINAL_VOTE."""
    vs = await get_vote_session(session, vote_session_id)

    if vs.state != VoteSessionState.SUGGESTIONS:
        raise VotingStateError(f"Must be in SUGGESTIONS, got {vs.state.value}")

    # Check if any movies have reactions
    q = select(func.count(EventMovie.id)).where(
        EventMovie.session_id == vote_session_id,
        EventMovie.reaction_count > 0,
    )
    has_reactions = ((await session.execute(q)).scalar() or 0) > 0

    if has_reactions:
        vs.state = VoteSessionState.FILTER
    else:
        vs.state = VoteSessionState.FINAL_VOTE

    await session.flush()
    return vs


__all__ = [
    "VotingError",
    "VotingStateError",
    "VotingNotFoundError",
    "VotingPermissionError",
    "can_transition",
    "transition_state",
    "create_vote_session",
    "get_vote_session",
    "get_active_session_for_chat",
    "suggest_movie",
    "list_movies",
    "add_fire_reaction",
    "remove_fire_reaction",
    "run_filter_stage",
    "cast_final_vote",
    "get_final_vote_counts",
    "select_winner",
    "get_winner",
    "retry_vote",
    "check_min_votes",
    "cancel_session",
    "end_vote",
    "end_suggest",
]