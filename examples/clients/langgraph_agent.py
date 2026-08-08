"""A LangGraph agent with governed memory, in about forty lines.

    export MNEMOS_API_URL=https://<host>
    export MNEMOS_API_KEY=mn_live_...
    uv run python examples/clients/langgraph_agent.py "who is on call Tuesday?"

The point of this example is not that an agent can call tools. It is what
happens at the boundaries: the agent gets memory it can write to and recall
from, and gets *no* ability to destroy any of it — not because the prompt asks
it nicely, but because the key is write-scoped, the API's database role holds
no DELETE grant, and the cluster is what refuses.

Run it, then look at what it left behind:

    curl -s -H "Authorization: Bearer $MNEMOS_API_KEY" \\
      "$MNEMOS_API_URL/v1/ledger/verify"

Every state-changing call the agent made appended a hash-chained audit row in
the same transaction as the change itself.
"""

from __future__ import annotations

import asyncio
import os
import sys

from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.prebuilt import create_react_agent

SYSTEM = """You have access to Mnemos, a governed memory service.

Recall before you answer. If `recall` reports `unverified_withheld` above zero,
say so — it means the system is holding back claims that nothing has
corroborated yet, and that is a real part of the answer rather than a gap to
paper over.

After you act on what you recalled, call `record_action` with the `recall_ids`
from the recall that informed you. That is what makes the decision explainable
later, and what lets someone find this decision if the evidence behind it is
ever withdrawn.
"""


async def main(question: str) -> None:
    url = os.environ.get("MNEMOS_API_URL", "http://localhost:8000")
    key = os.environ.get("MNEMOS_API_KEY")
    if not key:
        raise SystemExit(
            "MNEMOS_API_KEY is not set. Mint one:\n"
            "  mnemos-api mint-key --tenant clinic --scope write --label langgraph"
        )

    client = MultiServerMCPClient(
        {
            "mnemos": {
                "transport": "streamable_http",
                "url": f"{url}/mcp",
                "headers": {"Authorization": f"Bearer {key}"},
            }
        }
    )

    tools = await client.get_tools()
    print(f"connected: {len(tools)} tools — {', '.join(t.name for t in tools)}\n")

    agent = create_react_agent(
        os.environ.get("MNEMOS_AGENT_MODEL", "openai:gpt-5.6-luna"),
        tools,
        prompt=SYSTEM,
    )

    result = await agent.ainvoke({"messages": [("user", question)]})
    print(result["messages"][-1].content)


if __name__ == "__main__":
    query = " ".join(sys.argv[1:]) or "What do you know about the on-call rota?"
    asyncio.run(main(query))
