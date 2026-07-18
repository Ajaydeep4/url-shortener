"""add expires_at for link TTL expiration

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-18

"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("urls", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("urls", "expires_at")
