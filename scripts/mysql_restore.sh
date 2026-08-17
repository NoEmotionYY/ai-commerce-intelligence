#!/bin/sh
set -eu

: "${MYSQL_HOST:?MYSQL_HOST is required}"
: "${MYSQL_DATABASE:?MYSQL_DATABASE is required}"
: "${MYSQL_USER:?MYSQL_USER is required}"
: "${MYSQL_PASSWORD:?MYSQL_PASSWORD is required}"
: "${RESTORE_BACKUP_FILE:?RESTORE_BACKUP_FILE is required}"
: "${EXPECTED_SCHEMA_HEAD:?EXPECTED_SCHEMA_HEAD is required}"
: "${EXPECTED_BACKUP_MANIFEST_SHA256:?EXPECTED_BACKUP_MANIFEST_SHA256 is required}"

case "$MYSQL_DATABASE" in
    ""|*[!A-Za-z0-9_]*) echo "MYSQL_DATABASE is invalid" >&2; exit 2 ;;
esac
case "$EXPECTED_SCHEMA_HEAD" in
    ""|*[!A-Za-z0-9_]*) echo "EXPECTED_SCHEMA_HEAD is invalid" >&2; exit 2 ;;
esac
if [ "${#EXPECTED_BACKUP_MANIFEST_SHA256}" -ne 64 ]; then
    echo "EXPECTED_BACKUP_MANIFEST_SHA256 is invalid" >&2
    exit 2
fi
case "$EXPECTED_BACKUP_MANIFEST_SHA256" in
    *[!0-9a-f]*) echo "EXPECTED_BACKUP_MANIFEST_SHA256 is invalid" >&2; exit 2 ;;
esac

if [ "$RESTORE_CONFIRM" != "RESTORE_$MYSQL_DATABASE" ]; then
    echo "RESTORE_CONFIRM must equal RESTORE_$MYSQL_DATABASE" >&2
    exit 2
fi
case "$RESTORE_BACKUP_FILE" in
    *[!A-Za-z0-9._-]*|.*|*..*|"") echo "RESTORE_BACKUP_FILE is invalid" >&2; exit 2 ;;
    *.sql) ;;
    *) echo "RESTORE_BACKUP_FILE must end in .sql" >&2; exit 2 ;;
esac

source_file="/backups/$RESTORE_BACKUP_FILE"
metadata_file="$source_file.meta"
checksum_file="$source_file.sha256"
if [ ! -f "$source_file" ] || [ ! -f "$metadata_file" ] || [ ! -f "$checksum_file" ]; then
    echo "Backup package is incomplete" >&2
    exit 3
fi

staging_directory=$(mktemp -d /tmp/commerce-restore.XXXXXX)
cleanup_staging() {
    rm -rf "$staging_directory"
}
trap cleanup_staging EXIT HUP INT TERM
cp "$source_file" "$staging_directory/$RESTORE_BACKUP_FILE"
cp "$metadata_file" "$staging_directory/$RESTORE_BACKUP_FILE.meta"
cp "$checksum_file" "$staging_directory/$RESTORE_BACKUP_FILE.sha256"
chmod 0400 "$staging_directory/$RESTORE_BACKUP_FILE" \
    "$staging_directory/$RESTORE_BACKUP_FILE.meta" \
    "$staging_directory/$RESTORE_BACKUP_FILE.sha256"
source_file="$staging_directory/$RESTORE_BACKUP_FILE"
metadata_file="$source_file.meta"
checksum_file="$source_file.sha256"

actual_manifest_digest=$(sha256sum "$checksum_file" | awk '{print $1}')
if [ "$actual_manifest_digest" != "$EXPECTED_BACKUP_MANIFEST_SHA256" ]; then
    echo "Backup manifest source digest does not match the deployment record" >&2
    exit 4
fi

normalized_checksum_file="$staging_directory/normalized.sha256"
sed 's/\r$//' "$checksum_file" > "$normalized_checksum_file"
chmod 0400 "$normalized_checksum_file"
checksum_file="$normalized_checksum_file"

if ! awk -v dump="$RESTORE_BACKUP_FILE" -v metadata="$RESTORE_BACKUP_FILE.meta" '
    {
        lines += 1
        hash = substr($0, 1, 64)
        separator = substr($0, 65, 2)
        name = substr($0, 67)
        if (length(hash) != 64 || hash ~ /[^0-9a-f]/ || separator != "  ") bad = 1
        if (name == dump) dump_count += 1
        else if (name == metadata) metadata_count += 1
        else bad = 1
    }
    END { exit !(lines == 2 && dump_count == 1 && metadata_count == 1 && bad != 1) }
' "$checksum_file"; then
    echo "Backup checksum manifest is invalid" >&2
    exit 4
fi
if ! (cd "$staging_directory" && sha256sum --check "$checksum_file"); then
    echo "Backup package checksum verification failed" >&2
    exit 4
fi
metadata_format=$(sed -n 's/^format=//p' "$metadata_file")
metadata_database=$(sed -n 's/^database=//p' "$metadata_file")
metadata_head=$(sed -n 's/^schema_head=//p' "$metadata_file")
metadata_charset=$(sed -n 's/^character_set=//p' "$metadata_file")
metadata_collation=$(sed -n 's/^collation=//p' "$metadata_file")
case "$metadata_charset" in
    ""|*[!A-Za-z0-9_]*) echo "Backup character set is invalid" >&2; exit 4 ;;
esac
case "$metadata_collation" in
    ""|*[!A-Za-z0-9_]*) echo "Backup collation is invalid" >&2; exit 4 ;;
esac
if [ "$metadata_format" != "commerce-mysql-backup-v1" ] || \
   [ "$metadata_database" != "$MYSQL_DATABASE" ] || \
   [ "$metadata_head" != "$EXPECTED_SCHEMA_HEAD" ]; then
    echo "Backup package metadata does not match this deployment" >&2
    exit 4
fi

export MYSQL_PWD="$MYSQL_PASSWORD"
mysql --host="$MYSQL_HOST" --user="$MYSQL_USER" \
    --execute="DROP DATABASE IF EXISTS \`$MYSQL_DATABASE\`; CREATE DATABASE \`$MYSQL_DATABASE\` CHARACTER SET $metadata_charset COLLATE $metadata_collation;"
mysql --host="$MYSQL_HOST" --user="$MYSQL_USER" --database="$MYSQL_DATABASE" \
    --execute="CREATE TABLE deployment_restore_state (id TINYINT PRIMARY KEY, status VARCHAR(20) NOT NULL, started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP); INSERT INTO deployment_restore_state (id, status) VALUES (1, 'RESTORING');"
if ! mysql --host="$MYSQL_HOST" --user="$MYSQL_USER" --database="$MYSQL_DATABASE" < "$source_file"; then
    echo "Restore failed; deployment remains not ready until a verified package succeeds" >&2
    exit 5
fi
restored_head=$(mysql --host="$MYSQL_HOST" --user="$MYSQL_USER" --database="$MYSQL_DATABASE" \
    --batch --skip-column-names \
    --execute="SELECT version_num FROM alembic_version;")
if [ "$restored_head" != "$EXPECTED_SCHEMA_HEAD" ]; then
    echo "Restored database schema head is incorrect" >&2
    exit 5
fi
mysql --host="$MYSQL_HOST" --user="$MYSQL_USER" --database="$MYSQL_DATABASE" \
    --execute="DROP TABLE deployment_restore_state;"
trap - EXIT HUP INT TERM
cleanup_staging
echo "$restored_head"
