# Skills and Bundled Prompts Plan

This document is the next-stage implementation plan for the remaining local
parity gap on the `Skills / bundled command prompts` line after the
history/rewind, local agents, status dashboard, workspace/context, and local
plugin-framework milestones reached a stable local baseline.

It is intentionally scoped to the local coding-agent goal of this repo. The
target is not hosted skill marketplace parity. The target is a stronger local
skill workflow across:

- REPL
- TUI
- stdio
- remote session proxy

and across the current local sources:

- builtin prompt/command skills
- project-local `.pyclaude/skills/*.md`
- plugin-contributed declarative skills

## Current Baseline

The Python implementation already has substantial skill-related pieces:

- project-local skill loading from `.pyclaude/skills/*.md`
- frontmatter parsing for `description`, `auto_enable`, and `tags`
- session-local manual enable/disable overrides
- `/skills`, `/skills-enable`, `/skills-disable`, `/skills-reload`
- `/project-context skills`
- grouped skill state in `/project-context summary`, `/config`, and `/status`
- plugin-contributed declarative skills
- agent-definition inspection that already references skill resolution

Important current references:

- [PARITY_MATRIX.md](./PARITY_MATRIX.md)
- [PLUGIN_FRAMEWORK_PLAN.md](./PLUGIN_FRAMEWORK_PLAN.md)
- [STATUS_SURFACE_PLAN.md](./STATUS_SURFACE_PLAN.md)
- [claudecode_py/skills/loader.py](./claudecode_py/skills/loader.py)
- [claudecode_py/session.py](./claudecode_py/session.py)
- [claudecode_py/commands/builtin.py](./claudecode_py/commands/builtin.py)
- [claudecode_py/tui/state.py](./claudecode_py/tui/state.py)

## Current Read

The local gap is no longer "can skills load?" The remaining gap is that skill
state is still thinner and less unified than the now-completed plugin
framework.

The most valuable remaining local work is:

1. making `/skills` the canonical skill registry overview surface
2. making skill source/resolution explicit across builtin, project-local, and
   plugin-contributed skills
3. making enable/disable and reload-state semantics more explicit
4. exposing a shared structured `skills_surface` payload for stdio/remote/TUI
5. clarifying how skill state relates to prompt composition, command prompts,
   and project-context reload behavior

This means the local skill parity work should prioritize inspection, lifecycle
clarity, and structured metadata, not hosted packaging/distribution breadth.

## Problem Statement

The current implementation has strong local primitives, but skills still have a
few fragmentation problems:

- `/skills` is informative but not yet the same kind of canonical registry
  surface that `/plugins` became
- skill source is implicit in some places and absent in others
- skill enablement state, auto-enable state, and manual override state are not
  yet described with one shared vocabulary
- reload-state reporting exists at project-context level, but not as a stronger
  dedicated skill lifecycle story
- stdio/remote/TUI consumers still rely mostly on broader project-context or
  status summaries instead of a dedicated structured skill surface

## Scope Boundary

This plan is intentionally limited to:

- builtin prompt/command skills already shipped in the local runtime
- project-local `.pyclaude/skills/*.md`
- plugin-contributed declarative skills
- session-local manual enable/disable overrides
- local inspection, resolution, and lifecycle reporting

It does **not** attempt to reproduce:

- hosted skill marketplace/discovery/install flows
- arbitrary executable skill code loading
- user-level/global skill registries
- remote ecosystem/distribution features

## Implemented/Planned Phases

### Phase 1: Skills Vocabulary and Surface Alignment
Status: Completed for current local scope

Goal:
make every skill-related surface describe the same state model.

Target vocabulary:

- `skill registry`
- `skill source`
- `skill status`
- `skill auto-enable state`
- `manual skill overrides`
- `skill reload state`
- `skill diagnostics`
- `skill tags`

Primary outcomes:

- make `/skills` the canonical skill overview surface
- keep `/project-context skills` as the project-context-focused drill-down
- align `/status`, `/config`, TUI, stdio, and remote wording to the same skill
  vocabulary
- current local surfaces now use explicit `skill registry`, `skill source`,
  `skill status`, `skill auto-enable state`, `manual skill overrides`,
  `skill reload state`, and `skill diagnostics` wording

### Phase 2: Skill Source and Resolution Depth
Status: Completed for current local scope

Goal:
make the origin and effective resolution of every loaded skill explicit.

Primary outcomes:

- expose whether a skill is:
  - builtin
  - project-local
  - plugin-contributed
- expose:
  - effective enablement state
  - default/auto-enable state
  - manual override state
  - tags
  - description
  - path/source owner
- define conflict policy explicitly for same-name skills across sources
- make skill-source precedence readable in `/skills` and related surfaces

Current default direction:

- keep the local scope conservative
- prefer diagnostics over silent overriding when same-name cross-source conflicts
  would otherwise become ambiguous
- the current local implementation now uses explicit `project-local`,
  `plugin-contributed`, and `builtin` source labeling, and same-name
  plugin/project skill conflicts surface diagnostics while preserving the
  already-effective skill in prompt composition

### Phase 3: Skill Reload and Lifecycle Cohesion
Status: Completed for current local scope

Goal:
make skill reload part of the same local lifecycle story as project-context and
plugin refresh.

Primary outcomes:

- strengthen `/skills-reload` reporting so it clearly shows:
  - registry membership changes
  - enabled-set changes
  - source/resolution changes
  - diagnostics changes
- current local implementation now also distinguishes skill content changes from
  pure registry/resolution changes
- surface the same reload story in:
  - `/project-context reload-status`
  - `/project-context skills`
  - `/status`
  - `/config`
- preserve manual skill enable/disable overrides across reloads and resumed
  sessions while reloading the current workspace skill registry

### Phase 4: Structured Skills Payloads for Stdio / Remote
Status: Completed for current local scope

Goal:
stop forcing headless callers to infer skill state from text-only views.

Primary outcomes:

- add a shared `skills_surface` payload on `Session`
- expose it through stdio `session.describe` and `session.list_open`
- cache and expose it on `RemoteSessionProxy`

Minimum structured fields:

- registry summary
- enabled/disabled/manual-override counts
- per-skill source and status summary
- tags/auto-enable summary
- diagnostics summary
- reload-state summary
- skill action groups

Current local outcome:

- `Session.skills_surface_payload()` now exists
- stdio `session.describe` and `session.list_open` now expose `skills_surface`
- `RemoteSessionProxy` now caches and exposes the same structured payload

### Phase 5: Prompt Composition and Bundled Prompt Depth
Status: Completed for current local scope

Goal:
make the relationship between active skills and prompt composition more visible.

Primary outcomes:

- clarify how auto-enabled and manually enabled skills contribute to prompt
  composition
- strengthen `/skills` and related inspection so the user can see:
  - which skills are currently active in prompt composition
  - which are loaded but inactive
  - which are plugin-contributed versus project-local
- add compact prompt-composition summaries to `/status`, `/project-context`, or
  dedicated skill views where useful
- keep this inspection-first; do not introduce a hosted prompt marketplace or
  editor flow

Current local outcome:

- `/skills` and `/project-context` now expose an explicit `skill prompt composition`
  summary plus the active-auto / active-manual / inactive skill splits
- `/status` and the TUI status dashboard now surface a compact skill
  prompt-composition summary
- `/context` skill usage details now carry source-aware summaries for the
  currently prompt-active skills

### Phase 6: TUI Skills Workflow Depth
Status: Completed for current local scope

Goal:
make skill state visible in the same dashboard workflow as plugins/status.

Primary outcomes:

- add a dedicated skill block in the TUI dashboard using the structured
  `skills_surface` payload
- show:
  - skill registry summary
  - manual override summary
  - reload state summary
  - selected/high-signal skill actions
- keep TUI interactions light and inspection-first

Current local outcome:

- the TUI status/dashboard now consumes the shared `skills_surface` payload
- it renders a dedicated `Skill Registry` block with:
  - registry summary
  - prompt composition summary
  - source counts
  - status counts
  - manual override summary
  - reload-state summary
  - diagnostics count
  - selected skill summary
  - skill action groups

### Phase 7: Final Local Skill Workflow Polish
Status: Completed for current local scope

Goal:
close the local-scope gap and leave a stable implementation record.

Primary outcomes:

- remove duplicated skill summary assembly across session surfaces
- align skill wording with plugin/status/project-context vocabulary
- update parity notes and top-level docs once the skill workflow settles
- leave the remaining gap explicitly documented as hosted packaging/distribution
  breadth rather than missing local skill workflow depth

Current local outcome:

- duplicated skill summary/diagnostic/next-action assembly is now reduced
  through shared session helpers reused by `/skills` and `/project-context`
- skill wording is aligned across `/skills`, `/project-context skills`,
  `/status`, `/config`, stdio, remote, and TUI
- parity/top-level docs now describe the skills line as a completed local
  workflow with remaining hosted packaging/distribution breadth explicitly left
  out of scope

## Important Interface Changes

Public/user-facing behavior should evolve as follows:

- `/skills` becomes the canonical skill overview surface
- `/project-context skills` remains the project-context-focused skill surface
- `/status`, `/config`, stdio, remote, and TUI align to the same skill
  vocabulary
- `session.describe` and `session.list_open` gain a structured `skills_surface`
  payload
- `RemoteSessionProxy` gains a matching `skills_surface_payload()` accessor

Internal/framework changes should include:

- shared skill summary/action helpers, likely centered in `Session` and the
  existing project-context/session summary path
- explicit conflict/diagnostic policy for same-name skills across sources
- structured skill metadata emitted from `Session`, then reused by
  stdio/remote/TUI

## Test Plan

### Loader / registry

- builtin-only skill behavior still works unchanged
- valid project-local skills still load with correct description/auto-enable/tags
- plugin-contributed skills continue to load and follow plugin enable/disable
  state
- same-name cross-source skill conflicts surface stable diagnostics if/when the
  new policy is introduced

### Session / REPL

- `/skills`, `/project-context skills`, `/status`, and `/config` use aligned
  skill vocabulary
- skill source, auto-enable state, manual override state, and reload wording
  are stable and non-conflicting
- manual enable/disable overrides persist through reload/resume

### Structured payloads

- `session.describe` and `session.list_open` return `skills_surface`
- `RemoteSessionProxy` syncs and exposes the same payload

### TUI

- status/dashboard skill block renders from structured payload
- override counts and reload-state summaries appear consistently

### Regression

- existing `/skills-enable`, `/skills-disable`, `/skills-reload` behavior stays
  intact
- prompt composition still includes the same active skills
- plugin-contributed skills and project-local skills continue to work together
- no new marketplace/install/update behavior is introduced

## Assumptions

- scope remains local-first; marketplace/discovery/install flows stay out of
  scope
- skill sources remain builtin, project-local `.pyclaude/skills`, and
  plugin-contributed declarative skills
- this line deepens inspection, lifecycle clarity, and structured metadata
  first; it does not attempt a full skill editor or hosted ecosystem parity
