"""add incident lifecycle columns (last_activity_at, suspected_change)

Revision ID: 0002_incident_lifecycle
Revises: 0001_create_incidents
Create Date: 2026-07-01

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_incident_lifecycle"
down_revision: Union[str, Sequence[str], None] = "0001_create_incidents"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable first, backfill existing rows to opened_at, then enforce NOT NULL.
    op.add_column(
        "incidents", sa.Column("last_activity_at", sa.DateTime(timezone=True))
    )
    op.add_column("incidents", sa.Column("suspected_change", sa.String()))
    op.execute("UPDATE incidents SET last_activity_at = opened_at")
    op.alter_column("incidents", "last_activity_at", nullable=False)


def downgrade() -> None:
    op.drop_column("incidents", "suspected_change")
    op.drop_column("incidents", "last_activity_at")
