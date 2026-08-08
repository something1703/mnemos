# Mnemos API on AWS Lambda (arm64 / Graviton).
#
# A container image rather than a zip: psycopg[binary] plus boto3, fastapi and
# the MCP SDK are comfortably past the point where a 250 MB unzipped limit is
# something to think about, and a container removes that whole class of
# packaging surprise.
#
# arm64 because the build host is Apple Silicon — a native build avoids QEMU
# emulation, which turns a one-minute image build into a ten-minute one — and
# because Graviton Lambda is cheaper per GB-second for identical work.

FROM public.ecr.aws/lambda/python:3.12-arm64

# uv gives the same resolver in the image as locally, so what ships is what was
# tested. Copied from a pinned image rather than curl|sh at build time.
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /bin/uv

WORKDIR ${LAMBDA_TASK_ROOT}

# The workspace root has to come along. mnemos-api and mnemos-warden declare
# their siblings as `{ workspace = true }`, and uv resolves that by walking up
# for a [tool.uv.workspace] table — without it the install fails with
# "references a workspace ... but is not a workspace member".
COPY pyproject.toml uv.lock README.md ./
COPY packages/ packages/
COPY services/ services/

# Installing from source rather than from a pre-built wheel keeps this honest
# about what it ships: exactly the packages in this repo.
RUN uv pip install --system --no-cache \
      ./packages/engine \
      ./packages/warden \
      ./services/api

# psycopg[binary] ships its own libpq and OpenSSL, built on manylinux, whose
# compiled-in default CA paths do not exist in this image. The bundle is right
# here — the bundled OpenSSL just looks elsewhere for it — so `sslmode=
# verify-full sslrootcert=system` fails with "certificate verify failed" until
# it is pointed at the file. Fixing it here rather than in the connection
# string keeps the URL portable: this is a property of the image, not of the
# cluster.
ENV SSL_CERT_FILE=/etc/pki/tls/cert.pem \
    SSL_CERT_DIR=/etc/pki/tls/certs

CMD ["mnemos_api.lambda_handler.handler"]
