#!/bin/sh
set -eu

umask 077
: "${MYSQL_HOST:?MYSQL_HOST is required}"
: "${MYSQL_DATABASE:?MYSQL_DATABASE is required}"
: "${MYSQL_USER:?MYSQL_USER is required}"
: "${MYSQL_PASSWORD:?MYSQL_PASSWORD is required}"

case "$MYSQL_DATABASE" in
    ""|*[!A-Za-z0-9_]*) echo "MYSQL_DATABASE is invalid" >&2; exit 2 ;;
esac

case "${BACKUP_NAME:-}" in
    "") backup_name="commerce-$(date -u +%Y%m%dT%H%M%SZ).sql" ;;
    *[!A-Za-z0-9._-]*|.*|*..*) echo "BACKUP_NAME is invalid" >&2; exit 2 ;;
    *) backup_name="$BACKUP_NAME" ;;
esac

case "$backup_name" in
    *.sql) ;;
    *) echo "BACKUP_NAME must end in .sql" >&2; exit 2 ;;
esac

target="/backups/$backup_name"
temporary="/backups/.$backup_name.tmp"
metadata="$target.meta"
metadata_temporary="/backups/.$backup_name.meta.tmp"
checksum="$target.sha256"
checksum_temporary="/backups/.$backup_name.sha256.tmp"
if [ -e "$target" ] || [ -e "$metadata" ] || [ -e "$checksum" ] || \
   [ -e "$temporary" ] || [ -e "$metadata_temporary" ] || [ -e "$checksum_temporary" ]; then
    echo "Backup target already exists" >&2
    exit 3
fi

export MYSQL_PWD="$MYSQL_PASSWORD"
restore_state_count=$(mysql --host="$MYSQL_HOST" --user="$MYSQL_USER" --database="$MYSQL_DATABASE" \
    --batch --skip-column-names \
    --execute="SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = 'deployment_restore_state';")
if [ "$restore_state_count" != "0" ]; then
    echo "Database restore state is not clean" >&2
    exit 4
fi
schema_head_before=$(mysql --host="$MYSQL_HOST" --user="$MYSQL_USER" --database="$MYSQL_DATABASE" \
    --batch --skip-column-names --execute="SELECT version_num FROM alembic_version;")
schema_charset=$(mysql --host="$MYSQL_HOST" --user="$MYSQL_USER" --database="$MYSQL_DATABASE" \
    --batch --skip-column-names --execute="SELECT @@character_set_database;")
schema_collation=$(mysql --host="$MYSQL_HOST" --user="$MYSQL_USER" --database="$MYSQL_DATABASE" \
    --batch --skip-column-names --execute="SELECT @@collation_database;")
case "$schema_head_before" in
    ""|*[!A-Za-z0-9_]*) echo "Database schema head is invalid" >&2; exit 4 ;;
esac
case "$schema_charset" in
    ""|*[!A-Za-z0-9_]*) echo "Database character set is invalid" >&2; exit 4 ;;
esac
case "$schema_collation" in
    ""|*[!A-Za-z0-9_]*) echo "Database collation is invalid" >&2; exit 4 ;;
esac
complete=0
cleanup() {
    rm -f "$temporary" "$metadata_temporary" "$checksum_temporary"
    if [ "$complete" -ne 1 ]; then
        rm -f "$target" "$metadata" "$checksum"
    fi
}
trap cleanup EXIT HUP INT TERM
mysqldump \
    --host="$MYSQL_HOST" \
    --user="$MYSQL_USER" \
    --single-transaction \
    --triggers \
    --events \
    --set-gtid-purged=OFF \
    --no-tablespaces \
    "$MYSQL_DATABASE" > "$temporary"
schema_head_after=$(mysql --host="$MYSQL_HOST" --user="$MYSQL_USER" --database="$MYSQL_DATABASE" \
    --batch --skip-column-names --execute="SELECT version_num FROM alembic_version;")
schema_charset_after=$(mysql --host="$MYSQL_HOST" --user="$MYSQL_USER" --database="$MYSQL_DATABASE" \
    --batch --skip-column-names --execute="SELECT @@character_set_database;")
schema_collation_after=$(mysql --host="$MYSQL_HOST" --user="$MYSQL_USER" --database="$MYSQL_DATABASE" \
    --batch --skip-column-names --execute="SELECT @@collation_database;")
if [ "$schema_head_after" != "$schema_head_before" ] || \
   [ "$schema_charset_after" != "$schema_charset" ] || \
   [ "$schema_collation_after" != "$schema_collation" ]; then
    echo "Database schema metadata changed during backup" >&2
    exit 4
fi
{
    echo "format=commerce-mysql-backup-v1"
    echo "database=$MYSQL_DATABASE"
    echo "schema_head=$schema_head_before"
    echo "character_set=$schema_charset"
    echo "collation=$schema_collation"
} > "$metadata_temporary"
dump_hash=$(sha256sum "$temporary" | awk '{print $1}')
metadata_hash=$(sha256sum "$metadata_temporary" | awk '{print $1}')
{
    echo "$dump_hash  $backup_name"
    echo "$metadata_hash  $backup_name.meta"
} > "$checksum_temporary"
chmod 600 "$temporary" "$metadata_temporary" "$checksum_temporary"
mv "$temporary" "$target"
mv "$metadata_temporary" "$metadata"
mv "$checksum_temporary" "$checksum"
complete=1
trap - EXIT HUP INT TERM
manifest_digest=$(sha256sum "$checksum" | awk '{print $1}')
echo "$backup_name"
echo "manifest_sha256=$manifest_digest"
