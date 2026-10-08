#!/bin/sh
# Creates separate migration (owner) and application roles.
# The application role receives DML privileges from the Alembic migration, but only
# SELECT/INSERT on audit_events, so audit history is append-only through the app.
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE ROLE ${CCP_OWNER_USER} LOGIN PASSWORD '${CCP_OWNER_PASSWORD}';
CREATE ROLE ${CCP_APP_USER} LOGIN PASSWORD '${CCP_APP_PASSWORD}';
CREATE DATABASE ${CCP_DB} OWNER ${CCP_OWNER_USER};
CREATE DATABASE ${CCP_DB}_test OWNER ${CCP_OWNER_USER};
SQL

for db in "${CCP_DB}" "${CCP_DB}_test"; do
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$db" <<SQL
REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO ${CCP_OWNER_USER};
GRANT USAGE ON SCHEMA public TO ${CCP_APP_USER};
REVOKE CREATE ON SCHEMA public FROM ${CCP_APP_USER};
SQL
done
