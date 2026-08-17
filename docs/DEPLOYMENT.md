# Production Deployment

## Release Topology

The supported initial V2 production path is `docker-compose.production.yml`:

```text
HTTPS client
  -> Nginx TLS reverse proxy
     -> V2 Streamlit internal/admin UI
     -> FastAPI V2 API
        -> MySQL 8.4 persistent volume
```

The production Compose file contains no Mock ERP, Crawler, mock competitor site, fixture seed, or
legacy Streamlit service. MySQL, FastAPI, and Streamlit are not published directly to the host;
only the TLS proxy is exposed. `Dockerfile.production` runs application processes as an
unprivileged user with a read-only filesystem and a bounded temporary filesystem.

The database boundary uses four distinct deployment-owned identities: runtime DML, Alembic
migration, backup, and restore. The one-shot `db-users` service reconciles those least-privilege
grants before migration. The API receives only the runtime `DATABASE_URL`; it does not receive the
migration, backup, restore, or MySQL root password. MySQL and Nginx release images are pinned by
tag and manifest digest in the production Compose file. The Python 3.12 base is also digest-pinned,
and `requirements.production.lock` freezes the reviewed Linux runtime dependency graph used by the
release image. Updating the lock or any pinned digest is a release change that requires the full
deployment and security gates again.

The restore identity is intentionally high impact: in addition to schema privileges it receives
MySQL 8.4 `SET_ANY_DEFINER` so an audited dump can recreate Alembic-managed triggers with their
original migration definer. That credential must be available only to the stopped-traffic
maintenance profile; it is never passed to the API or frontend.

The current release UI is the authenticated V2 Streamlit internal/admin client over production V2
APIs. It is not represented as a public SaaS frontend or a React/Next.js implementation. The
backend product loop and browser release gate do not depend on a frontend rewrite. React/Next.js
remains a future product-UX milestone.

Redis and a background Worker are not configured because the current bounded request-time and sync
workloads do not demonstrate a mandatory queue workload. Add them only when a measured workload
requires durable asynchronous execution.

## Prerequisites

- Docker Engine 29 or compatible Docker Desktop;
- Docker Compose v2;
- Python 3.12 and a clean repository checkout on the release-verification workstation;
- DNS for the merchant/admin hostname;
- a TLS certificate and private key readable by Docker;
- an OCI registry/repository that preserves immutable application image digests;
- a deployment-owned MySQL backup directory on durable storage;
- independent random MySQL root, runtime, migration, backup, restore, signing, and
  credential-encryption secrets.

All command examples below use a POSIX shell. On Windows hosts, run them from WSL or Git Bash;
do not paste Bash continuations, substitutions, or permission checks directly into PowerShell.

Create the verification environment from the frozen source on a release workstation (not inside
the production host's application filesystem). This matches the Python 3.12 CI dependency
constraint; do not run the migration or full release verifiers from an unrelated global Python:

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -c requirements.production.lock -c requirements.ci.lock -e ".[dev]"
python -m playwright install --with-deps chromium
```

The browser download is mandatory for the authenticated release smoke gate. Installing the Python
package alone does not install Chromium. On a Windows release workstation use
`python -m playwright install chromium`; the Linux/WSL command above also installs the required OS
libraries and may require deployment-workstation administrator privileges.

The isolated backup-package verifier itself uses only Python's standard library plus Docker, but it
must still be run from the matching frozen checkout so it mounts the reviewed restore script.

Copy `.env.production.example` to a deployment-managed path outside version control. Replace every
placeholder. Set `COMMERCE_APP_IMAGE` to the reviewed registry digest, not a mutable tag. Configure
`DATABASE_URL` with the runtime identity and `MIGRATION_DATABASE_URL` with
the migration identity. Passwords for the four service identities must be at least 32 characters
and use only `A-Z`, `a-z`, digits, `.`, `_`, `~`, or `-`; this keeps SQL role provisioning and URL
handling unambiguous. Generate independent values and do not reuse database, token-signing, or
credential-encryption keys.

Materialize the env file from the deployment secret store only on the target host. On Linux, use a
deployment-owned directory and mode `0600`; verify the result before Compose reads it:

```bash
install -d -m 0700 /secure
install -m 0600 /path/to/populated-commerce-production.env /secure/commerce-production.env
test "$(stat -c '%a' /secure/commerce-production.env)" = "600"
```

On Windows, place it outside the repository, disable inherited ACLs, and grant only the deployment
account and required administrators access (verify with `icacls`). Do not upload the materialized
file as a CI artifact or include it in general host backups. `.gitignore` and `.dockerignore` reject
`.env.*` as defense in depth, but filesystem permissions remain mandatory. Treat membership in the
Docker daemon/Administrators group as secret access because those operators can inspect container
configuration. Apply equivalent ACLs to TLS private keys and the backup directory; keep the source
of truth in an audited secret manager.

Set `BACKUP_UID` and `BACKUP_GID` to the numeric owner of the deployment backup directory. The
maintenance backup container uses these values so the operator can read the generated package
without making the directory world-readable; do not leave the Compose default `0:0` on a non-root
host. The release verifier derives the current host UID/GID automatically.

Example secret generation:

```bash
python -c "import base64,secrets; print(secrets.token_urlsafe(48)); print(base64.b64encode(secrets.token_bytes(32)).decode())"
```

`CREDENTIAL_ENCRYPTION_KEYS` is a JSON object whose values are base64-encoded 32-byte AES keys. The
active id must exist in that object. Keep old keys during rotation until all stored credentials have
been re-encrypted. `AUTH_SIGNING_KEY` must contain at least 32 random bytes.

If `LLM_PROVIDER=deepseek` or `openai`, the corresponding API key is mandatory. Empty webhook
registries disable deployment-level webhook routing; a configured registry is validated at startup.
Legacy ERP/Crawler variables are intentionally absent from this release path.

## Start And Upgrade

Download `production-image-<commit>` from the successful security workflow for the frozen source.
That artifact is the exact image already scanned by Trivy; do not rebuild it on the production host.
Keep its four files together: `commerce-v2-rc-image.tar.gz`,
`commerce-v2-rc-image.tar.gz.sha256`, `production-image-id.txt`, and
`production-source-revision.txt`.
Verify and load the archive, compare its Docker image ID and source revision to the artifact record,
then tag/push that loaded image and put its registry digest in the deployment env file as
`COMMERCE_APP_IMAGE`. Never deploy `latest` or another mutable tag.

```bash
RC_REVISION=<frozen-40-character-commit>
python scripts/verify_release_image_artifact.py \
  --artifact-dir . \
  --expected-revision "${RC_REVISION}" \
  --load
EXPECTED_IMAGE_ID=$(cat production-image-id.txt)
ACTUAL_REVISION=$(docker image inspect \
  --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' \
  "commerce-v2-rc:${RC_REVISION}")
test "${ACTUAL_REVISION}" = "${RC_REVISION}"

RC_REPOSITORY="registry.example.com/commerce/copilot"
RC_IMAGE="${RC_REPOSITORY}:${RC_REVISION}"
docker tag "commerce-v2-rc:${RC_REVISION}" "${RC_IMAGE}"
docker push "${RC_IMAGE}"
REGISTRY_INSPECT=$(docker buildx imagetools inspect "${RC_IMAGE}")
REGISTRY_MANIFEST_DIGEST=$(printf '%s\n' "${REGISTRY_INSPECT}" | \
  awk '$1 == "Digest:" { print $2; exit }')
printf '%s\n' "${REGISTRY_MANIFEST_DIGEST}" | grep -Eq '^sha256:[0-9a-f]{64}$'
docker buildx imagetools inspect --raw \
  "${RC_REPOSITORY}@${REGISTRY_MANIFEST_DIGEST}" > /secure/commerce-registry-manifest.json
REGISTRY_CONFIG_DIGEST=$(python -c \
  'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["config"]["digest"])' \
  /secure/commerce-registry-manifest.json)
test "${REGISTRY_CONFIG_DIGEST}" = "${EXPECTED_IMAGE_ID}"
COMMERCE_APP_IMAGE="${RC_REPOSITORY}@${REGISTRY_MANIFEST_DIGEST}"
# Persist COMMERCE_APP_IMAGE in the protected env file and release record; never deploy the tag.

docker compose --env-file /secure/commerce-production.env \
  -f docker-compose.production.yml config --quiet
docker compose --env-file /secure/commerce-production.env \
  -f docker-compose.production.yml pull
docker compose --env-file /secure/commerce-production.env \
  -f docker-compose.production.yml up -d --no-build --wait
```

The verifier authenticates the archive checksum, requires one image/config/OCI manifest, hashes
the recorded config blob, checks the source-revision label, loads the archive, and validates the
loaded identity through `docker image load`. This intentionally does not compare
`docker image inspect .Id` directly across
hosts: the classic Docker store reports the config digest there, while Docker Desktop's containerd
store reports the OCI manifest digest. Both digests are independently bound and verified from the
same checksummed archive before either representation is accepted.

The tag lookup above is used only once: it captures a manifest digest, then every subsequent lookup
and deployment uses `<repository>@<digest>`. The registry manifest's `config.digest` must equal the
Trivy-scanned Docker image ID, and the local image revision label must equal the frozen commit;
this fails closed if the tag was overwritten with different content. Confirm from rendered Compose
and `docker inspect` that `migrate`, `agent-api`, and `frontend-v2` all reference the same recorded
application digest. Keep the source revision, registry digest,
database backup package name, Alembic head, workflow URLs, and deployment timestamp in the release
record.

The one-shot `db-users` service reconciles database identities after MySQL is healthy. The
one-shot `migrate` service then runs `alembic upgrade head` with the migration identity;
`agent-api` starts only after migration succeeds and connects with the runtime identity. A failed
role bootstrap, migration, or production configuration keeps the API unavailable; it never loads
Demo data.

Before every upgrade:

1. create and verify a backup;
2. review the Alembic revisions included in the release;
3. run the migration verifier in a disposable environment;
4. stop public traffic, pull the recorded image digest, and run the one-shot `migrate` service;
5. require `/health/ready` and the browser smoke gate before routing merchant traffic.

Run the production migration gate with an isolated official MySQL container:

```bash
python scripts/verify_production_migrations.py
```

The command chooses a random loopback port and a database name containing an explicit `test`
segment, runs the complete existing migration/integrity/concurrency verifier, and removes its
container even on failure. It never targets the Compose MySQL volume or a configured production
database.

Do not use Alembic downgrade as the default production recovery method. Prefer restoring the
pre-upgrade backup with the prior application image. Execute a tested revision downgrade only when
its data-loss consequences are explicitly accepted.

## Health And Failure Behavior

- `GET /health/live` checks only that the API process can serve requests. Use it for liveness.
- `GET /health/ready` checks production configuration, database connectivity, and exact Alembic
  head, and refuses traffic while a failed restore marker exists. It returns only the sanitized
  `{"detail":"服务尚未就绪"}` body with `503` when any dependency is unavailable or stale.
- `GET /health` remains a compatibility database probe and is not the production readiness gate.
- The reverse proxy healthcheck uses `/health/live`; the API container healthcheck uses
  `/health/ready`.

An unhealthy Docker container is not automatically restarted solely because a healthcheck fails.
Alert on unhealthy status and application failure logs. `restart: unless-stopped` handles process
or host restarts. After a database restart, require readiness recovery before resuming traffic.
Readiness warnings include only a bounded reason code (`configuration_invalid`,
`database_unavailable`, `restore_in_progress`, `migration_unavailable`, or
`migration_head_mismatch`); they do not log connection URLs, SQL, credentials, or the underlying
exception. Route alerts by reason code and diagnose sensitive connection detail inside the
database/platform boundary rather than widening public health responses.

## TLS Reverse Proxy

Set `PUBLIC_HOSTNAME` to the exact externally served DNS name and set `TLS_CERT_FILE` and
`TLS_KEY_FILE` to absolute host paths. Certificates are mounted read-only. Requests with a different
Host are rejected rather than redirected. The Nginx configuration enables TLS 1.2/1.3,
HTTP-to-HTTPS redirect, HSTS and security headers, API rate limiting, a bounded 12 MiB import
request body, bounded upstream timeouts, and Streamlit WebSocket forwarding. Production API docs
and the OpenAPI endpoint are disabled.

The default container ports are published as `8080` and `8443`; production ingress may map them to
public 80/443. Set `COMMERCE_PUBLIC_HTTPS_PORT` to the externally visible TLS port so redirects are
correct. Terminate TLS only at a trusted ingress and preserve the same security-header contract.

## Backup

Backups use `mysqldump --single-transaction` and never place the password in the host command line.
The service refuses to overwrite an existing package and creates three artifacts with a
restrictive container umask: the `.sql` dump, `.sql.meta` metadata binding the database and Alembic
head, and `.sql.sha256` checksums covering both. On Docker Desktop, also protect the host backup
directory with NTFS permissions because container mode bits do not replace host ACLs.

```bash
BACKUP_NAME=commerce-20260817T120000Z.sql \
docker compose --env-file /secure/commerce-production.env \
  -f docker-compose.production.yml --profile maintenance run --rm backup
```

The backup command prints `manifest_sha256=<digest>`. Record that value immediately in the audited,
immutable deployment/recovery record outside the backup directory. The colocated checksum detects
corruption; only this out-of-band digest authenticates that the checksum manifest is the one the
operator recorded. Anyone who can replace the dump, metadata, and checksum together can otherwise
recompute a self-consistent package.

Store backups off-host with retention, encryption, and restore testing appropriate to merchant and
financial data. A backup is not considered valid until a restore exercise verifies both business
rows and the `alembic_version` head.

Before using a newly created production package as an upgrade/rollback checkpoint, restore that
exact package into an isolated digest-pinned MySQL container. The verifier publishes no port,
mounts the package and restore script read-only, checks the release head, database charset and
collation, one expected tenant marker, and the absence of a restore-state marker, then removes only
the container ID and anonymous data volume it created:

```bash
python scripts/verify_backup_restore_package.py \
  --backup-directory /srv/commerce/backups \
  --backup-file commerce-20260817T120000Z.sql \
  --expected-organization-slug merchant-one \
  --expected-schema-head 0016_agent_workflow \
  --expected-manifest-sha256 <digest-from-deployment-record>
```

A non-zero result means the package is not an accepted recovery point. Preserve its output for
triage, keep production traffic unchanged, and do not substitute a destructive production restore
for this isolated exercise. The command deliberately rejects an unexpected schema head, unsafe
basename, incomplete package, missing tenant marker, or failed exact cleanup.

## Restore

Restore is a high-impact maintenance operation. Stop traffic and application services first. The
restore service requires the exact confirmation value `RESTORE_<MYSQL_DATABASE>` and a basename
ending in `.sql`; path traversal and incomplete packages are rejected. Before applying SQL it
verifies both checksums and requires metadata to match `MYSQL_DATABASE` and
`EXPECTED_SCHEMA_HEAD`. After restore it queries `alembic_version` and requires the same exact
head. The metadata also preserves the database character set and collation. Update
`EXPECTED_SCHEMA_HEAD` only as part of a reviewed migration release.

The restore container snapshots the selected three-file package into its private writable layer
before any authentication or import, then uses only that snapshot. Budget Docker root-disk free
space for at least one additional full dump copy plus MySQL's restored data and safety margin; a
capacity failure is a failed restore and traffic must remain stopped. The `run --rm` lifecycle
removes this staging layer after the command exits.

After preflight succeeds, restore replaces the entire target database rather than overlaying
non-transactional MySQL DDL. It creates `deployment_restore_state` before importing. Any import or
head-validation failure leaves that marker in the newly created database, and `/health/ready`
continues returning `503`. Do not reopen traffic or take a new backup from this state. Correct the
package or environment and rerun restore; the next attempt rebuilds a clean target. Only a fully
successful import removes the marker. Then verify the head, tenant-scoped business rows, and the
critical authenticated browser flow before reopening traffic. Backup and migration operations
must remain mutually exclusive; backup rejects an active restore marker and requires the Alembic
head to be unchanged before and after the dump.

```bash
docker compose --env-file /secure/commerce-production.env \
  -f docker-compose.production.yml stop reverse-proxy frontend-v2 agent-api

RESTORE_BACKUP_FILE=commerce-20260817T120000Z.sql \
RESTORE_CONFIRM=RESTORE_commerce \
EXPECTED_BACKUP_MANIFEST_SHA256=<digest-from-deployment-record> \
docker compose --env-file /secure/commerce-production.env \
  -f docker-compose.production.yml --profile maintenance run --rm restore

docker compose --env-file /secure/commerce-production.env \
  -f docker-compose.production.yml up -d --no-build --wait agent-api frontend-v2 reverse-proxy
```

After restore, verify `/health/ready`, the Alembic head, tenant-scoped API reads, and a critical
browser flow before reopening traffic.

## Rollback

Rollback is an image-and-data operation, not `docker compose down --volumes`. Never remove the
production MySQL volume. If the failed release ran a migration, keep traffic stopped, restore the
verified pre-upgrade backup using the matching expected head, then set `COMMERCE_APP_IMAGE` to the
previous recorded digest. Pull and start with `--no-build`:

```bash
docker compose --env-file /secure/commerce-production.env \
  -f docker-compose.production.yml pull
docker compose --env-file /secure/commerce-production.env \
  -f docker-compose.production.yml up -d --no-build --wait
```

Require readiness, tenant-scoped API reads, and the authenticated browser smoke before reopening
traffic. If restore or verification fails, preserve maintenance mode and treat the database as
untrusted; do not repeatedly restart application traffic around a partial restore.

## Production Owner Provisioning And Authentication Limitation

After a fresh deployment is ready, create the first active organization/OWNER and capture a
15-minute token to a mode-`0600` file. The command runs inside the already reviewed application
image, validates production configuration and migration readiness, serializes concurrent MySQL
bootstrap attempts, and writes an `identity.bootstrap_owner` operation log without recording the
token. Stdout contains only the bearer token; the organization id is reported on stderr for the V2
UI sidebar.

```bash
umask 077
docker compose --env-file /secure/commerce-production.env \
  -f docker-compose.production.yml exec -T agent-api \
  python scripts/bootstrap_production_owner.py \
    --organization-slug merchant-one \
    --organization-name "Merchant One" \
    --owner-email owner@example.com \
    --owner-name "Production Owner" \
    --token-ttl-seconds 900 \
  > /secure/commerce-initial-owner.token
test "$(stat -c '%a' /secure/commerce-initial-owner.token)" = "600"
```

On Windows, write the token into a WSL Linux filesystem with mode `0600`, or immediately disable
NTFS ACL inheritance and grant only the deployment account/required administrators access with
`icacls`; Bash `umask` alone is not an NTFS access-control proof. Record the real deployment
operator identity and the `identity.bootstrap_owner` request id in the external release audit.
Inside the application audit the actor is classified as `DEPLOYMENT_OPERATOR` and the new/existing
OWNER is the subject, not falsely attributed as the caller.

On a genuinely empty identity store the command creates exactly one organization, user, and OWNER
membership atomically. On a later invocation it only issues a token when the named organization,
user, and active OWNER membership already match exactly; it never creates a second identity or
changes a role. Use the token immediately for `/health/ready`, tenant-scoped API reads, and the
authenticated browser smoke, then securely delete the token file. Never put it in Compose env,
shell history, CI artifacts, chat, or logs.

The current V2 API validates HMAC-signed bearer tokens and organization membership, but does not
provide a public login, identity-provider integration, per-token session revocation, or self-service
account recovery. Suspending/revoking the membership blocks subsequent authorized requests;
rotating `AUTH_SIGNING_KEY` invalidates every previously issued token and therefore requires a
planned cutover. Keep operational tokens short-lived and reissue only to an existing active OWNER
through the audited command. This limitation does not weaken tenant and permission checks, but it
prevents describing the current UI as a public multi-tenant SaaS login experience.

## Monitoring And Operator Checks

At minimum alert on:

- reverse-proxy/API container exit, restart count, or unhealthy status;
- `/health/ready` failure reason code and recovery duration;
- HTTP 5xx rate, request latency, and rate-limit rejection growth;
- MySQL availability, connection saturation, disk/volume capacity, replication/host alerts when
  applicable, and a present `deployment_restore_state` marker;
- age and off-host copy status of the last checksum-verified backup plus the last successful restore
  exercise;
- TLS certificate expiry and unexpected Host rejection volume;
- failed or missing required RC CI/security jobs and new scanner findings.

Keep logs access-controlled and retain enough history to investigate merchant operations. The
application redaction filter is defense in depth, not permission to log request payloads or secrets.
Production Compose bounds each container's local `json-file` log to five 10 MiB files; central log
retention must also be bounded and monitored.
Define deployment-specific RPO, RTO, backup retention, alert routing, and escalation ownership; the
repository does not invent those business commitments.

## Automated Release Exercise

Run the isolated release verifier before a tagged release:

```bash
python scripts/verify_production_deployment.py
```

It creates temporary secrets and a one-day self-signed certificate outside the repository, starts
an isolated production Compose project, verifies HTTPS/readiness/browser behavior, restarts MySQL,
checks persisted tenant data, performs backup/delete/restore, verifies the migration head, and
removes the isolated containers and volume. Self-signed TLS is test evidence only.

Before promotion, also require both workflows in `docs/CI.md`, review all scanner artifacts under
`docs/SECURITY_SCANNING.md`, verify the recorded application digest is the digest that was scanned,
and record every skipped or externally blocked check. A script, workflow, or document existing is
not a PASS without its run evidence.
