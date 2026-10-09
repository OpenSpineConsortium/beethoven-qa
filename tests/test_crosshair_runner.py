"""The runner the gate starts CrossHair through (``beethoven_qa._crosshair``): it makes
icontract's bookkeeping set before CrossHair traces anything, hands CrossHair its arguments
unchanged, and leaves the contracts of a process that only imports it switched on. What goes
wrong without the set is pinned end to end in test_gate.py."""

from __future__ import annotations

import contextvars
import subprocess
import sys
from pathlib import Path

import icontract._checkers
import pytest

from beethoven_qa import _crosshair

SRC = Path(__file__).resolve().parent.parent / "src"

# The three functions CrossHair's import replaces with ones that check nothing.
ASSERTIONS = ("_assert_invariant", "_assert_preconditions", "_assert_postconditions")


def _made_in_a_new_process() -> object:
    # A context with nothing in it, as a new CrossHair process has before its first contracted call.
    assert icontract._checkers._IN_PROGRESS.get() is None
    _crosshair._bookkeeping_before_crosshair()
    made = icontract._checkers._IN_PROGRESS.get()
    _crosshair._bookkeeping_before_crosshair()
    assert icontract._checkers._IN_PROGRESS.get() is made  # one set, never replaced
    return made


def test_the_set_is_the_builtin_and_made_once() -> None:
    made = contextvars.Context().run(_made_in_a_new_process)
    assert type(made) is set and len(made) == 0


def test_the_runner_hands_crosshair_its_arguments(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Starting CrossHair here imports it into this process, which switches icontract's checks off;
    # the monkeypatch puts the three functions back when the test ends, so every later test runs
    # with its contracts. --help ends in argparse, before CrossHair engages its audit hooks.
    for name in ASSERTIONS:
        monkeypatch.setattr(icontract._checkers, name, getattr(icontract._checkers, name))
    here = icontract._checkers._IN_PROGRESS.get()
    with pytest.raises(SystemExit) as stopped:
        contextvars.Context().run(_crosshair._start, ["--help"])
    assert stopped.value.code == 0
    assert "check" in capsys.readouterr().out
    assert icontract._checkers._IN_PROGRESS.get() is here  # made in the run's context only


def test_importing_the_runner_keeps_the_contracts_on() -> None:
    # In a fresh interpreter, as the gate's first check imports every module of the package.
    probe = (
        "import beethoven_qa._crosshair\n"
        "from beethoven_qa.contracts import ViolationError\n"
        "from beethoven_qa.gate import crosshair_command\n"
        "try:\n"
        "    crosshair_command('python', 'target', 0)\n"
        "except ViolationError:\n"
        "    raise SystemExit(0)\n"
        "raise SystemExit('a precondition went unchecked')\n"
    )
    ran = subprocess.run(
        [sys.executable, "-c", probe],
        env={"PYTHONPATH": str(SRC), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr
