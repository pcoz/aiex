"""Regression test: every third-party module the package imports
unconditionally is declared in pyproject.toml.

Before this was fixed, ``aiex/cli.py`` imported ``dotenv`` at module load but
``python-dotenv`` was not a declared dependency, so ``aiex`` (even
``aiex --help``) crashed with ModuleNotFoundError on a clean install.
"""

import ast
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# import name -> distribution name, for the hard (non-optional) imports.
HARD_IMPORTS = {
    "anthropic": "anthropic",
    "pydantic": "pydantic",
    "rich": "rich",
    "dotenv": "python-dotenv",
}


def _declared() -> set[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    names = set()
    for spec in data["project"]["dependencies"]:
        name = spec
        for sep in "<>=!~[; ":
            name = name.split(sep)[0]
        names.add(name.strip().lower())
    return names


def _module_level_imports(path: Path) -> set[str]:
    """Top-level names imported outside try/except blocks."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            found.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return found


def test_hard_imports_are_declared():
    declared = _declared()
    missing = []
    for py in (ROOT / "src" / "aiex").rglob("*.py"):
        for mod in _module_level_imports(py):
            dist = HARD_IMPORTS.get(mod)
            if dist and dist not in declared:
                missing.append(f"{py.relative_to(ROOT)} imports {mod} ({dist})")
    assert not missing, missing


if __name__ == "__main__":
    test_hard_imports_are_declared()
    print("PACKAGING TEST PASSED")
