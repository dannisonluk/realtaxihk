"""order disputes and their message thread

Two new tables, both additive — nothing existing changes shape:

* `order_disputes` — the case. `order_id` is **nullable** on purpose: a driver
  can report an abusive passenger account and a passenger can report an app
  defect, and neither has a trip attached. A NOT NULL here would force those
  into a fake order, which then pollutes every trip-based report.

* `dispute_messages` — the thread, carrying `is_internal` so staff discussion
  lives beside the conversation it is about rather than in a second table that
  has to be joined back together to read a single thread.

`resolution` is deliberately **nullable with no server default**. NULL means
"not decided yet" and `'NONE'` means "decided: nobody is charged". Collapsing
them — by defaulting to `NONE` — would make an undecided money case
indistinguishable from a closed one, which is the state the operator most needs
to see.

There is no backfill and none is possible: the tables are new and the existing
`orders` rows carry no dispute information. Unlike `d7f3b21a6e08`, this
migration needs no follow-up action.

Revision ID: a1c4e8b7f209
Revises: d7f3b21a6e08
Create Date: 2026-10-02

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a1c4e8b7f209"
down_revision: str | Sequence[str] | None = "d7f3b21a6e08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "order_disputes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        # Nullable: account- and app-level complaints have no order. RESTRICT,
        # not CASCADE — deleting an order that is the subject of an open
        # complaint would destroy the evidence.
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("raised_by_kind", sa.String(length=16), nullable=False),
        sa.Column("raised_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("against_kind", sa.String(length=16), nullable=True),
        sa.Column("against_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False, server_default="NORMAL"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="OPEN"),
        sa.Column("assigned_admin_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        # NULL = undecided. See the module docstring.
        sa.Column("resolution", sa.String(length=24), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("resolved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("safety_flag", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("sla_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assigned_admin_id"], ["admin_accounts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["resolved_by"], ["admin_accounts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_order_disputes_order_id", "order_disputes", ["order_id"])
    op.create_index("ix_order_disputes_source", "order_disputes", ["source"])
    op.create_index("ix_order_disputes_category", "order_disputes", ["category"])
    op.create_index("ix_order_disputes_status", "order_disputes", ["status"])
    op.create_index("ix_order_disputes_assigned_admin_id", "order_disputes", ["assigned_admin_id"])
    op.create_index("ix_order_disputes_sla_due_at", "order_disputes", ["sla_due_at"])
    op.create_index("ix_order_disputes_created_at", "order_disputes", ["created_at"])
    # The queue's hot path: non-terminal cases, soonest SLA first. A plain
    # `created_at` index would not serve it and the planner would fall back to
    # a sort of the whole table.
    op.create_index("ix_order_disputes_queue", "order_disputes", ["status", "sla_due_at"])
    op.create_index("ix_order_disputes_assignee", "order_disputes", ["assigned_admin_id", "status"])

    op.create_table(
        "dispute_messages",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("dispute_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("author_kind", sa.String(length=16), nullable=False),
        sa.Column("author_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("author_label", sa.String(length=120), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("is_internal", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        # CASCADE here, unlike the dispute's own order FK: a message has no
        # meaning without its case, and a dispute is never deleted in normal
        # operation — this only matters for test cleanup and an explicit purge.
        sa.ForeignKeyConstraint(["dispute_id"], ["order_disputes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dispute_messages_dispute_id", "dispute_messages", ["dispute_id"])
    op.create_index("ix_dispute_messages_created_at", "dispute_messages", ["created_at"])
    op.create_index("ix_dispute_messages_thread", "dispute_messages", ["dispute_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_dispute_messages_thread", table_name="dispute_messages")
    op.drop_index("ix_dispute_messages_created_at", table_name="dispute_messages")
    op.drop_index("ix_dispute_messages_dispute_id", table_name="dispute_messages")
    op.drop_table("dispute_messages")

    for name in (
        "ix_order_disputes_assignee",
        "ix_order_disputes_queue",
        "ix_order_disputes_created_at",
        "ix_order_disputes_sla_due_at",
        "ix_order_disputes_assigned_admin_id",
        "ix_order_disputes_status",
        "ix_order_disputes_category",
        "ix_order_disputes_source",
        "ix_order_disputes_order_id",
    ):
        op.drop_index(name, table_name="order_disputes")
    op.drop_table("order_disputes")
