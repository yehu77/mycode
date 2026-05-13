from __future__ import annotations

from pathlib import Path
import json

from .permissions import PermissionDecision, PermissionRule, PermissionRuleScope


def default_permission_config_path(cwd: Path) -> Path:
    return cwd / ".pyclaude" / "permissions.json"


def load_permission_rules(cwd: Path, *, config_path: Path | None = None) -> list[PermissionRule]:
    path = config_path or default_permission_config_path(cwd)
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("rules", [])
    if not isinstance(items, list):
        raise ValueError("permissions.json must contain a 'rules' array.")
    rules: list[PermissionRule] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Each permission rule must be an object.")
        rules.append(permission_rule_from_dict(item))
    return rules


def save_permission_rules(
    cwd: Path,
    rules: list[PermissionRule],
    *,
    config_path: Path | None = None,
) -> Path:
    path = config_path or default_permission_config_path(cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "rules": [permission_rule_to_dict(rule) for rule in rules],
    }
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    return path


def permission_rule_to_dict(rule: PermissionRule) -> dict[str, str]:
    return {
        "decision": rule.decision.value,
        "scope": rule.scope.value,
        "value": rule.value,
    }


def permission_rule_from_dict(payload: dict[str, str]) -> PermissionRule:
    try:
        decision = PermissionDecision(str(payload["decision"]))
        scope = PermissionRuleScope(str(payload["scope"]))
    except KeyError as exc:
        raise ValueError(f"Missing permission rule field: {exc.args[0]}") from exc
    except ValueError as exc:
        raise ValueError(f"Invalid permission rule: {exc}") from exc
    value = str(payload.get("value", "")).strip()
    if not value:
        raise ValueError("Permission rule value cannot be empty.")
    return PermissionRule(decision=decision, scope=scope, value=value)
