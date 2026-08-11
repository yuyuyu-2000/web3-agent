# Web Search Tool Guide

## Overview

`web_search` is an optional ChainCloud Agent tool for grounding answers with public internet sources.

It is mainly used for:

- recent public events;
- protocol announcements;
- security reports;
- governance forum discussions;
- news verification;
- attack attribution;
- public fund-flow updates;
- cross-checking external facts before combining them with on-chain database results.

This tool was added because DeepSeek API / OpenAI-compatible API calls do not automatically inherit the web search capability available in provider web or app products. Public web search must be implemented as an explicit Agent tool so that the search process can be shown in trace and combined with other tools.

---

## Environment Variables

Add the following variables to local `.env`:

```env
WEB_SEARCH_ENABLED=true
WEB_SEARCH_PROVIDER=tavily
TAVILY_API_KEY=<your-tavily-api-key>
WEB_SEARCH_MAX_RESULTS=5
WEB_SEARCH_TIMEOUT_SEC=15
```

The `.env.example` file should only contain placeholder values. Do not commit real API keys.

Recommended `.env.example` values:

```env
WEB_SEARCH_ENABLED=false
WEB_SEARCH_PROVIDER=tavily
TAVILY_API_KEY=<TAVILY_API_KEY>
WEB_SEARCH_MAX_RESULTS=5
WEB_SEARCH_TIMEOUT_SEC=15
```

---

## Tool Input

The tool accepts the following input:

```json
{
  "query": "KelpDAO rsETH exploit fund flow",
  "max_results": 5
}
```

Fields:

- `query`: public web search query.
- `max_results`: number of returned results, from 1 to 10.

---

## Tool Output

The tool returns a JSON string:

```json
{
  "provider": "tavily",
  "query": "KelpDAO rsETH exploit fund flow",
  "count": 5,
  "results": [
    {
      "title": "Example title",
      "url": "https://example.com",
      "content": "Search result snippet...",
      "score": 0.87,
      "published_date": "2026-04-20"
    }
  ]
}
```

---

## When the Agent Should Use `web_search`

The Agent should call `web_search` when the user asks about:

- recent incidents;
- public security reports;
- governance proposals;
- protocol announcements;
- attack attribution;
- public fund-flow information;
- news or market events;
- external facts that may have changed recently.

For DeFi incident analysis, `web_search` should usually be called before querying internal databases, because it provides the public event background and known attacker addresses or transaction hashes.

---

## Recommended DeFi Incident Workflow

For a DeFi security incident or fund-flow question, the recommended tool flow is:

```text
User question
↓
web_search
↓
postgres_list_tables / postgres_table_schema
↓
postgres_select
↓
ethereum_jsonrpc, if transaction or log verification is needed
↓
chart or dashboard tools, if visualization is useful
↓
final answer with evidence separation
```

The final answer should separate:

1. public-source facts;
2. company database facts;
3. direct on-chain RPC verification;
4. assumptions or still-unverified hypotheses.

---

## Example Prompt for Testing

```text
Please search public sources and analyze the fund flow of the 2026-04-18 KelpDAO rsETH exploit.

Requirements:
1. Call web_search first.
2. Then query PostgreSQL for Aave-related rsETH, WETH, borrow, supply, and liquidation records.
3. If needed, call ethereum_jsonrpc for key transaction or address verification.
4. Clearly separate public-source facts, database-backed facts, direct on-chain verification, and still-unverified assumptions.
5. Do not treat token amount as ETH or USD equivalent unless price conversion is explicitly performed.
```

---

## Local Verification

Start the backend:

```bash
uv run uvicorn --app-dir src chaincloud_agent_service.main:app --reload --host 0.0.0.0 --port 8001
```

Check registered tools:

```bash
curl -s http://127.0.0.1:8001/tools | python -m json.tool
```

Expected result:

- the tool count includes `web_search`;
- the `web_search` tool appears in the returned tool list;
- the args schema includes `query` and `max_results`.

If `CHAT_API_TOKEN` is enabled, include the authorization header:

```bash
curl -s http://127.0.0.1:8001/tools \
  -H "Authorization: Bearer <your-chat-api-token>" | python -m json.tool
```

---

## Frontend Verification

In the ChainCloud Agent Web Console:

1. enable trace display;
2. ask a question that requires recent public information;
3. confirm the trace contains:
   - `tool_call_request: web_search`;
   - `tool_result: web_search`;
4. confirm the final answer uses public sources and separates them from database-backed facts.

---

## Notes and Limitations

- `web_search` provides public-source clues, not final truth.
- Public search results should be cross-checked with internal databases or RPC tools when the user asks about on-chain facts.
- Search result snippets may be incomplete.
- The Agent should not claim direct on-chain verification unless it actually used database records or RPC logs.
- For token amounts, do not label `amount / 1e18` as ETH or USD equivalent unless the asset and conversion method are explicit.
- Real API keys must remain in local `.env` and must never be committed.

---

## Follow-up Improvements

Future improvements may include:

- adding a user-level test script for `web_search`;
- improving query planning so the Agent preserves user-provided dates and entities;
- adding evidence-level constraints for DeFi incident analysis;
- adding an Answer Composer layer to produce more reliable, source-aware responses;
- adding an Insight Extractor layer to identify suspicious fund-flow paths, liquidation spikes, and unresolved assumptions.
