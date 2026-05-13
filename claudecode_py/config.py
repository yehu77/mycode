from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(slots=True)
class SessionConfig:
    cwd: Path
    transcript_cwd: Path | None = None
    model: str = "claude-3-7-sonnet-latest"
    max_tokens: int = 4096
    max_turns: int = 12
    max_tool_rounds_per_turn: int = 8
    max_history_messages: int = 200
    history_keep_last_messages: int = 40
    max_context_summary_chars: int = 4000
    provider_max_retries: int = 2
    provider_retry_base_delay_sec: float = 0.5
    permission_mode: str = "default"
    interactive: bool = True
    provider: str = "anthropic"
    api_key: str | None = None
    base_url: str | None = None
    mcp_config_path: Path | None = None
    permission_config_path: Path | None = None
    max_agent_depth: int = 2


def default_model_for_provider(provider: str) -> str:
    if provider == "openai-compatible":
        return "gpt-4.1-mini"
    return "claude-3-7-sonnet-latest"


def load_config(
    cwd: str | None = None,
    *,
    provider: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    mcp_config_path: str | None = None,
    permission_config_path: str | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    max_turns: int | None = None,
    max_tool_rounds_per_turn: int | None = None,
    max_history_messages: int | None = None,
    history_keep_last_messages: int | None = None,
    max_context_summary_chars: int | None = None,
    provider_max_retries: int | None = None,
    provider_retry_base_delay_sec: float | None = None,
    permission_mode: str | None = None,
    interactive: bool = True,
) -> SessionConfig:
    resolved_cwd = Path(cwd or os.getcwd()).resolve()
    resolved_provider = provider or os.getenv("PYCLAUDE_PROVIDER", "anthropic")
    resolved_model = model or os.getenv(
        "PYCLAUDE_MODEL",
        default_model_for_provider(resolved_provider),
    )
    resolved_mcp_config_path = Path(
        mcp_config_path
        or os.getenv("PYCLAUDE_MCP_CONFIG")
        or (resolved_cwd / ".pyclaude" / "mcp_servers.json")
    ).resolve()
    resolved_permission_config_path = Path(
        permission_config_path
        or os.getenv("PYCLAUDE_PERMISSION_CONFIG")
        or (resolved_cwd / ".pyclaude" / "permissions.json")
    ).resolve()
    return SessionConfig(
        cwd=resolved_cwd,
        transcript_cwd=resolved_cwd,
        provider=resolved_provider,
        api_key=api_key
        or os.getenv("PYCLAUDE_API_KEY")
        or (
            os.getenv("OPENAI_API_KEY")
            if resolved_provider == "openai-compatible"
            else os.getenv("ANTHROPIC_API_KEY")
        ),
        base_url=base_url
        or os.getenv("PYCLAUDE_BASE_URL")
        or (os.getenv("OPENAI_BASE_URL") if resolved_provider == "openai-compatible" else None),
        mcp_config_path=resolved_mcp_config_path,
        permission_config_path=resolved_permission_config_path,
        model=resolved_model,
        max_tokens=max_tokens or int(os.getenv("PYCLAUDE_MAX_TOKENS", "4096")),
        max_turns=max_turns or int(os.getenv("PYCLAUDE_MAX_TURNS", "12")),
        max_tool_rounds_per_turn=max_tool_rounds_per_turn
        if max_tool_rounds_per_turn is not None
        else int(os.getenv("PYCLAUDE_MAX_TOOL_ROUNDS_PER_TURN", "8")),
        max_history_messages=max_history_messages
        or int(os.getenv("PYCLAUDE_MAX_HISTORY_MESSAGES", "200")),
        history_keep_last_messages=history_keep_last_messages
        if history_keep_last_messages is not None
        else int(os.getenv("PYCLAUDE_HISTORY_KEEP_LAST_MESSAGES", "40")),
        max_context_summary_chars=max_context_summary_chars
        if max_context_summary_chars is not None
        else int(os.getenv("PYCLAUDE_MAX_CONTEXT_SUMMARY_CHARS", "4000")),
        provider_max_retries=provider_max_retries
        if provider_max_retries is not None
        else int(os.getenv("PYCLAUDE_PROVIDER_MAX_RETRIES", "2")),
        provider_retry_base_delay_sec=provider_retry_base_delay_sec
        if provider_retry_base_delay_sec is not None
        else float(os.getenv("PYCLAUDE_PROVIDER_RETRY_BASE_DELAY_SEC", "0.5")),
        permission_mode=permission_mode or os.getenv("PYCLAUDE_PERMISSION_MODE", "default"),
        interactive=interactive,
    )
