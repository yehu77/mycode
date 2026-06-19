# Skill Source-Depth Alignment

This document compares the current `python_claudecode` skill runtime to the upstream Claude Code skill implementation in `../package/src-extracted/src/skills/*`, `../package/src-extracted/src/commands.ts`, and `../package/src-extracted/src/tools/SkillTool/*` by **mechanism depth**, not by slash-command surface alone.

It is the canonical place to answer:

- how close the Python skill runtime is to upstream at the loader/runtime/orchestration level
- which parts are already structurally aligned
- which parts are only local functional substitutes
- which remaining gaps are real local runtime deficits vs broader upstream breadth

## Method

This comparison is grounded in mechanism families rather than user-facing command lists.

The main layers used here are:

- skill source discovery and precedence
- skill file format and frontmatter depth
- slash-command registration and prompt-command ownership
- prompt expansion semantics
- allowed-tools and permission semantics
- model-invocable skill runtime
- fork execution semantics
- conditional and path-scoped skill activation
- runtime guidance and discovery surfaces
- reload/cache lifecycle and broader source breadth

Depth verdict vocabulary:

- `Depth-aligned`
- `Locally aligned with narrower breadth`
- `Functional substitute, shallower mechanism`
- `Real local mechanism gap`
- `Broader upstream breadth`
- `Deliberately out of current local scope`

The goal is to separate:

- structural alignment to the same upstream idea
- local substitutes that achieve a similar outcome through simpler machinery
- broader upstream product/runtime breadth that should not be treated as a missing local core

## Skill Mechanism Alignment Matrix

| Mechanism family | Upstream mechanism | Current `python_claudecode` | Depth verdict | Why this verdict | Next action classification |
|---|---|---|---|---|---|
| Skill source discovery and precedence | Skills come from bundled, user, project, managed, plugin, MCP, additional-dir, and legacy command sources, with deduping and source-aware precedence. | Skills come from `.claude/skills/<name>/SKILL.md`, legacy `.pyclaude/skills/*.md`, and plugin-contributed skills merged into project context. | Functional substitute, shallower mechanism | Project-local directory skills and legacy compatibility are real, but the broader upstream source tree and dedupe/identity machinery are not present locally. | Broader upstream breadth |
| Skill file format and frontmatter depth | Upstream prompt commands support richer metadata including `model`, `effort`, `hooks`, `paths`, `shell`, `disable-model-invocation`, `version`, `agent`, plus prompt-command-specific helpers. | Local skills support `description`, `when-to-use`, `user-invocable`, `argument-hint`, `arguments`, `allowed-tools`, `context`, `disable-model-invocation`, `model`, `effort`, `auto-enable`, and `tags`. | Locally aligned with narrower breadth | The local loader now carries real runtime metadata and several behavior-changing fields, but the broader upstream frontmatter set is still materially deeper. | Real local mechanism gap |
| Slash-command registration model | Skills become first-class prompt commands in the shared upstream command graph, alongside bundled and legacy prompt commands. | User-invocable project skills become real `/<skill-name>` REPL commands through the local command registry. | Locally aligned with narrower breadth | The core idea is now matched locally, but it is narrower than the upstream shared command graph and does not cover the full upstream source set. | Broader upstream breadth |
| Prompt expansion semantics | Upstream commands expose `getPromptForCommand`, support richer substitution, session variables, shell execution in prompt bodies, and source-aware prompt preparation. | Local skills expand inline prompt text with argument substitution and `${CLAUDE_SKILL_DIR}`, then run through existing command execution. | Functional substitute, shallower mechanism | The local prompt-expansion line is real and usable, but it is materially simpler than upstream prompt-command preparation and inline shell semantics. | Real local mechanism gap |
| Allowed-tools / permission semantics | Upstream `allowed-tools` integrates with the broader prompt-command/tool permission model and shell execution path. | Local `allowed-tools` is mapped into existing command policy restrictions and bash-prefix allowlists. | Locally aligned with narrower breadth | The local runtime now scopes tools per skill in a meaningful way, but it is still an adaptation layer over the existing policy model rather than the broader upstream command/runtime contract. | Real local mechanism gap |
| Model-invocable skill runtime | Upstream `SkillTool` can invoke a wider skill set and return inline conversation mutations/new messages rather than only a final string. | Local `skill` tool invokes model-invocable skills, returns structured `tool_result + new_messages + context_update`, injects inline skill prompts into the parent session, and blocks model-disabled skills explicitly. | Locally aligned with narrower breadth | The local model-side runtime now uses real inline session mutation rather than final-text recursion, but the surrounding orchestration and command breadth are still simpler than upstream. | Real local mechanism gap |
| Fork execution semantics | `context: fork` can execute in a real forked sub-agent runtime with separate orchestration and result handling. | Model-side fork skills already run in a real foreground child session and inject the child message delta back into the parent session; slash fork skills still stay on the simpler text-return path. | Locally aligned with narrower breadth | The direct “no fork runtime” gap is closed for the model-side path, but local fork result handling and slash/runtime breadth are still narrower than upstream. | Real local mechanism gap |
| Conditional / path-scoped / dynamic skill activation | Upstream supports `paths`-based activation and dynamic skill discovery/activation during the session. | No `paths` frontmatter or conditional activation is implemented locally. | Real local mechanism gap | This materially changes how upstream skills become available in context and is not yet reproduced. | Real local mechanism gap |
| Runtime guidance and skill discovery surfaces | Upstream has a dedicated SkillTool prompt, budgeted skill listing, stronger blocking guidance, and broader skill discovery/runtime messaging. | Local system prompt explains `/<skill-name>` and the `skill` tool, and `/skills` surfaces distinguish prompt-active vs user-invocable skills. | Functional substitute, shallower mechanism | Local guidance is coherent and no longer shallow, but it lacks upstream skill listing, budgeting, and broader discovery/runtime semantics. | Real local mechanism gap |
| Persistence, reload, and cache lifecycle | Upstream has broader command caches, dynamic skill maps, prompt caches, and change-detection/reactivation paths. | Local skills reload through the existing project-context refresh path and have no deeper dynamic cache or watcher lifecycle. | Functional substitute, shallower mechanism | Local reload is sufficient for a local-first workflow, but it is much simpler than upstream lifecycle depth. | Real local mechanism gap |
| Skill breadth outside project-local scope | Upstream includes user/global/managed/MCP breadth and wider packaging/distribution ecosystems. | Local scope stays focused on project-local plus plugin-contributed skills. | Broader upstream breadth | This is a scope choice, not evidence that the local runtime lacks a core skill mechanism. | Broader upstream breadth |

## Subsystem Deep Dives

### Skill source discovery and precedence

**Upstream mechanism**  
Upstream loads skills from multiple strata: bundled, user, project, managed, plugin, MCP, additional directories, and legacy command directories. It also has stronger duplicate identity handling and source-aware precedence behavior.

**Current local implementation**  
The Python runtime now has a real project-local skill loader:

- `.claude/skills/<name>/SKILL.md` is the primary project-local format
- `.pyclaude/skills/*.md` is still supported as legacy compatibility
- plugin-contributed skills are merged into the same effective project-context skill registry
- same-name conflicts generate diagnostics rather than silently drifting

The local skill runtime is no longer just prompt-active skill blocks. It has a real source model and precedence rule for project-local skills.

**Depth verdict**  
`Functional substitute, shallower mechanism`

**Remaining gap**  
The missing piece is not project-local skill ownership anymore. The missing piece is the broader upstream source tree and its deeper source/identity/deduping behavior.

### Skill file format and frontmatter depth

**Upstream mechanism**  
Upstream skills are prompt commands with richer metadata. Important fields include `allowed-tools`, `argument-hint`, `arguments`, `when_to_use`, `user-invocable`, `context`, `model`, `effort`, `hooks`, `paths`, `shell`, `disable-model-invocation`, `version`, and `agent`.

**Current local implementation**  
The Python runtime now supports meaningful runtime metadata:

- `description`
- `when-to-use`
- `user-invocable`
- `argument-hint`
- `arguments`
- `allowed-tools`
- `context`
- compatibility fields such as `auto-enable` and `tags`

This means local skills now have a real runtime data model rather than just file content plus a description.

**Depth verdict**  
`Locally aligned with narrower breadth`

**Remaining gap**  
The still-missing fields are now the deeper remaining ones:

- no `hooks`
- no `paths`
- no `shell`
- no `version` / `agent` support
- `model` / `effort` / `disable-model-invocation` exist locally, but only the model-side `skill` tool consumes them today

That is a real mechanism gap, not just missing packaging breadth.

### Slash-command registration model

**Upstream mechanism**  
Upstream treats skills as first-class prompt commands inside the shared command graph rather than as an isolated secondary skill subsystem.

**Current local implementation**  
The Python runtime now automatically registers user-invocable skills as real `/<skill-name>` commands in the command registry.

That closes an important old gap: local skills are no longer only prompt-active system text. They now have a genuine user-facing command entrypoint.

**Depth verdict**  
`Locally aligned with narrower breadth`

**Remaining gap**  
The remaining difference is that upstream command registration spans more sources and more command-runtime depth than the local project-local line currently covers.

### Prompt expansion semantics

**Upstream mechanism**  
Upstream prompt commands expose a richer `getPromptForCommand` path, substitute arguments through shared command logic, inject session variables, and can execute shell snippets in prompt bodies under source-aware restrictions.

**Current local implementation**  
The Python runtime expands skills inline with:

- `$name`
- `${name}`
- `${CLAUDE_SKILL_DIR}`

It then executes the result through the existing `CommandExecution` flow.

**Depth verdict**  
`Functional substitute, shallower mechanism`

**Remaining gap**  
The local runtime does not yet reproduce:

- upstream-style `getPromptForCommand`
- richer session-variable substitution
- prompt-body shell execution semantics
- source-aware prompt execution differences

### Allowed-tools and permission semantics

**Upstream mechanism**  
Upstream `allowed-tools` participates in the broader prompt-command and tool permission model, including shell execution context.

**Current local implementation**  
The Python runtime maps `allowed-tools` into:

- explicit local tool IDs
- command-policy tool restrictions
- bash prefix allowlists for `Bash(...)`

This is real runtime scoping, not just a display field.

**Depth verdict**  
`Locally aligned with narrower breadth`

**Remaining gap**  
The remaining gap is that the local path is an adaptation layer over existing command policy, not a full upstream prompt-command/tool permission contract.

### Model-invocable skill runtime

**Upstream mechanism**  
Upstream `SkillTool` can invoke model-eligible skills, return inline conversation mutations, and participate in broader skill/runtime orchestration semantics. It is not limited to returning a final plain-text result.

**Current local implementation**  
The Python runtime now has a real `skill` tool. Inline skills no longer recurse for a final answer. They return structured status plus:

- injected `new_messages`
- inline `context_update`
- transient parent-turn tool/runtime overrides for model-side execution

Fork skills already run through a real child session and inject the child message delta back into the parent transcript.

**Depth verdict**  
`Locally aligned with narrower breadth`

**Remaining gap**  
The still-open mechanism gap is narrower now:

- no upstream-style broader command/runtime breadth around `SkillTool`
- no deeper context-modifier surface beyond the local tool/runtime overlay
- no slash-skill parity for the same inline-mutation semantics

### Fork execution semantics

**Upstream mechanism**  
Upstream `context: fork` can run a skill in a real forked sub-agent context with its own orchestration and result handling.

**Current local implementation**  
Model-side fork skills already run in a real foreground child session. The local runtime captures the child message delta and injects it back into the parent session as ordinary persisted messages with additive metadata.

Slash `/<skill-name>` fork execution is still shallower: it keeps the simpler text-return command path rather than the same inline mutation model.

**Depth verdict**  
`Locally aligned with narrower breadth`

**Remaining gap**  
The remaining gap is no longer “fork is missing.” It is narrower:

- slash fork skills still use simpler text-return semantics
- local fork result/context handling is still below upstream command/runtime breadth

### Conditional, path-scoped, and dynamic skill activation

**Upstream mechanism**  
Upstream supports `paths`-based conditional activation and dynamic discovery/activation of skills during the session.

**Current local implementation**  
No conditional or path-scoped skill activation exists locally. Skills are loaded from the project context, then surfaced as prompt-active and/or user-invocable based on current local rules.

**Depth verdict**  
`Real local mechanism gap`

**Remaining gap**  
This is not merely broader source breadth. It changes when and why a skill becomes active at runtime, so it remains a real local behavior gap.

### Runtime guidance and skill discovery surfaces

**Upstream mechanism**  
Upstream has a dedicated SkillTool prompt, budgeted skill listing, stronger blocking guidance around when skills must be invoked, and broader discovery/runtime surfaces.

**Current local implementation**  
The Python runtime now has:

- system-prompt guidance for user-invocable skills
- clear instruction that `/<skill-name>` is the user-facing entrypoint
- a local `skill` tool
- `/skills` and project-context surfaces that distinguish prompt-active vs user-invocable skills

**Depth verdict**  
`Functional substitute, shallower mechanism`

**Remaining gap**  
The local guidance is coherent, but it is still missing:

- a dedicated upstream-style SkillTool listing prompt
- budgeted skill-list behavior
- broader runtime discovery and invocation policy depth

### Persistence, reload, and cache lifecycle

**Upstream mechanism**  
Upstream has broader command caches, prompt caches, dynamic skill maps, and change-detection/reactivation behavior.

**Current local implementation**  
The Python runtime reloads skills through project-context refresh and surfaces reload status, but it does not yet have deeper dynamic caches, watchers, or reactivation lifecycle machinery.

**Depth verdict**  
`Functional substitute, shallower mechanism`

**Remaining gap**  
This remains a real local mechanism gap if the target is deeper source-depth alignment, though it is not a blocker for the current local-first workflow.

### Skill breadth outside project-local scope

**Upstream mechanism**  
Upstream includes broader user/global/managed/MCP skill breadth and wider packaging/distribution ecosystems.

**Current local implementation**  
The Python runtime intentionally stays focused on project-local plus plugin-contributed skill workflows.

**Depth verdict**  
`Broader upstream breadth`

**Remaining gap**  
This should not be treated as evidence that the current local runtime still lacks a core skill system.

## What Is Closed Locally

For the current local-first scope, these lines are substantively closed:

- **Project-local directory-based skills**  
  `.claude/skills/<name>/SKILL.md` is now the primary project-local format. Remaining difference is mostly broader upstream source breadth.

- **Legacy skill compatibility**  
  `.pyclaude/skills/*.md` still works. Remaining difference is mostly broader upstream command/source breadth.

- **Richer basic frontmatter**  
  The local loader now carries meaningful runtime fields instead of treating skills as plain text blobs. Remaining gap is still a real mechanism miss for richer upstream behavior-changing fields.

- **First-class slash skill commands**  
  User-invocable skills already become real `/<skill-name>` commands. Remaining difference is mostly broader upstream command graph breadth.

- **Model-side structured inline skill mutation**  
  Inline `SkillTool` calls already inject real `new_messages + context_update` into the parent session. Remaining gap is now narrower orchestration depth, not basic existence.

- **Model-side fork skill mutation**  
  Fork `SkillTool` calls already run in a real child session and inject the child delta into the parent transcript. Remaining gap is narrower slash/runtime breadth, not absence of fork execution.

- **Command-policy integration for skill-scoped allowed tools**  
  Local `allowed-tools` now affects actual execution constraints. Remaining difference is a narrower local permission/orchestration contract than upstream.

- **Inspection surfaces that distinguish prompt-active vs invocable skills**  
  Local `/skills` and related project-context surfaces now show this split clearly. Remaining difference is broader upstream discovery/runtime surface breadth.

These lines should no longer be described as “skills are only prompt-active blocks” or “skills are still missing as a real runtime” in local-first planning.

## What Remains a Real Local Mechanism Gap

The highest-value skill-specific local mechanism gaps are:

- richer frontmatter/runtime parity where it still changes behavior:
  - `paths`
  - `hooks`
  - `shell`
- deeper `SkillTool` semantics closer to upstream inline message insertion / orchestration
- slash-skill parity for the newer inline-mutation runtime shape
- conditional and path-scoped activation through `paths`
- richer reload/cache/dynamic skill lifecycle if the skill line is reopened further

These are real runtime-depth gaps, not merely missing product breadth.

## What Is Broader Upstream Breadth

The following differences are real, but should not be treated as core local deficits:

- hosted or managed skill distribution breadth
- wider user/global/additional-dir source ecosystems
- remote skill search, ranking, and telemetry ecosystems
- wider MCP/product-shell packaging breadth
- broader UI/product discovery surfaces outside the current local-first runtime target

These belong to upstream product/runtime breadth more than local core skill-runtime absence.

## Implications for Next Skill Work

1. Do not describe the current Python skill line as merely prompt-active prompt blocks anymore.  
   It already has a real core runtime with project-local directory skills, real slash-skill commands, and model-side invocation.

2. Treat the next skill work as depth follow-up, not basic enablement.  
   With documentation correction plus `disable-model-invocation`, `model`, and `effort` now closed locally, the next meaningful line is deeper remaining prompt/runtime semantics such as `paths`, richer `SkillTool` breadth, and slash/runtime convergence.

3. Keep broader upstream skill breadth separate from core local runtime planning.  
   Wider distribution, discovery, and hosted ecosystems are real differences, but they should not obscure the fact that a real local skill runtime now exists.
