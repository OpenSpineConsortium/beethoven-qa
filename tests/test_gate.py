"""Runtime checks of the gate.

CrossHair proves the contracts of gate.py's pure functions (the gate runs on this package too).
These tests pin what the proof does not reach: the configuration boundary, and the whole command
run against two tiny packages, one that must fail the contract check and one that must pass both
checks, so "the gate is green" is shown to mean something.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest
from pydantic import ValidationError

from beethoven_qa import gate
from beethoven_qa.contracts import ViolationError
from beethoven_qa.gate import (
    FunctionEntry,
    GateConfig,
    Verdicts,
    crosshair_command,
    is_dotted_identifier,
    is_public,
    set_aside,
    uncontracted,
    verdicts,
)
from beethoven_qa.scope import ROOT_FILES

SRC = Path(__file__).resolve().parent.parent / "src"

# The gate's last line before its verdict, as the report command reads it: byte for byte.
CONDITIONS = re.compile(
    r"^gate: conditions: (\d+) of (\d+) confirmed over all paths, (\d+) not confirmed within "
    r"(\d+) s per condition, (\d+) unable to meet a precondition$"
)


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
    assert cmd[:4] == ["/usr/bin/python3", "-m", "beethoven_qa._crosshair", "check"]
    assert "--analysis_kind=icontract" in cmd
    assert "--per_condition_timeout=7" in cmd
    # Every run, not only on request: a verdict nobody saw was a pass nobody earned.
    assert cmd[-1] == "--report_all"
    with pytest.raises(ViolationError):
        crosshair_command("/usr/bin/python3", "/repo/src/pkg", 0)
    with pytest.raises(ViolationError):  # found by the gate on its own first draft of the flag
        crosshair_command("/usr/bin/python3", "--report_all", 1)


def test_verdicts_count_crosshair_lines_and_nothing_else() -> None:
    text = (
        "/r/a.py:3: info: Confirmed over all paths.\n"
        "/r/a.py:5: info: Not confirmed.\n"
        "/r/a.py:7: info: Confirmed over all paths.\n"
        "/r/b.py:9: info: Unable to meet precondition.\n"
        "/r/b.py:12: error: false when calling f(1) (which returns 2)\n"
        "a checked function printed: Confirmed over all paths.\n"
        "/r/b.py:14: info: Not confirmed. (then more)\n"
        # CrossHair's own form when the precondition raised: the reason follows, and ends the line.
        '/r/c.py:4: info: Unable to meet precondition (possibly because it raised "KeyError(1)" '
        'at   File "/r/c.py", line 3, in <lambda>?).\n'
    )
    assert verdicts(text) == Verdicts(2, 1, 2)
    assert verdicts("") == Verdicts(0, 0, 0)
    assert verdicts("Traceback (most recent call last):\n  File x\nTypeError: y\n") == Verdicts(
        0, 0, 0
    )


def test_report_counts_each_target_and_names_what_the_gate_refuses(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # One CrossHair process per target: the first non-zero exit is the gate's, the counts add up
    # over every target, and what no exit code says (nothing checkable, a precondition no input
    # meets) is named for the gate to refuse. Every process's output is printed under its line.
    def ran(code: int, out: str, err: str = "") -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["crosshair"], code, out, err)

    results = [
        ran(0, "/r/a.py:3: info: Confirmed over all paths.\n/r/a.py:5: info: Not confirmed.\n"),
        ran(1, "/r/b.py:9: error: false when calling f(1) (which returns 2)\n"),
        ran(0, "", f"{gate._NOTHING_TO_CHECK}\n"),
        ran(0, "/r/d.py:2: info: Unable to meet precondition.\n"),
        ran(2, "", "Traceback (most recent call last):\n  File x\nTypeError: y\n"),
    ]
    targets = ["pkg.a", "pkg.b", "pkg.c", "pkg.d", "pkg.e"]
    failed, totals, problems = gate._report(targets, results, 20)
    assert failed == 1 and totals == Verdicts(1, 1, 1)
    assert problems == [
        "CrossHair found nothing to check in pkg.c, which has contracts",
        f"pkg.d: {gate._VACUOUS}",
    ]
    out = capsys.readouterr().out.splitlines()
    assert out[:2] == [
        "gate: pkg.a: exit 0",
        "gate: pkg.a: 1 confirmed over all paths, 1 not confirmed within 20 s, "
        "0 unable to meet a precondition",
    ]
    assert "/r/b.py:9: error: false when calling f(1) (which returns 2)" in out
    assert "gate: pkg.e: exit 2" in out and out.count("TypeError: y") == 1


def test_set_aside() -> None:
    functions = ("pkg.a.f", "pkg.a.g")
    classes = ("pkg.b.C",)
    assert set_aside(functions, classes, ("pkg.a.g",)) == (("pkg.a.f",), (), ())
    split = set_aside(functions, classes, ("pkg.a.gone", "pkg.a.f"))
    assert split == (("pkg.a.g",), (), ("pkg.a.gone",))
    # A class, or a method of one, is refused as such; a name that only resembles one is stale.
    split = set_aside(functions, classes, ("pkg.b.C", "pkg.b.C.load", "pkg.b.Cx"))
    assert split == (functions, ("pkg.b.C", "pkg.b.C.load"), ("pkg.b.Cx",))
    assert set_aside((), (), ()) == ((), (), ())
    with pytest.raises(ViolationError):  # a function named twice would be proven twice over
        set_aside(("pkg.a.f", "pkg.a.f"), (), ())


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


def _gate(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    # The command exactly as a consuming repository runs it, in a fresh interpreter, so the toy
    # package is imported clean and this package comes from src/ as it would from a vendored copy.
    return subprocess.run(
        [sys.executable, "-m", "beethoven_qa", "gate", "--root", str(root), *args],
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
    lines = result.stdout.splitlines()
    assert lines[-1] == "gate: no counterexample within 3 s per condition"
    match = CONDITIONS.match(lines[-2])  # the counts: the last line before the verdict
    assert match and match.groups() == ("1", "1", "0", "3", "0"), lines[-2]

    # The same function with a postcondition that is false for x > 0: CrossHair must find it.
    bad = good.replace("result >= x", "result == x")
    result = _gate(_repo(tmp_path / "bad", bad))
    assert result.returncode != 0, result.stdout + result.stderr
    assert "double" in result.stdout + result.stderr


def test_gate_proves_a_function_that_calls_a_contracted_one_many_times(tmp_path: Path) -> None:
    # icontract's bookkeeping, made inside CrossHair's first path, grew a frame deeper with every
    # contracted call until RecursionError (beethoven_qa._crosshair says how): 500 calls on one
    # path were enough, and this toy failed so before the gate made the set first.
    nested = """
        from beethoven_qa.contracts import ensure, require

        @require(lambda x: x >= 0)
        def one(x: int) -> int:
            return x

        @require(lambda n: 0 <= n <= 10)
        @ensure(lambda result, n: result == n + 500)
        def many(n: int) -> int:
            total = 0
            for _ in range(500):
                total += one(1)
            return n + total
    """
    result = _gate(_repo(tmp_path, nested))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "RecursionError" not in result.stdout + result.stderr
    assert "info: Confirmed over all paths." in result.stdout, result.stdout


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
        # A partial's own __module__ is functools' and it has no __name__: it counts where it is
        # bound, so a public one over package code is a public function without contracts.
        (
            {
                "logic.py": PROVEN,
                "part.py": "import functools\n\n\ndef _scale(k: int, x: int) -> int:\n"
                "    return k * x\n\n\ntriple = functools.partial(_scale, 3)\n",
            },
            "toy.part.triple",
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
        "partial",
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


NEVER = """
    from beethoven_qa.contracts import ensure, require

    @require(lambda x: x > 0 and x < 0)
    @ensure(lambda result: result > 0)
    def never(x: int) -> int:
        return x
"""

# A loop to a symbolic count: no counterexample and no proof within the bound.
SEARCHED = """
    from beethoven_qa.contracts import ensure

    @ensure(lambda result, n: result >= 0)
    def tri(n: int) -> int:
        return sum(i for i in range(n))
"""


def test_gate_one_module(tmp_path: Path) -> None:
    root = _toy(
        tmp_path,
        {
            "__init__.py": "",
            "logic.py": PROVEN,
            "empty.py": NEVER,
            "other.py": "def bare() -> None:\n    pass\n",
            "broken.py": "1 / 0\n",
        },
    )
    one = _gate(root, "--module", "toy.logic")
    assert one.returncode == 0, one.stdout + one.stderr  # toy.other's bare function is not in scope
    assert "info: Confirmed over all paths." in one.stdout, one.stdout
    never = _gate(root, "--module", "toy.empty")
    assert never.returncode != 0 and "vacuous" in never.stdout, never.stdout
    # --report-all is accepted and changes nothing: every run already reports every verdict.
    assert _gate(root, "--module", "toy.empty", "--report-all").stdout == never.stdout

    # exempt, missing, another package, an import that fails
    for outside in ("toy", "toy.nope", "other.logic", "toy.broken"):
        refused = _gate(root, "--module", outside)
        assert refused.returncode == 2, (outside, refused.stdout + refused.stderr)
        if outside == "toy.broken":  # and the reason, not only the refusal
            assert "ZeroDivisionError" in refused.stdout, refused.stdout


def _counted(target: str, confirmed: int, unconfirmed: int, unmet: int) -> str:
    return (
        f"gate: {target}: {confirmed} confirmed over all paths, {unconfirmed} not confirmed "
        f"within 3 s, {unmet} unable to meet a precondition"
    )


def test_gate_counts_every_verdict_and_fails_a_precondition_no_input_meets(
    tmp_path: Path,
) -> None:
    # With or without --report-all: each condition's verdict, the counts under each target right
    # after its exit line, the counts over the run as the last line, and a precondition no input
    # meets fails the gate. Before, only --report-all saw any verdict, and the plain gate the hook
    # and CI ran passed such a function in silence.
    files = {"__init__.py": "", "logic.py": PROVEN, "empty.py": NEVER, "loop.py": SEARCHED}
    root = _toy(tmp_path, files)
    for flags in ((), ("--report-all",)):
        result = _gate(root, *flags)
        assert result.returncode == 1, result.stdout + result.stderr
        lines = result.stdout.splitlines()
        assert lines.index("gate: toy.logic.double: exit 0") + 1 == lines.index(
            _counted("toy.logic.double", 1, 0, 0)
        )
        assert _counted("toy.empty.never", 0, 0, 1) in lines
        assert _counted("toy.loop.tri", 0, 1, 0) in lines
        assert sum(1 for line in lines if line.endswith(": info: Not confirmed.")) == 1
        assert "gate: toy.empty.never: a precondition no input can meet" in result.stdout
        assert "vacuous" in result.stdout and "CrossHair cannot build" in result.stdout
        match = CONDITIONS.match(lines[-1])
        assert match and match.groups() == ("1", "3", "1", "3", "1"), lines[-1]


# A test file carrying each way of keeping a test from running or from counting, as a decorator,
# as a call, by the full name, through an alias and through a from-import; and one string that
# only spells them, which is no finding.
HIDDEN_TESTS = """\
import unittest
from unittest import expectedFailure

import pytest
from pytest import mark as m

numpy = pytest.importorskip("numpy")


@pytest.mark.skip(reason="later")
def test_a() -> None:
    pass


@m.xfail
def test_b() -> None:
    pass


@unittest.skipIf(True, "no")
def test_c() -> None:
    pass


@expectedFailure
def test_d() -> None:
    pass


def test_e() -> None:
    pytest.skip("not here")


def test_f() -> None:
    text = "pytest.skip(x) @pytest.mark.skip # noqa # type: ignore # nosec"
    assert text


pytestmark = pytest.mark.skipif(False, reason="a mark bound to the module, no call, no decorator")


@pytest.mark.parametrize("v", [pytest.param(1, marks=pytest.mark.xfail)])
def test_g(v: int) -> None:
    assert v


class Case(unittest.TestCase):
    def test_h(self) -> None:
        self.skipTest("a method of the case, no import to resolve")

    @unittest.skip("x")
    def test_i(self) -> None:
        pass

    @unittest.skipUnless(False, "x")
    def test_j(self) -> None:
        pass


def test_k() -> None:
    raise unittest.SkipTest("raised, not called as a skip")


def test_l() -> None:
    pytest.xfail("x")
"""

# A module carrying each comment the standards' tools would read as a suppression, one string
# that only spells two of them, and one comment that mentions the word without being one.
COMMENTED = """\
from beethoven_qa.contracts import ensure, require


@require(lambda x: x >= 0)
@ensure(lambda result, x: result >= x)
def double(x: int) -> int:
    return 2 * x


a = 1  # noqa: E501
b = 2  #type:ignore[assignment]
c = 3  # nosec B101
d = 4  # pragma: no cover
# jscpd:ignore-start
# zizmor: ignore[template-injection]
# complexipy: ignore
e = 5  # ruff: noqa
f = 6  # mypy: ignore-errors
# flake8: noqa
# mypy: disable-error-code="assignment"
# isort: skip_file
# ruff: isort: skip_file
# fmt: off
#lizard forgive
h = 7  # this comment says GENERATED CODE mid-sentence, and lizard would read no further
i = 8  # lizard forgive, says this comment, and lizard forgives nothing for it
g = "# noqa and # type: ignore in a string"
# the standards ignore a noqa anyway, as this comment says without carrying one
"""


def _line(text: str, needle: str) -> int:
    return next(n for n, line in enumerate(text.splitlines(), 1) if needle in line)


def test_gate_refuses_suppressions_and_skipped_tests(tmp_path: Path) -> None:
    root = _toy(tmp_path, {"__init__.py": "", "logic.py": COMMENTED})
    (root / "tests").mkdir()
    (root / "tests" / "test_hidden.py").write_text(HIDDEN_TESTS, encoding="utf-8")
    # A consumer's pinned copy is never read by this check; vendor-check guards it.
    copy = root / "vendor" / "beethoven-qa" / "src" / "beethoven_qa"
    copy.mkdir(parents=True)
    (copy / "x.py").write_text("import pytest\n\nx = 1  # noqa\npytest.skip('a')\n", "utf-8")
    result = _gate(root)
    assert result.returncode == 1, result.stdout + result.stderr
    out = result.stdout
    why = "no comment sets a finding aside; fix what it hides"
    for comment in (
        "# noqa: E501",
        "#type:ignore[assignment]",
        "# nosec B101",
        "# pragma: no cover",
        "# jscpd:ignore-start",
        "# zizmor: ignore[template-injection]",
        "# complexipy: ignore",
        "# ruff: noqa",
        "# mypy: ignore-errors",
        "# flake8: noqa",
        '# mypy: disable-error-code="assignment"',
        "# isort: skip_file",
        "# ruff: isort: skip_file",
        "# fmt: off",
        "#lizard forgive",
        "# this comment says GENERATED CODE mid-sentence, and lizard would read no further",
    ):
        line = _line(COMMENTED, comment)
        assert f"gate: suppression src/toy/logic.py:{line}: {comment}: {why}" in out, out
    assert "in a string" not in out and "without carrying one" not in out, out
    assert "forgives nothing" not in out, out
    for name, needle in (
        ("pytest.importorskip", "importorskip"),
        ("pytest.mark.skip", "@pytest.mark.skip"),
        ("m.xfail", "@m.xfail"),
        ("unittest.skipIf", "@unittest.skipIf"),
        ("expectedFailure", "@expectedFailure"),
        ("pytest.skip", "pytest.skip("),
        ("pytest.mark.skipif", "pytestmark ="),
        ("pytest.mark.xfail", "marks=pytest.mark.xfail"),
        ("self.skipTest", "self.skipTest("),
        ("unittest.skip", "@unittest.skip("),
        ("unittest.skipUnless", "@unittest.skipUnless("),
        ("unittest.SkipTest", "raise unittest.SkipTest("),
        ("pytest.xfail", "pytest.xfail("),
    ):
        line = _line(HIDDEN_TESTS, needle)
        assert f"gate: skip tests/test_hidden.py:{line}: {name}: a test is never skipped" in out
    assert out.count("gate: skip ") == 13 and out.count("gate: suppression ") == 16, out
    assert "vendor" not in out, out
    assert "gate: instead of a suppression:" in out

    # Under --module only that module's own file is read, never the tests directory.
    root = _toy(tmp_path / "module", {"__init__.py": "", "logic.py": PROVEN})
    (root / "tests").mkdir()
    (root / "tests" / "test_hidden.py").write_text(HIDDEN_TESTS, encoding="utf-8")
    alone = _gate(root, "--module", "toy.logic")
    assert alone.returncode == 0 and "skip " not in alone.stdout, alone.stdout + alone.stderr
    whole = _gate(root)
    assert whole.returncode == 1 and whole.stdout.count("gate: skip ") == 13, whole.stdout


def test_this_repository_hides_nothing() -> None:
    # The gate's own package and tests carry no directive, no suppression comment and no skipped
    # test: what the check refuses in a consumer, this repository is held to first.
    config = GateConfig.from_pyproject(SRC.parent)
    assert gate._hidden(gate._checked_files(SRC.parent, config, [], None), SRC.parent) == []


def test_gate_refuses_a_stub_and_reads_where_pytest_collects(tmp_path: Path) -> None:
    # A .pyi beside a module is what mypy checks in its place (measured: a module returning a str
    # from an int function passed `mypy --strict` under a stub of the right signature), so the
    # gate refuses it, under --module too. The skip check reads the directories
    # [tool.pytest.ini_options].testpaths names and a conftest.py at the root, not "tests" by
    # name, since pytest collects there.
    root = _toy(
        tmp_path,
        {"__init__.py": "", "logic.py": PROVEN, "logic.pyi": "def double(x: int) -> int: ...\n"},
        extra='\n[tool.pytest.ini_options]\ntestpaths = ["checks"]\n',
    )
    (root / "checks").mkdir()
    (root / "checks" / "test_hidden.py").write_text(HIDDEN_TESTS, encoding="utf-8")
    (root / "conftest.py").write_text(
        "import pytest\n\n\ndef pytest_collection_modifyitems(items):\n"
        "    for item in items:\n        item.add_marker(pytest.mark.skip)\n",
        encoding="utf-8",
    )
    result = _gate(root)
    assert result.returncode == 1, result.stdout + result.stderr
    out = result.stdout
    assert f"gate: stub src/toy/logic.pyi: {gate._NO_STUB}" in out, out
    assert out.count("gate: skip checks/test_hidden.py:") == 13, out
    assert "gate: skip conftest.py:6: pytest.mark.skip: " in out, out
    assert "gate: instead of a suppression:" in out
    alone = _gate(root, "--module", "toy.logic")
    assert alone.returncode == 1 and "gate: stub src/toy/logic.pyi: " in alone.stdout, alone.stdout
    assert "skip " not in alone.stdout, alone.stdout


def test_hidden_names_each_kind_once_and_a_file_it_cannot_read(tmp_path: Path) -> None:
    # Each finding carries its kind, file and line, the way out follows once per kind, and a file
    # that is not Python is reported rather than passed over. A name a relative import brings in
    # is never read as pytest's or unittest's, while an aliased import is read as what it is.
    tests = tmp_path / "tests"
    tests.mkdir()
    hidden = tests / "test_hidden.py"
    hidden.write_text(
        "import pytest as pt\nfrom . import skip\n\n\ndef test_x() -> None:  # crosshair: off\n"
        "    skip()\n    pt.skip('x')  # noqa\n",
        "utf-8",
    )
    broken = tests / "broken.py"
    broken.write_text("def (:\n", "utf-8")
    found = gate._hidden([hidden, broken], tmp_path)
    assert found[:3] == [
        "directive tests/test_hidden.py:5: # crosshair: off",
        f"suppression tests/test_hidden.py:7: # noqa: {gate._NO_SUPPRESSION}",
        f"skip tests/test_hidden.py:7: pt.skip: {gate._NO_SKIP}",
    ]
    assert found[3].startswith("tests/broken.py: cannot be read: ")
    assert found[4:] == [gate._INSTEAD_OF_A_DIRECTIVE, gate._INSTEAD_OF_A_SUPPRESSION]
    assert gate._hidden([], tmp_path) == []


def test_imports_resolve_each_form_and_no_relative_one() -> None:
    # `import a.b` binds the package `a`, `import a.b as c` binds `c` to the module, a from-import
    # binds the name it brings in to its dotted origin, and a relative import binds nothing here.
    tree = ast.parse(
        "import a.b\nimport a.b as c\nfrom m import x as y, z\nfrom . import q\nfrom .n import r\n"
    )
    assert gate._imports(tree) == {"a": "a", "c": "a.b", "y": "m.x", "z": "m.z"}


def test_checked_files_under_module_are_that_modules_own() -> None:
    # Under --module the files read for what would hide a finding are that module's and no other.
    config = GateConfig.from_pyproject(SRC.parent)
    assert gate._checked_files(SRC.parent, config, [gate], "beethoven_qa.gate") == [
        Path(str(gate.__file__))
    ]


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


STORE = """
    from beethoven_qa.contracts import ensure


    class Store:
        def __init__(self, n: int) -> None:
            self.n = n

        @ensure(lambda self, result: result == self.n + 1)
        def bump(self) -> int:
            return self.n + 1
"""


def test_gate_refuses_an_unproven_entry_that_names_a_class_or_a_method(tmp_path: Path) -> None:
    # A class is proven whole: an entry naming it would take every method and invariant out of the
    # proof, and one naming a method matches nothing, since methods are never targets.
    false = STORE.replace("return self.n + 1", "return self.n")  # bump's postcondition fails
    files = {"__init__.py": "", "logic.py": PROVEN, "store.py": false}
    table = "[tool.beethoven-qa.unproven]\n"
    whole = _gate(_toy(tmp_path / "class", files, extra=table + '"toy.store.Store" = "reads"\n'))
    assert whole.returncode == 1, whole.stdout + whole.stderr
    assert "toy.store.Store names a class" in whole.stdout and "proven whole" in whole.stdout
    assert "false when calling bump" in whole.stdout, whole.stdout  # the class was still proven

    entry = '"toy.store.Store.bump" = "reads"\n'
    method = _gate(_toy(tmp_path / "method", files, extra=table + entry))
    assert method.returncode == 1 and "toy.store.Store.bump names a class" in method.stdout
    assert "names no contracted function" not in method.stdout, method.stdout

    # Under --module the entry belongs to its module and is reported there, never dropped; so is
    # an entry under the module that names nothing at all.
    root = _toy(tmp_path / "scoped", {**files, "store.py": STORE}, extra=table + entry)
    scoped = _gate(root, "--module", "toy.store")
    assert scoped.returncode == 1 and "toy.store.Store.bump names a class" in scoped.stdout
    stale = '"toy.logic.gone.read" = "renamed since"\n'
    root = _toy(tmp_path / "stale", {**files, "store.py": STORE}, extra=table + stale)
    scoped = _gate(root, "--module", "toy.logic")
    assert scoped.returncode == 1 and "toy.logic.gone.read names no contracted" in scoped.stdout


def test_gate_keeps_a_submodule_entry_out_of_its_package_module(tmp_path: Path) -> None:
    # An entry for toy.sub.mod belongs to that module: the gate on toy.sub (its __init__) neither
    # reports it as stale nor sets it aside there, and the gate on toy.sub.mod honours it.
    read = """
        from beethoven_qa.contracts import ensure

        @ensure(lambda result, x: result != 7)
        def read(x: int) -> int:
            return x
    """
    files = {"__init__.py": "", "sub/__init__.py": PROVEN, "sub/mod.py": read}
    table = '[tool.beethoven-qa.unproven]\n"toy.sub.mod.read" = "reads the clock"\n'
    root = _toy(tmp_path, files, extra=table)
    package = _gate(root, "--module", "toy.sub")
    assert package.returncode == 0, package.stdout + package.stderr
    assert "toy.sub.mod.read" not in package.stdout, package.stdout
    module = _gate(root, "--module", "toy.sub.mod")
    assert module.returncode == 1 and "nothing to prove in toy.sub.mod" in module.stdout
    assert "toy.sub.mod.read: reads the clock" in module.stdout, module.stdout


def test_gate_keeps_an_entry_with_a_submodule_that_failed_to_import(tmp_path: Path) -> None:
    # The same, when toy.sub.broken fails to import: it is then among no loaded module, but its
    # entry is still its own, not toy.sub's. The gate on toy.sub must not report the entry as
    # stale (a wrong-but-plausible verdict that hides the import failure behind "remove the
    # entry"); the gate on toy.sub.broken says why it is not a checked module; the whole run
    # reports the failure, and the entry beside it, since the module it names holds nothing yet.
    files = {"__init__.py": "", "sub/__init__.py": PROVEN, "sub/broken.py": "1 / 0\n"}
    table = '[tool.beethoven-qa.unproven]\n"toy.sub.broken.read" = "reads the clock"\n'
    root = _toy(tmp_path, files, extra=table)
    package = _gate(root, "--module", "toy.sub")
    assert package.returncode == 0, package.stdout + package.stderr
    assert "toy.sub.broken.read" not in package.stdout, package.stdout
    module = _gate(root, "--module", "toy.sub.broken")
    assert module.returncode == 2 and "ZeroDivisionError" in module.stdout, module.stdout
    whole = _gate(root)
    assert whole.returncode == 1, whole.stdout + whole.stderr
    assert "importing toy.sub.broken failed: ZeroDivisionError" in whole.stdout, whole.stdout
    assert "toy.sub.broken.read names no contracted" in whole.stdout, whole.stdout


def test_a_proven_caller_runs_a_listed_function_and_assumes_its_contracts(tmp_path: Path) -> None:
    # What listing does and does not do, pinned so the documents can say it: the gate never checks
    # a listed function as a target, but CrossHair runs its body while proving a contracted caller
    # (the I/O happens during the gate) and takes its contracts as given (a false one is never
    # found through the caller; at run time it is the listed function's own check that fires).
    clock = """
        from beethoven_qa.contracts import ensure

        @ensure(lambda result: result > 0)
        def now(seed: int) -> int:
            return seed

        @ensure(lambda result, seed: result > 1)
        def later(seed: int) -> int:
            return now(seed) + 1
    """
    table = '[tool.beethoven-qa.unproven]\n"toy.clock.now" = "reads the clock"\n'
    root = _toy(tmp_path / "clock", {"__init__.py": "", "clock.py": clock}, extra=table)
    assumed = _gate(root)
    assert assumed.returncode == 0, assumed.stdout + assumed.stderr
    assert "toy.clock.now: reads the clock" in assumed.stdout
    fired = subprocess.run(
        [sys.executable, "-c", "from toy.clock import later\nlater(0)\n"],
        env={"PYTHONPATH": f"{root / 'src'}{os.pathsep}{SRC}", "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert fired.returncode != 0 and "ViolationError" in fired.stderr, fired.stderr
    assert "result > 0" in fired.stderr, fired.stderr  # now's condition, not later's

    store = """
        from pathlib import Path

        from beethoven_qa.contracts import ensure

        @ensure(lambda result: isinstance(result, str))
        def load() -> str:
            text = Path(__file__).with_name("accounts.txt").read_text(encoding="utf-8")
            if "boom" in text:
                raise RuntimeError("load ran during the gate")
            return text

        @ensure(lambda result, x: len(result) <= 1)
        def first(x: int) -> str:
            return load()[:1]
    """
    files = {"__init__.py": "", "store.py": store, "accounts.txt": "boom\n"}
    table = '[tool.beethoven-qa.unproven]\n"toy.store.load" = "reads the account list"\n'
    ran = _gate(_toy(tmp_path / "store", files, extra=table))
    assert ran.returncode != 0 and "load ran during the gate" in ran.stdout, ran.stdout
    assert "toy.store.load: reads the account list" in ran.stdout


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


# The narrowing (--base, beethoven_qa.scope): a throwaway repository that is a real git checkout,
# its package's modules each with one proven function, so what a run proves is what it counts.
TRIPLE = PROVEN.replace("double", "triple").replace("2 * x", "3 * x")
USES = """
    from beethoven_qa.contracts import ensure, require
    from toy.logic import double

    @require(lambda x: x >= 0)
    @ensure(lambda result, x: result >= x)
    def quadruple(x: int) -> int:
        return double(double(x))
"""


def _git_in(root: Path, *args: str) -> str:
    # Hermetic: none of the person's configuration, a fixed author, no repository named for git.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
    for who in ("AUTHOR", "COMMITTER"):
        env.update({f"GIT_{who}_NAME": "test", f"GIT_{who}_EMAIL": "test@example.invalid"})
    done = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True, env=env
    )
    return done.stdout.strip()


def _checkout(tmp_path: Path) -> tuple[Path, str]:
    # The toy package as a commit: logic (double), other (triple), uses (quadruple, over double).
    root = _toy(
        tmp_path,
        {"__init__.py": "", "logic.py": PROVEN, "other.py": TRIPLE, "uses.py": USES},
    )
    _git_in(root, "init", "-q", "-b", "main")
    _git_in(root, "add", "-A")
    _git_in(root, "commit", "-q", "-m", "base")
    return root, _git_in(root, "rev-parse", "HEAD")


def _gate_with(root: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "beethoven_qa", "gate", "--root", str(root), *args],
        cwd=root,
        env={"PYTHONPATH": str(SRC), "PATH": "/usr/bin:/bin", **env},
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )


def test_a_base_proves_only_the_modules_the_change_reaches(tmp_path: Path) -> None:
    root, base = _checkout(tmp_path)
    logic = root / "src" / "toy" / "logic.py"
    logic.write_text(logic.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
    ran = _gate_with(root, "--base", base)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert (
        f"gate: proof: 2 of 4 modules, those the change since {base[:12]} reaches: "
        "toy.logic, toy.uses" in ran.stdout
    ), ran.stdout
    assert "over toy: 2 of 3 (those in the modules the change reaches) contracted" in ran.stdout
    assert "gate: toy.other.triple: exit" not in ran.stdout  # not reached, not proven
    assert "gate: toy.uses.quadruple: exit 0" in ran.stdout  # it imports what changed
    # The same from the environment, which is how the pre-push hook asks for it.
    assert _gate_with(root, BEETHOVEN_QA_BASE=base).stdout == ran.stdout


def test_a_base_proves_every_module_or_none_as_the_change_says(tmp_path: Path) -> None:
    root, base = _checkout(tmp_path)
    nothing = _gate_with(root, "--base", base)
    assert nothing.returncode == 0, nothing.stdout + nothing.stderr
    assert "0 of 4 modules, those the change since" in nothing.stdout
    assert "reaches: none" in nothing.stdout
    assert CONDITIONS.match(nothing.stdout.splitlines()[-2]), nothing.stdout
    (root / "docs").mkdir()
    (root / "docs" / "notes.md").write_text("a note\n", encoding="utf-8")
    assert "reaches: none" in _gate_with(root, "--base", base).stdout
    # The gate's table is read by every proof, so a change to it proves every module.
    pyproject = root / "pyproject.toml"
    pyproject.write_text(pyproject.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")
    every = _gate_with(root, "--base", base)
    assert every.returncode == 0, every.stdout + every.stderr
    assert "gate: proof: every module: pyproject.toml changed since" in every.stdout
    assert "over toy: 3 contracted functions and classes" in every.stdout


def test_without_a_base_or_under_module_the_proof_is_whole(tmp_path: Path) -> None:
    root, base = _checkout(tmp_path)
    plain = _gate_with(root)
    assert plain.returncode == 0 and "gate: proof:" not in plain.stdout, plain.stdout
    assert "over toy: 3 contracted functions and classes" in plain.stdout
    assert _gate_with(root, "--base", "none", BEETHOVEN_QA_BASE=base).stdout == plain.stdout
    one = _gate_with(root, "--module", "toy.other", "--base", base)
    assert one.returncode == 0 and "gate: proof:" not in one.stdout, one.stdout
    assert "gate: toy.other.triple: exit 0" in one.stdout
    # A base that cannot be told proves every module and says why.
    unknown = _gate_with(root, "--base", "no-such-ref")
    assert unknown.returncode == 0, unknown.stdout + unknown.stderr
    assert "gate: proof: every module, as what changed since no-such-ref cannot be told" in (
        unknown.stdout
    )
    assert "over toy: 3 contracted functions and classes" in unknown.stdout


def test_the_narrowing_in_process(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # The same decisions as above, made in this process, where the coverage of new lines counts.
    root, base = _checkout(tmp_path)
    root = root.resolve()
    config = GateConfig.from_pyproject(root)
    assert gate._reach(root, config, "none") == (None, "")
    other = root / "src" / "toy" / "other.py"
    other.write_text(other.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
    modules, why = gate._reach(root, config, base)
    assert modules == {"toy.other"} and why.startswith("1 of 4 modules"), why
    targets = ["toy.logic.double", "toy.other.triple", "toy.uses.quadruple"]
    assert gate._chosen(root, config, targets, base) == ["toy.other.triple"]
    assert capsys.readouterr().out.startswith("gate: proof: 1 of 4 modules")
    assert gate._how_many(["toy.other.triple"], targets) == (
        "1 of 3 (those in the modules the change reaches)"
    )
    assert gate._how_many(targets, targets) == "3"
    assert gate._chosen(root, config, targets, "none") == targets
    assert capsys.readouterr().out == ""
    unknown, why = gate._reach(root, config, "no-such-ref")
    assert unknown is None and "cannot be told" in why
    # A gate root inside a checkout but not its top level: git would give paths from elsewhere.
    inner = root / "src"
    assert gate._reach(inner, config, base)[1].endswith("is not the top level of a git repository")


def test_every_proof_reads_the_root_files_and_the_gates_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = SRC.parent.resolve()
    own = gate._everywhere(repo, GateConfig.from_pyproject(repo))
    assert ROOT_FILES <= own
    for name in ("__init__", "contracts", "gate", "scope", "_crosshair"):
        assert f"src/beethoven_qa/{name}.py" in own, name
    # A module of the package checked is reached through what imports it, not by every proof.
    assert "src/beethoven_qa/vendor.py" not in own
    # A module the process imported from beside the package counts, wherever it sits.
    root = _toy(tmp_path, {"__init__.py": "", "logic.py": PROVEN}).resolve()
    helper = types.ModuleType("helper_beside_the_package")
    helper.__file__ = str(root / "tools" / "helper.py")
    monkeypatch.setitem(sys.modules, helper.__name__, helper)
    beside = gate._everywhere(root, GateConfig.from_pyproject(root))
    assert "tools/helper.py" in beside
    assert not any(path.startswith("src/toy/") for path in beside)


def test_prove_starts_one_process_per_target(tmp_path: Path) -> None:
    root = _toy(tmp_path, {"__init__.py": "", "logic.py": PROVEN}).resolve()
    config = GateConfig.from_pyproject(root)
    assert gate._prove(root, config, [], 1) == []
    (done,) = gate._prove(root, config, ["toy.logic.double"], 1)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "info: Confirmed over all paths." in done.stdout
