# Using newsdesk from an agent harness (MCP addon)

Newsdesk ships as a **Model Context Protocol (MCP) server** so it plugs into
ZCode, Claude Code, Codex, DeepSeek harness, and any MCP-capable client.
All harnesses share one newsdesk database (`$NEWSDESK_HOME`); workflow state
lives in newsdesk, never in harness memory.

## Install once

```bash
pip install -e ".[all]"     # includes yt-dlp, ffmpeg wheel, mcp SDK
```

The server command is `newsdesk-mcp` (or `newsdesk mcp`). It inherits
`NEWSDESK_HOME` and `NEWSDESK_LLM_*` from the environment it runs in.

## Tools exposed

`list_sources`, `add_source`, `run_collection`, `search_items`, `get_item`,
`summarize_item`, `digest_item` (video: transcript + keyframes + summary),
`create_digest` (the daily briefing: AI-ranked "what is worth following"),
`export_log` — the wired subset of `GET /tools` on the HTTP API.

## Registering per harness

### Claude Code

```bash
claude mcp add newsdesk -- /path/to/newsdesk/.venv/bin/newsdesk-mcp
```
(or add to `.mcp.json`: `{"mcpServers": {"newsdesk": {"command": "/path/to/.venv/bin/newsdesk-mcp"}}}`)

### ZCode

Workspace/user MCP config (`mcpServers` block, same JSON shape as above):

```json
{
  "mcpServers": {
    "newsdesk": {"command": "/home/you/dev/newsdesk/.venv/bin/newsdesk-mcp"}
  }
}
```

### Codex CLI

`~/.codex/config.toml`:

```toml
[mcp_servers.newsdesk]
command = "/path/to/newsdesk/.venv/bin/newsdesk-mcp"
```

### Any OpenAI-function-calling harness (DeepSeek etc.)

If the harness speaks MCP, use the stdio server above. Otherwise point it at
the HTTP API (`newsdesk serve`) and expose `GET /tools` as the tool manifest;
each spec carries its HTTP route.

## Notes

- The MCP server is read-mostly: `add_source` / `run_collection` / `digest_item`
  mutate, and every mutation lands in the immutable log with actor tags.
- `digest_item` downloads media and calls vision/transcription models; it is
  long-running and costs tokens when an LLM key is configured.
- No credentials are stored by the MCP server itself; it only reads
  `NEWSDESK_*` env vars of the user that launched it.
