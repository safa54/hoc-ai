"""Enforces the dependency direction from ARCHITECTURE §12.2.

core <- store <- {llm, ingest} <- construct <- services <- {cli, web, app}
A violation here means a layer is reaching "upward"; fix the design, not the test.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "hoc"

# package -> hoc sub-packages it may import (itself always allowed)
ALLOWED: dict[str, set[str]] = {
    "core": set(),
    "store": {"core"},
    "llm": {"core", "store", "config"},
    "ingest": {"core"},
    "construct": {"core", "store", "llm", "config"},
    "learner": {"core", "store", "config"},
    "policy": {"core", "store", "config", "learner"},
    "assess": {"core", "store", "llm", "config", "learner"},
    "tutor": {"core", "store", "llm", "config", "learner", "policy", "assess"},
    "views": {"core", "store", "config"},
    "services": {
        "views",
        "core",
        "store",
        "llm",
        "config",
        "ingest",
        "construct",
        "learner",
        "policy",
        "assess",
        "tutor",
    },
    "web": {"core", "config", "services"},
    "config": {"core"},
}


def hoc_imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("hoc"):
            parts = node.module.split(".")
            found.add(parts[1] if len(parts) > 1 else "")
            if len(parts) == 1:  # `from hoc import x`
                found.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("hoc."):
                    found.add(alias.name.split(".")[1])
    return found


def test_layers_only_import_downward():
    violations = []
    for pkg, allowed in ALLOWED.items():
        files = (
            [SRC / f"{pkg}.py"] if (SRC / f"{pkg}.py").exists() else list((SRC / pkg).rglob("*.py"))
        )
        for f in files:
            bad = hoc_imports(f) - allowed - {pkg}
            if bad:
                violations.append(f"{f.relative_to(SRC)} imports {sorted(bad)}")
    assert not violations, "\n".join(violations)


def test_every_package_is_covered_by_the_rules():
    packages = {p.name for p in SRC.iterdir() if p.is_dir() and not p.name.startswith("__")}
    top_modules = {p.stem for p in SRC.glob("*.py") if p.stem not in {"__init__", "cli"}}
    assert (packages | top_modules) <= set(ALLOWED), "add new packages to ALLOWED"
