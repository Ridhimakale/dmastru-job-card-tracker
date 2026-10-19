"""Require authenticator-app MFA for sign-in."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261017_44"
down_revision = "20261017_43"
branch_labels = None
depends_on = None


def upgrade():
    inspector = inspect(op.get_bind())
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    session_columns = {column["name"] for column in inspector.get_columns("auth_sessions")}
    with op.batch_alter_table("users") as batch:
        if "mfa_secret_encrypted" not in user_columns:
            batch.add_column(sa.Column("mfa_secret_encrypted", sa.String(length=512), nullable=True))
        if "mfa_enabled" not in user_columns:
            batch.add_column(sa.Column("mfa_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
        if "mfa_last_counter" not in user_columns:
            batch.add_column(sa.Column("mfa_last_counter", sa.Integer(), nullable=True))
    with op.batch_alter_table("auth_sessions") as batch:
        if "mfa_verified" not in session_columns:
            batch.add_column(sa.Column("mfa_verified", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute("UPDATE auth_sessions SET revoked = TRUE")
    op.create_table(
        "auth_mfa_challenges",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_auth_mfa_challenges_token_hash", "auth_mfa_challenges", ["token_hash"], unique=True)
    op.create_index("ix_auth_mfa_challenges_user_id", "auth_mfa_challenges", ["user_id"])
    op.create_index("ix_auth_mfa_challenges_expires_at", "auth_mfa_challenges", ["expires_at"])


def downgrade():
    op.drop_index("ix_auth_mfa_challenges_expires_at", table_name="auth_mfa_challenges")
    op.drop_index("ix_auth_mfa_challenges_user_id", table_name="auth_mfa_challenges")
    op.drop_index("ix_auth_mfa_challenges_token_hash", table_name="auth_mfa_challenges")
    op.drop_table("auth_mfa_challenges")
    with op.batch_alter_table("auth_sessions") as batch:
        batch.drop_column("mfa_verified")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("mfa_last_counter")
        batch.drop_column("mfa_enabled")
        batch.drop_column("mfa_secret_encrypted")
