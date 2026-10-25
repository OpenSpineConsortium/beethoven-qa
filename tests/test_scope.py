"""Runtime checks of the narrowing (beethoven_qa.scope): which file a path is, what reaches every
module, the importers a change reaches, the import graph read from a package's text, and the files
git says changed.

CrossHair proves the contracts of the three decisions with the rest of the package; these tests
pin the cases a reader checks first, and the private readers of git and of the files, which the
proof does not reach. The whole gate run with a base is test_gate.py's.
"""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

from beethoven_qa import scope
from beethoven_qa.scope import ROOT_FILES, module_named, reached, reaches_every_module
from beethoven_qa.standards import run


@pytest.fixture(autouse=True)
def _hermetic_git(monkeypatch: pytest.MonkeyPatch) -> None:
    # See test_standards.git_env: no configuration of the person's, a fixed author, no GIT_DIR.
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CONFIG_PARAMETERS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("GIT_CONFIG_COUNT", raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "test")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "test@example.invalid")


def _git(root: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True, env=os.environ
    )
    return out.stdout


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")


def test_a_path_is_named_as_an_import_names_it() -> None:
    assert module_named("src/pkg/a.py", "src", "pkg") == "pkg.a"
    assert module_named("src/pkg/__init__.py", "src", "pkg") == "pkg"
    assert module_named("src/pkg/sub/__init__.py", "src", "pkg") == "pkg.sub"
    assert module_named("src/pkg/sub/b.py", "src", "pkg") == "pkg.sub.b"
    # The source directory as the table may spell it, the repository's root among them.
    assert module_named("pkg/a.py", ".", "pkg") == "pkg.a"
    assert module_named("pkg/a.py", "", "pkg") == "pkg.a"
    assert module_named("src/pkg/a.py", "./src/", "pkg") == "pkg.a"
    assert module_named("lib/src/pkg/a.py", "lib/src", "pkg") == "pkg.a"
    for none in (
        "src/pkg/a.pyi",  # a stub is no module
        "src/pkg/data.json",
        "src/other/a.py",  # another package
        "pkg/a.py",  # outside the source directory
        "src/pkg/my-module.py",  # no import can take the name
        "src/pkg/.py",
        "src/pkg/a//b.py",
        "src/pkgs/a.py",
    ):
        assert module_named(none, "src", "pkg") is None, none


def test_what_every_proof_reads_reaches_every_module() -> None:
    copy = "vendor/beethoven-qa/src/beethoven_qa/gate.py"
    everywhere = ROOT_FILES | {copy}
    for wide in (
        "pyproject.toml",
        "pixi.toml",
        "pixi.lock",
        copy,
        "src/pkg/a.pyi",  # a stub of the package, read in the module's place
        "src/pkg/data.json",  # a file of the package's directory that no reading says is unread
        "src/pkg/py.typed",
        "src/other/x.py",  # a Python file beside the package, which it may import
    ):
        assert reaches_every_module(wide, "src", "pkg", everywhere), wide
    for narrow in (
        "src/pkg/a.py",  # a module reaches its importers, not every module
        "src/pkg/__init__.py",
        "src/README.md",  # nothing an import reads
        "docs/notes.md",
        "tests/test_a.py",
        "data/sources/x.json",
        ".github/workflows/ci.yml",
    ):
        assert not reaches_every_module(narrow, "src", "pkg", everywhere), narrow
    # With the package at the root, every Python file beside it is importable, so it counts.
    assert reaches_every_module("tests/test_a.py", ".", "pkg", ROOT_FILES)
    assert not reaches_every_module("docs/notes.md", ".", "pkg", ROOT_FILES)


GRAPH = {
    "pkg": frozenset(),
    "pkg.a": frozenset({"pkg"}),
    "pkg.b": frozenset({"pkg", "pkg.a"}),
    "pkg.c": frozenset({"pkg", "pkg.b"}),
    "pkg.d": frozenset({"pkg"}),
    "pkg.e": frozenset({"pkg", "pkg.gone"}),
}


def test_a_change_reaches_its_importers_through_any_number() -> None:
    assert reached(frozenset({"pkg.a"}), GRAPH, frozenset()) == {"pkg.a", "pkg.b", "pkg.c"}
    assert reached(frozenset({"pkg.c"}), GRAPH, frozenset()) == {"pkg.c"}
    assert reached(frozenset({"pkg.d"}), GRAPH, frozenset()) == {"pkg.d"}
    # A package's __init__ is imported by every module under it.
    assert reached(frozenset({"pkg"}), GRAPH, frozenset()) == GRAPH.keys()
    # A module gone from the graph still reaches what imports it.
    assert reached(frozenset({"pkg.gone"}), GRAPH, frozenset()) == {"pkg.e"}
    # A module that computes its imports is reached by any change, and nothing by none.
    assert reached(frozenset({"pkg.d"}), GRAPH, frozenset({"pkg.c"})) == {"pkg.c", "pkg.d"}
    assert reached(frozenset(), GRAPH, frozenset({"pkg.c"})) == frozenset()
    cycle = {"pkg.x": frozenset({"pkg.y"}), "pkg.y": frozenset({"pkg.x"})}
    assert reached(frozenset({"pkg.x"}), cycle, frozenset()) == {"pkg.x", "pkg.y"}


TREE = {
    "src/pkg/__init__.py": "",
    "src/pkg/a.py": "from pkg import b\nimport pkg.c as cc\nimport os\n",
    "src/pkg/b.py": "from . import d\nfrom .sub import e\n",
    "src/pkg/c.py": "def f():\n    from pkg.sub.e import thing\n",
    "src/pkg/d.py": "import importlib\nM = importlib.import_module('pkg.c')\n",
    "src/pkg/sub/__init__.py": "from .. import a\n",
    "src/pkg/sub/e.py": "import importlib\nNAME = 'x'\nM = importlib.import_module(NAME)\n",
    "src/pkg/f.py": "exec('x = 1')\n",
    "src/pkg/g.py": (
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from pkg.a import x\n"
    ),
    "src/pkg/h.py": "def broken(:\n",
    "src/pkg/i.py": "from ... import nothing\nfrom pkg import *\n",
    "src/pkg/j.py": "def run(model):\n    model.eval()\n",
    "src/pkg/k.py": "M = __import__('pkg.d')\nN = __import__('.d')\n",
    "src/pkg/__pycache__/stale.py": "import pkg.f\n",
    "src/pkg/notes.txt": "not a module\n",
}


def test_the_graph_reads_every_form_of_import(tmp_path: Path) -> None:
    _write(tmp_path, TREE)
    graph = scope._graph(tmp_path, "src", "pkg")
    imports = graph.imports
    assert set(imports) == {
        "pkg",
        *(f"pkg.{m}" for m in ("a", "b", "c", "d", "f", "g", "h", "i", "j", "k")),
        "pkg.sub",
        "pkg.sub.e",
    }
    assert imports["pkg"] == frozenset()
    assert imports["pkg.a"] == {"pkg", "pkg.b", "pkg.c"}  # os is not the package's
    assert imports["pkg.b"] == {"pkg", "pkg.d", "pkg.sub", "pkg.sub.e"}
    # Inside a function, and through a name of a module that may be a submodule of its own.
    assert imports["pkg.c"] == {"pkg", "pkg.sub", "pkg.sub.e", "pkg.sub.e.thing"}
    assert imports["pkg.d"] == {"pkg", "pkg.c"}  # an absolute name as a string is read
    assert imports["pkg.sub"] == {"pkg", "pkg.a"}  # two dots from a package's __init__
    assert imports["pkg.g"] == {"pkg", "pkg.a", "pkg.a.x"}  # a TYPE_CHECKING import counts
    assert imports["pkg.h"] == {"pkg"}  # a file that does not parse keeps its packages
    # Above the top imports nothing; a star names no module, and imports what __all__ names.
    assert imports["pkg.i"] == {"pkg"}
    assert imports["pkg.k"] == {"pkg", "pkg.d"}
    assert graph.dynamic == {"pkg.sub.e", "pkg.f", "pkg.h", "pkg.i", "pkg.k"}
    # A method named eval is no import, and nothing names itself.
    assert "pkg.j" not in graph.dynamic
    assert all(module not in names for module, names in imports.items())
    # A change to pkg.c reaches what imports it, through any number of others (pkg.a and pkg.d
    # import it, pkg.sub and pkg.g import pkg.a), and every module that computes its imports;
    # not the package's __init__, nor pkg.j, which imports nothing it reaches.
    assert reached(frozenset({"pkg.c"}), imports, graph.dynamic) == set(imports) - {"pkg", "pkg.j"}


def test_the_change_is_both_names_of_a_rename_and_every_untracked_file(tmp_path: Path) -> None:
    _write(tmp_path, {"src/pkg/a.py": "A = 1\n", "docs/x.md": "x\n", "keep.txt": "same\n"})
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    base = _git(tmp_path, "rev-parse", "HEAD").strip()
    assert scope._changed(tmp_path, base) == ()
    _git(tmp_path, "mv", "src/pkg/a.py", "src/pkg/b.py")
    _write(tmp_path, {"docs/x.md": "y\n", "src/pkg/new.py": "N = 1\n"})
    assert scope._changed(tmp_path, base) == (
        "docs/x.md",
        "src/pkg/a.py",
        "src/pkg/b.py",
        "src/pkg/new.py",
    )
    # Committed or not, a change since the base reads the same.
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "head")
    assert scope._changed(tmp_path, base) == (
        "docs/x.md",
        "src/pkg/a.py",
        "src/pkg/b.py",
        "src/pkg/new.py",
    )
    with pytest.raises(subprocess.CalledProcessError):
        scope._changed(tmp_path, "no-such-commit")


def test_the_gate_and_the_standards_tell_the_base_by_one_rule() -> None:
    assert run._resolve is scope._resolve


def test_what_python_caches_is_no_change(tmp_path: Path) -> None:
    # The gate imports the package before it reads the change, and the import writes __pycache__.
    _write(tmp_path, {"src/pkg/a.py": "A = 1\n"})
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    base = _git(tmp_path, "rev-parse", "HEAD").strip()
    _write(tmp_path, {"src/pkg/__pycache__/a.cpython-311.pyc": "", "src/pkg/a.pyc": ""})
    # A compiled file beside the sources is importable without them, so it stays a change.
    assert scope._changed(tmp_path, base) == ("src/pkg/a.pyc",)
