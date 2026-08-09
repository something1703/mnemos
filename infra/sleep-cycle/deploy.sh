#!/usr/bin/env bash
#
# Build, push and deploy the Mnemos sleep cycle to AWS Lambda, plus its
# Step Functions state machine and EventBridge schedules.
#
#   make deploy-sleep-cycle
#   SKIP_BUILD=1 make deploy-sleep-cycle
#
# Idempotent, same shape as infra/api/deploy.sh: every resource is created
# only if missing, so this also works as the provisioning script for a fresh
# account. No Function URL, no API Gateway — this service has no HTTP
# surface; it is invoked by EventBridge or by a Step Functions Task state.
#
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
FUNCTION="${MNEMOS_LAMBDA_NAME:-mnemos-sleep-cycle}"
REPO="${MNEMOS_ECR_REPO:-mnemos-sleep-cycle}"
SECRET_NAME="${MNEMOS_SECRET_NAME:-mnemos/sleep-cycle}"
ROLE_NAME="${MNEMOS_LAMBDA_ROLE:-mnemos-sleep-cycle-exec}"
STATES_ROLE_NAME="${MNEMOS_STATES_ROLE:-mnemos-sleep-cycle-states-exec}"
EVENTS_ROLE_NAME="${MNEMOS_EVENTS_ROLE:-mnemos-sleep-cycle-events-exec}"
STATE_MACHINE_NAME="${MNEMOS_STATE_MACHINE_NAME:-mnemos-sleep-cycle-nightly}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"
IMAGE="${REGISTRY}/${REPO}:latest"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

# --- image ------------------------------------------------------------------
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

# --- secret -----------------------------------------------------------------
# Only what this service reads. MNEMOS_DB_URL_PIPELINE — the role-bound login
# with no DELETE grant anywhere — not the admin MNEMOS_DB_URL.
say "Syncing ${SECRET_NAME}"
SECRET_JSON="$(
  ROOT="$ROOT" python3 - <<'PY'
import json, os, pathlib
wanted = {"MNEMOS_DB_URL_PIPELINE", "OPENAI_API_KEY", "OPENAI_EMBED_MODEL",
          "OPENAI_EMBED_DIMENSIONS", "OPENAI_DISTILL_MODEL"}
out = {}
for line in pathlib.Path(os.environ["ROOT"], ".env").read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, _, value = line.partition("=")
    if key in wanted and value:
        # dotenv quotes these three DSNs so that sourcing the file with
        # "set -a" survives the literal ampersand in the query string -- a
        # shell convenience this plain line parser does not know about on
        # its own. Left unstripped, appending "&sslrootcert=system" below
        # lands OUTSIDE the trailing quote, splitting the value into two
        # connection strings glued together with an ampersand. Found by a
        # failed deploy, not by inspection: the deployed API secret carries
        # the same latent bug, just not yet triggered by a redeploy.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        out[key] = value

missing = {"MNEMOS_DB_URL_PIPELINE"} - out.keys()
if missing:
    raise SystemExit(
        f"missing from .env: {', '.join(sorted(missing))}.\n"
        "The sleep cycle needs a role-bound login (mnemos_pipeline) so the "
        "cluster itself enforces invariant 1. Run:\n"
        "  uv run python db/scripts/provision_users.py --url \"$MNEMOS_DB_URL\""
    )

out["MNEMOS_DB_URL"] = out.pop("MNEMOS_DB_URL_PIPELINE")
url = out["MNEMOS_DB_URL"]
if "sslmode=verify-full" in url and "sslrootcert" not in url:
    out["MNEMOS_DB_URL"] = url + "&sslrootcert=system"

print(json.dumps(out))
PY
)"

if aws secretsmanager describe-secret --secret-id "$SECRET_NAME" --region "$REGION" >/dev/null 2>&1; then
  aws secretsmanager put-secret-value --secret-id "$SECRET_NAME" \
    --secret-string "$SECRET_JSON" --region "$REGION" >/dev/null
else
  aws secretsmanager create-secret --name "$SECRET_NAME" \
    --description "Mnemos sleep-cycle runtime configuration" \
    --secret-string "$SECRET_JSON" --region "$REGION" >/dev/null
fi
SECRET_ARN="$(aws secretsmanager describe-secret --secret-id "$SECRET_NAME" \
  --region "$REGION" --query ARN --output text)"

# --- lambda execution role ----------------------------------------------
if ! aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  say "Creating execution role ${ROLE_NAME}"
  aws iam create-role --role-name "$ROLE_NAME" \
    --assume-role-policy-document "file://$HERE/trust-policy.json" >/dev/null
  aws iam attach-role-policy --role-name "$ROLE_NAME" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
fi
aws iam put-role-policy --role-name "$ROLE_NAME" --policy-name mnemos-sleep-cycle-runtime \
  --policy-document "file://$HERE/exec-policy.json"
ROLE_ARN="$(aws iam get-role --role-name "$ROLE_NAME" --query Role.Arn --output text)"

# --- function -----------------------------------------------------------
if aws lambda get-function --function-name "$FUNCTION" --region "$REGION" >/dev/null 2>&1; then
  say "Updating ${FUNCTION}"
  aws lambda update-function-code --function-name "$FUNCTION" \
    --image-uri "$IMAGE" --region "$REGION" >/dev/null
  aws lambda wait function-updated --function-name "$FUNCTION" --region "$REGION"
  aws lambda update-function-configuration --function-name "$FUNCTION" --region "$REGION" \
    --environment "Variables={MNEMOS_SECRET_ARN=${SECRET_ARN},MNEMOS_LOG_LEVEL=INFO}" >/dev/null
else
  say "Creating ${FUNCTION}"
  for attempt in 1 2 3 4 5; do
    if aws lambda create-function --function-name "$FUNCTION" --region "$REGION" \
        --package-type Image --code "ImageUri=${IMAGE}" --role "$ROLE_ARN" \
        --architectures arm64 --memory-size 1024 --timeout 300 \
        --environment "Variables={MNEMOS_SECRET_ARN=${SECRET_ARN},MNEMOS_LOG_LEVEL=INFO}" \
        --description "Mnemos sleep cycle — consolidation, decay, checkpoint" >/dev/null 2>&1; then
      break
    fi
    echo "   role not yet visible to Lambda (attempt ${attempt}/5); retrying"
    sleep 10
  done
fi
aws lambda wait function-updated --function-name "$FUNCTION" --region "$REGION"
LAMBDA_ARN="$(aws lambda get-function --function-name "$FUNCTION" --region "$REGION" \
  --query Configuration.FunctionArn --output text)"

# --- step functions -------------------------------------------------------
if ! aws iam get-role --role-name "$STATES_ROLE_NAME" >/dev/null 2>&1; then
  say "Creating Step Functions execution role ${STATES_ROLE_NAME}"
  aws iam create-role --role-name "$STATES_ROLE_NAME" \
    --assume-role-policy-document "file://$HERE/states-trust-policy.json" >/dev/null
fi
STATES_ROLE_ARN="$(aws iam get-role --role-name "$STATES_ROLE_NAME" --query Role.Arn --output text)"
INVOKE_POLICY="$(python3 -c "
import json
print(json.dumps({
  'Version': '2012-10-17',
  'Statement': [{'Effect': 'Allow', 'Action': 'lambda:InvokeFunction', 'Resource': '$LAMBDA_ARN'}],
}))
")"
aws iam put-role-policy --role-name "$STATES_ROLE_NAME" --policy-name invoke-sleep-cycle \
  --policy-document "$INVOKE_POLICY"

say "Syncing state machine ${STATE_MACHINE_NAME}"
DEFINITION="$(python3 -c "
import json
d = json.load(open('$HERE/state_machine.json'))
print(json.dumps(d).replace('\${LambdaArn}', '$LAMBDA_ARN'))
")"
SM_ARN="arn:aws:states:${REGION}:${ACCOUNT}:stateMachine:${STATE_MACHINE_NAME}"
if aws stepfunctions describe-state-machine --state-machine-arn "$SM_ARN" --region "$REGION" >/dev/null 2>&1; then
  aws stepfunctions update-state-machine --state-machine-arn "$SM_ARN" --region "$REGION" \
    --definition "$DEFINITION" --role-arn "$STATES_ROLE_ARN" >/dev/null
else
  aws stepfunctions create-state-machine --name "$STATE_MACHINE_NAME" --region "$REGION" \
    --definition "$DEFINITION" --role-arn "$STATES_ROLE_ARN" --type STANDARD >/dev/null
fi

# --- eventbridge schedules -------------------------------------------------
# Four cadences (Phase 05.6): hourly light consolidation, weekly decay, hourly
# checkpoint — each invokes the Lambda directly — and the nightly full cycle,
# which invokes the state machine so it gets its own retry per stage and a
# visible execution history. "Full" is a large page (limit=200 in
# state_machine.json), not a loop-until-empty sweep — a scale simplification
# honest for hackathon data volumes, not a claim of unbounded throughput.
say "Syncing EventBridge schedules"

if ! aws iam get-role --role-name "$EVENTS_ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-role --role-name "$EVENTS_ROLE_NAME" \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"events.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
fi
EVENTS_ROLE_ARN="$(aws iam get-role --role-name "$EVENTS_ROLE_NAME" --query Role.Arn --output text)"
START_EXEC_POLICY="$(python3 -c "
import json
print(json.dumps({
  'Version': '2012-10-17',
  'Statement': [{'Effect': 'Allow', 'Action': 'states:StartExecution', 'Resource': '$SM_ARN'}],
}))
")"
aws iam put-role-policy --role-name "$EVENTS_ROLE_NAME" --policy-name start-sleep-cycle-nightly \
  --policy-document "$START_EXEC_POLICY"

_rule() {
  # The shorthand form of --targets (Id=1,Arn=...,Input={...}) cannot survive
  # JSON braces/commas inside Input — the CLI's shorthand parser trips on the
  # first comma inside the JSON value. Building an explicit JSON array and
  # passing it as the argument body sidesteps that parser entirely.
  local name="$1" schedule="$2" input="$3" target_arn="$4" role_arg="$5"
  aws events put-rule --name "$name" --schedule-expression "$schedule" \
    --state ENABLED --region "$REGION" >/dev/null

  local targets
  targets="$(python3 -c "
import json
target = {'Id': '1', 'Arn': '$target_arn', 'Input': '''$input'''}
role_arg = '$role_arg'
if role_arg:
    target['RoleArn'] = role_arg
print(json.dumps([target]))
")"
  aws events put-targets --rule "$name" --region "$REGION" --targets "$targets" >/dev/null

  if [[ -z "$role_arg" ]]; then
    aws lambda add-permission --function-name "$FUNCTION" --region "$REGION" \
      --statement-id "eventbridge-${name}" --action lambda:InvokeFunction \
      --principal events.amazonaws.com \
      --source-arn "arn:aws:events:${REGION}:${ACCOUNT}:rule/${name}" >/dev/null 2>&1 || true
  fi
}

_rule "mnemos-sleep-cycle-hourly-light" "rate(1 hour)" \
  '{"stage":"consolidate","limit":5}' "$LAMBDA_ARN" ""
_rule "mnemos-sleep-cycle-weekly-decay" "cron(0 4 ? * SUN *)" \
  '{"stage":"decay"}' "$LAMBDA_ARN" ""
_rule "mnemos-sleep-cycle-hourly-checkpoint" "rate(1 hour)" \
  '{"stage":"checkpoint"}' "$LAMBDA_ARN" ""
_rule "mnemos-sleep-cycle-nightly" "cron(0 3 * * ? *)" \
  '{}' "$SM_ARN" "$EVENTS_ROLE_ARN"

# --- cloudwatch alarm -------------------------------------------------------
say "Syncing CloudWatch alarm"
aws cloudwatch put-metric-alarm --alarm-name "mnemos-sleep-cycle-errors" --region "$REGION" \
  --namespace AWS/Lambda --metric-name Errors \
  --dimensions "Name=FunctionName,Value=${FUNCTION}" \
  --statistic Sum --period 3600 --evaluation-periods 1 --threshold 0 \
  --comparison-operator GreaterThanThreshold --treat-missing-data notBreaching \
  --alarm-description "Any sleep-cycle Lambda error in the last hour — consolidation, decay, or checkpoint failed and the caller (EventBridge or Step Functions) exhausted its retries." \
  >/dev/null

# A dead man's switch, not a threshold on a value: the hourly-light schedule
# calls _emit_consolidation_heartbeat() (lambda_handler.py) after every
# successful consolidate/cycle stage. TreatMissingData=breaching means the
# alarm fires when 6 straight hourly windows produce zero heartbeats — i.e.
# consolidation has silently stopped succeeding, whether from an upstream
# EventBridge misconfiguration, a stuck warm container, or anything else
# that would NOT show up as a Lambda "Error" (mnemos-sleep-cycle-errors
# already covers that case).
aws cloudwatch put-metric-alarm --alarm-name "mnemos-sleep-cycle-consolidation-lag" --region "$REGION" \
  --namespace "Mnemos/SleepCycle" --metric-name ConsolidationHeartbeat \
  --statistic Sum --period 3600 --evaluation-periods 6 --threshold 1 \
  --comparison-operator LessThanThreshold --treat-missing-data breaching \
  --alarm-description "No successful consolidation heartbeat in 6 hours — consolidation has stopped making progress, whether or not it is also erroring." \
  >/dev/null

# --- smoke test -------------------------------------------------------------
say "Smoke test: checkpoint stage (no model call, no cost)"
RESULT="$(aws lambda invoke --function-name "$FUNCTION" --region "$REGION" \
  --cli-binary-format raw-in-base64-out \
  --payload '{"stage":"checkpoint"}' /tmp/mnemos-sleep-cycle-smoke.json --query FunctionError --output text)"
python3 -c "
import json
body = json.load(open('/tmp/mnemos-sleep-cycle-smoke.json'))
print(' ', json.dumps(body, indent=2))
"
if [[ "$RESULT" != "None" ]]; then
  echo "  WARNING: Lambda reported an error (see above)."
fi

say "Deployed"
echo "  Function       ${LAMBDA_ARN}"
echo "  State machine  ${SM_ARN}"
echo "  Schedules      hourly-light, weekly-decay, hourly-checkpoint, nightly (-> state machine)"
echo "  Alarms         mnemos-sleep-cycle-errors, mnemos-sleep-cycle-consolidation-lag"
