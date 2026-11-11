"""NSFW group settings and strikes.

Revision ID: 20261111_000011
Revises: 20260726_000010
"""
from alembic import op
import sqlalchemy as sa

revision = "20261111_000011"
down_revision = "20260726_000010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "nsfw_group_settings",
        sa.Column("chat_id", sa.BigInteger(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("photo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("video", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("gif", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sensitivity", sa.Text(), nullable=False, server_default="balanced"),
        sa.Column("action", sa.Text(), nullable=False, server_default="ban"),
    )
    op.create_table(
        "nsfw_strikes",
        sa.Column("chat_id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), primary_key=True),
        sa.Column("strikes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_nsfw_strikes_updated_at", "nsfw_strikes", ["updated_at"])


def downgrade():
    op.drop_index("ix_nsfw_strikes_updated_at", table_name="nsfw_strikes")
    op.drop_table("nsfw_strikes")
    op.drop_table("nsfw_group_settings")
