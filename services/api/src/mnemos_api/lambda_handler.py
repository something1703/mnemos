"""AWS Lambda entry point.

Mangum adapts the ASGI app to Lambda's event/response shape. Chosen over the
Lambda Web Adapter extension because it is a pure-Python dependency with
nothing to install into the image beyond a wheel — fewer moving parts in a
component whose failure mode is "the demo URL is down".

`lifespan="on"` is required, not optional: the runtime (database pools,
embedder, Warden) is constructed in the ASGI lifespan, so a handler that
skipped it would serve every request against an app that never finished
starting.

Secrets are resolved from Secrets Manager at cold start when
MNEMOS_SECRET_ARN is set, and written into the environment before settings are
read. Putting a database URL in a plaintext Lambda environment variable is
visible to anyone with lambda:GetFunctionConfiguration, which is a wider
audience than it looks.
"""

from __future__ import annotations

import json
import logging
import os

logging.basicConfig(level=os.environ.get("MNEMOS_LOG_LEVEL", "INFO"))
log = logging.getLogger("mnemos.api.lambda")


def _load_secrets() -> None:
    """Hydrate os.environ from Secrets Manager, before settings are read.

    Runs at import time (cold start) so the cost is paid once per execution
    environment rather than per request. Existing environment variables win,
    which makes local overrides possible without editing the secret.
    """
    arn = os.environ.get("MNEMOS_SECRET_ARN")
    if not arn:
        log.info("MNEMOS_SECRET_ARN not set; using environment variables as-is")
        return

    import boto3

    client = boto3.client("secretsmanager")
    payload = client.get_secret_value(SecretId=arn)["SecretString"]
    loaded = 0
    for key, value in json.loads(payload).items():
        if key not in os.environ:
            os.environ[key] = str(value)
            loaded += 1
    log.info("loaded %d value(s) from Secrets Manager", loaded)


_load_secrets()

from typing import Any, cast  # noqa: E402

from mangum import Mangum  # noqa: E402 - must follow secret hydration

from .asgi import create_app  # noqa: E402

# Starlette types its app as returning Awaitable[None]; Mangum's protocol wants
# Coroutine[Any, Any, None]. Structurally identical for an `async def`, so the
# cast is a type-level formality rather than a claim about runtime behaviour.
handler = Mangum(cast(Any, create_app()), lifespan="on")
