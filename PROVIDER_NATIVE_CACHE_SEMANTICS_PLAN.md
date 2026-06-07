# Provider-Native Cache Semantics Plan

## Summary

Move the prompt-prefix line from runtime-local diagnostics to a provider-consumable cache semantics layer.

The implementation is provider-abstracted first:

- the provider abstraction accepts additive cache-hint inputs
- the runtime derives provider-view cache plans from existing prompt-prefix machinery
- providers choose whether they consume those hints or ignore them
- Anthropic is the first real consuming path
- current string-based and tool-array-based provider behavior remains the fallback

This remains local-first. It does not add hosted cache services, transcript rewrites, or new product-scope transport.

## Key Changes

### 1. Provider-abstracted cache semantics model

- Add `ProviderCacheHint` and `ProviderPromptCachePlan` as runtime/provider-facing types.
- Derive cache plans from:
  - system prompt blocks and cache scopes
  - tool schema bundle/signature
  - provider-view replacement/artifact/microcompact assembly
  - stable-prefix vs dynamic-tail segmentation
- Keep cache-plan vocabulary fixed:
  - `disabled`
  - `diagnostic_only`
  - `provider_hinted`

### 2. Additive provider contract extension

- Extend `ProviderCapabilities` with cache-related fields:
  - `supports_prompt_cache_hints`
  - `supports_system_prompt_cache_blocks`
  - `supports_tool_schema_cache_hints`
- Extend provider entrypoints additively:
  - `create_message(..., cache_plan=None)`
  - `stream_message(..., cache_plan=None)`
- Preserve backward compatibility:
  - existing callers still work with `cache_plan=None`
  - unsupported providers safely ignore cache plans

### 3. Runtime assembly and observability

- Build provider cache plans from `PromptPrefixAssemblyResult`.
- Feed cache-plan state into prompt-prefix payloads and narratives:
  - `prompt_prefix_cache_mode`
  - `prompt_prefix_cache_supported`
  - `prompt_prefix_cache_provider`
  - `prompt_prefix_cache_summary`
  - `prompt_prefix_cache_fallback_reason`
- Surface the new fields through:
  - `/context`
  - `/status workflow`
  - stdio / remote status payloads
  - TUI prompt-prefix status block
- Emit additive runtime events when they add signal:
  - `prompt_cache_hints_applied`
  - `prompt_cache_hints_fallback`

### 4. First consuming provider path

- Implement Anthropic cache semantics through the new abstraction:
  - cache-hinted system prompt blocks
  - cache-hinted tool schema overlays
  - plain fallback when hinted request shape is rejected
- Keep fallback conservative:
  - downgrade to plain provider behavior once
  - record fallback reason in prompt-prefix payloads
  - do not convert hint rejection into a fatal runtime failure
- Keep OpenAI-compatible behavior explicit and safe:
  - accept `cache_plan`
  - ignore it
  - expose cache capabilities as false

## Test Plan

- Provider abstraction
  - providers accept `cache_plan=None` without behavior change
  - Anthropic consumes hinted blocks/tool overlays
  - OpenAI-compatible provider ignores cache plans safely
- Runtime integration
  - query loop passes cache plans to cache-capable providers
  - prompt-prefix payloads report aligned cache mode/provider/summary
  - cache fallback updates prompt-prefix payload state
- Surfaces
  - `/context` and `/status workflow` show cache semantics lines
  - stdio / remote / TUI receive the additive prompt-prefix cache fields
- Regression
  - no transcript mutation
  - no provider wire-shape break for existing callers
  - no regression to runtime budget, compact-retry recovery, replacement/artifact/microcompact, or existing prompt-prefix diagnostics

## Assumptions

- Scope is provider-abstracted first, with Anthropic as the first real consuming implementation.
- OpenAI-compatible providers remain explicit no-op consumers in this phase.
- This phase reports provider-consumable cache hints, not cache effectiveness or hosted telemetry.
- Existing prompt blocks, schema caching, replacement/artifact indirection, and prompt-prefix signatures remain the source of truth.
