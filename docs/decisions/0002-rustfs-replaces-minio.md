# 0002 — RustFS replaces MinIO as the local object store

- **Status:** accepted
- **Date:** 2026-10-12

## Context

The spec uses MinIO as the local, S3-compatible stand-in for S3. When Phase 1 was built, the
`minio/minio` GitHub repository had been archived (April 2026), and no `minio/minio` or `minio/mc`
image was published on Docker Hub or Quay. Building the archived source would mean running an
unmaintained AGPL server whose vulnerabilities will not be fixed.

## Decision

- Use **RustFS** (`rustfs/rustfs:1.0.1`, Apache-2.0), which is designed as a drop-in MinIO replacement.
  Locally it serves S3 on port 9000 and its console on port 9001. Only the S3 API is used, so the
  code, DuckDB and s3fs stay unchanged.
- The bucket-creation container (`lake-init`) uses the official `amazon/aws-cli` image, because MinIO's
  `mc` client is archived too.
- The settings `MINIO_ROOT_USER` and `MINIO_ROOT_PASSWORD` are renamed `RUSTFS_ACCESS_KEY` and
  `RUSTFS_SECRET_KEY`, which are RustFS's own variable names, so Compose passes them straight through.
- `.env` holds URLs as seen from the laptop (`http://localhost:9000`). `docker-compose.yml` overrides
  them with service names inside containers. That way tests and the CLI on the host and the containers
  share one `.env`.

## Consequences

- RustFS is younger than MinIO. Only standard S3 calls are relied on (GET, PUT, LIST, DELETE, multipart
  upload through DuckDB), and the integration tests exercise each of them.
- The kind manifest in Phase 9 deploys RustFS instead of MinIO.
- Revisit if RustFS stops being maintained. SeaweedFS was the alternative considered.
