#!/usr/bin/env bash
# Initialise the 9-node rig as a multi-region database.
#
# Turns three simulated localities into real CockroachDB regions, sets a
# survival goal that tolerates losing an entire region, and promotes the two
# memory tiers to REGIONAL BY ROW so every episode and fact is physically homed
# by the `home_region` column.
#
# On the single-region Cloud Basic cluster these statements are unavailable, so
# home_region stays an ordinary column enforced by the Warden. Same schema, same
# policies, different physical guarantee — and docs/limits.md says which is which.

set -euo pipefail

RIG_SQL="docker compose -f db/docker/multi-region.yml exec -T crdb-us-1 ./cockroach sql --insecure --host=crdb-us-1:26357"

echo "waiting for the cluster to accept connections..."
for _ in $(seq 1 40); do
  if $RIG_SQL -e "SELECT 1" >/dev/null 2>&1; then break; fi
  sleep 2
done

echo "enabling vector indexes and rangefeeds (Cloud enables these for us)"
$RIG_SQL -e "SET CLUSTER SETTING feature.vector_index.enabled = true"
$RIG_SQL -e "SET CLUSTER SETTING kv.rangefeed.enabled = true"

echo "creating database"
$RIG_SQL -e "CREATE DATABASE IF NOT EXISTS mnemos"

echo "declaring regions"
$RIG_SQL -d mnemos -e "ALTER DATABASE mnemos SET PRIMARY REGION 'us-east-1'"
$RIG_SQL -d mnemos -e "ALTER DATABASE mnemos ADD REGION IF NOT EXISTS 'eu-central-1'"
$RIG_SQL -d mnemos -e "ALTER DATABASE mnemos ADD REGION IF NOT EXISTS 'ap-south-1'"

# With three regions and three replicas, losing one region still leaves quorum.
# This is what `demos/resilience.sh` kills on camera.
$RIG_SQL -d mnemos -e "ALTER DATABASE mnemos SURVIVE REGION FAILURE"

echo "regions:"
$RIG_SQL -d mnemos -e "SHOW REGIONS FROM DATABASE mnemos"

cat <<'EOF'

Rig is up. Next:

  MNEMOS_DB_URL_RIG='postgresql://root@localhost:26357/mnemos?sslmode=disable'
  uv run python db/scripts/migrate.py up --url "$MNEMOS_DB_URL_RIG"
  uv run python db/scripts/promote_regional_by_row.py --url "$MNEMOS_DB_URL_RIG"
  uv run python db/seed.py --url "$MNEMOS_DB_URL_RIG"

SQL entry points, one per region (locality-optimized reads differ per port):
  us-east-1     localhost:26357
  eu-central-1  localhost:26358
  ap-south-1    localhost:26359
EOF
