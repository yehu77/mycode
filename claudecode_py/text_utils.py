from __future__ import annotations

import difflib


def summarize_text_diff(before: str, after: str) -> list[str]:
    if not before and not after:
        return []
    diff = list(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile="before",
            tofile="after",
            lineterm="",
        )
    )
    filtered = [line for line in diff[2:] if line.startswith("+") or line.startswith("-")]
    if not filtered:
        return []
    if len(filtered) > 6:
        filtered = filtered[:6] + [f"... {len(diff[2:]) - 6} more diff line(s)"]
    return filtered


def compact_multiline_text(text: str, *, max_lines: int, max_chars: int) -> str:
    stripped = text.strip()
    if len(stripped) > max_chars:
        stripped = stripped[: max_chars - 3] + "..."
    lines = stripped.splitlines()
    if len(lines) <= max_lines:
        return "\n".join(lines)
    return "\n".join([*lines[:max_lines], "..."])
