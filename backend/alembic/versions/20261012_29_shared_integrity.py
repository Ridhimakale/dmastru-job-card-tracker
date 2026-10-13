"""Protect generated identifiers and deduplicate task reminders."""
from alembic import op
import sqlalchemy as sa

revision = "20261012_29"
down_revision = "20261012_28"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("notifications", sa.Column("dedupe_key", sa.String(180), nullable=True))
    op.create_index("ix_notifications_dedupe_key", "notifications", ["dedupe_key"], unique=True)
    op.add_column("audit_logs", sa.Column("entity_ref", sa.String(80), nullable=True))
    op.create_index("ix_audit_logs_entity_ref", "audit_logs", ["entity_ref"])
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("""
            CREATE OR REPLACE FUNCTION reject_business_number_update() RETURNS trigger AS $$
            BEGIN
                IF NEW.number IS DISTINCT FROM OLD.number THEN
                    RAISE EXCEPTION 'Generated business numbers are immutable';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """)
        for table in ("tickets", "enquiries", "job_cards"):
            op.execute(f"CREATE TRIGGER {table}_number_immutable BEFORE UPDATE OF number ON {table} FOR EACH ROW EXECUTE FUNCTION reject_business_number_update()")
    elif dialect == "sqlite":
        for table in ("tickets", "enquiries", "job_cards"):
            op.execute(f"CREATE TRIGGER {table}_number_immutable BEFORE UPDATE OF number ON {table} WHEN OLD.number IS NOT NEW.number BEGIN SELECT RAISE(ABORT, 'Generated business numbers are immutable'); END")

def downgrade():
    dialect = op.get_bind().dialect.name
    for table in ("tickets", "enquiries", "job_cards"):
        suffix=f" ON {table}" if dialect=="postgresql" else ""
        op.execute(f"DROP TRIGGER IF EXISTS {table}_number_immutable{suffix}")
    if dialect == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS reject_business_number_update()")
    op.drop_index("ix_audit_logs_entity_ref", table_name="audit_logs")
    op.drop_column("audit_logs", "entity_ref")
    op.drop_index("ix_notifications_dedupe_key", table_name="notifications")
    op.drop_column("notifications", "dedupe_key")
