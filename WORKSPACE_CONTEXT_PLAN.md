# Workspace / Context / Files Plan

This document defines the next-stage implementation plan for the remaining
`Workspace / context / files` parity gap after the memory, history/rewind,
agents, and status lines reached a stable local-equivalent baseline.

It is intentionally scoped to the local coding-agent goal of this repo. The
target is not full upstream product breadth. The target is stronger local parity
for:

- working-set inspection and navigation
- focused-file continuity across surfaces
- explicit-vs-automatic context reasoning
- workspace state and recovery visibility
- REPL, TUI, stdio, and remote coherence around file/workspace surfaces

## Current Baseline

The current Python implementation already has a strong local workspace/context
foundation:

- runtime-aligned `/context` usage inspection
- explicit context curation through `/add-dir`
- shared focused-file and working-set model across:
  - `Changes`
  - `Task Detail`
  - `Active Plan`
  - `Status`
- `/files` and `/diff` compact entry surfaces
- `/changes` drill-down and working-set rendering
- `/workspaces current|show|list|repair|cleanup`
- focused-file navigation in TUI
- explicit/project context inspection through `/project-context`

Important current references:

- [PARITY_MATRIX.md](./PARITY_MATRIX.md)
- [README.md](./README.md)
- [claudecode_py/session.py](./claudecode_py/session.py)
- [claudecode_py/session_components/file_context_selection.py](./claudecode_py/session_components/file_context_selection.py)
- [claudecode_py/session_components/file_context_views.py](./claudecode_py/session_components/file_context_views.py)
- [claudecode_py/session_components/change_history_views.py](./claudecode_py/session_components/change_history_views.py)
- [claudecode_py/session_components/workspace.py](./claudecode_py/session_components/workspace.py)
- [claudecode_py/tui/state.py](./claudecode_py/tui/state.py)

## Current Read

The main gap is no longer missing basic capability. The main gap is that the
local workspace/file-context system is still spread across several surfaces with
slightly different browse, compare, and recovery semantics.

The remaining parity gap is mostly about:

1. turning working-set/focused-file metadata into a more explicit navigation
   model
2. making `/files`, `/diff`, `/changes`, `/workspaces`, and `/status` feel
   like one coherent workspace workflow
3. improving explicit-vs-automatic context visibility
4. making workspace anomalies and recovery routes easier to inspect from the
   same shared file/workspace surfaces

This means the next phase should focus on **workspace workflow depth**, not on
new product shells or unrelated command families.

## Non-Goals

This plan does not target:

- hosted IDE integrations
- external product UI breadth
- cloud/project sharing flows
- replacing the existing local working-set model with a different abstraction
- broad remote transport expansion

## Phase 1: Working-Set Vocabulary and Surface Alignment

Priority:
High

Status:
Completed for current local scope

Goal:
Unify the wording and top-level structure across `/files`, `/diff`, `/changes`,
`/status`, and `/workspaces` so they describe the same working-set and
focused-file story.

Key work:

- standardize common labels across surfaces:
  - `working set`
  - `focused file`
  - `focused file source`
  - `in scope because`
  - `explicit context`
  - `automatic context`
  - `workspace state`
  - `workspace anomaly`
- reduce small wording drift between REPL text and TUI labels
- ensure `/files focused`, `/diff focused`, `/changes working-set`, and
  `/status workflow` use the same field names for:
  - path
  - source
  - diff hunks
  - related change
  - context-only

Exit criteria:

- the main workspace/file surfaces read like one coherent vocabulary instead of
  related but slightly different reports

## Phase 2: File/Change/Diff Action Cohesion

Priority:
High

Status:
Completed for current local scope

Goal:
Make navigation between `/files`, `/diff`, `/changes`, `/task`, `/plan`, and
`/status` more stable and less lossy.

Key work:

- standardize action families emitted from file-oriented surfaces:
  - inspect focused file
  - inspect focused diff
  - inspect change
  - inspect task
  - inspect active plan
  - inspect explicit context
  - stay on current surface
- ensure focused-file preservation survives:
  - `/files -> /diff -> /changes`
  - `/status -> /files -> /task`
  - `/changes -> /plan -> /files`
- remove remaining surface-local action assembly where a shared
  focused-file/working-set action model can be reused

Exit criteria:

- file/workspace surfaces provide stable navigation routes without dropping the
  current focused-file context

## Phase 3: Explicit vs Automatic Context Depth

Priority:
High

Status:
Completed for current local scope

Goal:
Make the system explain more clearly why a file is in scope and whether that
scope comes from explicit user curation or automatic runtime inference.

Key work:

- deepen `/files explicit` and `/files auto`
- add compact compare-oriented summaries showing:
  - explicit-only files
  - auto-only files
  - overlapping files
  - unresolved explicit entries
- ensure `/status workflow`, `/files focused`, and `/changes working-set` expose
  the same scope-reason signals
- make explicit context removal/cleanup flows easier to inspect before acting

Exit criteria:

- users can quickly answer “why is this file here?” and “did I add this
  explicitly or did the session infer it?”

## Phase 4: Workspace Recovery and Anomaly Drill-Down

Priority:
Medium

Status:
Completed for current local scope

Goal:
Strengthen the bridge between the file/workingset model and isolated workspace
recovery flows.

Key work:

- make `/workspaces current` and `/workspaces show` feel closer to the same
  dashboard model used by `/status`
- surface workspace anomaly summaries in the same style as file/workspace
  surfaces:
  - unavailable
  - orphaned
  - cleanup pending/failed
  - fallback active
- ensure recommended recovery actions appear consistently across:
  - `/workspaces`
  - `/status`
  - TUI status/workspace blocks
- improve drill-down from file/workspace surfaces into repair/cleanup commands
  without introducing a new command family

Exit criteria:

- workspace anomaly recovery feels like part of the same local workflow instead
  of a separate maintenance subsystem

## Phase 5: Structured Workspace/File Payloads for Stdio / Remote

Priority:
Medium

Status:
Completed for current local scope

Goal:
Promote the strongest workspace/file-context surfaces from text-only reports to
stable structured payloads for headless and remote callers.

Key work:

- add shared structured payloads for:
  - working-set summary
  - focused-file summary
  - explicit-vs-automatic context summary
  - workspace anomaly summary
  - file/workspace action groups
- expose them through stdio/session.describe and remote proxy sync
- avoid forcing external callers to scrape `/files` or `/workspaces` text for
  core local workflow state

Exit criteria:

- headless and remote callers can build coherent file/workspace UI without text
  scraping

## Phase 6: TUI Workspace Workflow Depth

Priority:
Medium

Status:
Completed for current local scope

Goal:
Turn the existing file/workspace navigation in TUI into a fuller workspace
workflow surface rather than a collection of disconnected blocks.

Key work:

- deepen TUI blocks for:
  - working set
  - focused file
  - explicit context
  - workspace anomaly/recovery
- improve selected-file and selected-workspace continuity inside the TUI
- keep current bindings where possible, but make the panel organization closer
  to the REPL/shared workspace dashboard model

Exit criteria:

- TUI provides a stronger workspace home surface on top of the already-implemented
  focused-file navigation

## Phase 7: Final Workspace Workflow Polish

Priority:
Medium

Status:
Completed for current local scope

Goal:
Close the remaining local-scope gap by tightening compare views, action wording,
and recovery/orientation behavior across all workspace-related surfaces.

Key work:

- reduce duplicated file/workspace summary assembly
- align saved-session workspace wording with live workspace wording where useful
- ensure `/status` remains the top recovery/orientation surface while `/files`,
  `/diff`, `/changes`, and `/workspaces` remain the deep drill-down surfaces
- document the final local workspace/file-context model in top-level docs if
  needed

Exit criteria:

- workspace/context/files surfaces feel like one coherent local workflow stack
  rather than several adjacent inspection tools

## Recommended Delivery Order

The recommended order is:

1. Phase 1: Working-Set Vocabulary and Surface Alignment
2. Phase 2: File/Change/Diff Action Cohesion
3. Phase 3: Explicit vs Automatic Context Depth
4. Phase 4: Workspace Recovery and Anomaly Drill-Down
5. Phase 5: Structured Workspace/File Payloads for Stdio / Remote
6. Phase 6: TUI Workspace Workflow Depth
7. Phase 7: Final Workspace Workflow Polish

## Working Principle

When there is a tradeoff between adding more local workflow depth and adding
broader upstream product surface breadth, prefer the deeper local workflow.
