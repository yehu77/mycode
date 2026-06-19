# Skill Runtime Reproduction Plan

## Summary

This line pushes `python_claudecode` from prompt-only project skills toward the upstream core skill runtime shape:

- directory-based skills at `.claude/skills/<name>/SKILL.md`
- richer skill frontmatter
- real user-invocable `/<skill-name>` commands
- model-side skill invocation through a local `skill` tool

The current implementation is intentionally scoped to a local-first runtime. It does not claim full upstream skill breadth.

## Implemented Scope

- Added `.claude/skills/<name>/SKILL.md` as the primary project-local skill format.
- Kept `.pyclaude/skills/*.md` as a compatibility format.
- Extended `LoadedSkill` to carry:
  - description
  - when-to-use
  - user-invocable
  - argument-hint
  - arguments
  - allowed tool names
  - allowed bash command prefixes
  - execution context
  - skill root
- Added frontmatter parsing for:
  - `description`
  - `allowed-tools`
  - `when_to_use`
  - `argument-hint`
  - `arguments`
  - `user-invocable`
  - `context`
  - compatibility fields such as `auto_enable` and `tags`
- Added stable precedence:
  - `.claude/skills` wins over legacy `.pyclaude/skills` on same-name conflicts
  - conflicts emit skill diagnostics instead of silently merging
- Registered user-invocable skills as real `/<skill-name>` prompt commands.
- Added argument substitution for skill prompt expansion:
  - `$name`
  - `${name}`
  - `${CLAUDE_SKILL_DIR}`
- Added local `allowed-tools` mapping into existing command policy restrictions.
- Added a local `skill` tool so the model can invoke known user-invocable skills inline.
- Updated skill inspection surfaces to distinguish:
  - prompt-active skills
  - user-invocable skills
  - inactive skills
  - diagnostics
- Added runtime guidance so the model sees:
  - `/<skill-name>` is the user-facing entrypoint
  - the `skill` tool is only for known user-invocable skills

## Current Boundary

This implementation intentionally stops short of full upstream breadth:

- `context: fork` is parsed and surfaced, but still executes inline in the current runtime.
- No conditional skill activation by path or dynamic filters.
- No hot reload watcher or skill change detector beyond normal context reload.
- No skill search, ranking, or discovery heuristics.
- No transcript persistence for invoked-skill execution history.
- No broader user/global/add-dir multi-source skill search tree.

## Remaining Gap

The remaining gap is now mostly breadth and deeper execution semantics, not absence of a core local skill runtime.

Highest-value follow-up if this line is reopened:

- true forked skill execution for `context: fork`
- broader upstream-like skill source resolution
- better model/runtime guidance around invoked skill reuse
- compaction-aware preservation of invoked-skill state
