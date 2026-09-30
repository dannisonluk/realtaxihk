"""driver licence verification (P-3)

Two tables: `driver_licence_submissions` (the review lifecycle) and
`driver_documents` (the object keys attached to a submission).

Purely additive — nothing on `driver_profiles` changes, by design. The licence
is a recurring, document-shaped event rather than a field of the driver, so it
gets its own history table instead of columns that would overwrite it.

Revision ID: a4b8e2f6c150
Revises: f2c7d1a8e940
Create Date: 2026-09-30

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "a4b8e2f6c150"
down_revision: Union[str, Sequence[str], None] = "f2c7d1a8e940"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "driver_licence_submissions",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "driver_profile_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("driver_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("licence_no", sa.String(length=32), nullable=False),
        sa.Column("expires_on", sa.DateTime(timezone=True), nullable=False),
        # native_enum=False -> a VARCHAR with a CHECK constraint, matching the
        # existing `driver_status` / `account_status` columns. A native PG enum
        # makes adding a member a migration of its own.
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("submitted_note", sa.String(length=500), nullable=True),
        sa.Column("reviewed_by", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.String(length=500), nullable=True),
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_licence_review_status",
        "driver_licence_submissions",
        "status IN ('PENDING', 'APPROVED', 'REJECTED', 'SUPERSEDED')",
    )
    op.create_index(
        "ix_driver_licence_submissions_driver_profile_id",
        "driver_licence_submissions",
        ["driver_profile_id"],
    )
    op.create_index(
        "ix_driver_licence_submissions_status", "driver_licence_submissions", ["status"]
    )
    op.create_index(
        "ix_driver_licence_submissions_submitted_at",
        "driver_licence_submissions",
        ["submitted_at"],
    )
    # At most one open submission per driver. Partial, so the decided and
    # superseded history can accumulate without blocking the next submission —
    # which is the whole point of keeping that history.
    op.create_index(
        "uq_licence_one_open_per_driver",
        "driver_licence_submissions",
        ["driver_profile_id"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )

    op.create_table(
        "driver_documents",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "submission_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("driver_licence_submissions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        # Unique: the same stored object must not be attachable to two
        # submissions. It is a bearer reference into R2, and sharing one across
        # rows would make an approval ambiguous about which image was seen.
        sa.Column("object_key", sa.String(length=255), nullable=False, unique=True),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_document_kind",
        "driver_documents",
        "kind IN ('DRIVER_LICENCE', 'TAXI_DRIVER_PASS', 'VEHICLE_REGISTRATION',"
        " 'INSURANCE', 'OTHER')",
    )
    op.create_index("ix_driver_documents_submission_id", "driver_documents", ["submission_id"])
    op.create_index("ix_driver_documents_created_at", "driver_documents", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_driver_documents_created_at", table_name="driver_documents")
    op.drop_index("ix_driver_documents_submission_id", table_name="driver_documents")
    op.drop_constraint("ck_document_kind", "driver_documents", type_="check")
    op.drop_table("driver_documents")

    op.drop_index("uq_licence_one_open_per_driver", table_name="driver_licence_submissions")
    op.drop_index(
        "ix_driver_licence_submissions_submitted_at", table_name="driver_licence_submissions"
    )
    op.drop_index("ix_driver_licence_submissions_status", table_name="driver_licence_submissions")
    op.drop_index(
        "ix_driver_licence_submissions_driver_profile_id",
        table_name="driver_licence_submissions",
    )
    op.drop_constraint("ck_licence_review_status", "driver_licence_submissions", type_="check")
    op.drop_table("driver_licence_submissions")
