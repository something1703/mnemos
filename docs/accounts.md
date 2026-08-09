# Accounts & access

Inventory only. **No credential values ever appear in this file** — secrets live
in local `.env` (gitignored) and AWS Secrets Manager.

Last verified: 2026-08-08.

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
| Custodian service account (`mnemos-custodian2`) | **write-capable** (Cluster Admin) | Phase 07. Created 2026-08-09. Genuinely read-only is not achievable via any Cloud IAM role for this integration — see `docs/limits.md` "The Custodian's credential is not platform-enforced read-only". The guarantee is enforced in `mnemos_custodian.allowlist` + `CustodianMcpClient.call_tool()`'s backstop instead, both tested against the real server |

## AWS

| Item | Value |
|---|---|
| Account | `582054875648` |
| Principal | IAM user `mnemos` (**not** root) |
| Home region | `us-east-1` |
| Simulated jurisdictions | `eu-central-1`, `ap-south-1` — locality labels on the local 9-node rig only; nothing is deployed there |

Roles created per phase are documented with their grants **and their explicit
denies** in [security.md](security.md).

### KMS (ADR-013, provisioned 2026-08-08)

| Tenant | Alias | Key ARN | Rotation |
|---|---|---|---|
| clinic | `alias/mnemos-clinic` | `arn:...:key/e13f8b64-7407-46ea-b02f-062f1d77f8e6` | annual, on |
| ops | `alias/mnemos-ops` | `arn:...:key/688aaeda-b4f0-4a86-823d-f5b3ad3981d2` | annual, on |
| finance | `alias/mnemos-finance` | `arn:...:key/fd472753-f7ff-4456-b8b0-faf54fc21210` | annual, on |

Full ARNs in local `.env` (`MNEMOS_KMS_KEY_ARN_*`), never committed. Key
policy grants the `mnemos` IAM user usage and `ScheduleKeyDeletion` directly —
no separate Warden execution role is deployed yet; see ADR-013.

### S3 (ADR-013, provisioned 2026-08-08)

| Item | Value |
|---|---|
| Bucket | `mnemos-ledger-anchor-582054875648` |
| Purpose | Merkle-root checkpoint anchoring (ledger attestation) |
| Object Lock | Enabled at creation, **COMPLIANCE mode, 7-day retention** |
| Retained through | ≥ 2026-08-15 (past the hackathon deadline and judging window) |
| Public access | Blocked (all four settings) — deviates from the original "public-readable" phase sketch; see ADR-013 for why |
| Encryption | SSE-S3 default |
| Versioning | Enabled (required by, and auto-enabled with, Object Lock) |

**This bucket cannot be emptied or deleted before 2026-08-15**, even by
account root, even if the project is torn down early. Confirmed empirically
before any code was written against it (ADR-013).

### ECS Fargate (PHASE_07 7.6, provisioned 2026-08-09)

| Item | Value |
|---|---|
| Cluster | `mnemos-custodian` |
| Task definition | `mnemos-custodian` (0.25 vCPU / 0.5 GB, ARM64) |
| ECR repository | `mnemos-custodian` |
| Task execution role | `mnemos-custodian-exec` — `AmazonECSTaskExecutionRolePolicy` + `secretsmanager:GetSecretValue` on `mnemos/custodian-*` |
| Task role | `mnemos-custodian-task` — no `Allow` statements; explicit `Deny` only (see security.md) |
| Secret | `mnemos/custodian` |
| Log group | `/ecs/mnemos-custodian` |
| Security group | `mnemos-custodian-task` — no inbound rules |
| Schedule | `mnemos-custodian-scheduled`, `rate(6 hours)` |
| Alarm rule | `mnemos-custodian-alarm-triggered`, reacts to `mnemos-api-p95-latency`, `mnemos-sleep-cycle-consolidation-lag`, `mnemos-sleep-cycle-errors` |

Full detail and the reasoning behind each choice: `infra/custodian/README.md`.

## Still to provision

- [ ] Bedrock model access — Claude + `amazon.titan-embed-text-v2:0` (Phase 05)
- [ ] Scheduled anchoring (EventBridge) — today `mnemos-attest anchor` is
      manual; see docs/limits.md
- [ ] Dedicated Warden execution role — would narrow the KMS key policy's
      `ScheduleKeyDeletion` grant off the `mnemos` IAM user (Phase 04 deploy)
- [x] CockroachDB Cloud service account (Phase 07) — provisioned 2026-08-09,
      not read-only (see MCP access table above)
- [ ] Cockroach Labs community Slack
- [ ] Devpost registration
