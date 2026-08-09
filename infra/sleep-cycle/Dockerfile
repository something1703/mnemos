# Mnemos sleep cycle on AWS Lambda (arm64 / Graviton).
#
# Same base and same reasoning as infra/api/Dockerfile: a container rather
# than a zip because psycopg[binary] plus boto3 are past the point where a
# 250 MB unzipped limit is worth thinking about; arm64 to build natively on
# Apple Silicon and because Graviton is cheaper per GB-second.
#
# This service has no HTTP surface — no mangum, no uvicorn — it is invoked
# directly by EventBridge or a Step Functions Task with a {"stage": ...}
# event. See lambda_handler.py.

FROM public.ecr.aws/lambda/python:3.12-arm64

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /bin/uv

WORKDIR ${LAMBDA_TASK_ROOT}

# The workspace root has to come along — mnemos-sleep-cycle declares
# mnemos-engine as `{ workspace = true }`, and uv resolves that by walking up
# for [tool.uv.workspace]. Same reasoning as infra/api/Dockerfile.
COPY pyproject.toml uv.lock README.md ./
COPY packages/ packages/
COPY services/ services/

RUN uv pip install --system --no-cache \
      ./packages/engine \
      ./services/sleep-cycle

# psycopg[binary] bundles its own libpq and OpenSSL, built on manylinux, whose
# compiled-in default CA path does not exist in this image — sslmode=
# verify-full then fails with "certificate verify failed" despite the CA
# bundle being present at the paths below. Same fix as infra/api/Dockerfile.
ENV SSL_CERT_FILE=/etc/pki/tls/cert.pem \
    SSL_CERT_DIR=/etc/pki/tls/certs

CMD ["mnemos_sleep_cycle.lambda_handler.handler"]
