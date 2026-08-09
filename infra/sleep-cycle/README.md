# Deployment — the sleep cycle on AWS Lambda

`make deploy-sleep-cycle` builds the arm64 image, pushes it to ECR, and syncs
the Lambda function, its Step Functions state machine, four EventBridge
schedules, and a CloudWatch error alarm. Idempotent — safe to re-run, and
doubles as the provisioning script for a fresh account.

No Function URL, no API Gateway. This service has no HTTP surface at all —
`lambda_handler.py` is invoked directly, either by EventBridge on a schedule
or by a Step Functions Task state, with a `{"stage": "..."}` event.

## The four schedules

| Rule | Cadence | Invokes | Stage |
|---|---|---|---|
| `mnemos-sleep-cycle-hourly-light` | hourly | Lambda directly | `consolidate`, `limit=5` |
| `mnemos-sleep-cycle-nightly` | 03:00 UTC | the state machine | `consolidate` (`limit=200`) → `checkpoint` |
| `mnemos-sleep-cycle-weekly-decay` | Sunday 04:00 UTC | Lambda directly | `decay` |
| `mnemos-sleep-cycle-hourly-checkpoint` | hourly | Lambda directly | `checkpoint` |

**"Nightly full cycle" is a large page, not a loop-until-empty sweep.** The
state machine's `Consolidate` step passes `limit=200` — the phase plan's
"gather → distill → embed → revise → promote" happens inside that single
Task (one `consolidate_batch` call per session, exactly as it does for the
hourly light run), not decomposed into a Step Functions Map state per
session. That decomposition would multiply cold starts and state
transitions for zero functional benefit: `consolidate_batch` already commits
one serializable transaction per session, which is a stronger atomicity
guarantee than Step Functions could add on top. What Step Functions
contributes here is what the phase plan actually values it for — per-stage
retry and a visible execution history distinguishing "consolidation failed"
from "consolidation succeeded but the checkpoint didn't" — and a two-state
chain delivers that without the added machinery. `limit=200` is honest about
being a large page sized for hackathon data volumes, not a claim of
unbounded throughput; a backlog larger than that drains over several nights
rather than several minutes.

## Why the execution role has no KMS permissions

Unlike the API's execution role, this one grants nothing beyond reading its
own Secrets Manager secret. The live encrypt/decrypt path both services use
(`Envelope(LocalKeyWrapper())`, see `runtime.py`) is a local AES key, not AWS
KMS — `packages/warden`'s `KmsKeyProvider` exists for the Warden's
crypto-shred custody, a separate code path this service never touches. A
role granting KMS permissions no code here calls would be over-provisioned
relative to what actually runs; it isn't granted.

## Step Functions gets its own execution role

`mnemos-sleep-cycle-states-exec`, trusted by `states.amazonaws.com`, holding
exactly one permission: `lambda:InvokeFunction` on this one function ARN.
Kept separate from the Lambda's own execution role (`mnemos-sleep-cycle-exec`,
trusted by `lambda.amazonaws.com`) rather than one role serving both trust
relationships — blurring "the code that runs" and "the orchestrator that
calls it" into one principal is exactly the kind of privilege-boundary
shortcut this project avoids everywhere else.

## Step Functions `Retry` matches what Lambda actually reports

`state_machine.json`'s `ErrorEquals` lists `"LLMError"`, not
`"mnemos_engine.llm.LLMError"`. AWS Lambda's Python runtime reports an
unhandled exception's `errorType` as the exception class's bare name, not its
fully-qualified module path — verified against a real invocation, not
assumed, the same discipline the rest of this project applies to model and
provider behaviour.

## Warm-container event loop reuse

`lambda_handler.py` drives every invocation through one event loop cached at
module scope (`_get_loop`), not a fresh `asyncio.run()` per call. A plain
`asyncio.run()` would open and close a loop every invocation while
`psycopg_pool.AsyncConnectionPool` stays cached across invocations bound to
whichever loop opened it — the second warm invocation would hand the pool a
loop that had already closed. Same class of bug the API's Lambda handler hit
with Mangum re-running the ASGI lifespan per request
(`services/api/src/mnemos_api/asgi.py`), same fix shape: one loop, kept alive
for the life of the execution environment.

## Database role

`MNEMOS_DB_URL_PIPELINE` — the `mnemos_pipeline_svc` login, granted
`mnemos_pipeline`. That role carries `BYPASSRLS` for cross-tenant batch
discovery, but **`BYPASSRLS` does not propagate through `GRANT role TO
login`** — it is a role attribute, not a table privilege, matching real
PostgreSQL semantics. `db/scripts/provision_users.py` grants it to the login
directly; `tests/sleep_cycle/test_pipeline_role.py` regression-tests it by
connecting as the login itself, not as a superuser that would bypass RLS
regardless and hide the gap. No DELETE grant anywhere, either way — decay
lowers strength, quarantine changes a trust field, neither removes a row.
