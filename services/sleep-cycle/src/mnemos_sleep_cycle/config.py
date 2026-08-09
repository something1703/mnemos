"""Runtime configuration for the sleep cycle, resolved from the environment
once at startup.

**This service is untrusted by construction.** It is the only part of Mnemos
that reads free text and asks a model what it means, so everything it writes
lands at `trust='unverified'` regardless of how confident the model claims to
be (`distill.py`), and its database role (`mnemos_pipeline`, migration 011)
holds no DELETE grant anywhere — decay lowers strength, quarantine changes a
trust field, neither removes a row.

**`MNEMOS_DB_URL_PIPELINE` is the role-bound login this service should run as
in any real deployment.** It falls back to `MNEMOS_DB_URL` (the admin login
used by migrations and seeding) for local development, exactly like the API's
Warden-DSN fallback — and just like that one, the fallback is loud rather than
silent: a deployment that never set the dedicated variable finds out from its
own logs, not from an incident report.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import lru_cache

log = logging.getLogger("mnemos.sleep_cycle.config")


@dataclass(frozen=True)
class Settings:
    database_url: str
    """Should resolve to `mnemos_pipeline` — SELECT/INSERT/UPDATE, BYPASSRLS
    for cross-tenant batch discovery, no DELETE anywhere."""

    database_url_is_pipeline_role: bool
    """False means this fell back to the admin DSN. Reported at startup and in
    `posture()` for the same reason the API reports privilege_separation: a
    security posture that is merely assumed is not a security posture."""

    openai_api_key: str
    embed_model: str
    embed_dimensions: int
    distill_model: str

    batch_limit: int
    decay_lambda: float
    corroboration_ttl_days: int

    def describe_posture(self) -> dict[str, object]:
        return {
            "database_role": "pipeline"
            if self.database_url_is_pipeline_role
            else "admin (fallback)",
            "embed_model": self.embed_model,
            "embed_dimensions": self.embed_dimensions,
            "distill_model": self.distill_model,
            "batch_limit": self.batch_limit,
            "decay_lambda": self.decay_lambda,
            "corroboration_ttl_days": self.corroboration_ttl_days,
        }


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"{name} is not set. Copy .env.example to .env and fill it in, then:\n"
            "  set -a; source .env; set +a"
        )
    return value


def _pipeline_database_url() -> tuple[str, bool]:
    dedicated = os.environ.get("MNEMOS_DB_URL_PIPELINE", "").strip()
    if dedicated:
        return dedicated, True
    log.warning(
        "MNEMOS_DB_URL_PIPELINE is not set; falling back to MNEMOS_DB_URL (the admin login). "
        "Deployments should point this at a login granted the mnemos_pipeline role "
        "(migration 011), which holds no DELETE grant anywhere."
    )
    return _require("MNEMOS_DB_URL"), False


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    database_url, is_pipeline_role = _pipeline_database_url()
    return Settings(
        database_url=database_url,
        database_url_is_pipeline_role=is_pipeline_role,
        openai_api_key=_require("OPENAI_API_KEY"),
        embed_model=os.environ.get("OPENAI_EMBED_MODEL", "text-embedding-3-small"),
        embed_dimensions=int(os.environ.get("OPENAI_EMBED_DIMENSIONS", "1024")),
        distill_model=os.environ.get("OPENAI_DISTILL_MODEL") or "gpt-5.6-luna",
        batch_limit=int(os.environ.get("MNEMOS_SLEEP_BATCH_LIMIT", "25")),
        decay_lambda=float(os.environ.get("MNEMOS_DECAY_LAMBDA", "0.1")),
        corroboration_ttl_days=int(os.environ.get("MNEMOS_CORROBORATION_TTL_DAYS", "30")),
    )
