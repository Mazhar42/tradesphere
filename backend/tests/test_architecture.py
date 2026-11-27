"""Module boundaries (ADR-001, TS-21): domain modules depend on each other in one direction only,
and the foundation does not depend on them."""

import ast
from collections.abc import Iterator
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"

# The one place the foundation may look upwards: resolving who is calling.
FOUNDATION_EXCEPTIONS = {"core/permissions.py"}


def _imports(path: Path) -> Iterator[str]:
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "app.modules":
                yield from (f"app.modules.{alias.name}" for alias in node.names)
            else:
                yield node.module
        elif isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)


def _used_modules(path: Path) -> set[str]:
    return {name.split(".")[2] for name in _imports(path) if name.startswith("app.modules.")}


def _graph() -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    for module in sorted(p for p in (APP / "modules").iterdir() if (p / "__init__.py").exists()):
        used: set[str] = set()
        for path in module.rglob("*.py"):
            used |= _used_modules(path)
        graph[module.name] = used - {module.name}
    return graph


def test_domain_modules_have_no_import_cycles() -> None:
    graph = _graph()
    cycles: list[str] = []
    done: set[str] = set()

    def visit(module: str, trail: tuple[str, ...]) -> None:
        if module in trail:
            cycles.append(" -> ".join((*trail[trail.index(module) :], module)))
            return
        if module not in done:
            for used in sorted(graph.get(module, ())):
                visit(used, (*trail, module))
            done.add(module)

    for module in graph:
        visit(module, ())
    assert not cycles, "\n".join(cycles)


def test_the_foundation_does_not_import_domain_modules() -> None:
    offenders = [
        f"{path.relative_to(APP).as_posix()}: {sorted(_used_modules(path))}"
        for folder in ("core", "shared")
        for path in (APP / folder).rglob("*.py")
        if path.relative_to(APP).as_posix() not in FOUNDATION_EXCEPTIONS and _used_modules(path)
    ]
    assert not offenders, "\n".join(offenders)
