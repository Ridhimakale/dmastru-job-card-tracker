"""Add password reset and refresh-session fields."""
from alembic import op
import sqlalchemy as sa

revision = "20260825_02"
down_revision = "20260825_01"
branch_labels = None
depends_on = None

def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("reset_token_hash",sa.String(128),nullable=True))
        batch.add_column(sa.Column("reset_expires_at",sa.DateTime(timezone=True),nullable=True))
        batch.create_index("ix_users_reset_token_hash",["reset_token_hash"])
    with op.batch_alter_table("auth_sessions") as batch:
        batch.add_column(sa.Column("refresh_token_hash",sa.String(128),nullable=True))
        batch.add_column(sa.Column("refresh_expires_at",sa.DateTime(timezone=True),nullable=True))
        batch.create_index("ix_auth_sessions_refresh_token_hash",["refresh_token_hash"])
def downgrade():
    with op.batch_alter_table("auth_sessions") as batch:
        batch.drop_index("ix_auth_sessions_refresh_token_hash"); batch.drop_column("refresh_expires_at"); batch.drop_column("refresh_token_hash")
    with op.batch_alter_table("users") as batch:
        batch.drop_index("ix_users_reset_token_hash"); batch.drop_column("reset_expires_at"); batch.drop_column("reset_token_hash")
