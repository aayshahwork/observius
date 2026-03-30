#!/bin/bash
set -e

export PGPASSWORD="$DB_PASSWORD"

# Wait for postgres
until pg_isready -h "$DB_HOST" -p "${DB_PORT:-5432}" -U "$DB_USER"; do
  echo "Waiting for postgres..."
  sleep 1
done

# Create migrations tracking table if not exists
psql -h "$DB_HOST" -p "${DB_PORT:-5432}" -U "$DB_USER" -d "$DB_NAME" -c "
  CREATE TABLE IF NOT EXISTS _migrations (
    filename TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ DEFAULT now()
  );
"

# Apply each migration in order
for f in /migrations/*.sql; do
  fname=$(basename "$f")
  already=$(psql -h "$DB_HOST" -p "${DB_PORT:-5432}" -U "$DB_USER" -d "$DB_NAME" -tAc \
    "SELECT 1 FROM _migrations WHERE filename = '$fname'")
  if [ "$already" != "1" ]; then
    echo "Applying $fname..."
    psql -h "$DB_HOST" -p "${DB_PORT:-5432}" -U "$DB_USER" -d "$DB_NAME" -f "$f"
    psql -h "$DB_HOST" -p "${DB_PORT:-5432}" -U "$DB_USER" -d "$DB_NAME" -c \
      "INSERT INTO _migrations (filename) VALUES ('$fname')"
    echo "Applied $fname"
  else
    echo "Skipping $fname (already applied)"
  fi
done

echo "All migrations applied."
