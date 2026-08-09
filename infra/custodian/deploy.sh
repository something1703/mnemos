#!/usr/bin/env bash
#
# Build, push and deploy the Mnemos Custodian to AWS ECS Fargate, plus its
# EventBridge schedule and alarm-triggered rule (PHASE_07 7.6).
#
#   make deploy-custodian
#   SKIP_BUILD=1 make deploy-custodian
#
# Idempotent, same shape as infra/api and infra/sleep-cycle's deploy.sh:
# every resource is created only if missing, so this also works as the
# provisioning script for a fresh account.
#
# ECS Fargate, not Lambda, unlike the other two services: the Custodian is a
# scheduled batch job that also needs to react to an alarm, and a plain
# Fargate task started by EventBridge's RunTask target is a closer match to
# that than forcing "run one sweep and exit" into Lambda's request/response
# shape. No Function URL, no API Gateway, no state machine — one task
# definition, run on a schedule and on demand.
#
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
REPO="${MNEMOS_ECR_REPO:-mnemos-custodian}"
SECRET_NAME="${MNEMOS_SECRET_NAME:-mnemos/custodian}"
EXEC_ROLE_NAME="${MNEMOS_ECS_EXEC_ROLE:-mnemos-custodian-exec}"
TASK_ROLE_NAME="${MNEMOS_ECS_TASK_ROLE:-mnemos-custodian-task}"
EVENTS_ROLE_NAME="${MNEMOS_EVENTS_ROLE:-mnemos-custodian-events-exec}"
CLUSTER_NAME="${MNEMOS_ECS_CLUSTER:-mnemos-custodian}"
FAMILY="${MNEMOS_ECS_FAMILY:-mnemos-custodian}"
LOG_GROUP="${MNEMOS_LOG_GROUP:-/ecs/mnemos-custodian}"
SG_NAME="${MNEMOS_ECS_SG:-mnemos-custodian-task}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"
IMAGE="${REGISTRY}/${REPO}:latest"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

# --- image --------------------------------------------------------------
if [[ "${SKIP_BUILD:-0}" != "1" ]]; then
  say "Building ${IMAGE} (linux/arm64)"
  aws ecr describe-repositories --repository-names "$REPO" --region "$REGION" >/dev/null 2>&1 \
    || aws ecr create-repository --repository-name "$REPO" --region "$REGION" \
         --image-scanning-configuration scanOnPush=true >/dev/null

  aws ecr get-login-password --region "$REGION" \
    | docker login --username AWS --password-stdin "$REGISTRY" >/dev/null

  docker build --platform linux/arm64 --provenance=false --sbom=false \
    -f "$HERE/Dockerfile" -t "$IMAGE" "$ROOT"
  docker push "$IMAGE"
fi

# --- secret ---------------------------------------------------------------
# Everything build_runtime() needs (runtime.py's load_settings), read
# straight from .env — same quote-stripping fix as infra/sleep-cycle/
# deploy.sh, for the same reason: dotenv quotes DSNs for `source .env; set
# -a`, which a plain line parser has to undo itself.
say "Syncing ${SECRET_NAME}"
SECRET_JSON="$(
  ROOT="$ROOT" python3 - <<'PY'
import json, os, pathlib

wanted = {
    "MNEMOS_DB_URL_CUSTODIAN", "COCKROACH_MCP_URL", "COCKROACH_MCP_CLUSTER_ID",
    "COCKROACH_SERVICE_ACCOUNT_KEY_CUSTODIAN", "MNEMOS_API_URL",
    "MNEMOS_API_KEY_CUSTODIAN", "MNEMOS_CUSTODIAN_TENANT_SLUG",
    "OPENAI_API_KEY", "OPENAI_CUSTODIAN_MODEL", "OPENAI_DISTILL_MODEL",
}
out = {}
for line in pathlib.Path(os.environ["ROOT"], ".env").read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, _, value = line.partition("=")
    if key in wanted and value:
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        out[key] = value

missing = {
    "MNEMOS_DB_URL_CUSTODIAN", "COCKROACH_MCP_CLUSTER_ID",
    "COCKROACH_SERVICE_ACCOUNT_KEY_CUSTODIAN", "MNEMOS_API_URL",
    "MNEMOS_API_KEY_CUSTODIAN", "MNEMOS_CUSTODIAN_TENANT_SLUG", "OPENAI_API_KEY",
} - out.keys()
if missing:
    raise SystemExit(f"missing from .env: {', '.join(sorted(missing))}")
if "OPENAI_CUSTODIAN_MODEL" not in out and "OPENAI_DISTILL_MODEL" not in out:
    raise SystemExit("need OPENAI_CUSTODIAN_MODEL or OPENAI_DISTILL_MODEL in .env")

# The task definition's "secrets" list references every key in `wanted`
# unconditionally (ECS resolves each at RunTask time, and errors on a
# missing key) — so every key that has a code-level default also gets one
# here, guaranteeing all ten are always present rather than teaching the
# task definition to vary with what happens to be in .env.
out.setdefault("COCKROACH_MCP_URL", "https://cockroachlabs.cloud/mcp")
out.setdefault("OPENAI_CUSTODIAN_MODEL", out.get("OPENAI_DISTILL_MODEL", ""))
out.setdefault("OPENAI_DISTILL_MODEL", out.get("OPENAI_CUSTODIAN_MODEL", ""))

# psycopg[binary]'s bundled OpenSSL needs pointing at a real CA bundle
# (Dockerfile sets SSL_CERT_FILE/SSL_CERT_DIR) rather than the classic
# libpq default of ~/.postgresql/root.crt, which does not exist in the
# container — sslrootcert=system is what tells libpq to trust the system
# store those env vars populate. Verified against the real cluster from
# inside this exact image before being treated as settled.
url = out["MNEMOS_DB_URL_CUSTODIAN"]
if "sslmode=verify-full" in url and "sslrootcert" not in url:
    out["MNEMOS_DB_URL_CUSTODIAN"] = url + "&sslrootcert=system"

print(json.dumps(out))
PY
)"

if aws secretsmanager describe-secret --secret-id "$SECRET_NAME" --region "$REGION" >/dev/null 2>&1; then
  aws secretsmanager put-secret-value --secret-id "$SECRET_NAME" \
    --secret-string "$SECRET_JSON" --region "$REGION" >/dev/null
else
  aws secretsmanager create-secret --name "$SECRET_NAME" \
    --description "Mnemos Custodian runtime configuration" \
    --secret-string "$SECRET_JSON" --region "$REGION" >/dev/null
fi
SECRET_ARN="$(aws secretsmanager describe-secret --secret-id "$SECRET_NAME" \
  --region "$REGION" --query ARN --output text)"

# --- log group --------------------------------------------------------------
aws logs describe-log-groups --log-group-name-prefix "$LOG_GROUP" --region "$REGION" \
  --query "logGroups[?logGroupName=='${LOG_GROUP}']" --output text | grep -q . \
  || aws logs create-log-group --log-group-name "$LOG_GROUP" --region "$REGION"

# --- iam: task execution role (pulls the image, resolves secrets, writes logs) ---
if ! aws iam get-role --role-name "$EXEC_ROLE_NAME" >/dev/null 2>&1; then
  say "Creating task execution role ${EXEC_ROLE_NAME}"
  aws iam create-role --role-name "$EXEC_ROLE_NAME" \
    --assume-role-policy-document "file://$HERE/trust-policy.json" >/dev/null
  aws iam attach-role-policy --role-name "$EXEC_ROLE_NAME" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
fi
aws iam put-role-policy --role-name "$EXEC_ROLE_NAME" --policy-name mnemos-custodian-secrets \
  --policy-document "file://$HERE/exec-policy.json"
EXEC_ROLE_ARN="$(aws iam get-role --role-name "$EXEC_ROLE_NAME" --query Role.Arn --output text)"

# --- iam: task role (what the running app code would use, if it ever called
# AWS itself — it does not; mnemos_custodian has no boto3 dependency and
# every credential arrives as a plain env var. This role carries no Allow
# statements at all, only the explicit Deny in task-policy.json: forward-
# looking defense in depth so that if anything is ever attached to this role
# later, it still cannot invoke a Warden function or delete a KMS key,
# matching PHASE_07's "enforced by code *and* IAM *and* tests." ---
if ! aws iam get-role --role-name "$TASK_ROLE_NAME" >/dev/null 2>&1; then
  say "Creating task role ${TASK_ROLE_NAME}"
  aws iam create-role --role-name "$TASK_ROLE_NAME" \
    --assume-role-policy-document "file://$HERE/trust-policy.json" >/dev/null
fi
aws iam put-role-policy --role-name "$TASK_ROLE_NAME" --policy-name mnemos-custodian-boundary \
  --policy-document "file://$HERE/task-policy.json"
TASK_ROLE_ARN="$(aws iam get-role --role-name "$TASK_ROLE_NAME" --query Role.Arn --output text)"

# --- networking: default VPC's public subnets, one dedicated security group ---
# No inbound rules (this task accepts no connections; it only calls out to
# CockroachDB Cloud, the deployed API, and OpenAI). AssignPublicIp=ENABLED at
# run time, so no NAT gateway is needed for a task that runs a few minutes
# every few hours — a NAT gateway's own hourly charge would dwarf the task's.
VPC_ID="$(aws ec2 describe-vpcs --filters Name=is-default,Values=true --region "$REGION" \
  --query 'Vpcs[0].VpcId' --output text)"
SUBNETS="$(aws ec2 describe-subnets --filters "Name=vpc-id,Values=${VPC_ID}" \
  "Name=default-for-az,Values=true" --region "$REGION" \
  --query 'Subnets[].SubnetId' --output text | tr '\t' ',')"

SG_ID="$(aws ec2 describe-security-groups --region "$REGION" \
  --filters "Name=vpc-id,Values=${VPC_ID}" "Name=group-name,Values=${SG_NAME}" \
  --query 'SecurityGroups[0].GroupId' --output text)"
if [[ "$SG_ID" == "None" || -z "$SG_ID" ]]; then
  say "Creating security group ${SG_NAME}"
  SG_ID="$(aws ec2 create-security-group --region "$REGION" --vpc-id "$VPC_ID" \
    --group-name "$SG_NAME" \
    --description "Mnemos Custodian ECS task — outbound only, no inbound rules" \
    --query GroupId --output text)"
fi

# --- ecs cluster ------------------------------------------------------------
aws ecs create-cluster --cluster-name "$CLUSTER_NAME" --region "$REGION" >/dev/null
CLUSTER_ARN="arn:aws:ecs:${REGION}:${ACCOUNT}:cluster/${CLUSTER_NAME}"

# --- task definition (0.25 vCPU / 0.5 GB, PHASE_07 7.6) ---------------------
say "Registering task definition ${FAMILY}"
TASK_DEF_ARN="$(
  IMAGE="$IMAGE" FAMILY="$FAMILY" EXEC_ROLE_ARN="$EXEC_ROLE_ARN" TASK_ROLE_ARN="$TASK_ROLE_ARN" \
  LOG_GROUP="$LOG_GROUP" REGION="$REGION" SECRET_ARN="$SECRET_ARN" python3 - <<'PY'
import json, os, subprocess

secret_arn = os.environ["SECRET_ARN"]
secret_keys = [
    "MNEMOS_DB_URL_CUSTODIAN", "COCKROACH_MCP_URL", "COCKROACH_MCP_CLUSTER_ID",
    "COCKROACH_SERVICE_ACCOUNT_KEY_CUSTODIAN", "MNEMOS_API_URL",
    "MNEMOS_API_KEY_CUSTODIAN", "MNEMOS_CUSTODIAN_TENANT_SLUG",
    "OPENAI_API_KEY", "OPENAI_CUSTODIAN_MODEL", "OPENAI_DISTILL_MODEL",
]
definition = {
    "family": os.environ["FAMILY"],
    "requiresCompatibilities": ["FARGATE"],
    "networkMode": "awsvpc",
    "cpu": "256",
    "memory": "512",
    "executionRoleArn": os.environ["EXEC_ROLE_ARN"],
    "taskRoleArn": os.environ["TASK_ROLE_ARN"],
    "runtimePlatform": {"cpuArchitecture": "ARM64", "operatingSystemFamily": "LINUX"},
    "containerDefinitions": [{
        "name": "custodian",
        "image": os.environ["IMAGE"],
        "essential": True,
        # Each key becomes an env var of the same name — ECS resolves these
        # itself via the execution role at RunTask time, so the app never
        # calls Secrets Manager (or any AWS API) directly. The secret-sync
        # step above guarantees every one of these ten keys is present
        # (defaulting COCKROACH_MCP_URL and mirroring the two model-name
        # keys onto each other), so this list can be unconditional.
        "secrets": [
            {"name": k, "valueFrom": f"{secret_arn}:{k}::"} for k in secret_keys
        ],
        "logConfiguration": {
            "logDriver": "awslogs",
            "options": {
                "awslogs-group": os.environ["LOG_GROUP"],
                "awslogs-region": os.environ["REGION"],
                "awslogs-stream-prefix": "custodian",
            },
        },
    }],
}
out = subprocess.run(
    ["aws", "ecs", "register-task-definition", "--region", os.environ["REGION"],
     "--cli-input-json", json.dumps(definition),
     "--query", "taskDefinition.taskDefinitionArn", "--output", "text"],
    check=True, capture_output=True, text=True,
)
print(out.stdout.strip())
PY
)"

# --- eventbridge: shared role for both rules --------------------------------
say "Syncing EventBridge role and rules"
if ! aws iam get-role --role-name "$EVENTS_ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-role --role-name "$EVENTS_ROLE_NAME" \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"events.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
fi
EVENTS_ROLE_ARN="$(aws iam get-role --role-name "$EVENTS_ROLE_NAME" --query Role.Arn --output text)"
RUN_TASK_POLICY="$(python3 -c "
import json
print(json.dumps({
  'Version': '2012-10-17',
  'Statement': [
    {'Effect': 'Allow', 'Action': 'ecs:RunTask',
     'Resource': '$TASK_DEF_ARN'.rsplit(':', 1)[0] + ':*',
     'Condition': {'ArnLike': {'ecs:cluster': '$CLUSTER_ARN'}}},
    {'Effect': 'Allow', 'Action': 'iam:PassRole',
     'Resource': ['$EXEC_ROLE_ARN', '$TASK_ROLE_ARN'],
     'Condition': {'StringEquals': {'iam:PassedToService': 'ecs-tasks.amazonaws.com'}}},
  ],
}))
")"
aws iam put-role-policy --role-name "$EVENTS_ROLE_NAME" --policy-name run-custodian-sweep \
  --policy-document "$RUN_TASK_POLICY"

# --- eventbridge rules -------------------------------------------------------
_ecs_target() {
  # Builds the EventBridge target JSON in Python — same reasoning as
  # infra/sleep-cycle/deploy.sh's _rule(): the CLI's shorthand target syntax
  # cannot survive the JSON braces/commas nested inside EcsParameters/Input.
  local input_or_transformer="$1"
  python3 -c "
import json
target = {
  'Id': '1',
  'Arn': '$CLUSTER_ARN',
  'RoleArn': '$EVENTS_ROLE_ARN',
  'EcsParameters': {
    'TaskDefinitionArn': '$TASK_DEF_ARN',
    'TaskCount': 1,
    'LaunchType': 'FARGATE',
    'PlatformVersion': 'LATEST',
    'NetworkConfiguration': {
      'awsvpcConfiguration': {
        'Subnets': '$SUBNETS'.split(','),
        'SecurityGroups': ['$SG_ID'],
        'AssignPublicIp': 'ENABLED',
      },
    },
  },
}
target.update($input_or_transformer)
print(json.dumps([target]))
"
}

# Rule 1 — scheduled hygiene sweep, every 6 hours (PHASE_07 7.6).
aws events put-rule --name "mnemos-custodian-scheduled" --schedule-expression "rate(6 hours)" \
  --state ENABLED --region "$REGION" >/dev/null
TARGET_JSON="$(_ecs_target '{"Input": json.dumps({"containerOverrides": [{"name": "custodian", "command": ["--trigger", "schedule"]}]})}')"
aws events put-targets --rule "mnemos-custodian-scheduled" --region "$REGION" --targets "$TARGET_JSON" >/dev/null

# Rule 2 — reactive investigation. The three alarms are each owned by the
# service they watch (infra/api and infra/sleep-cycle's own deploy.sh), not
# recreated here — this rule only reacts to their state changes. An
# InputTransformer carries the actual alarm name through into --detail, so
# one rule covers all three rather than three near-identical rules.
ALARM_NAMES=("mnemos-api-p95-latency" "mnemos-sleep-cycle-consolidation-lag" "mnemos-sleep-cycle-errors")
ALARM_ARNS_JSON="$(python3 -c "
import json
names = '${ALARM_NAMES[*]}'.split()
print(json.dumps([f'arn:aws:cloudwatch:$REGION:$ACCOUNT:alarm:{n}' for n in names]))
")"
EVENT_PATTERN="$(python3 -c "
import json
print(json.dumps({
  'source': ['aws.cloudwatch'],
  'detail-type': ['CloudWatch Alarm State Change'],
  'resources': $ALARM_ARNS_JSON,
  'detail': {'state': {'value': ['ALARM']}},
}))
")"
aws events put-rule --name "mnemos-custodian-alarm-triggered" --event-pattern "$EVENT_PATTERN" \
  --state ENABLED --region "$REGION" >/dev/null
TARGET_JSON="$(_ecs_target '{"InputTransformer": {"InputPathsMap": {"alarm": "$.detail.alarmName"}, "InputTemplate": json.dumps({"containerOverrides": [{"name": "custodian", "command": ["--trigger", "alarm", "--detail", "<alarm>"]}]})}}')"
aws events put-targets --rule "mnemos-custodian-alarm-triggered" --region "$REGION" --targets "$TARGET_JSON" >/dev/null

# --- smoke test ---------------------------------------------------------
say "Smoke test: real sweep via RunTask (--trigger manual)"
RUN_ARN="$(aws ecs run-task --region "$REGION" --cluster "$CLUSTER_NAME" \
  --task-definition "$TASK_DEF_ARN" --launch-type FARGATE --platform-version LATEST \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$SG_ID],assignPublicIp=ENABLED}" \
  --overrides "{\"containerOverrides\":[{\"name\":\"custodian\",\"command\":[\"--trigger\",\"manual\",\"--detail\",\"deploy.sh smoke test\"]}]}" \
  --query 'tasks[0].taskArn' --output text)"
echo "  Task: ${RUN_ARN}"
echo "  Waiting for it to stop (this runs a real sweep against the live cluster)..."
aws ecs wait tasks-stopped --region "$REGION" --cluster "$CLUSTER_NAME" --tasks "$RUN_ARN"
EXIT_CODE="$(aws ecs describe-tasks --region "$REGION" --cluster "$CLUSTER_NAME" --tasks "$RUN_ARN" \
  --query 'tasks[0].containers[0].exitCode' --output text)"
echo "  Exit code: ${EXIT_CODE}"
if [[ "$EXIT_CODE" != "0" ]]; then
  echo "  WARNING: non-zero exit — check ${LOG_GROUP} in CloudWatch Logs."
fi

say "Deployed"
echo "  Cluster        ${CLUSTER_ARN}"
echo "  Task def       ${TASK_DEF_ARN}"
echo "  Log group      ${LOG_GROUP}"
echo "  Rules          mnemos-custodian-scheduled (rate(6 hours)), mnemos-custodian-alarm-triggered"
echo "  Watches alarms ${ALARM_NAMES[*]}"
