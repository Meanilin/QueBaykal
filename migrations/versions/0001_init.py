"""init: users, chats, chat_members, tv_devices, chat_tv_bindings

Revision ID: 0001_init
Revises:
Create Date: 2026-09-07

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0001_init"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Enums
    user_role = sa.Enum("member", "admin", "root", name="user_role")
    user_role.create(op.get_bind(), checkfirst=True)
    tv_device_status = sa.Enum("pending", "online", "offline", "disabled", name="tv_device_status")
    tv_device_status.create(op.get_bind(), checkfirst=True)
    chat_tv_binding_status = sa.Enum("active", "archived", name="chat_tv_binding_status")
    chat_tv_binding_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "chats",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("title", sa.String(length=256), nullable=True),
        sa.Column("chat_type", sa.String(length=16), nullable=False),
        sa.Column("is_private", sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column("settings_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "tv_devices",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("device_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column("display_name", sa.String(length=128), nullable=True),
        sa.Column("hostname", sa.String(length=128), nullable=True),
        sa.Column("agent_token_hash", sa.String(length=128), nullable=True),
        sa.Column("status", sa.Enum("pending", "online", "offline", "disabled", name="tv_device_status"), nullable=False, server_default=sa.text('pending')),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("full_name", sa.String(length=256), nullable=True),
        sa.Column("language_code", sa.String(length=8), nullable=True),
        sa.Column("is_blocked", sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_username", "users", ["username"])

    op.create_table(
        "chat_members",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("chat_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.Enum("member", "admin", "root", name="user_role"), nullable=False, server_default=sa.text('member')),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete='CASCADE'),
    )
    op.create_unique_constraint("uq_chat_members_chat_user", "chat_members", ["chat_id", "user_id"])
    op.create_index("ix_chat_members_user", "chat_members", ["user_id"])
    op.create_index("ix_chat_members_chat_id", "chat_members", ["chat_id"])

    op.create_table(
        "chat_tv_bindings",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("chat_id", sa.Integer(), nullable=False),
        sa.Column("tv_device_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.Enum("active", "archived", name="chat_tv_binding_status"), nullable=False, server_default=sa.text('active')),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tv_device_id"], ["tv_devices.id"], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete='CASCADE'),
    )
    op.create_index("ix_chat_tv_bindings_chat_active", "chat_tv_bindings", ["chat_id", "status"])


def downgrade() -> None:
    op.drop_table("chat_tv_bindings")
    op.drop_table("chat_members")
    op.drop_table("users")
    op.drop_table("tv_devices")
    op.drop_table("chats")
    sa.Enum(name="user_role").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="tv_device_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="chat_tv_binding_status").drop(op.get_bind(), checkfirst=True)
