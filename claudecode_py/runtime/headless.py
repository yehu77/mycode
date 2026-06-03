from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import SessionConfig
from ..integrations import (
    DiffTargetResult,
    EditorTarget,
    ReferenceLookupResult,
    ReferenceTargetResult,
    SymbolActionBundle,
    SymbolLookupResult,
)
from ..runtime.events import RuntimeEvent
from ..session import Session
from ..session_factory import SessionFactory
from ..storage.transcript import (
    get_session_path,
)


@dataclass(slots=True)
class HeadlessRunResult:
    output: str
    events: list[RuntimeEvent]
    session_id: str
    cwd: str
    message_count: int
    context_summary: str | None
    transcript_path: Path | None
    restored_from: Path | None = None

    def to_dict(self) -> dict:
        return {
            "kind": "run_result",
            "session_id": self.session_id,
            "cwd": self.cwd,
            "restored_from": str(self.restored_from) if self.restored_from is not None else None,
            "payload": {
                "output": self.output,
                "message_count": self.message_count,
                "context_summary": self.context_summary,
                "transcript_path": str(self.transcript_path) if self.transcript_path is not None else None,
                "events": [_runtime_event_to_dict(item) for item in self.events],
            },
        }


@dataclass(slots=True)
class HeadlessSymbolLookupResult:
    lookup: SymbolLookupResult
    session_id: str
    cwd: str
    restored_from: Path | None = None

    def to_dict(self) -> dict:
        return _headless_result_dict(
            "symbol_lookup",
            session_id=self.session_id,
            cwd=self.cwd,
            restored_from=self.restored_from,
            payload=self.lookup.to_dict(),
        )


@dataclass(slots=True)
class HeadlessReferenceLookupResult:
    lookup: ReferenceLookupResult
    session_id: str
    cwd: str
    restored_from: Path | None = None

    def to_dict(self) -> dict:
        return _headless_result_dict(
            "reference_lookup",
            session_id=self.session_id,
            cwd=self.cwd,
            restored_from=self.restored_from,
            payload=self.lookup.to_dict(),
        )


@dataclass(slots=True)
class HeadlessEditorTargetResult:
    target: EditorTarget
    session_id: str
    cwd: str
    restored_from: Path | None = None

    def to_dict(self) -> dict:
        return _headless_result_dict(
            "editor_target",
            session_id=self.session_id,
            cwd=self.cwd,
            restored_from=self.restored_from,
            payload=self.target.to_dict(),
        )


@dataclass(slots=True)
class HeadlessDiffTargetResult:
    diff: DiffTargetResult
    session_id: str
    cwd: str
    restored_from: Path | None = None

    def to_dict(self) -> dict:
        return _headless_result_dict(
            "diff_targets",
            session_id=self.session_id,
            cwd=self.cwd,
            restored_from=self.restored_from,
            payload=self.diff.to_dict(),
        )


@dataclass(slots=True)
class HeadlessReferenceTargetResult:
    targets: ReferenceTargetResult
    session_id: str
    cwd: str
    restored_from: Path | None = None

    def to_dict(self) -> dict:
        return _headless_result_dict(
            "reference_targets",
            session_id=self.session_id,
            cwd=self.cwd,
            restored_from=self.restored_from,
            payload=self.targets.to_dict(),
        )


@dataclass(slots=True)
class HeadlessSymbolActionBundleResult:
    bundle: SymbolActionBundle
    session_id: str
    cwd: str
    restored_from: Path | None = None

    def to_dict(self) -> dict:
        return _headless_result_dict(
            "symbol_actions",
            session_id=self.session_id,
            cwd=self.cwd,
            restored_from=self.restored_from,
            payload=self.bundle.to_dict(),
        )


def _headless_result_dict(
    kind: str,
    *,
    session_id: str,
    cwd: str,
    restored_from: Path | None,
    payload: dict,
) -> dict:
    return {
        "kind": kind,
        "session_id": session_id,
        "cwd": cwd,
        "restored_from": str(restored_from) if restored_from is not None else None,
        "payload": payload,
    }


def _runtime_event_to_dict(event: RuntimeEvent) -> dict:
    return {
        "kind": event.kind,
        "message": event.message,
        "tool_name": event.tool_name,
        "tool_call_id": event.tool_call_id,
        "duration_ms": event.duration_ms,
        "prompt_tokens": event.prompt_tokens,
        "completion_tokens": event.completion_tokens,
        "total_tokens": event.total_tokens,
        "usage_source": event.usage_source,
        "is_error": event.is_error,
    }


def create_headless_session(
    config: SessionConfig,
    *,
    restore_latest: bool = False,
    resume_session_id: str | None = None,
) -> tuple[Session, Path | None]:
    factory = SessionFactory(load_mcp_from_config=True)
    return factory.create_or_restore_session(
        config,
        restore_latest=restore_latest,
        resume_session_id=resume_session_id,
    )


class HeadlessRunner:
    def __init__(self, session: Session) -> None:
        self.session = session

    def run(self, prompt: str) -> HeadlessRunResult:
        events: list[RuntimeEvent] = []
        output = self.session.ask(prompt, sink=events.append)
        transcript_path = (
            get_session_path(self.session.config.cwd, self.session.state.session_id)
            if self.session.persist_transcript
            else None
        )
        return HeadlessRunResult(
            output=output,
            events=events,
            session_id=self.session.state.session_id,
            cwd=str(self.session.config.cwd),
            message_count=len(self.session.state.messages),
            context_summary=self.session.state.context_summary,
            transcript_path=transcript_path,
        )

    def locate_symbol(
        self,
        symbol: str,
        *,
        path: str = ".",
        max_results: int = 50,
    ) -> HeadlessSymbolLookupResult:
        return HeadlessSymbolLookupResult(
            lookup=self.session.locate_symbol(symbol, path=path, max_results=max_results),
            session_id=self.session.state.session_id,
            cwd=str(self.session.config.cwd),
        )

    def collect_references(
        self,
        symbol: str,
        *,
        path: str = ".",
        scope: str = "auto",
        max_results: int = 100,
    ) -> HeadlessReferenceLookupResult:
        return HeadlessReferenceLookupResult(
            lookup=self.session.collect_references(
                symbol,
                path=path,
                scope=scope,
                max_results=max_results,
            ),
            session_id=self.session.state.session_id,
            cwd=str(self.session.config.cwd),
        )

    def build_open_file_target(
        self,
        path: str,
        *,
        line: int = 1,
        column: int = 1,
        end_line: int | None = None,
        end_column: int | None = None,
        label: str = "",
    ) -> HeadlessEditorTargetResult:
        return HeadlessEditorTargetResult(
            target=self.session.build_open_file_target(
                path,
                line=line,
                column=column,
                end_line=end_line,
                end_column=end_column,
                label=label,
            ),
            session_id=self.session.state.session_id,
            cwd=str(self.session.config.cwd),
        )

    def build_symbol_target(
        self,
        symbol: str,
        *,
        path: str = ".",
        match_index: int = 0,
    ) -> HeadlessEditorTargetResult:
        return HeadlessEditorTargetResult(
            target=self.session.build_symbol_target(
                symbol,
                path=path,
                match_index=match_index,
            ),
            session_id=self.session.state.session_id,
            cwd=str(self.session.config.cwd),
        )

    def build_diff_targets(
        self,
        path: str,
        *,
        before: str,
        after: str,
    ) -> HeadlessDiffTargetResult:
        return HeadlessDiffTargetResult(
            diff=self.session.build_diff_targets(path, before=before, after=after),
            session_id=self.session.state.session_id,
            cwd=str(self.session.config.cwd),
        )

    def build_reference_targets(
        self,
        symbol: str,
        *,
        path: str = ".",
        scope: str = "auto",
        max_results: int = 100,
    ) -> HeadlessReferenceTargetResult:
        return HeadlessReferenceTargetResult(
            targets=self.session.build_reference_targets(
                symbol,
                path=path,
                scope=scope,
                max_results=max_results,
            ),
            session_id=self.session.state.session_id,
            cwd=str(self.session.config.cwd),
        )

    def build_symbol_action_bundle(
        self,
        symbol: str,
        *,
        path: str = ".",
        scope: str = "workspace",
        max_definition_results: int = 50,
        max_reference_results: int = 100,
    ) -> HeadlessSymbolActionBundleResult:
        return HeadlessSymbolActionBundleResult(
            bundle=self.session.build_symbol_action_bundle(
                symbol,
                path=path,
                scope=scope,
                max_definition_results=max_definition_results,
                max_reference_results=max_reference_results,
            ),
            session_id=self.session.state.session_id,
            cwd=str(self.session.config.cwd),
        )

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "HeadlessRunner":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def run_headless(
    prompt: str,
    *,
    config: SessionConfig,
    restore_latest: bool = False,
    resume_session_id: str | None = None,
) -> HeadlessRunResult:
    session, restored_from = create_headless_session(
        config,
        restore_latest=restore_latest,
        resume_session_id=resume_session_id,
    )
    with HeadlessRunner(session) as runner:
        result = runner.run(prompt)
    result.restored_from = restored_from
    return result


def locate_symbol_headless(
    symbol: str,
    *,
    config: SessionConfig,
    path: str = ".",
    max_results: int = 50,
    restore_latest: bool = False,
    resume_session_id: str | None = None,
) -> HeadlessSymbolLookupResult:
    session, restored_from = create_headless_session(
        config,
        restore_latest=restore_latest,
        resume_session_id=resume_session_id,
    )
    with HeadlessRunner(session) as runner:
        result = runner.locate_symbol(symbol, path=path, max_results=max_results)
    result.restored_from = restored_from
    return result


def collect_references_headless(
    symbol: str,
    *,
    config: SessionConfig,
    path: str = ".",
    scope: str = "auto",
    max_results: int = 100,
    restore_latest: bool = False,
    resume_session_id: str | None = None,
) -> HeadlessReferenceLookupResult:
    session, restored_from = create_headless_session(
        config,
        restore_latest=restore_latest,
        resume_session_id=resume_session_id,
    )
    with HeadlessRunner(session) as runner:
        result = runner.collect_references(
            symbol,
            path=path,
            scope=scope,
            max_results=max_results,
        )
    result.restored_from = restored_from
    return result


def open_file_target_headless(
    path: str,
    *,
    config: SessionConfig,
    line: int = 1,
    column: int = 1,
    end_line: int | None = None,
    end_column: int | None = None,
    label: str = "",
    restore_latest: bool = False,
    resume_session_id: str | None = None,
) -> HeadlessEditorTargetResult:
    session, restored_from = create_headless_session(
        config,
        restore_latest=restore_latest,
        resume_session_id=resume_session_id,
    )
    with HeadlessRunner(session) as runner:
        result = runner.build_open_file_target(
            path,
            line=line,
            column=column,
            end_line=end_line,
            end_column=end_column,
            label=label,
        )
    result.restored_from = restored_from
    return result


def open_symbol_target_headless(
    symbol: str,
    *,
    config: SessionConfig,
    path: str = ".",
    match_index: int = 0,
    restore_latest: bool = False,
    resume_session_id: str | None = None,
) -> HeadlessEditorTargetResult:
    session, restored_from = create_headless_session(
        config,
        restore_latest=restore_latest,
        resume_session_id=resume_session_id,
    )
    with HeadlessRunner(session) as runner:
        result = runner.build_symbol_target(symbol, path=path, match_index=match_index)
    result.restored_from = restored_from
    return result


def diff_targets_headless(
    path: str,
    *,
    before: str,
    after: str,
    config: SessionConfig,
    restore_latest: bool = False,
    resume_session_id: str | None = None,
) -> HeadlessDiffTargetResult:
    session, restored_from = create_headless_session(
        config,
        restore_latest=restore_latest,
        resume_session_id=resume_session_id,
    )
    with HeadlessRunner(session) as runner:
        result = runner.build_diff_targets(path, before=before, after=after)
    result.restored_from = restored_from
    return result


def reference_targets_headless(
    symbol: str,
    *,
    config: SessionConfig,
    path: str = ".",
    scope: str = "auto",
    max_results: int = 100,
    restore_latest: bool = False,
    resume_session_id: str | None = None,
) -> HeadlessReferenceTargetResult:
    session, restored_from = create_headless_session(
        config,
        restore_latest=restore_latest,
        resume_session_id=resume_session_id,
    )
    with HeadlessRunner(session) as runner:
        result = runner.build_reference_targets(
            symbol,
            path=path,
            scope=scope,
            max_results=max_results,
        )
    result.restored_from = restored_from
    return result


def symbol_actions_headless(
    symbol: str,
    *,
    config: SessionConfig,
    path: str = ".",
    scope: str = "workspace",
    max_definition_results: int = 50,
    max_reference_results: int = 100,
    restore_latest: bool = False,
    resume_session_id: str | None = None,
) -> HeadlessSymbolActionBundleResult:
    session, restored_from = create_headless_session(
        config,
        restore_latest=restore_latest,
        resume_session_id=resume_session_id,
    )
    with HeadlessRunner(session) as runner:
        result = runner.build_symbol_action_bundle(
            symbol,
            path=path,
            scope=scope,
            max_definition_results=max_definition_results,
            max_reference_results=max_reference_results,
        )
    result.restored_from = restored_from
    return result
