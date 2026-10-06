"""078 — customer portal tables: portal_user, portal_license, portal_instance, portal_magic_link, portal_session

Revision ID: 078_portal
Revises: 077_byok_audit
Create Date: 2026-10-05
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "078_portal"
down_revision = "077"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portal_user",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("email", sa.Text, nullable=False, unique=True),
        sa.Column("ls_customer_id", sa.Text, nullable=True),
        sa.Column("ls_order_id", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_portal_user_email", "portal_user", ["email"])
    op.create_index("ix_portal_user_ls_customer_id", "portal_user", ["ls_customer_id"])

    op.create_table(
        "portal_license",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("portal_user_id", sa.Integer, sa.ForeignKey("portal_user.id"), nullable=False),
        sa.Column("tier", sa.Text, nullable=False),
        sa.Column("jwt_token", sa.Text, nullable=False),
        sa.Column("max_instances", sa.Integer, nullable=False, server_default="1"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_portal_license_portal_user_id", "portal_license", ["portal_user_id"])
    op.create_index("ix_portal_license_jwt_token", "portal_license", ["jwt_token"])

    op.create_table(
        "portal_instance",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("license_id", sa.Integer, sa.ForeignKey("portal_license.id"), nullable=False),
        sa.Column("fingerprint", sa.Text, nullable=False),
        sa.Column("hostname", sa.Text, nullable=True),
        sa.Column("platform_version", sa.Text, nullable=True),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean, nullable=False, server_default="false"),
    )
    op.create_index("ix_portal_instance_license_id", "portal_instance", ["license_id"])
    op.create_index("ix_portal_instance_fingerprint", "portal_instance", ["fingerprint"])

    op.create_table(
        "portal_magic_link",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("portal_user_id", sa.Integer, sa.ForeignKey("portal_user.id"), nullable=False),
        sa.Column("token_hash", sa.Text, nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_portal_magic_link_portal_user_id", "portal_magic_link", ["portal_user_id"])
    op.create_index("ix_portal_magic_link_token_hash", "portal_magic_link", ["token_hash"], unique=True)

    op.create_table(
        "portal_session",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("portal_user_id", sa.Integer, sa.ForeignKey("portal_user.id"), nullable=False),
        sa.Column("token_hash", sa.Text, nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_portal_session_portal_user_id", "portal_session", ["portal_user_id"])
    op.create_index("ix_portal_session_token_hash", "portal_session", ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_table("portal_session")
    op.drop_table("portal_magic_link")
    op.drop_table("portal_instance")
    op.drop_table("portal_license")
    op.drop_table("portal_user")
