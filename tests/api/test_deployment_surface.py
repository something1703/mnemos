"""Regressions for bugs that only appeared once the service was deployed.

Every test here corresponds to something that passed locally, passed CI, and
then failed on AWS. They are grouped because they share a cause: the local
`mnemos-api serve` path exercises a long-lived process with a dev virtualenv
and a loopback hostname, and none of those three things hold on Lambda.
"""

from __future__ import annotations

import ast
import pathlib

import psycopg
import pytest
from mnemos_api.config import Settings
from mnemos_api.runtime import DbPosture, Runtime, build_runtime
from mnemos_api.server import transport_security_for, with_slash_alias
from psycopg_pool import PoolTimeout
from starlette.types import Receive, Scope, Send

from .conftest import LOCAL_DSN, _settings

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# Packages that exist only in the dev dependency group. Importing one at module
# scope works in the test venv and raises ModuleNotFoundError in the deployed
# image, where the dev group is not installed.
DEV_ONLY_PREFIXES = ("mypy_boto3", "boto3_stubs", "pytest", "hypothesis")

SHIPPED_SOURCE = [
    REPO_ROOT / "packages" / "engine" / "src",
    REPO_ROOT / "packages" / "warden" / "src",
    REPO_ROOT / "services" / "api" / "src",
]


def _module_level_imports(tree: ast.Module) -> list[str]:
    """Imports that actually execute at import time.

    Anything nested inside `if TYPE_CHECKING:` or a function body is skipped by
    only walking the module's own top-level statements.
    """
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module)
    return names


def test_no_dev_only_imports_execute_at_runtime() -> None:
    """The deployed image has no dev dependencies; a stub import will crash it.

    `mypy_boto3_kms` was imported at module scope in mnemos_warden.keys purely
    for a type annotation. Every test passed — the test venv installs
    boto3-stubs — and the Lambda container failed on `import mnemos_warden`
    before serving a single request.
    """
    offenders: list[str] = []
    for root in SHIPPED_SOURCE:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(), filename=str(path))
            for name in _module_level_imports(tree):
                if name.startswith(DEV_ONLY_PREFIXES):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}: {name}")

    assert not offenders, (
        "dev-only packages imported at module scope in shipped code; move them "
        "under `if TYPE_CHECKING:`\n  " + "\n  ".join(offenders)
    )


class TestSlashAlias:
    """A bare `/mcp` must route like `/mcp/`.

    Starlette's Mount matches the prefix only when followed by a slash, so
    `POST /mcp` fell through to the REST app and returned an unexplained 404 —
    and `/mcp` is what every pasted client config contains.
    """

    @staticmethod
    async def _paths_seen(path: str) -> list[str]:
        seen: list[str] = []

        async def sink(scope: Scope, receive: Receive, send: Send) -> None:
            seen.append(str(scope["path"]))

        app = with_slash_alias(sink, "/mcp")
        await app({"type": "http", "path": path, "raw_path": path.encode()}, None, None)  # type: ignore[arg-type]
        return seen

    @pytest.mark.anyio
    async def test_bare_prefix_is_rewritten(self) -> None:
        assert await self._paths_seen("/mcp") == ["/mcp/"]

    @pytest.mark.anyio
    async def test_trailing_slash_is_untouched(self) -> None:
        assert await self._paths_seen("/mcp/") == ["/mcp/"]

    @pytest.mark.anyio
    async def test_other_paths_are_untouched(self) -> None:
        assert await self._paths_seen("/v1/ledger/verify") == ["/v1/ledger/verify"]

    @pytest.mark.anyio
    async def test_prefix_is_not_matched_by_similar_path(self) -> None:
        """`/mcpx` is a different route and must not be rewritten."""
        assert await self._paths_seen("/mcpx") == ["/mcpx"]

    @pytest.mark.anyio
    async def test_non_http_scopes_pass_through(self) -> None:
        """Lifespan messages have no `path`; rewriting one would crash startup."""
        seen: list[Scope] = []

        async def sink(scope: Scope, receive: Receive, send: Send) -> None:
            seen.append(scope)

        app = with_slash_alias(sink, "/mcp")
        await app({"type": "lifespan"}, None, None)  # type: ignore[arg-type]
        assert seen == [{"type": "lifespan"}]


class TestTransportSecurity:
    """The MCP SDK rejects unknown Host headers with 421.

    Correct for a loopback server, and it means a deployed hostname must be
    declared. The deployed service answered 421 to everything until it was.
    """

    def test_loopback_is_always_allowed(self) -> None:
        settings = _settings()
        security = transport_security_for(settings)
        assert security.enable_dns_rebinding_protection
        assert "127.0.0.1:*" in security.allowed_hosts

    def test_configured_hosts_are_added(self) -> None:
        settings = _settings(
            allowed_hosts=("127.0.0.1:*", "abc.execute-api.us-east-1.amazonaws.com")
        )
        security = transport_security_for(settings)
        assert "abc.execute-api.us-east-1.amazonaws.com" in security.allowed_hosts
        assert security.enable_dns_rebinding_protection

    def test_star_disables_protection_explicitly(self) -> None:
        """Opting out is possible but must be spelled, never inferred."""
        settings = _settings(allowed_hosts=("127.0.0.1:*", "*"))
        assert not transport_security_for(settings).enable_dns_rebinding_protection


class TestMeasuredPosture:
    """`privilege_separation` must report what the cluster grants.

    The configured value only compares two DSN strings, which two URLs
    differing by password alone would also satisfy.
    """

    @staticmethod
    def _runtime(posture: DbPosture | None, settings: Settings | None = None) -> Runtime:
        # Only describe_posture() is under test, so the pools and engine are
        # irrelevant; constructing them would need a database.
        return Runtime(
            settings=settings or _settings(),
            db=None,  # type: ignore[arg-type]
            warden_db=None,  # type: ignore[arg-type]
            engine=None,  # type: ignore[arg-type]
            warden=None,  # type: ignore[arg-type]
            embedder=None,  # type: ignore[arg-type]
            key_provider=None,  # type: ignore[arg-type]
            db_posture=posture,
        )

    def test_measurement_overrides_configuration(self) -> None:
        """Two distinct URLs, but the API role holds DELETE: not separated."""
        settings = _settings(warden_database_url="postgresql://other@localhost/mnemos")
        assert settings.privilege_separation_active  # what config alone believes

        posture = self._runtime(
            DbPosture("api_user", "warden_user", api_can_delete=True, warden_can_delete=True),
            settings,
        ).describe_posture()

        assert posture["privilege_separation"] is False
        assert posture["privilege_separation_source"] == "measured"
        assert posture["api_can_delete"] is True

    def test_enforced_requires_both_halves(self) -> None:
        """A Warden that cannot delete either is a broken deployment, not a
        secure one — erasure requests would fail."""
        neither = DbPosture("a", "b", api_can_delete=False, warden_can_delete=False)
        assert not neither.enforced

        both = DbPosture("a", "b", api_can_delete=False, warden_can_delete=True)
        assert both.enforced

    def test_unmeasured_posture_says_so(self) -> None:
        posture = self._runtime(None).describe_posture()
        assert posture["privilege_separation_source"] == "configured"
        assert "api_can_delete" not in posture

    @pytest.mark.anyio
    async def test_probe_reads_real_grants_from_the_cluster(self) -> None:
        """The end-to-end version: two role-bound logins on a live cluster.

        This is the arrangement the deployment actually uses — `mnemos_api_svc`
        and `mnemos_warden_svc` in production, the session-scoped test users
        here — so a grant that stops working shows up as a failing test rather
        than as a posture field quietly flipping to false in production.
        """
        api_dsn = LOCAL_DSN.replace("//root@", "//test_api_user@")
        warden_dsn = LOCAL_DSN.replace("//root@", "//test_warden_user@")

        try:
            runtime = await build_runtime(
                _settings(database_url=api_dsn, warden_database_url=warden_dsn)
            )
        except (psycopg.OperationalError, PoolTimeout) as exc:
            pytest.skip(f"local CockroachDB unavailable ({exc}). Run: make db-local")

        try:
            posture = runtime.describe_posture()
        finally:
            await runtime.close()

        assert posture["privilege_separation_source"] == "measured"
        assert posture["db_user"] == "test_api_user"
        assert posture["api_can_delete"] is False, (
            "the API role holds DELETE on a memory table — invariant 1 is not "
            "being enforced by the cluster"
        )
        assert posture["warden_can_delete"] is True
        assert posture["privilege_separation"] is True
