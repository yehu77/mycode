from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Callable
import json


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


@dataclass(slots=True, frozen=True)
class CachedToolSchema:
    cache_key: str
    spec: dict[str, Any]


class ToolSchemaCache:
    def __init__(self) -> None:
        self._entries: dict[str, CachedToolSchema] = {}
        self._epoch = 0

    @property
    def epoch(self) -> int:
        return self._epoch

    def entry_count(self) -> int:
        return len(self._entries)

    def clear(self) -> None:
        self._entries.clear()
        self._epoch += 1

    def get(self, cache_key: str) -> CachedToolSchema | None:
        return self._entries.get(cache_key)

    def get_or_create(
        self,
        cache_key: str,
        *,
        builder: Callable[[], dict[str, Any]],
    ) -> tuple[dict[str, Any], bool]:
        cached = self._entries.get(cache_key)
        if cached is not None:
            return deepcopy(cached.spec), True
        spec = deepcopy(builder())
        self._entries[cache_key] = CachedToolSchema(cache_key=cache_key, spec=spec)
        return deepcopy(spec), False

    def combined_cache_key(self, keys: list[str]) -> str:
        if not keys:
            return "none"
        digest = sha256()
        for key in keys:
            digest.update(key.encode("utf-8"))
            digest.update(b"\n")
        return digest.hexdigest()[:16]
