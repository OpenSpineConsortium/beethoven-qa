"""The evidence report: its readers on real and crafted inputs, and the command end to end on a
throwaway repository whose lanes are stub shell commands.

CrossHair proves the contracted functions within their bound. These tests pin what a reader of a
report checks first: the gate's three line shapes are read back and nothing else is, a SARIF log
the real writer made is counted, the lock's file names give the versions pixi pinned, the hook's
command and the branch rule's checks are read from the real files, and the command records every
argv verbatim, writes both files and exits as the lanes did.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

from beethoven_qa import __version__
from beethoven_qa.__main__ import main
from beethoven_qa.contracts import ViolationError
from beethoven_qa.report import (
    LANES,
    NOT_IN_LOCK,
    PINNED,
    SECTIONS,
    VENDOR_LANE,
    Enforcement,
    Evidence,
    GateSummary,
    GroupSummary,
    Lane,
    Repository,
    Target,
    Tool,
    Totals,
    _groups,
    _lane_tasks,
    _lock_entries,
    _ruleset,
    _run,
    _tasks,
    _workflows,
    conda_name_version,
    count_by,
    deciders,
    exit_code,
    gate_summary,
    group_verdicts,
    hook_command,
    pinned,
    render,
    split_commands,
    strip_credentials,
    table,
)
from beethoven_qa.standards.findings import Finding
from beethoven_qa.standards.sarif import Rule, Run, sarif_log

REPO = Path(__file__).resolve().parent.parent

COUNTS = (
    "gate: conditions: 5 of 6 confirmed over all paths, 1 not confirmed within 20 s per "
    "condition, 0 unable to meet a precondition"
)
TARGET_F = (
    "gate: toy.logic.f: 3 confirmed over all paths, 1 not confirmed within 20 s, 0 unable to meet "
    "a precondition"
)
TARGET_G = (
    "gate: toy.logic.g: 2 confirmed over all paths, 0 not confirmed within 20 s, 0 unable to meet "
    "a precondition"
)
UNPROVEN = "gate: not proven, contracts checked at run time only: toy.io.read: reads a file"
GATE_OUTPUT = (
    "gate: CrossHair + Z3 over toy: 2 contracted functions and classes, 4 at a time, 20 s per "
    "condition",
    UNPROVEN,
    TARGET_F,
    TARGET_G,
    COUNTS,
    "gate: no counterexample within 20 s per condition",
)


# --- parsing -------------------------------------------------------------------------------------


def test_split_commands_runs_each_piece_of_a_task_on_its_own() -> None:
    assert split_commands("ruff check src tests && ruff format --check src tests") == (
        "ruff check src tests",
        "ruff format --check src tests",
    )
    assert split_commands("mypy src") == ("mypy src",)
    assert split_commands(" && ") == ()


def test_gate_summary_reads_the_three_line_shapes_and_nothing_else() -> None:
    summary = gate_summary(GATE_OUTPUT)
    assert summary.totals == Totals(5, 6, 1, 20, 0)
    assert summary.targets == (
        Target("toy.logic.f", 3, 1, 20, 0),
        Target("toy.logic.g", 2, 0, 20, 0),
    )
    assert summary.unproven == (("toy.io.read", "reads a file"),)


def test_gate_summary_without_a_counts_line_or_with_a_malformed_one() -> None:
    assert gate_summary((TARGET_F,)).totals is None
    # Not as the gate prints a number: a leading zero, a sign, a non-ASCII digit, text after.
    for bad in (
        COUNTS.replace("5 of", "05 of"),
        COUNTS.replace("5 of", "+5 of"),
        COUNTS.replace("5 of", "٣ of"),
        COUNTS + " more",
        TARGET_F.replace("3 confirmed", "3 of 4 confirmed"),
    ):
        read = gate_summary((bad,))
        assert read.totals is None and read.targets == (), bad
    assert gate_summary(("gate: toy.logic.f: exit 1", "Confirmed over all paths")).targets == ()


def test_group_verdicts_reads_what_the_standards_print() -> None:
    lines = (
        "standards: readability, design on src/toy and .github/workflows, against 0123456789ab",
        "standards: readability: passed: 0 function measures over a limit in the tree",
        "  src/toy/a.py:3: readability/cognitive-complexity: `f`: its cognitive complexity is 16",
        "standards: design: FAILED: 1 class measure over a limit in the tree, 1 new or worse",
        "standards: coverage: could not run: the tests failed under coverage",
        "standards: git failed: boom",
        "standards: security: passed",
    )
    assert group_verdicts(lines) == (
        ("readability", "passed"),
        ("design", "FAILED"),
        ("coverage", "could not run"),
    )


def test_count_by_keeps_the_order_first_seen() -> None:
    assert count_by(("error", "note", "error")) == (("error", 2), ("note", 1))
    assert count_by(()) == ()


# --- the environment -----------------------------------------------------------------------------


def test_conda_file_names_from_the_lock() -> None:
    assert conda_name_version("python-3.11.17-h5f976f7_1_cpython.conda") == ("python", "3.11.17")
    assert conda_name_version("ld_impl_linux-64-2.46.1-default_hbd61a6d_102.conda") == (
        "ld_impl_linux-64",
        "2.46.1",
    )
    assert conda_name_version("mando-0.6.4-py_0.tar.bz2") == ("mando", "0.6.4")
    assert conda_name_version("jscpd-5.4.0-py3-none-manylinux_2_34_x86_64.whl") is None
    assert conda_name_version("a-b.conda") is None
    assert conda_name_version("-a-b.conda") is None


def test_the_real_lock_pins_every_tool_that_decides() -> None:
    entries = _lock_entries(REPO)
    versions = dict(pinned(entries, PINNED))
    assert set(versions) == set(PINNED)
    assert NOT_IN_LOCK not in versions.values(), versions
    assert all(v[0].isdigit() for v in versions.values()), versions
    assert pinned(entries, ("no-such-package",)) == (("no-such-package", NOT_IN_LOCK),)
    with pytest.raises(ViolationError):
        pinned(entries, ("ruff", "ruff"))


# --- enforcement ---------------------------------------------------------------------------------


def test_the_real_hook_hands_over_to_the_gate() -> None:
    text = (REPO / ".githooks" / "pre-push").read_text(encoding="utf-8")
    assert hook_command(text) == "pixi run --locked gate"
    assert hook_command("#!/bin/sh\nexit 0\n") is None
    assert hook_command("  exec   pixi run gate  \n") == "pixi run gate"


def test_strip_credentials() -> None:
    assert strip_credentials("https://user:token@github.com/o/r") == "https://github.com/o/r"
    assert strip_credentials("https://github.com/o/r.git") == "https://github.com/o/r.git"
    assert strip_credentials("git@github.com:o/r.git") == "git@github.com:o/r.git"
    assert strip_credentials("ssh://git@github.com/o/r") == "ssh://github.com/o/r"


def test_the_real_branch_rule_and_workflows() -> None:
    required, strict = _ruleset(REPO / ".github" / "rulesets" / "main.json")
    assert "gate (lock → lint, typecheck, tests, CrossHair/Z3)" in required
    assert "standards (every group green)" in required
    assert strict is False
    workflows = dict(_workflows(REPO))
    assert "report (evidence)" in workflows["ci.yml"]
    assert "report (evidence)" not in required  # the report is evidence, never a required check
    assert _ruleset(REPO / "nowhere.json") == ((), None)


def test_the_real_tasks_are_the_lanes() -> None:
    tasks = _tasks(REPO)
    assert set(LANES) <= set(tasks)
    assert tasks["report"] == "python -m beethoven_qa report"
    assert "gate" not in tasks  # depends on the lanes, runs nothing of its own
    assert [name for name, _ in _lane_tasks(REPO)] == list(LANES)  # no pinned copy here


# --- tables and rendering ------------------------------------------------------------------------


def test_table_keeps_a_cell_from_breaking_it() -> None:
    text = table(("a", "b"), (("x|y", "line\nbreak"),))
    assert text == "| a | b |\n| - | - |\n| x\\|y | line break |"
    with pytest.raises(ViolationError):
        table(("a", "b"), (("only one",),))


def test_exit_code_follows_the_lanes() -> None:
    assert exit_code((0, 0)) == 0 and exit_code(()) == 0
    assert exit_code((0, 1)) == 1 and exit_code((127,)) == 1


def _sarif_dir(tmp_path: Path, group: str = "readability") -> Path:
    rule = Rule("r/x", "X", "x is at most 10.", "a standard", "https://example.invalid/x")
    head = (
        Finding("r/x", "a.py", 1, 1, "f", 20, 10, "error", "f is 20"),
        Finding("r/x", "a.py", 5, 5, "g", 20, 10, "note", "g is 20"),
        Finding("r/x", "a.py", 9, 9, "h", 20, 10, "error", "h is 20"),
    )
    run = Run(
        group,
        "complexipy",
        "8.0.1",
        "https://example.invalid",
        (rule,),
        head,
        ("new", "unchanged", "unchanged"),
    )
    other = Run(group, "beethoven-qa", __version__, "https://example.invalid", (rule,), (), ())
    out = tmp_path / "sarif"
    out.mkdir()
    (out / f"{group}.sarif").write_text(json.dumps(sarif_log((run, other))), encoding="utf-8")
    return out


def test_a_sarif_log_the_real_writer_made_is_counted(tmp_path: Path) -> None:
    out = _sarif_dir(tmp_path)
    (out / "notes.txt").write_text("not a log", encoding="utf-8")
    (out / "broken.sarif").write_text("{", encoding="utf-8")
    (group,) = _groups(out, {"readability": "passed"})
    assert group == GroupSummary(
        "readability",
        "passed",
        (("complexipy", "8.0.1"), ("beethoven-qa", __version__)),
        (("error", 2), ("note", 1)),
        (("new", 1), ("unchanged", 2)),
    )
    assert _groups(out, {})[0].verdict == "not run"
    assert _groups(tmp_path / "none", {}) == ()


def _evidence(groups: tuple[GroupSummary, ...] = ()) -> Evidence:
    tools = tuple(Tool(n, "1.0", "") for n in PINNED)
    lanes = (
        Lane("lint", ("ruff", "check", "src"), 0, 0.5, "lanes/lint-1.txt"),
        Lane("te|st", ("pytest", "-q"), 1, 2.25, "lanes/test-1.txt"),
    )
    gate = GateSummary(Totals(5, 6, 1, 20, 0), (Target("f", 3, 1, 20, 0),), (("g", "reads"),))
    rule = Enforcement(
        ".githooks",
        True,
        True,
        "pixi run --locked gate",
        ("gate",),
        False,
        (("ci.yml", ("gate", "plugin")),),
    )
    decided = deciders(tools, groups, False)
    return Evidence(
        "2026-10-08T00:00:00+00:00",
        __version__,
        Repository("abc123", "main", True, "https://example.invalid/r"),
        tools,
        lanes,
        gate,
        groups,
        rule,
        decided,
        False,
    )


def test_deciders_names_each_check_and_what_the_logs_say() -> None:
    tools = (Tool("ruff", "0.16.10", "ruff 0.16.10"), Tool("crosshair-tool", "0.0.111", ""))
    groups = (GroupSummary("design", "passed", (("beethoven-qa", "4.0.0"),), (), ()),)
    rows = dict(deciders(tools, groups, True))
    assert rows["lint"] == "a tool, ruff 0.16.10"
    assert rows["typecheck"] == f"a tool, mypy {NOT_IN_LOCK}"
    assert "crosshair-tool 0.0.111" in rows["symbolic, check 2: no counterexample within the bound"]
    assert rows["standards: design"].startswith("home-made code, beethoven-qa 4.0.0, contracted")
    assert VENDOR_LANE in rows and "no person" in rows["whether a change merges"]
    assert VENDOR_LANE not in dict(deciders(tools, groups, False))


def test_render_writes_every_section_and_escapes_what_it_tables(tmp_path: Path) -> None:
    text = render(_evidence(_groups(_sarif_dir(tmp_path), {"readability": "FAILED"})))
    assert text.startswith("# Evidence report: https://example.invalid/r\n")
    assert [line[3:] for line in text.splitlines() if line.startswith("## ")] == list(SECTIONS)
    assert "| te\\|st | pytest -q | 1 | 2.2 | lanes/test-1.txt |" in text
    assert "**5 were confirmed over all paths**" in text and "| g | reads |" in text
    assert "- **readability**: FAILED; complexipy 8.0.1, beethoven-qa" in text
    assert "`gate` (strict: false)" in text and "A lane failed" in text
    assert "not a proof" in text and "No verdict in this report comes from a model" in text


# --- the command, end to end ---------------------------------------------------------------------

GATE_SCRIPT = "#!/bin/sh\n" + "".join(f"echo '{line}'\n" for line in GATE_OUTPUT)
STANDARDS_SCRIPT = textwrap.dedent(
    """\
    #!/bin/sh
    # $1 is --sarif and $2 the directory the report asked for.
    mkdir -p "$2" && cp fixtures/readability.sarif "$2/readability.sarif"
    echo "standards: readability on src/toy and .github/workflows, against 0123456789ab"
    echo "standards: readability: passed: 0 function measures over a limit in the tree"
    """
)
HOOK = "#!/bin/sh\nexec pixi run --locked gate\n"
RULESET = {
    "rules": [
        {"type": "deletion"},
        {
            "type": "required_status_checks",
            "parameters": {
                "strict_required_status_checks_policy": True,
                "required_status_checks": [{"context": "gate", "integration_id": 15368}],
            },
        },
    ]
}
LOCK = textwrap.dedent(
    """\
    version: 7
    packages:
    - conda: https://conda.anaconda.org/conda-forge/linux-64/python-3.11.17-h5f976f7_1_cpython.conda
      sha256: 524df25896667b5f7e391c2115495cac5e0077b97967981091af8afcc98fd312
    - pypi: https://files.pythonhosted.org/packages/3a/15/crosshair_tool-0.0.111-cp311-cp311-manylinux1_x86_64.whl
      name: crosshair-tool
      version: 0.0.111
    """
)


def _pixi_toml(lint: str) -> str:
    return textwrap.dedent(
        f"""\
        [workspace]
        name = "toy"
        [tasks]
        lint = "{lint}"
        typecheck = "true"
        test = "sh -c 'echo tests ran; exit 0'"
        boundary = {{ cmd = "true" }}
        symbolic = "sh tools/gate.sh"
        standards = "sh tools/standards.sh"
        gate = {{ depends-on = ["lint", "typecheck", "test", "boundary", "symbolic", "standards"] }}
        """
    )


@pytest.fixture
def git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Hermetic git for the throwaway repository: no configuration of the person's, a fixed author,
    # and no repository named by the environment (a pre-push hook's GIT_DIR).
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CONFIG_PARAMETERS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("GIT_CONFIG_COUNT", raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "test@example.invalid")


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=True,
        env=dict(os.environ),
    )
    return done.stdout


def _write(root: Path, rel: str, text: str, executable: bool = False) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _throwaway(tmp_path: Path) -> Path:
    root = tmp_path / "toy"
    root.mkdir()
    _write(root, "pyproject.toml", '[project]\nname = "toy"\n')
    _write(root, "pixi.toml", _pixi_toml("true && false"))
    _write(root, "pixi.lock", LOCK)
    _write(root, "tools/gate.sh", GATE_SCRIPT)
    _write(root, "tools/standards.sh", STANDARDS_SCRIPT)
    _write(root, ".githooks/pre-push", HOOK, executable=True)
    _write(root, ".github/rulesets/main.json", json.dumps(RULESET))
    _write(root, ".github/workflows/ci.yml", "jobs:\n  gate:\n    name: gate\n  plugin: {}\n")
    _write(root, "fixtures/readability.sarif", json.dumps(_log()))
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "core.hooksPath", ".githooks")
    _git(root, "remote", "add", "origin", "https://user:secret@example.invalid/toy.git")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "toy")
    return root


def _log() -> dict[str, object]:
    rule = Rule("r/x", "X", "x is at most 10.", "a standard", "https://example.invalid/x")
    finding = Finding("r/x", "a.py", 1, 1, "f", 20, 10, "error", "f is 20")
    run = Run(
        "readability",
        "complexipy",
        "8.0.1",
        "https://x.invalid",
        (rule,),
        (finding,),
        ("unchanged",),
    )
    return sarif_log((run,))


def test_the_command_end_to_end(tmp_path: Path, git_env: None) -> None:
    root = _throwaway(tmp_path)
    out = tmp_path / "evidence"
    assert _run(root, out) == 1  # lint's second command fails
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    md = (out / "report.md").read_text(encoding="utf-8")
    assert report["format"] == 1 and report["version"] == __version__ and not report["passed"]

    # Every argv verbatim, every command of a task on its own, the standards lane given --sarif.
    lanes = [(lane["task"], lane["argv"], lane["exit"]) for lane in report["lanes"]]
    assert lanes == [
        ("lint", ["true"], 0),
        ("lint", ["false"], 1),
        ("typecheck", ["true"], 0),
        ("test", ["sh", "-c", "echo tests ran; exit 0"], 0),
        ("boundary", ["true"], 0),
        ("symbolic", ["sh", "tools/gate.sh"], 0),
        ("standards", ["sh", "tools/standards.sh", "--sarif", str(out / "sarif")], 0),
    ]
    assert all(lane["seconds"] >= 0 for lane in report["lanes"])
    outputs = {lane["output"] for lane in report["lanes"]}
    assert outputs == {
        f"lanes/{t}-{i}.txt"
        for t, i in [
            ("lint", 1),
            ("lint", 2),
            ("typecheck", 1),
            ("test", 1),
            ("boundary", 1),
            ("symbolic", 1),
            ("standards", 1),
        ]
    }
    assert (out / "lanes" / "test-1.txt").read_text(encoding="utf-8") == "tests ran\n"

    # The repository, read before anything was written; the origin without its credential.
    head = _git(root, "rev-parse", "HEAD").strip()
    assert report["repository"] == {
        "head": head,
        "branch": "main",
        "clean": True,
        "origin": "https://example.invalid/toy.git",
    }
    # The gate's output read back, the SARIF log counted, the lock and the files read.
    assert report["gate"]["totals"] == {
        "confirmed": 5,
        "conditions": 6,
        "not_confirmed": 1,
        "bound": 20,
        "unable": 0,
    }
    assert [t["name"] for t in report["gate"]["targets"]] == ["toy.logic.f", "toy.logic.g"]
    assert report["gate"]["unproven"] == [["toy.io.read", "reads a file"]]
    (group,) = report["standards"]
    assert group["group"] == "readability" and group["verdict"] == "passed"
    assert group["levels"] == [["error", 1]] and group["states"] == [["unchanged", 1]]
    tools = {t["name"]: t for t in report["tools"]}
    assert tools["python"]["locked"] == "3.11.17" and tools["python"]["reported"].startswith(
        "Python "
    )
    assert tools["crosshair-tool"] == {
        "name": "crosshair-tool",
        "locked": "0.0.111",
        "reported": "",
    }
    assert tools["ruff"]["locked"] == NOT_IN_LOCK and tools["ruff"]["reported"].startswith("ruff ")
    assert report["enforcement"] == {
        "hooks_path": ".githooks",
        "hook_present": True,
        "hook_executable": True,
        "hook_command": "pixi run --locked gate",
        "required": ["gate"],
        "strict": True,
        "workflows": [["ci.yml", ["gate", "plugin"]]],
    }
    assert dict(report["decided"])["standards: readability"] == "a tool, complexipy 8.0.1"
    assert report["language_model"] and report["not_shown"]
    assert "| lint | false | 1 |" in md and f"HEAD `{head}` on `main`" in md

    # Through the command line, with the failing command fixed and the tree now dirty.
    _write(root, "pixi.toml", _pixi_toml("true"))
    assert main(["report", "--root", str(root), "--out", str(out)]) == 0
    again = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert again["passed"] and not again["repository"]["clean"]


def test_the_command_refuses_what_it_cannot_run(
    tmp_path: Path, git_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _throwaway(tmp_path)
    out = tmp_path / "evidence"
    (root / "pyproject.toml").unlink()
    assert _run(root, out) == 2 and "pyproject.toml is missing" in capsys.readouterr().out
    _write(root, "pyproject.toml", "")
    _write(root, "pixi.toml", "[tasks]\nlint = 'true'\n")
    assert _run(root, out) == 2 and "has no task typecheck, test" in capsys.readouterr().out
    _write(root, "pixi.toml", "[workspace]\n")
    assert _run(root, out) == 2 and "no [tasks] table" in capsys.readouterr().out
    (root / "pixi.toml").unlink()
    assert _run(root, out) == 2 and "pixi.toml is missing" in capsys.readouterr().out
    bare = tmp_path / "bare"
    bare.mkdir()
    _write(bare, "pyproject.toml", "")
    _write(bare, "pixi.toml", _pixi_toml("true"))
    assert _run(bare, out) == 2 and "git failed" in capsys.readouterr().out
    assert not out.exists()  # nothing was written by a refused run
