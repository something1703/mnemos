# Deployment — the Mnemos API on AWS Lambda

The function is a container image (`Dockerfile`, arm64/Graviton) exposed through an
API Gateway HTTP API. `make deploy-api` builds, pushes and updates it; it is
idempotent, so it also provisions a fresh account from nothing.

A Lambda Function URL was the first choice and did not work here: with
`--auth-type NONE` and a resource policy allowing `Principal: "*"` under the
`lambda:FunctionUrlAuthType` condition, every request still came back
`AccessDeniedException`. Something above the function denies anonymous
`lambda:InvokeFunctionUrl` in this account; the control was not identified. The
gateway avoids the question and gives better CORS and logging besides.

## What the execution role deliberately cannot do

`exec-policy.json` is the interesting file, because of what is missing from it.

**No `kms:ScheduleKeyDeletion`.** Crypto-shredding a tenant — destroying the CMK
that wraps its data keys — is irreversible and is the Warden's privilege alone.
The API Lambda can `Encrypt`, `Decrypt` and `GenerateDataKey` with each tenant CMK
because that is what serving memory requires; it cannot destroy one. This is
invariant 1 ("no LLM-driven process holds DELETE or governance privileges")
expressed a second time, at the IAM layer, for the part of the system that lives
outside the database and so cannot be covered by a role grant.

**No `s3:PutObject`.** The API reads anchors so it can show a judge that the live
chain matches what was committed to Object Lock. Writing anchors belongs to
`mnemos-attest`. A component that can both write an anchor and serve the
verification of it is not an independent check.

**No `secretsmanager:PutSecretValue`.** Read-only on exactly one secret ARN
pattern.

## Database identity

`MNEMOS_DB_URL` points at `mnemos_api_svc`, a login granted the `mnemos_api` role
(migration 011), which holds no `DELETE` anywhere in the `mnemos` schema.
`MNEMOS_DB_URL_WARDEN` points at `mnemos_warden_svc`, which does.

The service does not take this on trust. At startup it asks the cluster
(`has_table_privilege`) and reports the answer at `GET /health`:

```json
"privilege_separation": true,
"privilege_separation_source": "measured",
"db_user": "mnemos_api_svc",
"api_can_delete": false,
"warden_can_delete": true
```

`"source": "configured"` means the probe did not run and the value is only a
comparison of two connection strings — which two URLs differing by password alone
would also satisfy. Treat `measured` as the claim and `configured` as a guess.

## TLS to CockroachDB Cloud

The image sets `SSL_CERT_FILE`/`SSL_CERT_DIR`. `psycopg[binary]` bundles its own
libpq and OpenSSL built on manylinux, whose compiled-in CA paths do not exist in
the Lambda base image — so `sslmode=verify-full sslrootcert=system` fails with
`certificate verify failed` even though the bundle is present. The connection
stays `verify-full`; only the path to the trust store is supplied.

## Concurrency and the connection pool

Each execution environment holds its own pool, so pool size multiplies by
concurrency. Reserved concurrency is capped, deliberately, well below what
CockroachDB Cloud Basic allows in connections — an unbounded serverless front end
is a reliable way to exhaust your own database.
