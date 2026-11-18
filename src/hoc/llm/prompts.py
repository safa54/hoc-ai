"""Versioned prompt templates stored as files: ``prompts/<step>/<version>.md``.

File format::

    # SYSTEM
    ...text with {{variables}}...

    # USER
    ...text with {{variables}}...
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from hoc.core.errors import PromptError

_VAR = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
_SECTION = re.compile(r"^# (SYSTEM|USER)\s*$", re.MULTILINE)
_VERSION = re.compile(r"^v(\d+)$")


@dataclass(frozen=True)
class RenderedPrompt:
    step: str
    version: str
    system: str
    user: str


@dataclass(frozen=True)
class PromptTemplate:
    step: str
    version: str
    system: str
    user: str

    @property
    def variables(self) -> set[str]:
        return set(_VAR.findall(self.system)) | set(_VAR.findall(self.user))

    def render(self, variables: dict[str, object]) -> RenderedPrompt:
        missing = self.variables - set(variables)
        if missing:
            raise PromptError(f"{self.step}/{self.version}: missing variables {sorted(missing)}")
        extra = set(variables) - self.variables
        if extra:
            raise PromptError(f"{self.step}/{self.version}: unused variables {sorted(extra)}")

        def sub(text: str) -> str:
            return _VAR.sub(lambda m: str(variables[m.group(1)]), text)

        return RenderedPrompt(self.step, self.version, sub(self.system), sub(self.user))


def parse_template(step: str, version: str, text: str) -> PromptTemplate:
    parts = _SECTION.split(text)
    # parts: [preamble, 'SYSTEM', body, 'USER', body] in file order
    sections = dict(zip(parts[1::2], parts[2::2], strict=False))
    if set(sections) != {"SYSTEM", "USER"}:
        raise PromptError(f"{step}/{version}: needs exactly '# SYSTEM' and '# USER' sections")
    return PromptTemplate(step, version, sections["SYSTEM"].strip(), sections["USER"].strip())


class PromptRegistry:
    def __init__(self, root: str | Path, pinned: dict[str, str] | None = None) -> None:
        self._root = Path(root)
        self._pinned = pinned or {}

    def versions(self, step: str) -> list[str]:
        folder = self._root / step
        found = [p.stem for p in folder.glob("*.md") if _VERSION.match(p.stem)]
        return sorted(found, key=lambda v: int(v[1:]))

    def get(self, step: str, version: str | None = None) -> PromptTemplate:
        version = version or self._pinned.get(step)
        if version is None:
            available = self.versions(step)
            if not available:
                raise PromptError(f"no prompt versions found for step {step!r} in {self._root}")
            version = available[-1]
        path = self._root / step / f"{version}.md"
        if not path.is_file():
            raise PromptError(f"prompt file not found: {path}")
        return parse_template(step, version, path.read_text())
