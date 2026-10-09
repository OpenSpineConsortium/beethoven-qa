"""The CI steps that name a failed lane in GitHub annotations, and their script.

GitHub's API serves a check run's annotations to anyone who can read the check, where the job's
log and artifact sit behind a second download from a host a session may not reach, so what
tools/name_failures.sh prints is the only account of a failure some readers get. Each test runs
the real script, or the workflow's real step around it, on a lane's output and reads back the
workflow commands it printed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from test_hooks import CI, _step_command

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "tools" / "name_failures.sh"

GATE_OUTPUT = """\
gate: CrossHair + Z3 over pkg: 4 contracted functions and classes, 2 at a time, 20 s per condition
gate: not proven, contracts checked at run time only: p.load: reads a file
gate: p.a: exit 0
gate: p.a: 1 confirmed over all paths, 0 not confirmed within 20 s, 0 unable to meet a precondition
/src/p.py:3: info: Confirmed over all paths.
gate: p.b: exit 1
gate: p.b: 0 confirmed over all paths, 1 not confirmed within 20 s, 0 unable to meet a precondition
/src/p.py:9: info: Not confirmed.
/src/p.py:12: error: false when calling b(s='50%, a: b') (which returns 2)
gate: p.c: exit 1
gate: p.c: 0 confirmed over all paths, 0 not confirmed within 20 s, 0 unable to meet a precondition
Traceback (most recent call last):
RuntimeError: inside the tool
gate: p.d: exit 0
gate: p.d: 0 confirmed over all paths, 0 not confirmed within 20 s, 1 unable to meet a precondition
/src/p.py:20: info: Unable to meet precondition.
gate: p.d: a precondition no input can meet: either the contract is vacuous, or it is stated
gate: conditions: 1 of 3 confirmed over all paths, 1 not confirmed within 20 s per condition, \
1 unable to meet a precondition
"""


def _decode(text: str) -> str:
    # GitHub's own unescaping of a workflow command's message, in its order: %25 last.
    return text.replace("%0D", "\r").replace("%0A", "\n").replace("%25", "%")


def _commands(stdout: str) -> list[tuple[str, str]]:
    # Every line is one `::error title=<title>::<message>` command; the title and the message.
    found = []
    for line in stdout.splitlines():
        assert line.startswith("::error title="), line
        title, _, message = line.removeprefix("::error title=").partition("::")
        found.append((title, _decode(message)))
    return found


def _name(tmp_path: Path, title: str, text: str | None) -> list[tuple[str, str]]:
    lane = tmp_path / "lane.txt"
    if text is not None:
        lane.write_text(text)
    done = subprocess.run(
        ["bash", str(SCRIPT), title, str(lane)], capture_output=True, text=True, check=True
    )
    assert done.stderr == ""
    return _commands(done.stdout)


def test_each_failed_target_is_named_with_its_error_lines(tmp_path: Path) -> None:
    named = _name(tmp_path, "symbolic", GATE_OUTPUT)
    assert [title for title, _ in named] == ["symbolic"] * 3
    b, c, summary = (message for _, message in named)
    # The counterexample, and none of the verdicts CrossHair printed beside it.
    assert b.splitlines() == [
        "gate: p.b: exit 1",
        "/src/p.py:12: error: false when calling b(s='50%, a: b') (which returns 2)",
    ]
    # A process that printed no error line is named by its last lines.
    assert c.splitlines() == [
        "gate: p.c: exit 1",
        "(no error line; its last lines)",
        "Traceback (most recent call last):",
        "RuntimeError: inside the tool",
    ]
    # The runner, every failed target, what the gate refused and its count; never a target that
    # passed, a per-target count or the list of what is set aside.
    lines = summary.splitlines()
    assert lines[0].startswith("the runner: ") and " cores, " in lines[0]
    assert lines[1:] == [
        "gate: p.b: exit 1",
        "gate: p.c: exit 1",
        "gate: p.d: a precondition no input can meet: either the contract is vacuous, or it is "
        "stated",
        "gate: conditions: 1 of 3 confirmed over all paths, 1 not confirmed within 20 s per "
        "condition, 1 unable to meet a precondition",
    ]


def test_past_the_first_four_failed_targets_the_summary_still_names_every_one(
    tmp_path: Path,
) -> None:
    # GitHub keeps ten error annotations a step and the report job names several lanes in one, so
    # only the first four targets get an annotation of their own; nothing is dropped unsaid.
    text = "".join(
        f"gate: p.f{i}: exit 1\n"
        f"gate: p.f{i}: 0 confirmed over all paths, 0 not confirmed within 20 s, 0 unable\n"
        f"/src/p.py:{i}: error: false when calling f{i}()\n"
        for i in range(6)
    )
    named = _name(tmp_path, "symbolic", text)
    assert [message.splitlines()[0] for _, message in named[:-1]] == [
        f"gate: p.f{i}: exit 1" for i in range(4)
    ]
    summary = named[-1][1].splitlines()
    assert "the first 4 of 6 failed targets have an annotation of their own" in summary[1]
    assert summary[2:] == [f"gate: p.f{i}: exit 1" for i in range(6)]


def test_a_long_run_of_error_lines_is_cut_and_says_so(tmp_path: Path) -> None:
    # More than a pipe holds (200 lines of 1000 characters once cut): the script once cut them
    # with head on a pipe and died of the broken pipe, at random with 25 lines on GitHub's runner
    # and every time with these.
    errors = "".join(f"/src/p.py:{i}: error: {'x' * 2000}\n" for i in range(200))
    named = _name(tmp_path, "symbolic", f"gate: p.g: exit 1\n{errors}")
    lines = named[0][1].splitlines()
    assert len(lines) == 1 + 20 + 1
    assert all(len(line) == 1000 for line in lines[1:21])
    assert lines[-1] == "and 180 more error lines, in the job's log"


def test_another_lane_is_named_by_the_lines_that_say_why_and_its_last_lines(
    tmp_path: Path,
) -> None:
    text = "checking\n" + "".join(f"ok {i}\n" for i in range(20)) + "src/a.py:1: error: no\n"
    ((title, message),) = _name(tmp_path, "report lane typecheck exited 1, 100%: here", text)
    # A property's value escapes what would end it: the comma, the colon and the percent sign.
    assert title == "report lane typecheck exited 1%2C 100%25%3A here"
    lines = message.splitlines()
    assert lines[1] == "src/a.py:1: error: no"
    assert lines[2] == "--- the last lines:"
    assert lines[3:] == [f"ok {i}" for i in range(9, 20)] + ["src/a.py:1: error: no"]


def test_a_lane_that_printed_nothing_is_named_too(tmp_path: Path) -> None:
    for text in (None, ""):
        ((_, message),) = _name(tmp_path, "symbolic", text)
        assert message.startswith("the lane printed nothing")


LANES = (
    ("lint", "lint"),
    ("typecheck", "typecheck"),
    ("tests (typeguard enclave)", "test"),
    ("boundary (the interface standard's rules)", "boundary"),
    ("symbolic verification (CrossHair + Z3)", "symbolic"),
)


def _stub_pixi(bin_dir: Path) -> None:
    # Stands in for `pixi run --locked <task>`: prints what the task's file holds and exits with
    # the code STUB_CODE names, as the lane did.
    bin_dir.mkdir()
    pixi = bin_dir / "pixi"
    pixi.write_text(f'#!/bin/bash\ncat "{bin_dir}/$3.out"\nexit "$STUB_CODE"\n')
    pixi.chmod(0o755)


def _run_step(name: str, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    # As GitHub runs a `run:` on ubuntu: bash -e, the step's text as its script.
    return subprocess.run(
        ["bash", "-e", "-c", _step_command(CI, name)],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )


def test_each_gate_lane_keeps_its_exit_code_and_its_output(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    _stub_pixi(bin_dir)
    for step, lane in LANES:
        (bin_dir / f"{lane}.out").write_text(f"what {lane} printed\n")
        for code in ("0", "1"):
            env = {
                **os.environ,
                "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                "RUNNER_TEMP": str(tmp_path),
                "STUB_CODE": code,
            }
            ran = _run_step(step, REPO, env)
            # The tee in between never turns a failed lane green, nor a green one red.
            assert str(ran.returncode) == code, (step, ran.stderr)
            assert (tmp_path / f"{lane}.txt").read_text() == f"what {lane} printed\n"


def test_the_gate_job_names_each_failed_lane_and_no_other(tmp_path: Path) -> None:
    (tmp_path / "test.txt").write_text("..F\nFAILED tests/test_x.py::test_y - AssertionError\n")
    (tmp_path / "symbolic.txt").write_text(GATE_OUTPUT)
    (tmp_path / "lint.txt").write_text("All checks passed!\n")
    outcomes = {"LINT": "success", "TYPECHECK": "success", "TEST": "failure"}
    outcomes |= {"BOUNDARY": "skipped", "SYMBOLIC": "failure"}
    env = {**os.environ, "RUNNER_TEMP": str(tmp_path), **outcomes}
    named = _run_step("name the failed lane", REPO, env)
    assert named.returncode == 0, named.stderr
    found = _commands(named.stdout)
    assert [title for title, _ in found] == ["test"] + ["symbolic"] * 3
    assert "FAILED tests/test_x.py::test_y - AssertionError" in found[0][1].splitlines()
    assert found[1][1].splitlines()[0] == "gate: p.b: exit 1"
    # Every lane's step keeps its output where this step reads it, by the same name.
    text = CI.read_text()
    for _, lane in LANES:
        assert f'"$RUNNER_TEMP/{lane}.txt"' in text
        assert f"{lane.upper()}: ${{{{ steps.{lane}.outcome }}}}" in text


def test_the_report_job_names_each_failed_lane(tmp_path: Path) -> None:
    (tmp_path / "tools").mkdir()
    shutil.copy(SCRIPT, tmp_path / "tools" / SCRIPT.name)
    report = tmp_path / "report"
    report.mkdir()
    (report / "symbolic.txt").write_text(GATE_OUTPUT)
    (report / "lint.txt").write_text("all good\n")
    (report / "typecheck.txt").write_text("src/a.py:1: error: no\nFound 1 error\n")
    lanes = [
        {"task": "lint", "exit": 0, "output": "lint.txt"},
        {"task": "typecheck", "exit": 1, "output": "typecheck.txt"},
        {"task": "symbolic", "exit": 1, "output": "symbolic.txt"},
    ]
    (report / "report.json").write_text(json.dumps({"lanes": lanes}))
    named = _run_step("name the failed lanes", tmp_path, dict(os.environ))
    assert named.returncode == 0, named.stderr
    titles = [title for title, _ in _commands(named.stdout)]
    assert titles == ["report lane typecheck exited 1"] + ["report lane symbolic exited 1"] * 3
