"""Phase 04/05 — service user provisioning.

Creates one LOGIN user per service role (`mnemos_api`, `mnemos_pipeline`,
`mnemos_warden`), grants it the role, and grants CONNECT. Referenced by
migration 011's comment since the roles themselves were created, but never
actually built until this bit: `mnemos_pipeline_svc` was hand-provisioned
with `GRANT mnemos_pipeline TO mnemos_pipeline_svc` and connected to the live
cluster seeing zero rows on a cross-tenant query that should have returned
thirteen.

**Why:** `BYPASSRLS`, like `SUPERUSER`, is a role *attribute*, not a table
privilege — CockroachDB matches real PostgreSQL semantics here, where
LOGIN/SUPERUSER/BYPASSRLS/CREATEDB/CREATEROLE do not propagate through
`GRANT role TO user` membership the way SELECT/INSERT/UPDATE do. `mnemos_
pipeline` carries `BYPASSRLS` (migration 011); a login only granted that
role does not inherit it, and the failure mode is silent — no error, just an
empty result that reads as "nothing to consolidate" instead of "wrong
permissions". This script grants `BYPASSRLS` directly to the pipeline login,
and `tests/sleep_cycle/test_pipeline_role.py` regression-tests it against a
login connection, not the admin superuser that would hide the gap.

**Never overwrites a password silently.** Re-running this script is "ensure
these users and grants exist" — a login that already exists keeps its
password unless named in `--rotate`, so this can run idempotently as part of
a deploy without invalidating a credential already sitting in Secrets
Manager.

Usage:
    uv run python db/scripts/provision_users.py --url "$MNEMOS_DB_URL"
    uv run python db/scripts/provision_users.py --url "$MNEMOS_DB_URL" \\
        --rotate mnemos_pipeline_svc --rotate mnemos_api_svc
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import string
import sys
from dataclasses import dataclass
from urllib.parse import quote, urlsplit, urlunsplit

try:
    import psycopg
except ModuleNotFoundError:  # pragma: no cover - dependency guidance
    sys.exit("psycopg is not installed. Run: uv sync --all-packages --group dev")

PASSWORD_ALPHABET = string.ascii_letters + string.digits
PASSWORD_LENGTH = 28


@dataclass(frozen=True)
class ServiceRole:
    role: str
    login: str
    # Role attributes that do not propagate through `GRANT role TO login` and
    # must be applied to the login directly. Empty for roles that only need
    # ordinary table privileges (which DO propagate, and are already granted
    # via GRANT ROLE + the role's own GRANT TABLE statements in migration
    # 011).
    login_attributes: tuple[str, ...] = ()


SERVICE_ROLES = (
    ServiceRole(role="mnemos_api", login="mnemos_api_svc"),
    ServiceRole(
        role="mnemos_pipeline", login="mnemos_pipeline_svc", login_attributes=("BYPASSRLS",)
    ),
    ServiceRole(role="mnemos_warden", login="mnemos_warden_svc"),
)


def _generate_password() -> str:
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(PASSWORD_LENGTH))


def _build_dsn(admin_url: str, login: str, password: str) -> str:
    parts = urlsplit(admin_url)
    host = parts.netloc.split("@")[-1]
    netloc = f"{login}:{quote(password, safe='')}@{host}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def provision(
    admin_url: str,
    *,
    roles: tuple[ServiceRole, ...] = SERVICE_ROLES,
    rotate: frozenset[str] = frozenset(),
) -> dict[str, str]:
    """Ensure each service login exists with correct grants and attributes.

    Returns a dict of `{login: dsn}` — but ONLY for logins that were just
    created or rotated in this call. A login that already existed and was not
    named in `rotate` is fully provisioned (grants/attributes reconciled) but
    its password is not known to this process, so it is not in the return
    value; nothing to show, nothing to leak.
    """
    minted: dict[str, str] = {}
    with (
        psycopg.connect(admin_url, autocommit=True, connect_timeout=20) as conn,
        conn.cursor() as cur,
    ):
        for service in roles:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (service.login,))
            exists = cur.fetchone() is not None
            should_mint = not exists or service.login in rotate

            if should_mint:
                password = _generate_password()
                cur.execute(f"CREATE USER IF NOT EXISTS {service.login}")
                cur.execute(f"ALTER USER {service.login} WITH PASSWORD %s", (password,))
                minted[service.login] = _build_dsn(admin_url, service.login, password)

            cur.execute(f"GRANT {service.role} TO {service.login}")
            cur.execute(f"GRANT CONNECT ON DATABASE mnemos TO {service.login}")
            for attribute in service.login_attributes:
                # ALTER USER ... <ATTRIBUTE> with no argument sets it true;
                # applied every run, not just on creation, so a role whose
                # required attributes changed after this login already
                # existed still gets reconciled next time this runs.
                cur.execute(f"ALTER USER {service.login} {attribute}")

    return minted


def main() -> int:
    parser = argparse.ArgumentParser(prog="provision_users.py", description=__doc__)
    parser.add_argument(
        "--url", default=os.environ.get("MNEMOS_DB_URL"), help="admin connection string"
    )
    parser.add_argument(
        "--rotate",
        action="append",
        default=[],
        metavar="LOGIN",
        help="force a new password for this login even if it already exists (repeatable)",
    )
    args = parser.parse_args()

    if not args.url:
        print("No database URL. Set MNEMOS_DB_URL or pass --url.", file=sys.stderr)
        return 2

    minted = provision(args.url, rotate=frozenset(args.rotate))

    for service in SERVICE_ROLES:
        status = "minted" if service.login in minted else "already provisioned"
        attrs = f" [{', '.join(service.login_attributes)}]" if service.login_attributes else ""
        print(f"  {service.login:<24} -> {service.role}{attrs}  ({status})")

    if minted:
        print(
            "\nNew credentials — shown once, not stored anywhere by this script. "
            "Put them in .env / Secrets Manager now:\n"
        )
        print(json.dumps(minted, indent=2))
    else:
        print("\nNo new credentials minted. Use --rotate LOGIN to force a new password.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
