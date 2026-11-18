# ADR 0006 — Any model: providers, tiers and per-role overrides

**Decision.** `LLMBackend` stays the only provider seam. Three implementations:
`AnthropicBackend`, `OpenAICompatBackend` (OpenAI, Azure, OpenRouter, Gemini's compat endpoint,
Ollama, vLLM, LM Studio…), and `FixtureBackend` (replay). `RoutingBackend` resolves a model
reference `provider:model-id` (a bare id uses `default_provider`; a prefix only counts if it is a
configured provider, so ids like `qwen3:32b` survive). Providers are config
(`llm.providers`: kind, `api_key_env`, `base_url`, `structured_mode`, `extra`); keys are read from
the environment, never stored. Models are chosen per tier, per step (`step_tiers`, `step_models`)
and per role per project from the UI (stored in the project DB, never overwriting the config).

**Structured output** differs per API, so `structured_mode` is per provider: `json_schema`
(native), `tool` (forced/strict function), `json_prompt` (schema in the prompt, JSON parsed from
text; works with any model). Schemas sent are made portable (`llm.schema.portable_schema`: all
properties required, `additionalProperties: false`, no constraints); Pydantic still validates
the real constraints and the client retries once with the error fed back.
Anthropic calls are streamed, so long outputs never hit HTTP timeouts.

**New API family:** add a class, register it in `BACKEND_KINDS`.
