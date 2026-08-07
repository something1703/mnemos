# Accounts & access

Inventory only. **No credential values ever appear in this file** — secrets live
in local `.env` (gitignored) and AWS Secrets Manager.

Last verified: 2026-08-07.

## GitHub

| Item | Value |
|---|---|
| Repository | `something1703/mnemos` (public) |
| License | Apache-2.0, present from the first commit |
| Default branch | `main` |

## CockroachDB Cloud

| Item | Value |
|---|---|
| Cluster name | `mnemos` |
| Cluster ID | `244bd64e-3168-4d39-a467-efad02a4dcb0` |
| Plan | **Basic** (free tier) |
| Provider / region | AWS `us-east-1` |
| Version | v26.2.5 (verified via MCP `get_cluster`, 2026-08-07) |
| SQL user | `rudrararaa` |
| Database | `defaultdb` today; `mnemos` from Phase 02.2 |
| CA certificate | `~/.postgresql/root.crt`, `sslmode=verify-full` |

The cluster ID is an identifier rather than a credential, and is committed in
`.mcp.json` so the repo works from a clean clone. Access still requires OAuth or
a service-account key.

### MCP access

| Principal | Mode | Notes |
|---|---|---|
| Development OAuth session | **write-capable** | ADR-012. Used read-only in practice; schema changes go through versioned migrations, never `create_table` over MCP |
| Custodian service account | read-only | Phase 07. Not yet created. Startup probe hard-fails if any write tool is reachable |

## AWS

| Item | Value |
|---|---|
| Account | `582054875648` |
| Principal | IAM user `mnemos` (**not** root) |
| Home region | `us-east-1` |
| Simulated jurisdictions | `eu-central-1`, `ap-south-1` — locality labels on the local 9-node rig only; nothing is deployed there |

Roles created per phase are documented with their grants **and their explicit
denies** in [security.md](security.md).

## Still to provision

- [ ] Bedrock model access — Claude + `amazon.titan-embed-text-v2:0` (Phase 05)
- [ ] KMS customer-managed key per tenant (Phase 02.4)
- [ ] S3 Object Lock bucket, compliance mode — **requires explicit user
      approval before creation; objects cannot be deleted until retention
      expires** (Phase 06.6)
- [ ] CockroachDB Cloud service account, read-only (Phase 07)
- [ ] Cockroach Labs community Slack
- [ ] Devpost registration
