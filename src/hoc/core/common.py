"""Shared primitives: base model, ids, time, actors."""

from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

SLUG_PATTERN = r"^[a-z0-9][a-z0-9-]*$"


class StrictModel(BaseModel):
    """Base for all domain types: unknown fields are errors, not silently dropped."""

    model_config = ConfigDict(extra="forbid")


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    """Opaque unique id such as ``cs_3f9a1c2b7d10``. Used for records, never for concepts."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def slugify(text: str) -> str:
    """Lower-case ASCII slug usable as a stable concept id."""
    norm = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", norm.lower()).strip("-")
    if not slug:
        raise ValueError(f"cannot derive a slug from {text!r}")
    return slug


class Actor(StrictModel):
    """Who produced a record: an agent (name+version), a human, or the system."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["agent", "human", "system"]
    name: str
    version: str | None = None

    @property
    def is_human(self) -> bool:
        return self.kind == "human"


SYSTEM_ACTOR = Actor(kind="system", name="hoc")
