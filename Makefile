.DEFAULT_GOAL := help
SHELL := /bin/bash

# Mnemos — see MASTER_PLAN.md. Every target here is referenced by a phase file
# or by the README quickstart. If a target stops working, the quickstart is
# broken and Phase 12's judge simulation will fail.

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
.PHONY: setup
setup: ## Install toolchain + all workspace deps
	@command -v uv >/dev/null || { echo "uv not found: https://docs.astral.sh/uv/"; exit 1; }
	uv sync --all-packages --group dev
	@command -v pnpm >/dev/null && pnpm install --frozen-lockfile || \
		echo "note: pnpm not installed — apps/console skipped (Phase 08)"

# ---------------------------------------------------------------------------
# Quality gates
# ---------------------------------------------------------------------------
.PHONY: lint
lint: ## ruff check + format check
	uv run ruff check .
	uv run ruff format --check .

.PHONY: fmt
fmt: ## Auto-format
	uv run ruff check --fix .
	uv run ruff format .

.PHONY: typecheck
typecheck: ## mypy (strict on packages/)
	uv run mypy

.PHONY: test
test: ## Unit tests (no cloud, no rig, no AWS)
	uv run pytest -m "not cloud and not rig and not aws and not slow"

.PHONY: test-aws
test-aws: ## Tests needing real AWS resources (KMS keys, S3 Object Lock bucket)
	uv run pytest -m aws -v

.PHONY: cov
cov: ## Coverage gate: packages/ must hold 90%
	uv run pytest -m "not cloud and not rig and not aws and not slow" \
		--cov=packages --cov-report=term-missing --cov-fail-under=90

.PHONY: invariants
invariants: ## Prove all five sacred invariants (~60s). Cited in the README.
	uv run pytest -m invariant -v

.PHONY: no-delete-in-engine
no-delete-in-engine: ## Invariant 1, statically: the engine may not contain DELETE
	@# Each pattern requires a following SQL keyword so that prose describing this
	@# guard does not trip it. A bare /TRUNCATE/ matched this rule's own docstring.
	@! grep -rniE '\b(DELETE[[:space:]]+FROM|DROP[[:space:]]+(TABLE|SCHEMA|DATABASE)|TRUNCATE[[:space:]]+TABLE)\b' \
		packages/engine services/api services/sleep-cycle services/custodian \
		--include='*.py' --include='*.sql' 2>/dev/null \
		|| { echo "FAIL: destructive SQL outside packages/warden (invariant 1)"; exit 1; }
	@echo "OK: no destructive SQL outside the Warden"

.PHONY: no-model-in-warden
no-model-in-warden: ## Invariant 1, statically: the Warden may not import an LLM client
	@# Match imports and client construction, NOT prose. The Warden's own docs
	@# describe the bedrock:InvokeModel deny, and a naive keyword grep would flag
	@# the documentation of the guarantee as a violation of it.
	@! grep -rnE "^[[:space:]]*(import|from)[[:space:]]+(anthropic|openai|langchain|litellm|cohere|ollama|mistralai|google\.generativeai)" \
		packages/warden services/warden --include='*.py' 2>/dev/null \
		|| { echo "FAIL: LLM SDK imported inside the Warden (invariant 1)"; exit 1; }
	@! grep -rnE "(client|resource)\([\"'][^\"']*bedrock" \
		packages/warden services/warden --include='*.py' 2>/dev/null \
		|| { echo "FAIL: Bedrock client constructed inside the Warden (invariant 1)"; exit 1; }
	@echo "OK: no model dependency in the Warden"

.PHONY: secrets
secrets: ## gitleaks over the full history
	@command -v gitleaks >/dev/null || { echo "install gitleaks: brew install gitleaks"; exit 1; }
	gitleaks detect --no-banner --redact

.PHONY: check
check: lint typecheck test no-delete-in-engine no-model-in-warden ## Everything CI runs

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
.PHONY: db-local
db-local: ## Single-node CockroachDB in Docker (tests + dev)
	docker compose -f db/docker/single-node.yml up -d --wait
	@# Separate -e flags: multiple statements in one -e form a single transaction,
	@# and SET CLUSTER SETTING cannot run inside one.
	@docker compose -f db/docker/single-node.yml exec -T crdb ./cockroach sql --insecure \
		-e "CREATE DATABASE IF NOT EXISTS mnemos" \
		-e "SET CLUSTER SETTING feature.vector_index.enabled = true" \
		-e "SET CLUSTER SETTING kv.rangefeed.enabled = true"
	@echo "ready: vector indexes + rangefeeds enabled (see docs/cluster-capabilities.md)"

.PHONY: db-multiregion
db-multiregion: ## 9-node rig across 3 simulated localities (residency + node-kill)
	docker compose -f db/docker/multi-region.yml up -d
	bash db/scripts/init-rig.sh

.PHONY: db-down
db-down: ## Tear down all local clusters
	-docker compose -f db/docker/single-node.yml down -v
	-docker compose -f db/docker/multi-region.yml down -v

.PHONY: db-probe
db-probe: ## Phase 02.1 capability probe -> docs/cluster-capabilities.md
	uv run python db/scripts/probe.py

.PHONY: db-migrate
db-migrate: ## Apply migrations (idempotent from zero)
	uv run python db/scripts/migrate.py up

.PHONY: db-seed
db-seed: ## Seed tenants, agents, episodes, facts, one poisoned source, one hold
	uv run python db/seed.py

# ---------------------------------------------------------------------------
# Verification (Phase 03.8 / 06.6)
# ---------------------------------------------------------------------------
.PHONY: verify-ledger
verify-ledger: ## Walk every shard, recompute every hash
	uv run mnemos-verify --tenant $${TENANT:-demo}

.PHONY: attest
attest: ## Verify the live chain against the S3-anchored Merkle root
	uv run mnemos-attest verify --tenant $${TENANT:-demo}

# ---------------------------------------------------------------------------
# Demos (Phase 09)
# ---------------------------------------------------------------------------
.PHONY: demo-continuity
demo-continuity: ## Pillar I — cross-border clinic: recall, residency, erasure, legal hold
	bash demos/continuity/run.sh

.PHONY: demo-contagion
demo-contagion: ## Pillar III — poisoned runbook, blast radius, revoke_source
	bash demos/contagion/run.sh

.PHONY: demo-deposition
demo-deposition: ## Pillar II — temporal recall, deposition, offline verification
	bash demos/deposition/run.sh

.PHONY: demo-resilience
demo-resilience: ## Node kill, pipeline kill, Bedrock outage
	bash demos/resilience.sh

.PHONY: demo-all
demo-all: demo-continuity demo-contagion demo-deposition ## All three, <12 min

# ---------------------------------------------------------------------------
# Red team (Phase 10)
# ---------------------------------------------------------------------------
.PHONY: redteam
redteam: ## Full six-class attack suite against our own stack
	uv run pytest redteam/ -v

.PHONY: redteam-ci
redteam-ci: ## The regression subset that runs on every PR
	uv run pytest redteam/ -m "not slow and not aws" -v
