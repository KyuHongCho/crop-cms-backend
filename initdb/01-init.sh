#!/bin/bash
# Runs ONCE, on first boot of an empty data directory, as the Postgres superuser.
# Two jobs the application role cannot do for itself:
#   1. pgvector is not a "trusted" extension - only a superuser may install it.
#   2. Creating the least-privilege application role.
# Credentials come from the environment, so no secret is stored in this file.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
    CREATE EXTENSION IF NOT EXISTS vector;

    CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASSWORD}';
    GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO ${DB_USER};
    -- PostgreSQL 15+ no longer grants CREATE on schema public to PUBLIC,
    -- so the application role needs it explicitly in order to run migrations.
    GRANT USAGE, CREATE ON SCHEMA public TO ${DB_USER};
SQL
