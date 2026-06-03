# Claude Code 核心运行时差异矩阵

这份文档只对比 **Claude Code 上游核心运行时** 与当前 Python 版的本地实现。

它刻意 **不** 扩到 plugins / skills / agents / status / workspace 这些上层 workflow surface。目标是回答：

- 上游 `query/context/state/tools` 主链怎么分层
- Python 版是否有一条真实对应的运行时主链
- 哪些机制已经实质对齐
- 哪些机制只是本地等价实现
- 哪些差距如果继续复现，收益最高

主参考源码：

- 上游：
  - `package/src-extracted/src/query.ts`
  - `package/src-extracted/src/query/*`
  - `package/src-extracted/src/context.ts`
  - `package/src-extracted/src/state/*`
  - `package/src-extracted/src/tools/*`
  - `package/src-extracted/src/services/tools/*`
  - `package/src-extracted/src/utils/queryContext.ts`
- Python：
  - `python_claudecode/claudecode_py/runtime/*`
  - `python_claudecode/claudecode_py/session.py`
  - `python_claudecode/claudecode_py/state.py`
  - `python_claudecode/claudecode_py/tools/*`
  - `python_claudecode/claudecode_py/service/stdio.py`
  - `python_claudecode/claudecode_py/storage/transcript.py`
  - `python_claudecode/claudecode_py/permissions.py`

## 核心运行时映射表

| 机制 | 上游源码 | Python 对应 | 当前状态 | 结论 |
|---|---|---|---|---|
| query loop / turn orchestration | `src/query.ts`, `src/query/config.ts`, `src/query/stopHooks.ts`, `src/query/tokenBudget.ts` | `claudecode_py/runtime/query_loop.py`, `session.py` | 本地等价 | Python 版有真实的 turn loop、tool round、advisor review、message append、compaction gate，但没有上游那套更重的 reactive compact / context collapse / continuation budget machinery。 |
| context assembly / prompt budgeting | `src/context.ts`, `src/utils/queryContext.ts`, `src/utils/tokens.ts` | `claudecode_py/runtime/context.py`, `prompts.py`, `context_usage.py`, `session.py` | 本地等价 | Python 版有明确的 system prompt 组合、project context、skill/plugin/memory 注入和 context-usage 估算，但 token budgeting 更偏本地估算与 surface inspection，不是上游那套 cache-key / thinking / API-prefix 编排。 |
| session state / transcript persistence | `src/state/*`, `src/bootstrap/state.ts`, message utils in `src/utils/messages.ts` | `claudecode_py/state.py`, `storage/transcript.py`, `session.py` | 已对齐 | Python 版有强而完整的 `SessionState`、history boundaries、planning/task/workspace persistence、saved-session restore；虽然状态模型不与上游 React/store 同构，但在本地运行时语义上已实质对齐。 |
| tool execution / approval / runtime events | `src/services/tools/toolOrchestration.ts`, `toolExecution.ts`, `toolHooks.ts`, `src/tools/*` | `claudecode_py/runtime/orchestrator.py`, `runtime/events.py`, `permissions.py`, `tools/*` | 本地等价 | Python 版有明确的 tool batching、只读并行、approval gate、runtime events 和 tool-result 回流，但没有上游 hook/telemetry/progress message 的完整产品级深度。 |
| remote-facing runtime boundary | `src/remote/RemoteSessionManager.ts`, `src/cli/remoteIO.ts`, `src/bridge/remoteBridgeCore.ts` | `service/stdio.py`, `service/bridge.py`, `remote_session.py` | 本地等价 | Python 版有可用的 stdio/TCP attach boundary 和结构化 session surfaces，但不是上游 WebSocket/SDK/hybrid transport 那套更宽的远端协议栈。 |

## 机制差异矩阵

| 机制 | 上游源码 | Python 对应 | 当前状态 | 结论 |
|---|---|---|---|---|
| turn 输入输出生命周期 | `src/query.ts` 主循环、`buildQueryConfig`, `handleStopHooks` | `runtime/query_loop.py:run_query_loop`, `Session.build_turn_prompt()`, `session.ask()` | 已对齐 | Python 版确实有一条真实的 turn lifecycle：用户消息入栈、模型请求、assistant/tool 往返、final text 收口，不是表面命令拼出来的假循环。 |
| message/context compaction 接点 | `src/query.ts` 中 `autoCompact`, `reactiveCompact`, `buildPostCompactMessages`, `snipCompact` | `session.compact_history_into_context_summary()`, `query_loop._enforce_message_budget`, `history_compaction.py`, history boundary model | 本地等价 | Python 版 compaction 是真的接在 runtime turn loop 和 transcript state 上，但机制明显更轻，没有上游 reactive compact / microcompact / context collapse。 |
| context window / token budgeting 接点 | `src/query/tokenBudget.ts`, `src/utils/tokens.ts`, `src/utils/queryContext.ts` | `session.compaction_policy_payload()`, `context_usage.py`, `models.py` usage fields, `query_loop` usage aggregation | 本地等价 | Python 版已经有 provider-usage 聚合、context usage 估算和 auto-compact policy，但更像本地控制面；上游则把 token budgeting 深度嵌进 continuation、cache prefix 和 recovery loop。 |
| tool 调度、streaming、result 回流 | `services/tools/toolOrchestration.ts`, `toolExecution.ts`, `StreamingToolExecutor.ts` | `runtime/orchestrator.py`, `providers/*` streaming adapter, `session.execute_tool_calls()` | 本地等价 | Python 版保留了核心语义：tool call 分批、只读并行、tool_result 回写到消息链；但上游 tool execution 还带 hook、telemetry、attachment/progress message、more granular context mutation。 |
| approval / permission bridge | `toolExecution.ts`, `remotePermissionBridge.ts`, SDK control request flow | `permissions.py`, `service/stdio.py`, `remote_session.py`, TUI approval flow | 已对齐 | 对本地 coding-agent 目标来说，Python 版 approval 主链已经足够接近：本地 require_approval、bridge/stdio remote resolve、TUI/REPL 可视化都是真运行时机制。 |
| runtime event shape | stream events in `src/query.ts`, progress/tool messages, control events | `runtime/events.py`, `SessionRecord.append_event()`, `TuiState.record_runtime_event()` | 本地等价 | Python 版 event shape 更紧凑，但已经承担了相同角色：tool started/finished/failed、assistant text、usage、advisor/plan/memory 事件。差距主要在上游 UI/progress richness，而不是事件总线缺失。 |
| transcript/state 恢复语义 | `src/state/*`, session restore, remote session manager, cached prompt parts | `storage/transcript.py`, `session_factory.py`, `service/stdio.py`, `remote_session.py` | 已对齐 | Python 版的 saved-session resume、live attach、workspace fallback、history boundary restore 都是实打实的底层语义，不是单纯 view 层补丁。 |
| provider fallback / retry / recovery | `src/query.ts` with retry/fallback branches, `withRetry`, prompt-too-long handling | `runtime/query_loop.py:_create_message_with_retries`, `providers/errors.py` | 本地等价 | Python 版有 provider retry、timeout/network/rate-limit handling，但没有上游同等复杂的 prompt-too-long reactive path 和 model/cache recovery 链。 |
| remote-facing runtime protocol | `RemoteSessionManager`, `SessionsWebSocket`, `cli/remoteIO.ts` | `service/stdio.py`, `service/bridge.py`, `remote_session.py` | 部分缺失 | Python 版远端边界足够支撑本地 attach/headless workflow，但它不是上游那种更重的 session ingress / websocket / SDK protocol 体系；对当前 local-first scope，不建议强追同构。 |

## 最值得继续补的底层机制

### 1. 已经足够接近上游，不值得继续深挖的

- **session state / transcript persistence**
  - Python 版这块已经是强项。
  - memory/rewind/status/workspace 近期成果有很多就是建立在这条底座之上，不是纯 view 层装饰。
- **approval / permission bridge**
  - 对本地 coding-agent 目标已经够完整。
  - 再往上游追，多半会掉进更宽的 remote protocol/product shell。
- **基本 query loop / tool roundtrip**
  - turn loop、tool result 回流、assistant/tool 交替这些主机制都已经有真实实现。

### 2. 属于“本地等价但并非同构”的

- **context assembly / prompt budgeting**
  - Python 版有真实的 prompt composition 和 usage inspection。
  - 但它没有上游那种更深的 cache-key、thinking、query-prefix、task-budget 一体化机制。
- **tool orchestration**
  - Python 版保留了最关键的只读并行、写工具串行、approval gate。
  - 上游的 hook/progress/telemetry/attachment richness 没有完全复现。
- **compaction / recovery loop**
  - Python 版已经把 compaction 真接到了 runtime 上。
  - 但它仍然是更轻的本地路径，不是上游 reactive compact / microcompact 那条深链。

### 3. 如果继续做底层复现，收益最高的

在当前 local-first scope 里，上一轮最值得补的三块已经完成：

1. **shared runtime token budgeting**
   - 预算判断现在是 query loop 真实消费的 runtime state，而不是分散在 surface 上的展示逻辑。

2. **prompt-too-long / compact recovery path**
   - 主 turn loop 现在会在 provider context-limit 失败后走受控的 compact-and-retry 恢复链路，并把 recovery compact 明确写进 history lifecycle。

3. **tool execution progress / runtime event richness**
   - richer runtime events、runtime-progress summaries、background/status/TUI/headless consumers 现在都接到了同一条本地 runtime 主链。

这意味着：**core runtime 的本地预算/恢复/执行进度主线已经阶段性收口**。

如果接下来还要继续从 **core runtime** 往上游追，优先级更像是：

1. **更深的 prompt assembly / context-prefix mechanics**
   - 例如更接近上游的 prefix/cache-key/thinking 组合方式。
   - 这会更偏实现同构，而不只是本地 runtime 可用性。

2. **更宽的 remote transport / hosted runtime boundary**
   - 包括 websocket/SSE/hybrid transport、session ingress、SDK-facing protocol breadth。
   - 对当前 local-first scope，仍然不建议优先追。

### 4. 对近期成果的判断

- **memory / rewind**
  - 不是纯上层补出来的。
  - 它们有一部分是真正落在 transcript state、history boundaries、query-loop compaction gate 上的底层语义扩展。
  - 但距离上游最深的 reactive compact internals 仍然有刻意保留的差距。

- **status**
  - 主要是上层聚合面。
  - 它依赖已经很强的 runtime/state/payload 基础，但自身不是核心 runtime 机制。

- **agents**
  - 当前 local scope 里，background follow-up、handoff、progress metadata 有一部分已经触到 runtime events 和 session surfaces。
  - 但整体仍然更多是 workflow/orchestration 层，不是上游 teammate/swarm runtime 的同构实现。

## 结论

当前 Python 版**确实有一条真实的 `query/context/state/tools` 主链**，不是靠 REPL surface 拼出来的伪实现。

最接近上游、也是当前最强的底层机制是：

- session state / transcript persistence
- approval / permission bridge
- basic turn loop + tool roundtrip

当前最明显的剩余底层差距则更多集中在：

- 更深的 prompt assembly / context-prefix mechanics
- 更宽的 remote transport / hosted runtime boundary
- 更重的 hosted/product runtime breadth，而不是本地预算/恢复/执行进度主线

如果下一步还要只从 **core runtime** 继续推进复现，我会把优先级排成：

1. `更深的 prompt assembly / context-prefix mechanics`
2. `remote transport breadth`（当前仍不建议优先追）
3. `更重的 hosted/product runtime breadth`（当前 local-first scope 不建议追）
