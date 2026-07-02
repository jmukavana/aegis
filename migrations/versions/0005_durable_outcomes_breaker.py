"""durable outcome store + circuit breaker (Phase 4c)

Revision ID: 0005_durable_outcomes
Revises: 0004_remediation_audit
Create Date: 2026-07-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005_durable_outcomes"
down_revision: Union[str, Sequence[str], None] = "0004_remediation_audit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The trust flywheel — one row per verified outcome, so success rates survive restarts.
    op.create_table(
        "remediation_outcomes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("runbook_id", sa.String(), nullable=False),
        sa.Column("fingerprint", sa.String(), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_remediation_outcomes_runbook_fingerprint",
        "remediation_outcomes",
        ["runbook_id", "fingerprint"],
    )

    # Circuit-breaker action log — one row per executed action, windowed at query time.
    op.create_table(
        "circuit_breaker_actions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("acted_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_circuit_breaker_actions_incident_id",
        "circuit_breaker_actions",
        ["incident_id"],
    )


def downgrade() -> None:
    op.drop_table("circuit_breaker_actions")
    op.drop_table("remediation_outcomes")
