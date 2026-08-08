"""`mnemos-api` — mint keys, inspect posture, serve.

mnemos-api mint-key --tenant clinic --scope read   --label "judge console"
mnemos-api posture
mnemos-api serve
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Any
from uuid import UUID

from .config import get_settings
from .keys import Scope, mint_key
from .runtime import build_runtime

EXIT_OK = 0
EXIT_ERROR = 2


async def _resolve_tenant(runtime: Any, tenant: str) -> UUID | None:
    try:
        return UUID(tenant)
    except ValueError:
        pass

    async def lookup(cur: Any) -> Any:
        await cur.execute("SELECT tenant_id FROM mnemos.tenants WHERE slug = %s", (tenant,))
        return await cur.fetchone()

    row = await runtime.db.transaction(None, lookup, label="resolve_tenant")
    return row[0] if row else None


async def _cmd_mint(tenant: str, scope_raw: str, label: str) -> int:
    runtime = await build_runtime()
    try:
        tenant_id = await _resolve_tenant(runtime, tenant)
        if tenant_id is None:
            print(f"no tenant matching {tenant!r}", file=sys.stderr)
            return EXIT_ERROR

        scope = Scope.parse(scope_raw)

        async def run(cur: Any) -> tuple[str, UUID]:
            return await mint_key(cur, tenant_id, scope=scope, label=label)

        plaintext, key_id = await runtime.db.transaction(tenant_id, run, label="mint_key")
    finally:
        await runtime.close()

    print(f"key_id : {key_id}")
    print(f"tenant : {tenant}")
    print(f"scope  : {scope.label}")
    print(f"label  : {label}")
    print()
    print(plaintext)
    print()
    print(
        "This is the only time the key is shown — only its SHA-256 is stored, so it\n"
        "cannot be recovered. Mint another if you lose it."
    )
    return EXIT_OK


async def _cmd_posture() -> int:
    runtime = await build_runtime()
    try:
        posture = runtime.settings.describe_posture()
    finally:
        await runtime.close()

    width = 62
    print("─" * width)
    print("  mnemos-api — configured posture")
    print("─" * width)
    for key, value in posture.items():
        print(f"  {key:<28} {value}")
    print("─" * width)
    if not posture["privilege_separation"]:
        print("  WARNING: the Warden shares the API's database connection.")
        print("  Set MNEMOS_DB_URL_WARDEN to a role holding DELETE, and point")
        print("  MNEMOS_DB_URL at one that does not.")
        print("─" * width)
    return EXIT_OK


async def _cmd_serve() -> int:
    import uvicorn

    from .server import build_server, with_auth

    settings = get_settings()
    runtime = await build_runtime(settings)
    server = build_server(runtime)

    app = with_auth(server.streamable_http_app(streamable_http_path="/mcp"), runtime)

    config = uvicorn.Config(
        app,
        host=settings.host,
        port=settings.port,
        log_level="info",
    )
    print(f"MCP endpoint: http://{settings.host}:{settings.port}/mcp")
    try:
        await uvicorn.Server(config).serve()
    finally:
        await runtime.close()
    return EXIT_OK


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="mnemos-api", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    mint = sub.add_parser("mint-key", help="create an API key (shown once)")
    mint.add_argument("--tenant", required=True, help="slug or UUID")
    mint.add_argument("--scope", required=True, choices=["read", "write", "admin"])
    mint.add_argument("--label", required=True, help="what this key is for")

    sub.add_parser("posture", help="show the configured security posture")
    sub.add_parser("serve", help="run the MCP server")

    args = parser.parse_args()

    try:
        if args.command == "mint-key":
            return asyncio.run(_cmd_mint(args.tenant, args.scope, args.label))
        if args.command == "posture":
            return asyncio.run(_cmd_posture())
        return asyncio.run(_cmd_serve())
    except KeyboardInterrupt:
        return 130
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
