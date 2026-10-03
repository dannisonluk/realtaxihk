"""Search — the console's front door. `/search`. Any admin role."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.schemas import AdminSearchOut
from app.core.db import get_session_factory
from app.core.deps import Principal, require_admin
from app.services.search_service import (
    DEFAULT_LIMIT as DEFAULT_SEARCH_LIMIT,
)
from app.services.search_service import (
    MAX_LIMIT as MAX_SEARCH_LIMIT,
)
from app.services.search_service import (
    MIN_QUERY_LENGTH as MIN_SEARCH_LENGTH,
)
from app.services.search_service import (
    SearchService,
    normalize_query,
)

router = APIRouter()


@router.get("/search", response_model=AdminSearchOut)
async def search_subjects(
    q: str = Query(min_length=0, max_length=120),
    limit: int = Query(default=DEFAULT_SEARCH_LIMIT, ge=1, le=MAX_SEARCH_LIMIT),
    admin: Principal = Depends(require_admin),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
):
    """Find an account by phone, name, username or plate. Any admin role.

    This is the endpoint support hits while a passenger is on the line, so it is
    deliberately the widest-guarded thing here: every role may search, because
    the alternative is that the person answering the phone cannot look up the
    caller's account.

    **Not audited per call.** Every keystroke that triggers a search writing an
    audit row would make the trail useless through volume, and would record
    *that* someone searched without recording what they then looked at. The
    audited event is opening the subject's detail page, which is where identity
    is actually revealed.
    """
    items, truncated = await SearchService(session_factory).search(q, limit=limit)
    return {
        "items": items,
        "query": normalize_query(q),
        "truncated": truncated,
        "query_too_short": len(normalize_query(q)) < MIN_SEARCH_LENGTH,
        "min_query_length": MIN_SEARCH_LENGTH,
    }
