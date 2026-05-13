from __future__ import annotations

from base64 import b64decode
from pathlib import Path
from typing import Any

from .base import BaseTool


class ReadMcpResourceTool(BaseTool):
    name = "read_mcp_resource"
    description = "Read a specific MCP resource by server name and URI."
    read_only = True
    concurrency_safe = True
    deferred = True
    search_terms = ("mcp resource", "resource read", "mcp read")
    input_schema = {
        "type": "object",
        "properties": {
            "server": {"type": "string", "description": "The MCP server name."},
            "uri": {"type": "string", "description": "The resource URI to read."},
        },
        "required": ["server", "uri"],
    }

    def execute(self, tool_input: dict, ctx):
        server_name = str(tool_input["server"]).strip()
        uri = str(tool_input["uri"]).strip()
        server = ctx.session.ensure_mcp_server_connected(server_name)
        if server is None:
            raise RuntimeError(f'MCP server "{server_name}" is not configured.')
        if server.status != "connected":
            retry_in = ctx.session.mcp_registry.retry_wait_seconds(server_name)
            retry_text = f" Retry in {retry_in}s." if retry_in else ""
            raise RuntimeError(
                f'MCP server "{server_name}" is {server.status}. '
                f"Last error: {server.last_error or 'unknown'}."
                f"{retry_text}"
            )
        resource = ctx.session.mcp_registry.find_resource(server_name, uri)
        if resource is None:
            raise RuntimeError(
                f'Resource "{uri}" is not currently discovered on MCP server "{server_name}". '
                "Run /mcp-refresh or reconnect the server to refresh advertised resources."
            )
        result = server.client.read_resource(uri)
        contents: list[dict[str, Any]] = []
        for index, item in enumerate(result.contents):
            rendered = self._normalize_content(ctx.cwd, server_name, index, item)
            contents.append(rendered)
        return {"contents": contents}

    def _normalize_content(
        self,
        cwd: Path,
        server_name: str,
        index: int,
        item: dict[str, Any],
    ) -> dict[str, Any]:
        rendered: dict[str, Any] = {
            "uri": str(item.get("uri", "")),
            "mime_type": str(item.get("mimeType", "")),
        }
        if item.get("text") is not None:
            rendered["text"] = str(item.get("text", ""))
            return rendered
        blob = item.get("blob")
        if blob is None:
            return rendered
        output_path = self._persist_blob(
            cwd,
            server_name=server_name,
            index=index,
            uri=rendered["uri"],
            mime_type=rendered["mime_type"],
            blob=str(blob),
        )
        rendered["blob_saved_to"] = str(output_path)
        rendered["text"] = (
            f"Binary resource saved to {output_path} "
            f"(server={server_name}, uri={rendered['uri']}, mime_type={rendered['mime_type'] or 'unknown'})."
        )
        return rendered

    def _persist_blob(
        self,
        cwd: Path,
        *,
        server_name: str,
        index: int,
        uri: str,
        mime_type: str,
        blob: str,
    ) -> Path:
        base_dir = cwd / ".pyclaude" / "mcp_resources"
        base_dir.mkdir(parents=True, exist_ok=True)
        suffix = _suffix_for_mime_type(mime_type)
        filename = _safe_filename(server_name, uri, index, suffix)
        output_path = (base_dir / filename).resolve()
        output_path.write_bytes(b64decode(blob))
        return output_path


def _safe_filename(server_name: str, uri: str, index: int, suffix: str) -> str:
    seed = f"{server_name}_{index}_{uri}"
    safe = "".join(ch if ch.isalnum() else "_" for ch in seed).strip("_")
    if len(safe) > 80:
        safe = safe[:80]
    return safe + suffix


def _suffix_for_mime_type(mime_type: str) -> str:
    normalized = mime_type.casefold()
    if normalized == "application/json":
        return ".json"
    if normalized.startswith("text/"):
        return ".txt"
    if normalized == "application/pdf":
        return ".pdf"
    if normalized.endswith("png"):
        return ".png"
    if normalized.endswith("jpeg") or normalized.endswith("jpg"):
        return ".jpg"
    if normalized.endswith("gif"):
        return ".gif"
    return ".bin"
