"""Which modules of the package a change reaches, so the gate proves only those (``--base``).

CrossHair's verdict on a target is decided by the module that defines it, by every module of the
package that module imports, directly or through others (CrossHair runs the body of every function
a target calls), by the packages they sit in (whose ``__init__`` runs first), and by what every
proof reads: ``pyproject.toml`` (the ``[tool.beethoven-qa]`` table and its bound), the locked
environment (``pixi.toml``, ``pixi.lock``) and the gate's own code. A change that reaches none of
those leaves the verdict as it was at the base commit, so with a base the gate proves again only
the modules the change reaches:

- a change to ``pyproject.toml``, ``pixi.toml`` or ``pixi.lock``, to the gate's own files (the
  ``__init__``, ``contracts``, ``gate``, ``scope`` and ``_crosshair`` of the ``beethoven_qa`` the
  gate runs from, wherever that copy sits), to any other file of the repository the gate's process
  imported from outside the package, to a file of the package's directory that is not one of its
  modules (a stub, a data file) or to a Python file of the source directory outside the package
  reaches every module;
- a change to a module reaches that module and every module that imports it, directly or through
  others, a package's ``__init__`` counting as imported by every module under it;
- a module that imports by a name it computes (``importlib.import_module`` or ``__import__`` given
  anything but an absolute name as a string, ``exec``, ``eval``, ``runpy``, a loader by path), or
  by a star (``from x import *``, which runs whatever submodules ``x``'s ``__all__`` names), is
  reached by every change to the package, since its text does not say what it imports;
- anything else (documents, data, tests, workflows, the kit) reaches no module.

Check 1 of the gate (contracts, imports, directives, suppressions, skipped tests, the unproven
entries) is cheap and always covers the whole package; only check 2, the proof, is narrowed. When
the base cannot be told, or git fails, every module is proven, so the narrowing can only ever prove
more than it must, never less than the rule above. What it assumes is that the modules a change
does not reach passed at the base: the pre-push hook's base is where the branch left the remote's
default branch, which that branch's own required check passed, and a CI run names the newest commit
whose gate passed on a push. It assumes too that a proof reads no file but those above: a contracted
function is pure, as the layering rule asks (the definition-of-done skill), so a data file outside
the package's directory decides no verdict. CrossHair's search is bounded and timed, so it is not
quite the same twice; the full run, with no base (the default), stays the backstop.

The decisions carry contracts and are proven with the rest of the package; reading git and the
files is private and carries none, as in the gate itself. ``_resolve`` is the standards' base too.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from typing import NamedTuple

from beartype import beartype

from beethoven_qa.contracts import ensure
from beethoven_qa.vendor import _git

__all__ = ["ROOT_FILES", "module_named", "reached", "reaches_every_module"]

# The files at the repository's root that every proof reads: the gate's table and its bound, and
# the environment CrossHair, Z3 and the package's dependencies come from.
ROOT_FILES = frozenset({"pyproject.toml", "pixi.toml", "pixi.lock"})

# What a Python file of the source directory may be, beside the package: a module, or a stub that
# mypy and an import would read in its place.
_PYTHON = (".py", ".pyi")


def _source_prefix(src: str) -> str:
    # The prefix every file of the source directory starts with, as git prints a path: nothing
    # when the source directory is the repository's root.
    parts = [part for part in src.strip("/").split("/") if part not in ("", ".")]
    return "".join(f"{part}/" for part in parts)


@beartype
@ensure(
    lambda result, package: result is None or result == package or result.startswith(package + "."),
    "only a module of the package is named",
)
@ensure(
    lambda result: result is None or all(part.isidentifier() for part in result.split(".")),
    "a name an import can take",
)
@ensure(lambda result, path: result is None or path.endswith(".py"), "only a Python file is one")
def module_named(path: str, src: str, package: str) -> str | None:
    """The module of ``package`` the repository-relative file ``path`` holds, by the name an import
    gives it (an ``__init__.py`` holds its package), or ``None`` when it holds none: not under
    ``<src>/<package>/``, not a ``.py`` file, or a name no import can take.
    """
    prefix = f"{_source_prefix(src)}{package}/"
    if not path.startswith(prefix) or not path.endswith(".py"):
        return None
    parts = [package, *path[len(prefix) : -len(".py")].split("/")]
    if parts[-1] == "__init__":
        parts.pop()
    if not all(part.isidentifier() for part in parts):
        return None
    return ".".join(parts)


@beartype
@ensure(
    lambda result, path, everywhere: result or path not in everywhere,
    "a file every proof reads reaches every module",
)
@ensure(
    lambda result, path, src, package, everywhere: (
        not result or path in everywhere or module_named(path, src, package) is None
    ),
    "a module of the package reaches its importers, never every module on its own",
)
@ensure(
    lambda result, path, src, everywhere: (
        not result or path in everywhere or path.startswith(_source_prefix(src))
    ),
    "outside the source directory only a file every proof reads reaches every module",
)
def reaches_every_module(path: str, src: str, package: str, everywhere: frozenset[str]) -> bool:
    """Whether a change to the repository-relative file ``path`` can change the verdict on every
    module: a file every proof reads (``everywhere``: the root files, the gate's own, what the
    gate imported from outside the package), any file of the package's directory that is not one
    of its modules, or a Python file of the source directory outside the package.
    """
    if path in everywhere:
        return True
    in_package = path.startswith(f"{_source_prefix(src)}{package}/")
    in_source = path.startswith(_source_prefix(src)) and path.endswith(_PYTHON)
    return (in_package or in_source) and module_named(path, src, package) is None


@beartype
@ensure(lambda result, imports: result <= imports.keys(), "only modules of the package")
@ensure(lambda result, changed, imports: changed & imports.keys() <= result, "each changed one")
@ensure(
    lambda result, imports: all(m in result for m, names in imports.items() if names & result),
    "every module that imports a reached one is reached",
)
@ensure(
    lambda result, changed, imports, dynamic: not changed or dynamic & imports.keys() <= result,
    "a module that computes its imports is reached by every change",
)
@ensure(lambda result, changed: changed or not result, "no change reaches nothing")
def reached(
    changed: frozenset[str], imports: dict[str, frozenset[str]], dynamic: frozenset[str]
) -> frozenset[str]:
    """The modules whose verdict a change to the modules ``changed`` can alter: each of them
    (``imports`` names every module of the package with the names its text imports, the packages
    above it among them), every module that imports a reached one, through any number of others,
    and every module of ``dynamic``, which imports by names it computes, once anything changed.
    A changed name the graph does not hold (a module deleted) still reaches what imports it.
    """
    if not changed:
        return frozenset()
    found = set((changed | dynamic) & imports.keys())
    frontier = changed | frozenset(found)
    while frontier:
        found_now = {m for m, names in imports.items() if m not in found and names & frontier}
        found |= found_now
        frontier = frozenset(found_now)
    return frozenset(found)


def _commit(root: Path, ref: str) -> str:
    return _git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").strip()


def _resolve(root: Path, base: str) -> str | None:
    # The base commit, or None for no base; ValueError when it cannot be told.
    if base == "none":
        return None
    if base != "auto":
        try:
            return _commit(root, base)
        except subprocess.CalledProcessError:
            raise ValueError(f"{base} is not a commit of this repository") from None
    candidates = ["refs/remotes/origin/main", "refs/remotes/origin/master"]
    try:
        named = _git(root, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD").strip()
        candidates.insert(0, named)
    except subprocess.CalledProcessError:
        pass
    for ref in candidates:
        try:
            return _git(root, "merge-base", "HEAD", _commit(root, ref)).strip()
        except subprocess.CalledProcessError:
            continue
    raise ValueError("the remote's default branch is not known here; pass --base <commit>")


def _changed(root: Path, base: str) -> tuple[str, ...]:
    # Every file the working tree holds otherwise than the base commit, a renamed one under both of
    # its names (a module renamed is one gone and one new), and every file git does not track yet,
    # but what Python caches under __pycache__, which it reads only for a source that matches it,
    # and which the gate's own import of the package writes before the change is read.
    diff = _git(root, "diff", "--no-renames", "--no-ext-diff", "--name-only", "-z", base)
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "-z")
    paths = {path for path in (diff + untracked).split("\x00") if path}
    return tuple(sorted(path for path in paths if "__pycache__" not in path.split("/")))


class _Graph(NamedTuple):
    # Every module of the package's directory, with the names under the package its text imports
    # (the packages above it among them), and the modules that import by a name they compute.
    imports: dict[str, frozenset[str]]
    dynamic: frozenset[str]


# What a call may be named when it imports, or runs code, by a name the text does not hold: a
# builtin called by its own name, or the function of a module (importlib, runpy, a spec's loader).
_COMPUTED_NAMES = frozenset({"__import__", "exec", "eval", "import_module", "run_module"})
_COMPUTED_ATTRIBUTES = frozenset(
    {
        "__import__",
        "import_module",
        "run_module",
        "run_path",
        "spec_from_file_location",
        "exec_module",
        "load_module",
    }
)


def _anchor(module: str, is_package: bool, level: int) -> str | None:
    # The package a relative import with ``level`` dots starts from, or None above the top.
    parts = module.split(".") if is_package else module.split(".")[:-1]
    keep = len(parts) - (level - 1)
    return ".".join(parts[:keep]) if keep >= 1 else None


def _from_names(node: ast.ImportFrom, module: str, is_package: bool) -> list[str]:
    # The module a `from` import reads, and what it names in it, any of which may be a submodule.
    start = _anchor(module, is_package, node.level) if node.level else ""
    if start is None:
        return []
    where = ".".join(part for part in (start, node.module or "") if part)
    return [where, *(f"{where}.{alias.name}" for alias in node.names if alias.name != "*")]


def _literal(call: ast.Call) -> str | None:
    # The absolute module name a call to import_module or __import__ gives as a plain string.
    first = call.args[0] if call.args else None
    text = first.value if isinstance(first, ast.Constant) else None
    if isinstance(text, str) and text and not text.startswith(".") and len(call.args) == 1:
        return text
    return None


def _called(call: ast.Call) -> str | None:
    # The name a call is made by, when it is one that imports or runs code by a computed name.
    func = call.func
    if isinstance(func, ast.Name) and func.id in _COMPUTED_NAMES:
        return func.id
    if isinstance(func, ast.Attribute) and func.attr in _COMPUTED_ATTRIBUTES:
        return func.attr
    return None


def _call_names(call: ast.Call) -> tuple[list[str], bool]:
    # What one call imports by a name its text holds, and whether it imports by a computed one.
    called = _called(call)
    if called is None:
        return [], False
    literal = _literal(call) if called in ("import_module", "__import__") else None
    return ([literal], False) if literal else ([], True)


def _node_names(node: ast.AST, module: str, is_package: bool) -> tuple[list[str], bool]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names], False
    if isinstance(node, ast.ImportFrom):
        star = any(alias.name == "*" for alias in node.names)
        return _from_names(node, module, is_package), star
    if isinstance(node, ast.Call):
        return _call_names(node)
    return [], False


def _with_parents(name: str) -> list[str]:
    # A dotted name and every package above it, each of which an import of it runs.
    parts = name.split(".")
    return [".".join(parts[:end]) for end in range(1, len(parts) + 1)]


def _edges(
    module: str, is_package: bool, tree: ast.Module, package: str
) -> tuple[frozenset[str], bool]:
    # The names under the package one module's text imports, anywhere in it (a function, a
    # TYPE_CHECKING block, a try), each with the packages above it, the module's own packages
    # among them; and whether it imports by a computed name.
    names = set(_with_parents(module)[:-1])
    computes = False
    for node in ast.walk(tree):
        found, computed = _node_names(node, module, is_package)
        computes = computes or computed
        names.update(n for name in found for n in _with_parents(name))
    inside = {n for n in names if n == package or n.startswith(f"{package}.")}
    return frozenset(inside - {module}), computes


def _graph(root: Path, src: str, package: str) -> _Graph:
    # Every module of the package's directory, read from its file whether or not it imports (the
    # gate reports a failed import itself); a file that cannot be parsed counts as importing by a
    # computed name, since nothing says what it imports.
    imports: dict[str, frozenset[str]] = {}
    dynamic: set[str] = set()
    home = root / f"{_source_prefix(src)}{package}"
    for path in sorted(home.rglob("*.py")):
        module = module_named(path.relative_to(root).as_posix(), src, package)
        if module is None or "__pycache__" in path.parts:
            continue
        is_package = path.name == "__init__.py"
        try:
            tree = ast.parse(path.read_bytes(), str(path))
        except (SyntaxError, ValueError):
            imports[module], computes = frozenset(_with_parents(module)[:-1]), True
        else:
            imports[module], computes = _edges(module, is_package, tree, package)
        if computes:
            dynamic.add(module)
    return _Graph(imports, frozenset(dynamic))
