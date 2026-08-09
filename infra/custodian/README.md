# Deployment — the Custodian on AWS ECS Fargate

`make deploy-custodian` builds the arm64 image, pushes it to ECR, and syncs
an ECS cluster, task definition, task execution + task IAM roles, a security
group, and two EventBridge rules. Idempotent — safe to re-run, and doubles
as the provisioning script for a fresh account.

## Why Fargate, not Lambda

Unlike `infra/api` and `infra/sleep-cycle`, this is not a Lambda image. The
Custodian is a scheduled batch job that also needs to react to an alarm —
"run one sweep and exit" maps onto EventBridge's `RunTask` target for an ECS
task more directly than it does onto Lambda's request/response shape, and a
sweep genuinely opening four live connections (DB, two Cloud MCP sessions,
one REST client) has no request to hold open either way. No Function URL, no
API Gateway, no state machine — one task definition, run on a schedule and
on demand.

## The two rules

| Rule | Trigger | `--trigger` passed |
|---|---|---|
| `mnemos-custodian-scheduled` | `rate(6 hours)` | `schedule` |
| `mnemos-custodian-alarm-triggered` | any of 3 alarms entering `ALARM` | `alarm --detail <alarm name>` |

The alarm rule matches on the event's `resources` field (each alarm's own
ARN) rather than three near-identical rules — an `InputTransformer` carries
the actual alarm name from `$.detail.alarmName` into the container command's
`--detail` flag, so one CloudWatch Logs line always says which alarm fired.

The three alarms are each owned by the service they watch, not recreated
here:

- `mnemos-api-p95-latency` — `infra/api/deploy.sh`
- `mnemos-sleep-cycle-consolidation-lag` — `infra/sleep-cycle/deploy.sh`
- `mnemos-sleep-cycle-errors` — `infra/sleep-cycle/deploy.sh`

## Two IAM roles, deliberately unequal

**Task execution role** (`mnemos-custodian-exec`, trusted by
`ecs-tasks.amazonaws.com`) — `AmazonECSTaskExecutionRolePolicy` (ECR pull,
log group write) plus `secretsmanager:GetSecretValue` on
`mnemos/custodian-*`. This is what resolves the task definition's `secrets`
list into container env vars at RunTask time — the app itself never calls
Secrets Manager.

**Task role** (`mnemos-custodian-task`, same trust) — carries **no Allow
statements at all**. `mnemos_custodian` has no AWS SDK dependency; every
credential arrives as a plain env var, resolved by the execution role above.
`task-policy.json` is two explicit `Deny` statements instead: no
`lambda:InvokeFunction` on any `mnemos-warden-*` function (none is deployed
yet — this is forward-looking, the same shape as the Makefile's
`no-warden-in-custodian` check at the code layer), and no
`kms:ScheduleKeyDeletion` / `kms:DisableKey` on the three tenant CMKs.
Nothing in this role's Allow surface could reach either action regardless —
the point of stating it as an explicit `Deny` is that it stays true even if
something broader is ever attached to this role by mistake later. PHASE_07's
Definition of Done asks for "enforced by code *and* IAM *and* tests"; this
is the IAM layer.

## Networking: public IP, no NAT gateway

The task runs in the default VPC's public subnets with
`AssignPublicIp=ENABLED` and a dedicated security group
(`mnemos-custodian-task`) with **no inbound rules** — it accepts no
connections, only calls out to CockroachDB Cloud, the deployed API, and
OpenAI. A NAT gateway would cost more per hour than the task spends running
in a month; a public IP on a task that makes outbound HTTPS calls and holds
no long-lived listener is the cheaper and simpler choice for this workload.

## `sslrootcert=system`, verified from inside the actual image

`python:3.12-slim` ships no CA bundle at all. `psycopg[binary]`'s bundled
OpenSSL doesn't automatically trust whatever `SSL_CERT_FILE`/`SSL_CERT_DIR`
point at unless the connection string says `sslrootcert=system` — without
it, `sslmode=verify-full` falls back to libpq's classic default of
`~/.postgresql/root.crt`, which doesn't exist in the container. The deploy
script appends `&sslrootcert=system` to `MNEMOS_DB_URL_CUSTODIAN` before
writing the secret, and this was verified by actually running
`docker run mnemos-custodian:local` against the real cluster before being
wired into EventBridge — the same "measured, not assumed" standard the rest
of this project holds itself to.

## `boto3` was a dependency with nothing calling it

`services/custodian/pyproject.toml` declared `boto3>=1.35` since sub-phase
7.1, left over from an earlier assumption that the app would fetch its own
secrets (the same pattern `mnemos-sleep-cycle`'s Lambda handler uses).
Nothing in `mnemos_custodian` ever imported it. Removed rather than kept
"just in case" — the native ECS `secrets` field on the task definition does
the same job without giving the app code an AWS SDK it has no other reason
to hold.
