"""create urls table

Revision ID: 0001
Revises:
Create Date: 2026-07-18

"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "urls",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("alias", sa.String(length=64), nullable=False),
        sa.Column("long_url", sa.Text(), nullable=False),
        sa.Column("is_custom", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("access_count", sa.BigInteger(), nullable=False, server_default="0"),
    )
    # Unique index backs both fast redirect lookups and ON CONFLICT handling.
    op.create_index("ix_urls_alias", "urls", ["alias"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_urls_alias", table_name="urls")
    op.drop_table("urls")
