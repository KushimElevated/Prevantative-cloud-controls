"""Immutability triggers and application-role privileges.

- Append-only tables reject UPDATE/DELETE (and audit_events rejects TRUNCATE).
- Submitted control/implementation revisions reject content changes and deletion.
- Change package manifests and digests cannot change after creation.
- The application role gets DML, but only SELECT/INSERT on audit_events and approval_decisions.

These protections are enforced through the application role. They are NOT proof against a
database administrator or the owner role, which can disable triggers.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

import os

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

APPEND_ONLY_TABLES = [
    "audit_events",
    "approval_decisions",
    "inventory_snapshots",
    "resource_snapshots",
    "request_fixture_sets",
    "assessment_runs",
    "assessment_results",
    "implementation_validations",
    "readiness_evidence",
    "handoff_bundles",
    "deployment_receipts",
    "observations",
]

REVISION_TABLES = ["control_revisions", "implementation_revisions"]

# Columns that may change after submission (lifecycle bookkeeping only).
REVISION_MUTABLE = "'status','lock_version','updated_at','approved_at','superseded_at','retired_at'"


def _app_role() -> str:
    role = os.environ.get("APP_DB_ROLE", "ccp_app")
    if not role.replace("_", "").isalnum():
        raise RuntimeError("APP_DB_ROLE must be alphanumeric/underscore")
    return role


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION ccp_forbid_mutation() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'ccp: % on % is not permitted (append-only table)', TG_OP, TG_TABLE_NAME
            USING ERRCODE = 'insufficient_privilege';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table in APPEND_ONLY_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION ccp_forbid_mutation();"
        )
    op.execute(
        "CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON audit_events "
        "FOR EACH STATEMENT EXECUTE FUNCTION ccp_forbid_mutation();"
    )

    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION ccp_protect_submitted_revision() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            IF OLD.status <> 'DRAFT' THEN
              RAISE EXCEPTION 'ccp: submitted revision % cannot be deleted', OLD.id
                USING ERRCODE = 'insufficient_privilege';
            END IF;
            RETURN OLD;
          END IF;
          IF OLD.status <> 'DRAFT' AND
             (to_jsonb(NEW) - ARRAY[{REVISION_MUTABLE}]) IS DISTINCT FROM
             (to_jsonb(OLD) - ARRAY[{REVISION_MUTABLE}]) THEN
            RAISE EXCEPTION 'ccp: submitted revision % is immutable', OLD.id
              USING ERRCODE = 'insufficient_privilege';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table in REVISION_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION ccp_protect_submitted_revision();"
        )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION ccp_protect_package_manifest() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'ccp: change packages cannot be deleted'
              USING ERRCODE = 'insufficient_privilege';
          END IF;
          IF NEW.manifest_digest IS DISTINCT FROM OLD.manifest_digest
             OR NEW.manifest IS DISTINCT FROM OLD.manifest
             OR NEW.created_by IS DISTINCT FROM OLD.created_by THEN
            RAISE EXCEPTION 'ccp: change package % manifest is immutable', OLD.id
              USING ERRCODE = 'insufficient_privilege';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER change_packages_immutable BEFORE UPDATE OR DELETE ON change_packages "
        "FOR EACH ROW EXECUTE FUNCTION ccp_protect_package_manifest();"
    )

    role = _app_role()
    op.execute(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
            EXECUTE 'GRANT USAGE ON SCHEMA public TO {role}';
            EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}';
            EXECUTE 'REVOKE ALL ON alembic_version FROM {role}';
            EXECUTE 'GRANT SELECT ON alembic_version TO {role}';
            EXECUTE 'REVOKE UPDATE, DELETE, TRUNCATE ON audit_events FROM {role}';
            EXECUTE 'REVOKE UPDATE, DELETE, TRUNCATE ON approval_decisions FROM {role}';
            EXECUTE 'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}';
          ELSE
            RAISE NOTICE 'ccp: application role {role} not found; privileges not granted';
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS change_packages_immutable ON change_packages")
    for table in REVISION_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_truncate ON audit_events")
    for table in APPEND_ONLY_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
    op.execute("DROP FUNCTION IF EXISTS ccp_protect_package_manifest()")
    op.execute("DROP FUNCTION IF EXISTS ccp_protect_submitted_revision()")
    op.execute("DROP FUNCTION IF EXISTS ccp_forbid_mutation()")
