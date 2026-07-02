"""remediation_plans + audit_entries tables (Phase 3b)

Revision ID: 0004_remediation_audit
Revises: 0003_incident_memory
Create Date: 2026-07-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_remediation_audit"
down_revision: Union[str, Sequence[str], None] = "0003_incident_memory"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "remediation_plans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("runbook_id", sa.String(), nullable=False),
        sa.Column("environment", sa.String(), nullable=False),
        sa.Column("risk_tier", sa.String(), nullable=False),
        sa.Column("requested_by", sa.String(), nullable=False),
        sa.Column("params_json", sa.String(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dry_run_effect", sa.String()),
        sa.Column("dry_run_reversible", sa.Boolean()),
        sa.Column("decision_outcome", sa.String()),
        sa.Column("decision_reasons_json", sa.String()),
        sa.Column("required_permission", sa.String()),
        sa.Column("approver", sa.String()),
        sa.Column("approval_permission", sa.String()),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_remediation_plans_incident_id", "remediation_plans", ["incident_id"]
    )

    op.create_table(
        "audit_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("event", sa.String(), nullable=False),
        sa.Column("detail", sa.String(), nullable=False),
    )
    op.create_index("ix_audit_entries_incident_id", "audit_entries", ["incident_id"])
    op.create_index("ix_audit_entries_plan_id", "audit_entries", ["plan_id"])


def downgrade() -> None:
    op.drop_table("audit_entries")
    op.drop_table("remediation_plans")
