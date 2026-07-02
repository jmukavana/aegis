"""incident_memory table for Phase 2 RAG (pgvector)

Revision ID: 0003_incident_memory
Revises: 0002_incident_lifecycle
Create Date: 2026-07-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision: str = "0003_incident_memory"
down_revision: Union[str, Sequence[str], None] = "0002_incident_lifecycle"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Keep in lockstep with EMBED_DIM in infrastructure/persistence/incident_memory.py
# and the embedding_model setting (nomic-embed-text -> 768).
EMBED_DIM = 768


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "incident_memory",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("fingerprint", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("summary", sa.String(), nullable=False),
        sa.Column("root_cause", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("embedding", Vector(EMBED_DIM), nullable=False),
    )
    op.create_index(
        "ix_incident_memory_incident_id", "incident_memory", ["incident_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_incident_memory_incident_id", table_name="incident_memory")
    op.drop_table("incident_memory")
    # Leave the `vector` extension installed — other objects may rely on it.
