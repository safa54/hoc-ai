"""Typed settings loaded from YAML. Thresholds and weights live here, not in code."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field

from hoc.core.common import StrictModel

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "default.yaml"


class Pricing(StrictModel):
    input_per_mtok: float = 0.0
    output_per_mtok: float = 0.0


class ProviderSettings(StrictModel):
    """One LLM API endpoint. ``kind`` selects the backend class (see hoc.llm.backend)."""

    kind: Literal["anthropic", "openai_compat", "fixture"]
    api_key_env: str | None = None  # name of the env var holding the key; None = no key
    base_url: str | None = None
    structured_mode: Literal["json_schema", "tool", "json_prompt"] = "json_schema"
    max_tokens_param: str = "max_completion_tokens"  # some compatible servers want max_tokens
    extra: dict[str, Any] = Field(default_factory=dict)  # merged into every request
    path: str | None = None  # fixture file (kind: fixture)


def _default_providers() -> dict[str, ProviderSettings]:
    return {"anthropic": ProviderSettings(kind="anthropic", api_key_env="ANTHROPIC_API_KEY")}


class LLMSettings(StrictModel):
    providers: dict[str, ProviderSettings] = Field(default_factory=_default_providers)
    default_provider: str = "anthropic"
    models: dict[str, str]  # tier -> "provider:model-id" (bare id = default provider)
    default_tier: str = "standard"
    step_tiers: dict[str, str] = Field(default_factory=dict)
    step_models: dict[str, str] = Field(default_factory=dict)  # step -> model, beats tiers
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    max_tokens: int = 4096
    step_max_tokens: dict[str, int] = Field(default_factory=dict)
    max_attempts: int = 2
    cache_enabled: bool = True
    pricing: dict[str, Pricing] = Field(default_factory=dict)

    def max_tokens_for(self, step: str) -> int:
        return self.step_max_tokens.get(step, self.max_tokens)

    def split_model(self, ref: str) -> tuple[str, str]:
        """``provider:model`` -> (provider, model). Model ids may contain colons themselves,
        so a prefix only counts as a provider if it is a configured one."""
        prefix, sep, rest = ref.partition(":")
        if sep and prefix in self.providers:
            return prefix, rest
        return self.default_provider, ref

    def model_for(self, step: str) -> str:
        if step in self.step_models:
            return self.step_models[step]
        tier = self.step_tiers.get(step, self.default_tier)
        try:
            return self.models[tier]
        except KeyError as e:
            raise ValueError(f"unknown model tier {tier!r} for step {step!r}") from e


class TriageSettings(StrictModel):
    auto_confidence: float = 0.8  # "ask" mode: below this a proposal goes to the expert
    review_structural: bool = True  # "ask" mode: merges/splits/removals always go to review


class AgentSettings(StrictModel):
    """Which implementation fills each role, and the limits of a run (ARCHITECTURE §13)."""

    team: dict[str, str] = Field(default_factory=dict)  # role -> registered agent name
    coordinator: str = "phased"
    triage: str = "threshold"
    max_rounds: int = 3
    max_refine_steps: int = 4  # per review round
    refine_batch: int = 8  # feedback items handed to the refiner per step
    max_feedback_attempts: int = 2
    min_tests: int = 5
    source_budget_chars: int = 24000  # source text included per prompt
    chunk_chars: int = 1800
    # prompt size: what each agent is shown of the graph
    outline_summary_chars: int = 90  # per concept in the outline every agent gets
    detail_definition_chars: int = 400  # per concept shown in full (critic, refiner)
    review_batch: int = 40  # critic: concepts read in full per review (rest: next round)
    known_feedback_limit: int = 30  # "already known" notes shown to the critic
    known_feedback_chars: int = 160  # ... each clipped to this
    retrace_covered: bool = False  # re-trace tests that were fully covered last round
    # simplicity: the smallest graph that covers the brief is the goal
    max_areas: int = 6  # planner: upper bound on areas
    area_concepts: int = 6  # builder: upper bound on concepts per area
    max_findings: int = 8  # critic: findings kept per review, most severe first
    refine_min_severity: Literal["low", "medium", "high"] = "medium"  # agent notes below: parked
    prune_redundant_prerequisites: bool = True  # drop edges implied by a chain, without an LLM


class BeliefSettings(StrictModel):
    """Student-model thresholds (ARCHITECTURE §8.2). Tunable without code changes."""

    unknown_mass: float = 1.0  # evidence mass below this = "unknown"
    likely_not: float = 0.35  # posterior mean thresholds
    uncertain: float = 0.65
    likely: float = 0.85
    mastered_mass: float = 2.5  # evidence needed before "mastered"
    half_life_days: float = 14.0
    propagate_transfer: float = 0.3  # transfer.strong -> apply, explain
    propagate_explain: float = 0.2  # explain.strong -> recall
    propagate_recall_negative: float = 0.3  # recall.contradicting -> apply, explain
    hint_discount: bool = True


class ContextSettings(StrictModel):
    """Character budgets per prompt section; the tutor never sees more than this."""

    turns: int = 6
    focus: int = 1800
    prerequisites: int = 900
    leads_to: int = 500
    bridges: int = 500
    outline: int = 500
    learner: int = 600
    moves: int = 400
    pending_check: int = 300
    thread: int = 1800
    exercises: int = 200
    task: int = 1200
    map: int = 2500


class BudgetSettings(StrictModel):
    max_llm_calls_per_session: int = 120
    max_llm_calls_per_day: int = 400


def _reliability() -> dict[str, float]:
    return {"exact": 1.0, "rule": 0.9, "llm": 0.7, "self_report": 0.2}


def _policy_weights() -> dict[str, float]:
    return {
        "weakness": 1.0,
        "necessity": 0.6,
        "recent_failure": 0.8,
        "readiness": 1.0,
        "proximity": 0.4,
        "depth_gap": 0.7,
        "info_gain": 0.5,
        "repetition": 0.5,
        "path": 1.0,
        "alternative": 0.4,
    }


class LearnSettings(StrictModel):
    """Learning side: which implementations to use and their parameters (LEARNING_SIDE_PLAN L10)."""

    student_model: str = "beta-facet"
    policy: str = "heuristic"
    tutor: str = "llm"
    visible_statuses: list[str] = Field(default_factory=lambda: ["accepted", "proposed"])
    split_evidence_weight: float = 0.5
    ready_threshold: float = 0.6  # inferred prerequisite mean that counts as "in place"
    max_moves: int = 4
    judge_reliability: dict[str, float] = Field(default_factory=_reliability)
    beliefs: BeliefSettings = Field(default_factory=BeliefSettings)
    policy_weights: dict[str, float] = Field(default_factory=_policy_weights)
    context: ContextSettings = Field(default_factory=ContextSettings)
    budget: BudgetSettings = Field(default_factory=BudgetSettings)
    collapse_threshold: int = 12  # UI: open as super-nodes above this many nodes


def _view_providers() -> list[str]:
    return ["overview", "areas", "questions", "exercises", "saved"]


class ViewSettings(StrictModel):
    """Graph views / lenses (ADR 0012): which providers run and how views are shaped."""

    providers: list[str] = Field(default_factory=_view_providers)  # hoc.views registry, in order
    max_bridge_hops: int = 3  # longest path added to connect two parts of a view
    overview_size: int = 14  # concepts in the topic backbone (before connecting)
    neighbourhood_depth: int = 1
    min_members: int = 2  # smaller computed views are not listed
    max_exercise_views: int = 40


class Settings(StrictModel):
    db_path: str = "data/hoc.db"
    projects_dir: str = "data/projects"
    prompts_dir: str = "prompts"
    llm: LLMSettings
    agents: AgentSettings = Field(default_factory=AgentSettings)
    triage: TriageSettings = Field(default_factory=TriageSettings)
    learn: LearnSettings = Field(default_factory=LearnSettings)
    views: ViewSettings = Field(default_factory=ViewSettings)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_settings(override_path: str | Path | None = None) -> Settings:
    data: dict[str, Any] = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text()) or {}
    if override_path is not None:
        data = _deep_merge(data, yaml.safe_load(Path(override_path).read_text()) or {})
    return Settings.model_validate(data)


def with_overrides(settings: Settings, overrides: dict[str, Any]) -> Settings:
    """Settings deep-merged with an override dict (e.g. per-project model choices)."""
    return Settings.model_validate(_deep_merge(settings.model_dump(), overrides))
