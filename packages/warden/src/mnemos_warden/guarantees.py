"""The no-model guarantee, checked at runtime.

Two static checks already exist and run in CI:
  - `make no-model-in-warden` scans this package's source for LLM imports.
  - The Warden's deployed IAM role (Phase 06.4 infra) carries an explicit Deny
    on bedrock:InvokeModel and equivalent actions for other providers.

This module is the THIRD, independent check: it runs at process startup,
inside the Warden's own runtime, and refuses to serve if either guarantee
looks compromised. A static scan can miss a dynamic import
(`importlib.import_module("anthropic")`); a runtime check catches what static
analysis cannot, at the cost of only catching it after packaging rather than
at commit time. Together the three checks cover commit time, build time, and
run time.
"""

from __future__ import annotations

import sys

from .errors import ConfirmationRequired  # noqa: F401 - re-exported for convenience

# Import names that would indicate a model client made it into this process.
# Deliberately broad: better to false-positive on a coincidentally-named
# module than to miss a real one.
FORBIDDEN_MODULE_PREFIXES = (
    "anthropic",
    "openai",
    "langchain",
    "litellm",
    "cohere",
    "ollama",
    "mistralai",
    "google.generativeai",
)


class NoModelGuaranteeViolated(RuntimeError):
    """Raised at startup if a model client is present in this process."""


def assert_no_model_loaded() -> None:
    """Refuse to serve if any forbidden module is already imported.

    Call this once, at process startup, before accepting any request. It is
    intentionally a hard `raise` rather than a log line — a Warden process
    that might be able to call a model is a Warden process that has failed
    its one job.
    """
    loaded = sorted(sys.modules.keys())
    offenders = [
        name
        for name in loaded
        if any(name == p or name.startswith(p + ".") for p in FORBIDDEN_MODULE_PREFIXES)
    ]
    if offenders:
        raise NoModelGuaranteeViolated(
            "the Warden process has an LLM client loaded: "
            f"{offenders}. This process must never be able to invoke a model — "
            "see AGENTS.md invariant 1 and ADR-002."
        )


def assert_bedrock_denied(boto3_session: object) -> None:
    """Probe that the IAM role actually denies bedrock:InvokeModel.

    Belt-and-suspenders over `assert_no_model_loaded`: even if no model SDK is
    imported, boto3 alone is enough to call Bedrock's HTTP API. This performs
    a real (harmless, zero-cost) STS/IAM simulation call rather than trusting
    the policy document, because a policy that looks right and a policy that
    is attached are two different things.

    Wired fully in Phase 06.4 alongside the deployed IAM role; the interface
    is defined now so services/warden can call it from day one.
    """
    raise NotImplementedError(
        "wired in Phase 06.4 once the Warden's IAM role exists to probe against"
    )
