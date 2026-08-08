# Connecting to Mnemos

Mnemos speaks MCP over streamable HTTP, plus a read-only REST facade for
anything that would rather use `curl`.

```
  MCP    https://l78rw3uwyb.execute-api.us-east-1.amazonaws.com/mcp        14 tools, bearer auth, scope-gated
  REST   https://l78rw3uwyb.execute-api.us-east-1.amazonaws.com/v1/...     read-only, same tenancy rules
  Meta   https://l78rw3uwyb.execute-api.us-east-1.amazonaws.com/health     unauthenticated: liveness + security posture
         https://l78rw3uwyb.execute-api.us-east-1.amazonaws.com/docs       OpenAPI
```

The deployed instance is on AWS Lambda behind API Gateway. `make deploy-api`
prints its URL; it is also recorded as `MNEMOS_API_URL` in `.env`.

## First, look at it without a credential

`/health` is deliberately open, because a judge should be able to see what the
service is before being handed a key:

```console
$ curl -s https://l78rw3uwyb.execute-api.us-east-1.amazonaws.com/health | jq .posture
{
  "privilege_separation": true,
  "privilege_separation_source": "measured",
  "db_user": "mnemos_api_svc",
  "api_can_delete": false,
  "warden_can_delete": true,
  ...
}
```

`api_can_delete: false` is the interesting line. It is not read from
configuration — the service asks CockroachDB at startup whether the role it
connected as holds `DELETE` on the memory tables, and reports the answer.
`"source": "measured"` means the cluster said so. `"source": "configured"`
would mean the probe did not run and the value is only a comparison of two
connection strings.

## Get a key

Keys are per-tenant and scoped. The scope is enforced server-side on every
tool call, not advisory:

| Scope   | Can                                                        |
| ------- | ---------------------------------------------------------- |
| `read`  | `recall`, `explain`, `verify_ledger`, `blast_radius`, …     |
| `write` | the above, plus `remember`, `record_action`, `learn_skill`  |
| `admin` | the above, plus `forget`, `revoke_source`, `set_legal_hold` |

```console
$ mnemos-api mint-key --tenant clinic --scope write --label "my laptop"
mn_live_...
```

Shown once — only its SHA-256 is stored.

## Claude Code

```console
$ claude mcp add --transport http mnemos https://l78rw3uwyb.execute-api.us-east-1.amazonaws.com/mcp \
    --header "Authorization: Bearer mn_live_..."
```

Or in `.mcp.json`, checked into a project so the whole team gets it:

```json
{
  "mcpServers": {
    "mnemos": {
      "type": "http",
      "url": "https://l78rw3uwyb.execute-api.us-east-1.amazonaws.com/mcp",
      "headers": { "Authorization": "Bearer ${MNEMOS_API_KEY}" }
    }
  }
}
```

Prefer the `${VAR}` form. A bearer token pasted into a file that gets committed
is the most common way these leak.

## Cursor

`~/.cursor/mcp.json`, or `.cursor/mcp.json` for one project:

```json
{
  "mcpServers": {
    "mnemos": {
      "url": "https://l78rw3uwyb.execute-api.us-east-1.amazonaws.com/mcp",
      "headers": { "Authorization": "Bearer mn_live_..." }
    }
  }
}
```

## LangGraph / LangChain

```python
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.prebuilt import create_react_agent

client = MultiServerMCPClient(
    {
        "mnemos": {
            "transport": "streamable_http",
            "url": "https://l78rw3uwyb.execute-api.us-east-1.amazonaws.com/mcp",
            "headers": {"Authorization": f"Bearer {os.environ['MNEMOS_API_KEY']}"},
        }
    }
)

agent = create_react_agent("openai:gpt-5.6-luna", await client.get_tools())
```

## Plain MCP SDK

```python
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

client = httpx2.AsyncClient(headers={"Authorization": f"Bearer {key}"})
async with client:
    async with streamable_http_client(f"{url}/mcp", http_client=client) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            result = await session.call_tool(
                "recall",
                {
                    "subject_key": "patient:eu:d5b1",
                    "query": "drug allergies",
                },
            )
```

## Telling your agent how to use it

The server ships `instructions` in its initialize response, so a well-behaved
client already tells the model three things:

1. **Everything written is untrusted until corroborated.** `recall` hiding an
   unverified fact is the system working, not a retrieval failure. Check
   `unverified_withheld` before concluding nothing is known.
2. **Declare what caused an action.** Pass the `recall_ids` from `recall` into
   `record_action`. That is what makes `explain` able to reconstruct a decision
   later, and what lets a revocation say which decisions rested on evidence
   since withdrawn.
3. **Destruction needs the admin scope and an explicit confirm**, and a legal
   hold can refuse it outright. Preview with `confirm=false` first; the preview
   is exact, not an estimate.

## Things that will look like bugs and are not

**`recall` returns no facts for something you just remembered.** Episodes are
raw experience; facts are distilled from them by the Sleep Cycle, and no fact
becomes recallable without provenance back to at least one episode (invariant
3). Until distillation runs, the episode exists and the fact does not.

**`remember` refused with a residency error.** The subject is homed in another
region and this instance is not it (invariant 4). The refusal is itself
recorded in `region_crossings` — see `GET /v1/crossings`.

**`forget` refused with a scope error on a write key.** Working as intended:
no LLM-driven process holds destruction rights (invariant 1). The cluster
enforces the same rule underneath — the API's database role has no `DELETE`
grant at all, which is what `/health` reports.

**A `mcp-session-id` from one request is unknown to the next.** The deployment
is stateless per request, because Lambda may route a follow-up to a different
execution environment. Each request carries its own context.

## Verifying it independently

The ledger's Merkle roots are anchored to S3 Object Lock in COMPLIANCE mode,
so nobody — including the account that wrote them — can alter one for the
retention period. `scripts/independent_verify.py` is stdlib-only and needs no
AWS credential:

```console
$ curl -s -H "Authorization: Bearer $KEY" https://l78rw3uwyb.execute-api.us-east-1.amazonaws.com/v1/ledger/verify
{"valid":true,"entries_checked":13,"shards_checked":6,"checkpoints_checked":1,...}

$ mnemos-attest presign --tenant clinic          # time-limited link to one anchor
$ python3 scripts/independent_verify.py --url "<that link>"
```

The REST verifier recomputes the chain from the database. The anchor check
compares it against a root committed outside the database, which is the part a
whole-shard rewrite inside CockroachDB cannot survive.
