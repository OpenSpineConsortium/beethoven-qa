"""Runtime checks of the gate.

CrossHair proves the contracts of gate.py's pure functions (the gate runs on this package too).
These tests pin what the proof does not reach: the configuration boundary, and the whole command
run against two tiny packages, one that must fail the contract check and one that must pass both
checks, so "the gate is green" is shown to mean something.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from beethoven_qa.contracts import ViolationError
from beethoven_qa.gate import (
    FunctionEntry,
    GateConfig,
    crosshair_command,
    is_dotted_identifier,
    is_public,
    set_aside,
    uncontracted,
)

SRC = Path(__file__).resolve().parent.parent / "src"


def test_is_public() -> None:
    assert is_public("remaining")
    assert not is_public("_helper")
    assert not is_public("__init__")
    assert not is_public("")


def test_is_dotted_identifier() -> None:
    assert is_dotted_identifier("beethoven_qa")
    assert is_dotted_identifier("beethoven_qa.gate")
    for bad in ("", ".gate", "beethoven_qa.", "beethoven_qa..gate", "beethoven-qa", "1qa", "a b"):
        assert not is_dotted_identifier(bad), bad


def test_uncontracted_counts_a_function_in_its_own_module_only() -> None:
    entries = (
        FunctionEntry("pkg.a", "proven", "pkg.a", True),
        FunctionEntry("pkg.a", "bare", "pkg.a", False),
        FunctionEntry("pkg.a", "_private", "pkg.a", False),
        FunctionEntry("pkg.a", "imported", "pkg.b", False),  # checked where it is defined
        FunctionEntry("pkg.b", "imported", "pkg.b", False),
        FunctionEntry("pkg.cli", "main", "pkg.cli", False),  # exempt module
    )
    assert uncontracted(entries, frozenset({"pkg.cli"})) == ["pkg.a.bare", "pkg.b.imported"]
    assert uncontracted((), frozenset()) == []


def test_crosshair_command_applies_the_declared_bound() -> None:
    cmd = crosshair_command("/usr/bin/python3", "/repo/src/pkg", 7)
    assert cmd[:4] == ["/usr/bin/python3", "-m", "crosshair", "check"]
    assert "--analysis_kind=icontract" in cmd
    assert "--per_condition_timeout=7" in cmd
    assert "--report_all" not in cmd
    assert (
        crosshair_command("/usr/bin/python3", "pkg.mod", 7, report_all=True)[-1] == "--report_all"
    )
    with pytest.raises(ViolationError):
        crosshair_command("/usr/bin/python3", "/repo/src/pkg", 0)
    with pytest.raises(ViolationError):  # found by the gate on its own first draft of the flag
        crosshair_command("/usr/bin/python3", "--report_all", 1)


def test_set_aside() -> None:
    targets = ("pkg.a.f", "pkg.a.g", "pkg.b.C")
    assert set_aside(targets, ("pkg.a.g",)) == (("pkg.a.f", "pkg.b.C"), ())
    split = set_aside(targets, ("pkg.a.gone", "pkg.a.f"))
    assert split == (("pkg.a.g", "pkg.b.C"), ("pkg.a.gone",))
    assert set_aside((), ()) == ((), ())


def test_config_boundary() -> None:
    config = GateConfig(package="pkg", exempt=("pkg", "pkg.cli"))
    assert config.src == "src" and config.per_condition_timeout == 20 and config.unproven == {}
    for bad in (
        {"package": "pkg.sub"},  # a top-level package, not a module
        {"package": "my-pkg"},
        {"package": "pkg", "src": "/abs"},
        {"package": "pkg", "src": "../elsewhere"},
        {"package": "pkg", "exempt": ["other.cli"]},
        {"package": "pkg", "per_condition_timeout": 0},
        {"package": "pkg", "unknown": 1},
        {"package": "pkg", "unproven": {"other.io.read": "reads a file"}},
        {"package": "pkg", "unproven": {"pkg.io.read": "  "}},  # an entry without its reason
        {"package": "pkg", "unproven": {"pkg.io.read-all": "reads a file"}},
    ):
        with pytest.raises(ValidationError):
            GateConfig.model_validate(bad)


def test_this_repository_configures_itself() -> None:
    config = GateConfig.from_pyproject(SRC.parent)
    assert config.package == "beethoven_qa"
    assert "beethoven_qa.__main__" in config.exempt


def _repo(tmp_path: Path, module: str) -> Path:
    (tmp_path / "src" / "toy").mkdir(parents=True)
    (tmp_path / "src" / "toy" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "toy" / "logic.py").write_text(textwrap.dedent(module), encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[tool.beethoven-qa]\npackage = "toy"\nexempt = ["toy"]\nper_condition_timeout = 3\n',
        encoding="utf-8",
    )
    return tmp_path


def _gate(root: Path) -> subprocess.CompletedProcess[str]:
    # The command exactly as a consuming repository runs it, in a fresh interpreter, so the toy
    # package is imported clean and this package comes from src/ as it would from a vendored copy.
    return subprocess.run(
        [sys.executable, "-m", "beethoven_qa", "gate", "--root", str(root)],
        cwd=root,
        env={"PYTHONPATH": str(SRC), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )


def test_gate_refuses_a_public_function_without_contracts(tmp_path: Path) -> None:
    root = _repo(tmp_path, "def double(x: int) -> int:\n    return 2 * x\n")
    result = _gate(root)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "toy.logic.double" in result.stdout


def test_gate_passes_a_proven_module_and_fails_a_false_one(tmp_path: Path) -> None:
    good = """
        from beethoven_qa.contracts import ensure, require

        @require(lambda x: x >= 0)
        @ensure(lambda result, x: result >= x)
        def double(x: int) -> int:
            return 2 * x
    """
    result = _gate(_repo(tmp_path / "good", good))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "no counterexample" in result.stdout

    # The same function with a postcondition that is false for x > 0: CrossHair must find it.
    bad = good.replace("result >= x", "result == x")
    result = _gate(_repo(tmp_path / "bad", bad))
    assert result.returncode != 0, result.stdout + result.stderr
    assert "double" in result.stdout + result.stderr


def _toy(tmp_path: Path, files: dict[str, str], exempt: str = '["toy"]', extra: str = "") -> Path:
    # A throwaway repository whose package is ``toy``, holding exactly ``files``; ``extra`` is
    # appended to its pyproject.toml.
    for rel, text in files.items():
        path = tmp_path / "src" / "toy" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        f'[tool.beethoven-qa]\npackage = "toy"\nexempt = {exempt}\nper_condition_timeout = 3\n'
        + extra,
        encoding="utf-8",
    )
    return tmp_path


PROVEN = """
    from beethoven_qa.contracts import ensure, require

    @require(lambda x: x >= 0)
    @ensure(lambda result, x: result >= x)
    def double(x: int) -> int:
        return 2 * x
"""


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        # A directory without __init__.py is never imported, so nothing in it would be checked.
        (
            {"logic.py": PROVEN, "extra/more.py": "def bare(x: int) -> int:\n    return x\n"},
            "was not imported as a module of toy",
        ),
        # A module that exits while being imported would end the gate with its own exit code.
        ({"logic.py": PROVEN, "cli.py": "import sys\nsys.exit(0)\n"}, "exited"),
        (
            {"logic.py": PROVEN, "broken.py": "import does_not_exist\n"},
            "failed: ModuleNotFoundError",
        ),
        # A directive can switch CrossHair off; none is allowed.
        ({"logic.py": "# crosshair: off\n" + textwrap.dedent(PROVEN)}, "directive"),
        # A cache over a function without contracts is still a public function without contracts.
        (
            {
                "logic.py": "import functools\n\n@functools.cache\ndef triple(x: int) -> int:\n"
                "    return 3 * x\n"
            },
            "toy.logic.triple",
        ),
        # A private function published under a public name counts where it is published.
        (
            {
                "logic.py": "def _triple(x: int) -> int:\n    return 3 * x\n",
                "api.py": "from toy.logic import _triple as triple\n",
            },
            "toy.api.triple",
        ),
        # Nothing to prove is not a pass.
        ({"logic.py": "def _helper(x: int) -> int:\n    return x\n"}, "nothing to prove"),
        # CrossHair would check the coroutine object, never the awaited result.
        (
            {
                "logic.py": textwrap.dedent(PROVEN)
                + "\nasync def later(x: int) -> int:\n    return x\n"
            },
            "is a coroutine",
        ),
        # CrossHair never builds a pydantic model, so a contract on its method is never run.
        (
            {
                "logic.py": textwrap.dedent(PROVEN)
                + "\nfrom pydantic import BaseModel\n\n\nclass W(BaseModel):\n"
                "    cap: int\n\n    @ensure(lambda self, result: result <= self.cap)\n"
                "    def left(self) -> int:\n        return self.cap + 1\n"
            },
            "is a pydantic model with contracts",
        ),
    ],
    ids=[
        "namespace",
        "exit",
        "import-error",
        "directive",
        "cache",
        "re-export",
        "empty",
        "async",
        "pydantic",
    ],
)
def test_gate_refuses_what_it_cannot_see(
    tmp_path: Path, files: dict[str, str], expected: str
) -> None:
    result = _gate(_toy(tmp_path, {"__init__.py": "", **files}))
    assert result.returncode != 0, result.stdout + result.stderr
    assert expected in result.stdout, result.stdout + result.stderr


def test_gate_runs_crosshair_even_when_a_function_lacks_contracts(tmp_path: Path) -> None:
    bad = PROVEN.replace("result >= x", "result == x")
    root = _toy(
        tmp_path,
        {"__init__.py": "", "logic.py": bad, "other.py": "def bare() -> None:\n    pass\n"},
    )
    result = _gate(root)
    assert result.returncode != 0
    assert "toy.other.bare" in result.stdout and "false when calling double" in result.stdout


def test_gate_one_module_and_every_verdict(tmp_path: Path) -> None:
    vacuous = """
        from beethoven_qa.contracts import ensure, require

        @require(lambda x: x > 0 and x < 0)
        @ensure(lambda result: result > 0)
        def never(x: int) -> int:
            return x
    """
    root = _toy(
        tmp_path,
        {
            "__init__.py": "",
            "logic.py": PROVEN,
            "empty.py": vacuous,
            "other.py": "def bare() -> None:\n    pass\n",
            "broken.py": "1 / 0\n",
        },
    )
    one = subprocess.run(
        [
            sys.executable,
            "-m",
            "beethoven_qa",
            "gate",
            "--root",
            str(root),
            "--module",
            "toy.logic",
        ],
        cwd=root,
        env={"PYTHONPATH": str(SRC), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    assert one.returncode == 0, one.stdout + one.stderr  # toy.other's bare function is not in scope

    def report_all(module: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "beethoven_qa",
                "gate",
                "--root",
                str(root),
                "--module",
                module,
                "--report-all",
            ],
            cwd=root,
            env={"PYTHONPATH": str(SRC), "PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )

    proven = report_all("toy.logic")
    assert proven.returncode == 0 and "Confirmed over all paths" in proven.stdout, proven.stdout
    never = report_all("toy.empty")
    assert never.returncode != 0 and "vacuous" in never.stdout, never.stdout

    # exempt, missing, another package, an import that fails
    for outside in ("toy", "toy.nope", "other.logic", "toy.broken"):
        refused = subprocess.run(
            [
                sys.executable,
                "-m",
                "beethoven_qa",
                "gate",
                "--root",
                str(root),
                "--module",
                outside,
            ],
            cwd=root,
            env={"PYTHONPATH": str(SRC), "PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
        assert refused.returncode == 2, (outside, refused.stdout + refused.stderr)
        if outside == "toy.broken":  # and the reason, not only the refusal
            assert "ZeroDivisionError" in refused.stdout, refused.stdout


def test_gate_sets_aside_only_what_is_listed_and_says_so(tmp_path: Path) -> None:
    # A contract CrossHair would refute (x == 7), standing in for a function CrossHair must not
    # run: listed with its reason it is set aside and named; unlisted, the gate finds it.
    io = """
        from beethoven_qa.contracts import ensure

        @ensure(lambda result, x: result != 7)
        def read(x: int) -> int:
            return x
    """
    files = {"__init__.py": "", "logic.py": PROVEN, "io.py": io}
    why = '[tool.beethoven-qa.unproven]\n"toy.io.read" = "reads the clock"\n'
    listed = _gate(_toy(tmp_path / "listed", files, extra=why))
    assert listed.returncode == 0, listed.stdout + listed.stderr
    assert "toy.io.read: reads the clock" in listed.stdout
    assert _gate(_toy(tmp_path / "unlisted", files)).returncode == 1

    stale = '[tool.beethoven-qa.unproven]\n"toy.io.gone" = "renamed since"\n'
    result = _gate(_toy(tmp_path / "stale", files, extra=why + stale.split("\n", 1)[1]))
    assert result.returncode == 1 and "toy.io.gone names no contracted" in result.stdout

    # Under --module, a module whose every function is set aside has nothing proven.
    alone = subprocess.run(
        [sys.executable, "-m", "beethoven_qa", "gate", "--root", str(tmp_path / "listed")]
        + ["--module", "toy.io"],
        cwd=tmp_path / "listed",
        env={"PYTHONPATH": str(SRC), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    assert alone.returncode == 1 and "nothing to prove in toy.io" in alone.stdout, alone.stdout


def test_gate_refuses_a_package_imported_from_elsewhere(tmp_path: Path) -> None:
    # The gate run on a second checkout of beethoven-qa would find its own copy already imported.
    other = tmp_path / "other"
    (other / "src" / "beethoven_qa").mkdir(parents=True)
    for module in (SRC / "beethoven_qa").glob("*.py"):
        (other / "src" / "beethoven_qa" / module.name).write_bytes(module.read_bytes())
    (other / "pyproject.toml").write_bytes((SRC.parent / "pyproject.toml").read_bytes())
    result = _gate(other)
    assert result.returncode != 0 and "was imported from" in result.stdout, result.stdout


def test_module_gate_is_not_a_command() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "beethoven_qa.gate"],
        env={"PYTHONPATH": str(SRC), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0 and "python -m beethoven_qa gate" in result.stderr
