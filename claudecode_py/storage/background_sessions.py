from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4
import json

from ..workspace.isolation import derive_workspace_health


@dataclass(slots=True)
class BackgroundSessionRecord:
    bg_id: str
    cwd: str
    session_execution_mode: str
    session_command_policy_name: str | None
    session_command_policy_source: str | None
    session_command_policy_allowed_tool_names: list[str]
    session_command_policy_allowed_bash_prefixes: list[str]
    session_command_policy_require_read_only_subagents: bool
    original_cwd: str | None
    effective_cwd: str | None
    workspace_mode: str
    workspace_label: str | None
    workspace_created_at: str | None
    workspace_health: str
    workspace_cleanup_status: str
    workspace_cleanup_error: str | None
    workspace_unavailable: bool
    workspace_unavailable_reason: str | None
    workspace_fallback_cwd: str | None
    prompt: str
    provider: str
    model: str
    status: str
    created_at: str
    updated_at: str | None = None
    ended_at: str | None = None
    pid: int | None = None
    session_id: str | None = None
    transcript_path: str | None = None
    log_path: str | None = None
    bridge_host: str | None = None
    bridge_port: int | None = None
    error: str | None = None
    exit_code: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def new_background_session_id() -> str:
    return uuid4().hex[:10]


def get_background_sessions_dir(cwd: Path) -> Path:
    return cwd / ".pyclaude" / "background_sessions"


def get_background_session_registry_dir(cwd: Path) -> Path:
    return get_background_sessions_dir(cwd) / "registry"


def get_background_session_logs_dir(cwd: Path) -> Path:
    return get_background_sessions_dir(cwd) / "logs"


def get_background_session_path(cwd: Path, bg_id: str) -> Path:
    return get_background_session_registry_dir(cwd) / f"{bg_id}.json"


def get_background_session_log_path(cwd: Path, bg_id: str) -> Path:
    return get_background_session_logs_dir(cwd) / f"{bg_id}.log"


def save_background_session(cwd: Path, record: BackgroundSessionRecord) -> Path:
    registry_dir = get_background_session_registry_dir(cwd)
    registry_dir.mkdir(parents=True, exist_ok=True)
    if record.updated_at is None:
        record.updated_at = utc_now_iso()
    path = get_background_session_path(cwd, record.bg_id)
    path.write_text(json.dumps(record.to_dict(), ensure_ascii=True, indent=2), encoding="utf-8")
    return path


def create_background_session(
    cwd: Path,
    *,
    prompt: str,
    provider: str,
    model: str,
    status: str = "queued",
) -> BackgroundSessionRecord:
    bg_id = new_background_session_id()
    logs_dir = get_background_session_logs_dir(cwd)
    logs_dir.mkdir(parents=True, exist_ok=True)
    record = BackgroundSessionRecord(
        bg_id=bg_id,
        cwd=str(cwd),
        session_execution_mode="background-session",
        session_command_policy_name=None,
        session_command_policy_source=None,
        session_command_policy_allowed_tool_names=[],
        session_command_policy_allowed_bash_prefixes=[],
        session_command_policy_require_read_only_subagents=False,
        original_cwd=str(cwd),
        effective_cwd=str(cwd),
        workspace_mode="main",
        workspace_label=None,
        workspace_created_at=None,
        workspace_health="healthy",
        workspace_cleanup_status="none",
        workspace_cleanup_error=None,
        workspace_unavailable=False,
        workspace_unavailable_reason=None,
        workspace_fallback_cwd=str(cwd),
        prompt=prompt,
        provider=provider,
        model=model,
        status=status,
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
        log_path=str(get_background_session_log_path(cwd, bg_id)),
    )
    save_background_session(cwd, record)
    return record


def load_background_session(cwd: Path, bg_id: str) -> BackgroundSessionRecord | None:
    path = get_background_session_path(cwd, bg_id)
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return _load_background_record(payload)


def update_background_session(cwd: Path, bg_id: str, **updates) -> BackgroundSessionRecord:
    record = load_background_session(cwd, bg_id)
    if record is None:
        raise FileNotFoundError(bg_id)
    for key, value in updates.items():
        setattr(record, key, value)
    record.updated_at = utc_now_iso()
    if record.status in {"completed", "failed", "stopped"} and record.ended_at is None:
        record.ended_at = record.updated_at
    save_background_session(cwd, record)
    return record


def list_background_sessions(cwd: Path) -> list[BackgroundSessionRecord]:
    registry_dir = get_background_session_registry_dir(cwd)
    if not registry_dir.exists():
        return []
    records: list[BackgroundSessionRecord] = []
    for path in registry_dir.glob("*.json"):
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            records.append(_load_background_record(payload))
        except Exception:  # noqa: BLE001
            continue
    records.sort(
        key=lambda item: (
            item.updated_at or "",
            item.created_at,
            item.bg_id,
        ),
        reverse=True,
    )
    return records


def resolve_background_session(cwd: Path, identifier: str) -> BackgroundSessionRecord | None:
    exact = load_background_session(cwd, identifier)
    if exact is not None:
        return exact
    matches = [
        item
        for item in list_background_sessions(cwd)
        if item.bg_id.startswith(identifier) or (item.session_id and item.session_id.startswith(identifier))
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def _load_background_record(payload: dict) -> BackgroundSessionRecord:
    cwd = str(payload.get("cwd", ""))
    session_execution_mode = str(payload.get("session_execution_mode", "background-session") or "background-session")
    workspace_mode = str(payload.get("workspace_mode", "main") or "main")
    workspace_cleanup_status = str(payload.get("workspace_cleanup_status", "none") or "none")
    workspace_unavailable = bool(payload.get("workspace_unavailable", False))
    return BackgroundSessionRecord(
        bg_id=str(payload["bg_id"]),
        cwd=cwd,
        session_execution_mode=session_execution_mode,
        session_command_policy_name=(
            str(payload.get("session_command_policy_name"))
            if payload.get("session_command_policy_name") is not None
            else None
        ),
        session_command_policy_source=(
            str(payload.get("session_command_policy_source"))
            if payload.get("session_command_policy_source") is not None
            else None
        ),
        session_command_policy_allowed_tool_names=[
            str(item)
            for item in payload.get("session_command_policy_allowed_tool_names", [])
            if item is not None
        ],
        session_command_policy_allowed_bash_prefixes=[
            str(item)
            for item in payload.get("session_command_policy_allowed_bash_prefixes", [])
            if item is not None
        ],
        session_command_policy_require_read_only_subagents=bool(
            payload.get("session_command_policy_require_read_only_subagents", False)
        ),
        original_cwd=str(payload.get("original_cwd") or cwd),
        effective_cwd=str(payload.get("effective_cwd") or cwd),
        workspace_mode=workspace_mode,
        workspace_label=(
            str(payload.get("workspace_label"))
            if payload.get("workspace_label") is not None
            else None
        ),
        workspace_created_at=(
            str(payload.get("workspace_created_at"))
            if payload.get("workspace_created_at") is not None
            else None
        ),
        workspace_health=str(
            payload.get(
                "workspace_health",
                derive_workspace_health(
                    workspace_mode=workspace_mode,
                    workspace_cleanup_status=workspace_cleanup_status,
                    workspace_unavailable=workspace_unavailable,
                ),
            )
            or "healthy"
        ),
        workspace_cleanup_status=workspace_cleanup_status,
        workspace_cleanup_error=(
            str(payload.get("workspace_cleanup_error"))
            if payload.get("workspace_cleanup_error") is not None
            else None
        ),
        workspace_unavailable=workspace_unavailable,
        workspace_unavailable_reason=(
            str(payload.get("workspace_unavailable_reason"))
            if payload.get("workspace_unavailable_reason") is not None
            else None
        ),
        workspace_fallback_cwd=(
            str(payload.get("workspace_fallback_cwd"))
            if payload.get("workspace_fallback_cwd") is not None
            else None
        ),
        prompt=str(payload.get("prompt", "")),
        provider=str(payload.get("provider", "")),
        model=str(payload.get("model", "")),
        status=str(payload.get("status", "queued")),
        created_at=str(payload.get("created_at", utc_now_iso())),
        updated_at=str(payload.get("updated_at")) if payload.get("updated_at") is not None else None,
        ended_at=str(payload.get("ended_at")) if payload.get("ended_at") is not None else None,
        pid=int(payload["pid"]) if payload.get("pid") is not None else None,
        session_id=str(payload.get("session_id")) if payload.get("session_id") is not None else None,
        transcript_path=(
            str(payload.get("transcript_path"))
            if payload.get("transcript_path") is not None
            else None
        ),
        log_path=str(payload.get("log_path")) if payload.get("log_path") is not None else None,
        bridge_host=str(payload.get("bridge_host")) if payload.get("bridge_host") is not None else None,
        bridge_port=int(payload["bridge_port"]) if payload.get("bridge_port") is not None else None,
        error=str(payload.get("error")) if payload.get("error") is not None else None,
        exit_code=int(payload["exit_code"]) if payload.get("exit_code") is not None else None,
    )
