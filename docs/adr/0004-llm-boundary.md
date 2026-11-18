# ADR 0004 — One LLM boundary

**Decision.** All model calls go through `LLMClient.structured(step, variables, OutputModel)`.
Structured output uses a forced tool call whose schema is the Pydantic model's JSON schema.
Calls are cached by hash of (model, rendered prompt, schema, max_tokens), validated, retried once
with the validation error fed back, and logged in `llm_calls` (cache hits too) so any produced
record can cite `call_id`. Prompts are versioned files; models are chosen by tier per step in
config. Providers are replaceable behind the one-method `LLMBackend` protocol.

**Consequences.** Re-running unchanged work is free and reproducible; tests use `ScriptedBackend`
with no network; changing a prompt file (new version) or model tier invalidates only affected calls.
