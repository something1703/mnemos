#!/usr/bin/env bash
#
# Build, push and deploy the Mnemos API to AWS Lambda.
#
#   make deploy-api          # build + push + update code + smoke test
#   SKIP_BUILD=1 make deploy-api
#
# Idempotent: every AWS resource is created only if missing, so this doubles as
# the provisioning script for a fresh account. It reads the same .env the local
# tooling does, and pushes the runtime subset of it to Secrets Manager — the
# deployed function never receives a plaintext database URL in its environment.
#
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
FUNCTION="${MNEMOS_LAMBDA_NAME:-mnemos-api}"
REPO="${MNEMOS_ECR_REPO:-mnemos-api}"
SECRET_NAME="${MNEMOS_SECRET_NAME:-mnemos/api}"
ROLE_NAME="${MNEMOS_LAMBDA_ROLE:-mnemos-api-exec}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"
IMAGE="${REGISTRY}/${REPO}:latest"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

# --- image ------------------------------------------------------------------
# arm64 to match the function's architecture and to build natively on Apple
# Silicon. --provenance=false because Lambda rejects the OCI manifest list that
# buildx otherwise produces, with an error that does not mention attestations.
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
# Only the keys the deployed service reads. AWS_PROFILE in particular must not
# travel: inside Lambda it would send the SDK looking for a profile that is not
# there, in preference to the execution role's own credentials.
say "Syncing ${SECRET_NAME}"
SECRET_JSON="$(
  ROOT="$ROOT" python3 - <<'PY'
import json, os, pathlib
wanted = {
    "MNEMOS_DB_URL_API", "MNEMOS_DB_URL_WARDEN", "MNEMOS_REGIONS", "MNEMOS_CHAIN_SHARDS",
    "MNEMOS_S3_ANCHOR_BUCKET", "MNEMOS_ALLOWED_HOSTS", "MNEMOS_KMS_KEY_ARN_CLINIC",
    "MNEMOS_KMS_KEY_ARN_OPS", "MNEMOS_KMS_KEY_ARN_FINANCE", "OPENAI_API_KEY",
    "OPENAI_EMBED_MODEL", "OPENAI_EMBED_DIMENSIONS", "OPENAI_DISTILL_MODEL",
}
raw = {}
for line in pathlib.Path(os.environ["ROOT"], ".env").read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, _, value = line.partition("=")
    if key in wanted and value:
        # dotenv quotes these DSNs so that sourcing the file with
        # "set -a" survives the literal ampersand in the query string -- a
        # shell convenience this plain line parser does not know about on
        # its own. Left unstripped, appending "&sslrootcert=system" below
        # lands OUTSIDE the trailing quote, splitting the value into two
        # connection strings glued together with an ampersand. Found via
        # the sleep-cycle deploy script failing to connect: this deployed
        # secret carried the same latent bug, just not yet triggered by a
        # redeploy since the dotenv format changed.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        raw[key] = value

# MNEMOS_DB_URL in .env is the admin login: migrations and seeding need DDL, so
# local tooling keeps it. The deployment must NOT have it. The two role-bound
# logins are named separately and the API one is what ships as MNEMOS_DB_URL —
# a deployment that silently fell back to admin would report
# privilege_separation: false, but only to whoever read the health endpoint.
missing = {"MNEMOS_DB_URL_API", "MNEMOS_DB_URL_WARDEN"} - raw.keys()
if missing:
    raise SystemExit(
        f"missing from .env: {', '.join(sorted(missing))}.\n"
        "The deployment needs two role-bound logins so the cluster itself can "
        "refuse an API-side DELETE (invariant 1). See infra/api/README.md."
    )

out = {k: v for k, v in raw.items() if k != "MNEMOS_DB_URL_API"}
out["MNEMOS_DB_URL"] = raw["MNEMOS_DB_URL_API"]

# psycopg[binary] bundles an OpenSSL whose default CA path does not exist in the
# Lambda image; sslrootcert=system plus SSL_CERT_FILE (set in the Dockerfile)
# keeps verify-full working without shipping a certificate.
for key in ("MNEMOS_DB_URL", "MNEMOS_DB_URL_WARDEN"):
    url = out[key]
    if "sslmode=verify-full" in url and "sslrootcert" not in url:
        out[key] = url + "&sslrootcert=system"

print(json.dumps(out))
PY
)"

if aws secretsmanager describe-secret --secret-id "$SECRET_NAME" --region "$REGION" >/dev/null 2>&1; then
  aws secretsmanager put-secret-value --secret-id "$SECRET_NAME" \
    --secret-string "$SECRET_JSON" --region "$REGION" >/dev/null
else
  aws secretsmanager create-secret --name "$SECRET_NAME" \
    --description "Mnemos API runtime configuration" \
    --secret-string "$SECRET_JSON" --region "$REGION" >/dev/null
fi
SECRET_ARN="$(aws secretsmanager describe-secret --secret-id "$SECRET_NAME" \
  --region "$REGION" --query ARN --output text)"

# --- role -------------------------------------------------------------------
if ! aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  say "Creating execution role ${ROLE_NAME}"
  aws iam create-role --role-name "$ROLE_NAME" \
    --assume-role-policy-document "file://$HERE/trust-policy.json" >/dev/null
  aws iam attach-role-policy --role-name "$ROLE_NAME" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
fi
# Reapplied every deploy so the policy file stays the source of truth.
aws iam put-role-policy --role-name "$ROLE_NAME" --policy-name mnemos-api-runtime \
  --policy-document "file://$HERE/exec-policy.json"
ROLE_ARN="$(aws iam get-role --role-name "$ROLE_NAME" --query Role.Arn --output text)"

# --- function ---------------------------------------------------------------
if aws lambda get-function --function-name "$FUNCTION" --region "$REGION" >/dev/null 2>&1; then
  say "Updating ${FUNCTION}"
  aws lambda update-function-code --function-name "$FUNCTION" \
    --image-uri "$IMAGE" --region "$REGION" >/dev/null
  aws lambda wait function-updated --function-name "$FUNCTION" --region "$REGION"
  aws lambda update-function-configuration --function-name "$FUNCTION" --region "$REGION" \
    --environment "Variables={MNEMOS_SECRET_ARN=${SECRET_ARN},MNEMOS_LOG_LEVEL=INFO}" >/dev/null
else
  say "Creating ${FUNCTION}"
  # IAM role propagation to Lambda is eventually consistent; retry rather than
  # fail a fresh-account run on a race that resolves itself in seconds.
  for attempt in 1 2 3 4 5; do
    if aws lambda create-function --function-name "$FUNCTION" --region "$REGION" \
        --package-type Image --code "ImageUri=${IMAGE}" --role "$ROLE_ARN" \
        --architectures arm64 --memory-size 1024 --timeout 60 \
        --environment "Variables={MNEMOS_SECRET_ARN=${SECRET_ARN},MNEMOS_LOG_LEVEL=INFO}" \
        --description "Mnemos — accountable memory for agents (MCP + REST)" >/dev/null 2>&1; then
      break
    fi
    echo "   role not yet visible to Lambda (attempt ${attempt}/5); retrying"
    sleep 10
  done
fi
aws lambda wait function-updated --function-name "$FUNCTION" --region "$REGION"

# --- public edge ------------------------------------------------------------
# API Gateway rather than a Lambda Function URL. A Function URL was tried first
# and returned AccessDeniedException to every caller despite auth-type NONE and
# a resource policy allowing Principal "*" with the FunctionUrlAuthType
# condition — so something above the function (an SCP or account-level control)
# denies anonymous lambda:InvokeFunctionUrl here. The exact control was not
# identified; the gateway sidesteps it and is a better public edge anyway.
API_ID="$(aws apigatewayv2 get-apis --region "$REGION" \
  --query "Items[?Name=='${FUNCTION}'].ApiId | [0]" --output text)"
if [[ "$API_ID" == "None" || -z "$API_ID" ]]; then
  say "Creating HTTP API"
  API_ID="$(aws apigatewayv2 create-api --name "$FUNCTION" --protocol-type HTTP \
    --target "arn:aws:lambda:${REGION}:${ACCOUNT}:function:${FUNCTION}" \
    --region "$REGION" --query ApiId --output text)"
  aws lambda add-permission --function-name "$FUNCTION" --region "$REGION" \
    --statement-id ApiGatewayInvoke --action lambda:InvokeFunction \
    --principal apigateway.amazonaws.com \
    --source-arn "arn:aws:execute-api:${REGION}:${ACCOUNT}:${API_ID}/*/*" >/dev/null
fi
ENDPOINT="https://${API_ID}.execute-api.${REGION}.amazonaws.com"

# The MCP SDK rejects an unrecognised Host header with 421, so the gateway's own
# hostname has to be in the allow-list. It is only knowable after the API
# exists, which is why this is a second secret write rather than one.
HOST="${API_ID}.execute-api.${REGION}.amazonaws.com"
if ! grep -q "$HOST" <<<"$SECRET_JSON"; then
  say "Recording gateway hostname in the allow-list"
  SECRET_JSON="$(HOST="$HOST" python3 -c '
import json, os, sys
d = json.load(sys.stdin)
hosts = [h for h in d.get("MNEMOS_ALLOWED_HOSTS", "").split(",") if h]
hosts.append(os.environ["HOST"])
d["MNEMOS_ALLOWED_HOSTS"] = ",".join(dict.fromkeys(hosts))
print(json.dumps(d))' <<<"$SECRET_JSON")"
  aws secretsmanager put-secret-value --secret-id "$SECRET_NAME" \
    --secret-string "$SECRET_JSON" --region "$REGION" >/dev/null
  # Force a cold start so the new allow-list is actually read.
  aws lambda update-function-configuration --function-name "$FUNCTION" --region "$REGION" \
    --environment "Variables={MNEMOS_SECRET_ARN=${SECRET_ARN},MNEMOS_LOG_LEVEL=INFO}" >/dev/null
  aws lambda wait function-updated --function-name "$FUNCTION" --region "$REGION"
fi

# --- cloudwatch alarm --------------------------------------------------------
# One of the three signals PHASE_07 7.6 wires to the Custodian's
# alarm-triggered sweep — a p95 latency spike is exactly the kind of thing an
# on-call engineer would want investigated (slow query? full table scan?
# missing index?), which is what the Custodian's skills are for. 3 seconds
# is a judgment call, not a measured SLO: generous enough not to page on
# CockroachDB Cloud's normal tail latency, tight enough to catch a real
# regression.
say "Syncing CloudWatch alarm"
aws cloudwatch put-metric-alarm --alarm-name "mnemos-api-p95-latency" --region "$REGION" \
  --namespace AWS/Lambda --metric-name Duration \
  --dimensions "Name=FunctionName,Value=${FUNCTION}" \
  --extended-statistic p95 --period 300 --evaluation-periods 3 --threshold 3000 \
  --comparison-operator GreaterThanThreshold --treat-missing-data notBreaching \
  --alarm-description "API p95 latency over 3s for 15 minutes straight." \
  >/dev/null

# --- smoke test -------------------------------------------------------------
say "Smoke test"
HEALTH="$(curl -fsS --retry 5 --retry-all-errors --retry-delay 4 "${ENDPOINT}/health")"
python3 - "$HEALTH" <<'PY'
import json, sys
health = json.loads(sys.argv[1])
posture = health.get("posture", {})
print(f"  status              {health.get('status')}  (database {health.get('database')})")
print(f"  db user             {posture.get('db_user', 'unknown')}")
print(f"  api_can_delete      {posture.get('api_can_delete')}")
print(f"  privilege sep.      {posture.get('privilege_separation')} "
      f"({posture.get('privilege_separation_source')})")
if posture.get("privilege_separation_source") != "measured":
    print("\n  WARNING: posture is configured, not measured — the privilege probe "
          "did not run.")
if posture.get("api_can_delete"):
    print("\n  WARNING: the API database role holds DELETE. Invariant 1 is not "
          "enforced by the cluster for this deployment.")
PY

say "Deployed"
echo "  MCP    ${ENDPOINT}/mcp"
echo "  REST   ${ENDPOINT}/v1/..."
echo "  Alarm  mnemos-api-p95-latency"
echo
echo "Record it locally so the demos and client snippets point at the deployment:"
echo "  MNEMOS_API_URL=${ENDPOINT}"
