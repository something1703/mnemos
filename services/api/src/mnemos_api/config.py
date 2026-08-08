"""Runtime configuration, resolved from the environment once at startup.

Two things here are load-bearing rather than cosmetic.

**Privilege separation is reported, never assumed.** The API serves ordinary
memory operations through a database role that was never granted DELETE
(`mnemos_api`, migration 011), and reaches destruction only through a separate
Warden connection. If a distinct Warden DSN is not configured, the service
still runs — but it says so, loudly, at startup and in `memory_stats`. A
system that quietly downgrades its own security posture is worse than one that
never claimed it.

**The model provider is a setting, not a hardcoded vendor.** See ADR-014: the
Warden must hold no model at all, and which vendor the *rest* of the system
calls is deliberately swappable so that "resistant to memory poisoning" is not
secretly a claim about one provider's behaviour.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    database_url: str
    """The API's own connection. Should resolve to a role WITHOUT DELETE."""

    warden_database_url: str | None
    """Separate, privileged connection used only for admin-scoped operations.

    None means privilege separation is not active — the API falls back to its
    own connection for Warden work. Acceptable for local development, reported
    honestly everywhere it matters, and replaced by a distinct IAM-bounded
    Lambda in deployment (Phase 04.4).
    """

    region: str
    openai_api_key: str | None
    embed_model: str
    embed_dimensions: int
    distill_model: str | None
    model_provider: str

    anchor_bucket: str | None
    chain_shards: int

    host: str
    port: int

    @property
    def privilege_separation_active(self) -> bool:
        return (
            self.warden_database_url is not None and self.warden_database_url != self.database_url
        )

    def describe_posture(self) -> dict[str, object]:
        """What a caller (or a judge) should know about how this instance is
        actually configured, as opposed to how it could be."""
        return {
            "privilege_separation": self.privilege_separation_active,
            "model_provider": self.model_provider,
            "embed_model": self.embed_model,
            "embed_dimensions": self.embed_dimensions,
            "distill_model": self.distill_model,
            "anchor_bucket_configured": self.anchor_bucket is not None,
            "chain_shards": self.chain_shards,
        }


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"{name} is not set. Copy .env.example to .env and fill it in, then:\n"
            "  set -a; source .env; set +a"
        )
    return value


def _optional(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        database_url=_require("MNEMOS_DB_URL"),
        warden_database_url=_optional("MNEMOS_DB_URL_WARDEN"),
        region=os.environ.get("AWS_REGION", "us-east-1"),
        openai_api_key=_optional("OPENAI_API_KEY"),
        embed_model=os.environ.get("OPENAI_EMBED_MODEL", "text-embedding-3-small"),
        embed_dimensions=int(os.environ.get("OPENAI_EMBED_DIMENSIONS", "1024")),
        distill_model=_optional("OPENAI_DISTILL_MODEL"),
        model_provider=os.environ.get("MNEMOS_MODEL_PROVIDER", "openai"),
        anchor_bucket=_optional("MNEMOS_S3_ANCHOR_BUCKET"),
        chain_shards=int(os.environ.get("MNEMOS_CHAIN_SHARDS", "16")),
        host=os.environ.get("MNEMOS_API_HOST", "127.0.0.1"),
        port=int(os.environ.get("MNEMOS_API_PORT", "8000")),
    )
