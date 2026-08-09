# Costs — the Custodian on ECS Fargate

PHASE_07 7.6 asks for a cost note here: "~10 compute-hours/month." This
covers only the Custodian's own AWS footprint (`infra/custodian/`) — the
API and sleep-cycle Lambdas predate this file and are not re-costed here.

## Measured, not estimated

Both `mnemos-custodian-scheduled` and `mnemos-custodian-alarm-triggered`
were fired for real during deployment (2026-08-09) — one scheduled run, one
manually-triggered alarm run, one deploy-time smoke test. Actual task
duration, from ECS's own `startedAt`/`stoppedAt`:

| Task | Duration |
|---|---|
| Smoke test (`--trigger manual`) | 77.6s |
| Scheduled (`--trigger schedule`) | 60.7s |

Call it 90 seconds per sweep as a rounded-up planning number — most of that
is nine MCP tool calls plus two Cloud REST API calls, each a real network
round trip to `cockroachlabs.cloud`, not CPU-bound work.

## Fargate compute

Real on-demand pricing, `us-east-1`, ARM64 (Graviton), looked up via
`aws pricing get-products` rather than assumed: **$0.03238 / vCPU-hour**,
**$0.00356 / GB-hour** (effective 2026-07-01).

At 0.25 vCPU / 0.5 GB and 90 seconds (0.025 hours) per run:

```
vCPU:   0.25 × $0.03238 × 0.025h ≈ $0.00020
memory: 0.50 × $0.00356 × 0.025h ≈ $0.00004
                                  ≈ $0.00025 per sweep
```

The schedule alone (`rate(6 hours)` = 4/day) is **120 runs/month ≈ $0.03**.
Alarm-triggered runs add whatever the alarms actually fire — bounded in
practice by how often `mnemos-api-p95-latency`,
`mnemos-sleep-cycle-consolidation-lag`, and `mnemos-sleep-cycle-errors`
genuinely misbehave, not by anything the Custodian itself controls. Even a
noisy month of one alarm-triggered sweep an hour (730 extra runs) only adds
another ≈$0.18. This is nowhere near the phase plan's "~10 compute-hours/
month" ballpark — the actual number is closer to 5 **task**-hours/month
(120 runs × 90s ÷ 3600), and Fargate bills vCPU-hours and GB-hours
separately from wall-clock task-hours, so neither figure is really "10
compute-hours" in the AWS-bill sense. The plan's estimate was a
conservative upper bound written before the real skill set (and its actual
per-tool latency) existed; the measured number is smaller.

## Everything else this service touches

| Resource | Monthly cost |
|---|---|
| ECR repository (image storage, ~200 MB) | ~$0.02 (first 500 MB/month free) |
| CloudWatch Logs (`/ecs/mnemos-custodian`, text, low volume) | well under $0.01 |
| Secrets Manager (`mnemos/custodian`, one secret) | $0.40 flat + ~$0.00 in API calls at this call volume |
| Data transfer out (a handful of small JSON/SQL-result payloads per run) | negligible |

**Total: well under $1/month**, dominated by Secrets Manager's flat $0.40
per-secret charge rather than by any actual compute. No NAT gateway (the
task uses a public IP directly — see `infra/custodian/README.md`), which
would otherwise have been the single largest line item on this list by a
wide margin (NAT gateways bill hourly regardless of a workload's own
duty cycle).
