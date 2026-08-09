# Mnemos Custodian on AWS ECS Fargate (arm64 / Graviton).
#
# Not a Lambda image, unlike infra/api and infra/sleep-cycle: PHASE_07 7.6
# calls for a scheduled AND alarm-triggered task, and a plain Fargate task
# started by EventBridge (RunTask) is a closer match than forcing this into
# Lambda's request/response shape for a job that just runs one sweep and
# exits. mnemos-custodian (services/custodian/pyproject.toml's
# [project.scripts] entry) is the whole container's job.
#
# arm64 for the same reason as the other two images: native build on Apple
# Silicon, cheaper per vCPU-second on Graviton Fargate.

FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /bin/uv

# python:3.12-slim ships no CA bundle at all — psycopg[binary]'s manylinux
# wheel needs one on disk to verify the CockroachDB Cloud cert
# (sslmode=verify-full). Same class of fix as infra/api and
# infra/sleep-cycle's SSL_CERT_FILE override, different path because this is
# a Debian base rather than the Lambda base image's Amazon Linux: the bundled
# OpenSSL's compiled-in default doesn't match either base's real CA bundle
# location, so both need an explicit pointer — verified against the real
# cluster (docs/limits.md's "measured, not assumed" standard) before this
# was treated as settled rather than assumed.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_DIR=/etc/ssl/certs

WORKDIR /app

# The workspace root has to come along — mnemos-custodian declares
# mnemos-engine as `{ workspace = true }`, and uv resolves that by walking up
# for [tool.uv.workspace]. Same reasoning as infra/api and infra/sleep-cycle.
COPY pyproject.toml uv.lock README.md ./
COPY packages/ packages/
COPY services/ services/

RUN uv pip install --system --no-cache \
      ./packages/engine \
      ./services/custodian

ENTRYPOINT ["mnemos-custodian"]
