"""Receipt API — issue and read the frozen receipt for one order.

Mounted on its own router (rather than added to `app/api/orders.py`) because the
receipt is a separate bounded concern: it reads the order and writes only the
`receipt_*` columns. Keeping it out of the order module also keeps that module's
route table stable for the clients that already pin it.

Paths are `/api/v1/orders/{order_id}/receipt` — two segments, so they cannot be
swallowed by `orders.py`'s `GET /{order_id}` (one segment). Registration is still
deliberately *after* `orders_router` in `app/api/router.py`, so the specific
order routes keep matching first regardless of how those patterns grow.

Cap. 374D: the platform is an information intermediary. A receipt is therefore a
record of what an order was priced at — frozen once, never recomputed. See
`app/services/receipt/receipt_service.py` for why that is a stored snapshot
rather than a render-on-read.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import ReceiptOut
from app.core.db import get_session
from app.core.deps import Principal, require_active_user
from app.models import DriverProfile, Order, UserRole
from app.services.receipt.receipt_service import ReceiptService, render_receipt_text

router = APIRouter(prefix="/api/v1/orders", tags=["orders"])

# A receipt carries a passenger's name and the trip addresses, so it is served
# to the two parties of the order and to an admin view — the same set
# `GET /orders/{order_id}` already answers. A 403 is the server's answer, not a
# client-side decision.
_RECEIPT_ROLES = "the passenger, the assigned driver, or an admin"


async def _get_order_for_receipt(session: AsyncSession, order_id: str, user: Principal) -> Order:
    """Load one order and assert the caller may see its receipt.

    Existence is checked before ownership so a caller cannot probe which order
    ids exist: both failure modes answer 404/403 without revealing the other.
    """
    try:
        oid = uuid.UUID(order_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="order not found") from exc
    order = await session.get(Order, oid)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")

    profile = await DriverProfile.for_user(session, user.id)
    is_party = order.passenger_id == user.id or (
        profile is not None and order.driver_id == profile.id
    )
    if not is_party and user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail=f"receipt is for {_RECEIPT_ROLES}")
    return order


@router.post("/{order_id}/receipt", status_code=201, response_model=ReceiptOut)
async def request_receipt(
    order_id: str,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    """Issue the receipt for an order (201).

    Idempotent: a second call returns the receipt already frozen on the order
    rather than rebuilding it, so a refresh cannot change what the document says.
    That is the money-safe direction — rebuilding would let a passenger re-issue
    until the numbers read the way they preferred.
    """
    order = await _get_order_for_receipt(session, order_id, user)
    service = ReceiptService(session)
    await service.request(order)
    snapshot = order.receipt_snapshot_json or {}
    return {**snapshot, "text": render_receipt_text(snapshot)}


@router.get("/{order_id}/receipt", response_model=ReceiptOut)
async def read_receipt(
    order_id: str,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    """Read the receipt, freezing it on first read.

    Reading issues: the document a client downloads and the document the
    passenger requested must be the same frozen bytes, so a read of an
    unrequested receipt writes it rather than rendering a second, drifting copy.
    """
    order = await _get_order_for_receipt(session, order_id, user)
    service = ReceiptService(session)
    text = await service.get(order)
    return {**(order.receipt_snapshot_json or {}), "text": text}


@router.get(
    "/{order_id}/receipt.txt",
    response_class=Response,
    responses={
        200: {
            "content": {"text/plain": {"schema": {"type": "string"}}},
            "description": "The frozen receipt, rendered as a plain-text document.",
        }
    },
)
async def read_receipt_text(
    order_id: str,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    """The receipt as a downloadable plain-text document.

    A non-JSON body is deliberate: the point of this route is a file the
    passenger can save or forward, and wrapping it in JSON would make every
    consumer unwrap it. The project has no PDF dependency, and adding one is a
    deployment decision rather than a receipt one.
    """
    order = await _get_order_for_receipt(session, order_id, user)
    text = await ReceiptService(session).get(order)
    return Response(
        content=text,
        media_type="text/plain; charset=utf-8",
        headers={
            # A receipt is a document, not a screen: name the download.
            "Content-Disposition": f'attachment; filename="receipt-{order.id}.txt"'
        },
    )
