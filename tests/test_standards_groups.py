"""Each standards group on throwaway repositories: what the change made blocks, what the base
already held is recorded and passes, and nothing in the repository's own files loosens a limit.

Every repository here is a real git checkout with a package at ``src/pkg``; the base is a commit
of it and the head is its working tree, so the groups read the same ``git diff`` the command line
and CI read. The command line itself is run in-process (``__main__.main``), its exit codes and its
SARIF logs checked, every log against the OASIS schema.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import textwrap
from pathlib import Path
from typing import Any

import pytest
from beartype.roar import BeartypeCallHintParamViolation
from pydantic import ValidationError

from beethoven_qa.__main__ import main
from beethoven_qa.standards import (
    coach,
    coverage,
    design,
    duplication,
    readability,
    run,
    security,
    supply_chain,
)
from beethoven_qa.standards.group import Context, Report
from beethoven_qa.standards.sarif import sarif_log
from test_standards import validate_sarif

PKG = "src/pkg"

# Cognitive complexity 19 (Campbell's rules: each loop and if 1 plus its nesting, each run of a
# boolean operator 1, elif and else 1), cyclomatic 9: over one readability limit only.
TANGLED = """\
def tangled(a, b, c, d):
    total = 0
    for i in range(a):
        if i % 2 and b:
            for j in range(b):
                if j > c or d:
                    if i == j:
                        total += 1
                    elif i < j:
                        total -= 1
                    else:
                        total += 2
    return total
"""

# Cyclomatic complexity 12, cognitive 11.
BRANCHES = (
    "def branches(x):  # noqa: C901\n"
    + "".join(
        f"    {'if' if n == 1 else 'elif'} x == {n}:\n        return {n}\n" for n in range(1, 12)
    )
    + "    return 0\n"
)


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


def _repo(tmp_path: Path, files: dict[str, str], extra_toml: str = "") -> Path:
    # A repository whose first commit holds ``files``; the head is its working tree from then on.
    root = tmp_path / "repo"
    pyproject = '[tool.beethoven-qa]\npackage = "pkg"\n\n[tool.pytest.ini_options]\n'
    pyproject += 'pythonpath = ["src"]\ntestpaths = ["tests"]\n' + extra_toml
    _write(root, {"pyproject.toml": pyproject, f"{PKG}/__init__.py": "", **files})
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    return root


def _check(root: Path, group: str, base: str = "HEAD") -> Report:
    commit = run._resolve(root, base)
    changes = run._changes(root, commit)
    with tempfile.TemporaryDirectory() as tmp:
        if commit is not None:
            run._extract(root, commit, [PKG, supply_chain.WORKFLOWS], Path(tmp))
        context = Context(root, Path(tmp) if commit else None, PKG, changes)
        report: Report = run.GROUPS[group]._check(context)
    validate_sarif(sarif_log(report.runs))
    return report


def _states(report: Report) -> dict[tuple[str, str], str]:
    # Each finding's (rule, anchor), with its state against the base.
    pairs = (zip(r.findings, r.states, strict=True) for r in report.runs)
    return {(f.rule, f.anchor): s for pair in pairs for f, s in pair}


def _blocked(report: Report) -> set[tuple[str, str]]:
    return {(f.rule, f.anchor) for f in report.blocking}


# --- readability --------------------------------------------------------------------------------


def test_readability_blocks_only_what_the_change_made(tmp_path: Path) -> None:
    root = _repo(tmp_path, {f"{PKG}/legacy.py": TANGLED})
    _write(root, {f"{PKG}/fresh.py": "def fine(x):\n    return x + 1\n"})
    report = _check(root, "readability")
    assert report.passed, report.summary
    assert _states(report)[("readability/cognitive-complexity", "tangled")] == "unchanged"

    # Moved down the file, behind a new function: still the same finding.
    _write(root, {f"{PKG}/legacy.py": "def first():\n    return 1\n\n\n" + TANGLED})
    assert _check(root, "readability").passed

    # Made worse: one more nested branch.
    deeper = "                        total += 2\n"
    deeper += "                        if d:\n                            total += 3\n"
    worse = TANGLED.replace("                        total += 2\n", deeper)
    _write(root, {f"{PKG}/legacy.py": worse})
    report = _check(root, "readability")
    assert not report.passed
    assert _states(report)[("readability/cognitive-complexity", "tangled")] == "updated"


def test_a_new_function_over_a_limit_blocks_whatever_its_comments_say(tmp_path: Path) -> None:
    # A noqa, and every file through which ruff or complexipy could be told to raise a limit, leave
    # a file out or report only what fails its own limit, change nothing.
    cx = '\nmax-complexity-allowed = 99\nfailed = true\nexclude = ["src/pkg/new.py"]\n'
    loose = f"[tool.ruff.lint.mccabe]\nmax-complexity = 50\n[tool.complexipy]{cx}"
    ruff = '[lint]\nignore = ["C901", "PLR0913"]\nexclude = ["src/pkg/new.py"]\n'
    files = {"ruff.toml": ruff, "complexipy.toml": cx, f"{PKG}/.complexipy.toml": cx}
    root = _repo(tmp_path, files, extra_toml=loose)
    params = "def many(a, b, c, d, e):\n    return a\n\n\n"
    params += "class K:\n    def m(self, a, b, c, d):\n        return a\n"
    long_body = "def long():\n    x = 0\n" + "    x += 1\n" * 25 + "    return x\n"
    _write(root, {f"{PKG}/new.py": BRANCHES + "\n\n" + params + "\n\n" + long_body})
    report = _check(root, "readability")
    assert not report.passed
    assert _blocked(report) == {
        ("readability/cyclomatic-complexity", "branches"),
        ("readability/parameters", "many"),
        ("readability/function-length", "long"),
    }


def test_a_renamed_file_keeps_its_old_findings(tmp_path: Path) -> None:
    root = _repo(tmp_path, {f"{PKG}/legacy.py": TANGLED})
    _git(root, "mv", f"{PKG}/legacy.py", f"{PKG}/kept.py")
    report = _check(root, "readability")
    assert report.passed and report.runs[0].findings[0].path == f"{PKG}/kept.py"


def test_the_base_is_read_from_git_not_from_its_export_attributes(tmp_path: Path) -> None:
    # export-ignore would drop the file from `git archive`, and its old finding would look new.
    attributes = {".gitattributes": f"{PKG}/legacy.py export-ignore\n"}
    root = _repo(tmp_path, {f"{PKG}/legacy.py": TANGLED, **attributes})
    _write(root, {f"{PKG}/fresh.py": "X = 1\n"})
    assert _check(root, "readability").passed


# --- design -------------------------------------------------------------------------------------


SPLIT = """\
class Split:
    def __init__(self):
        self.a = 1
        self.b = 2

    def one(self):
        return self.a

    def two(self):
        return self.b
"""


def test_design_measures() -> None:
    m = (("one", frozenset({"a"}), frozenset()), ("two", frozenset({"b"}), frozenset()))
    assert design.lcom4(m) == 2
    joined = (*m, ("both", frozenset(), frozenset({"one", "two"})))
    assert design.lcom4(joined) == 1
    assert design.lcom4(()) == 0
    chain = tuple((f"C{n}", (f"C{n - 1}",) if n else ("Base",)) for n in range(6))
    assert design.inheritance_depth("C5", chain) == 7
    assert design.inheritance_depth("C0", chain) == 2
    refs = (("A", frozenset({"B"})), ("B", frozenset()), ("C", frozenset({"A", "int"})))
    assert design.coupling("A", refs) == 2 and design.coupling("B", refs) == 1


def test_a_new_class_that_falls_apart_blocks_and_an_old_one_does_not(tmp_path: Path) -> None:
    root = _repo(tmp_path, {f"{PKG}/old.py": SPLIT})
    _write(root, {f"{PKG}/new.py": SPLIT.replace("Split", "Again")})
    report = _check(root, "design")
    assert not report.passed
    assert _blocked(report) == {("design/lcom4", "Again")}
    assert _states(report)[("design/lcom4", "Split")] == "unchanged"


def test_a_deep_hierarchy_blocks(tmp_path: Path) -> None:
    root = _repo(tmp_path, {})
    chain = "class C0(Exception):\n    pass\n" + "".join(
        f"\n\nclass C{n}(C{n - 1}):\n    pass\n" for n in range(1, 6)
    )
    _write(root, {f"{PKG}/deep.py": chain})
    report = _check(root, "design")
    assert {a for r, a in _blocked(report) if r == "design/dit"} == {"C4", "C5"}


# --- duplication --------------------------------------------------------------------------------


def _block(name: str) -> str:
    body = "".join(f"    total = total + values[{n}] * 3 - offset // {n + 2}\n" for n in range(12))
    return f"def {name}(values, offset):\n    total = 0\n{body}    return total\n"


def test_duplication_measures() -> None:
    def line(n: int, k: str) -> duplication.Line:
        return (n, k, 10)

    a = tuple(line(n, f"k{n % 3}") for n in range(1, 13))
    assert duplication.repeated((a,), 3, 30) != ()
    assert duplication.repeated((a,), 3, 31) == ()
    assert duplication.within_limit(3, 100) and not duplication.within_limit(4, 100)


def test_copied_new_code_blocks_and_copies_the_base_held_do_not(tmp_path: Path) -> None:
    root = _repo(tmp_path, {})
    _write(root, {f"{PKG}/copies.py": _block("first") + "\n\n" + _block("second")})
    report = _check(root, "duplication")
    assert not report.passed and report.blocking
    assert report.blocking[0].message.startswith("lines ")

    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "copies")
    _write(root, {f"{PKG}/other.py": "def small(x):\n    return x\n"})
    report = _check(root, "duplication")
    assert report.passed and not report.runs[0].findings, report.summary


def test_a_block_with_its_names_changed_is_not_a_copy(tmp_path: Path) -> None:
    root = _repo(tmp_path, {})
    other = _block("second").replace("total", "acc")
    _write(root, {f"{PKG}/copies.py": _block("first") + "\n\n" + other})
    assert _check(root, "duplication").passed


# --- coverage -----------------------------------------------------------------------------------


MOD = """\
def half(x):
    if x > 0:
        return 1
    return 2


def other(y):  # pragma: no cover
    return y + 1
"""


def test_coverage_of_the_new_lines(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"tests/test_mod.py": "def test_nothing():\n    assert True\n"})
    one = "from pkg.mod import half\n\n\ndef test_half():\n    assert half(1) == 1\n"
    _write(root, {f"{PKG}/mod.py": MOD, "tests/test_mod.py": one})
    report = _check(root, "coverage")
    # 4 of 6 lines that can run: the pragma does not take `other`'s body out of the count.
    assert not report.passed and "4 of 6" in report.summary
    assert {f.line for f in report.blocking} == {4, 8}

    rest = "\n\ndef test_rest():\n    from pkg.mod import other\n\n"
    both = one + rest + "    assert half(0) == 2 and other(1) == 2\n"
    _write(root, {"tests/test_mod.py": both})
    report = _check(root, "coverage")
    assert report.passed and "6 of 6" in report.summary


def test_failing_tests_fail_the_coverage_group(tmp_path: Path) -> None:
    root = _repo(tmp_path, {})
    _write(root, {f"{PKG}/mod.py": MOD, "tests/test_mod.py": "def test_no():\n    assert False\n"})
    report = _check(root, "coverage")
    assert not report.passed and report.summary.startswith("the tests failed under coverage")


def test_coverage_measures() -> None:
    new = frozenset({1, 2, 3, 9})
    assert coverage.new_line_coverage(frozenset({1, 2}), frozenset({3, 4}), new) == (2, 3)
    assert coverage.meets_minimum(4, 5) and not coverage.meets_minimum(3, 5)
    assert coverage.meets_minimum(0, 0)


# --- security -----------------------------------------------------------------------------------


SHELL = """\
import subprocess


def go(cmd):
    return subprocess.run(cmd, shell=True)  # nosec
"""


def test_a_new_weakness_blocks_whatever_nosec_or_a_bandit_file_says(tmp_path: Path) -> None:
    skip = "[bandit]\nskips = B602,B404\n"
    root = _repo(tmp_path, {".bandit": skip, f"{PKG}/.bandit": skip})
    _write(root, {f"{PKG}/shell.py": SHELL})
    report = _check(root, "security")
    assert not report.passed
    (stop,) = report.blocking
    assert stop.rule == "security/B602" and "external/cwe/cwe-78" in stop.tags
    notes = [f for f in report.runs[0].findings if f.level == "note"]
    assert [f.rule for f in notes] == ["security/B404"]  # importing subprocess: recorded only


def test_a_weakness_the_base_held_is_recorded_and_passes(tmp_path: Path) -> None:
    root = _repo(tmp_path, {f"{PKG}/shell.py": SHELL})
    _write(root, {f"{PKG}/shell.py": "# moved down\n\n" + SHELL})
    report = _check(root, "security")
    assert report.passed
    anchor = "go: return subprocess.run(cmd, shell=True) # nosec"
    assert _states(report)[("security/B602", anchor)] == "unchanged"


def test_security_levels() -> None:
    grade = security.Grade
    assert security.level_for(grade.HIGH, grade.MEDIUM) == "error"
    assert security.level_for(grade.MEDIUM, grade.LOW) == "note"
    assert security.level_for(grade.LOW, grade.HIGH) == "note"
    # The callers pass the member; bandit's string is validated into it at the boundary, once.
    spelled: Any = "HIGH"
    with pytest.raises(BeartypeCallHintParamViolation):
        security.level_for(spelled, grade.HIGH)
    raw = {
        "filename": "a.py",
        "line_number": 1,
        "line_range": [1],
        "test_id": "B101",
        "test_name": "assert_used",
        "issue_severity": "HIGH",
        "issue_confidence": "LOW",
        "issue_text": "x",
    }
    issue = security._Issue.model_validate(raw)
    assert issue.issue_severity is grade.HIGH and issue.issue_confidence is grade.LOW
    with pytest.raises(ValidationError):
        security._Issue.model_validate({**raw, "issue_severity": "SEVERE"})


# --- supply chain -------------------------------------------------------------------------------


SHA = "11bd71901bbe5b1630ceea73d27597364c9af683"

GOOD = f"""\
name: good
on: [push]
permissions:
  contents: read
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@{SHA}
      - uses: ./local-action
      - run: echo "$TITLE"
        env:
          TITLE: ${{{{ github.event.issue.title }}}}
"""

BAD = """\
name: bad
on:
  pull_request_target:
permissions: write-all
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.head.sha }}
      - name: greet
        run: echo "${{ github.event.pull_request.title }}"
      - run: curl -fsSL https://example.invalid/install.sh | sudo bash
  reuse:
    uses: some/repo/.github/workflows/x.yml@main
"""


def test_supply_chain_rules() -> None:
    assert supply_chain.pinned(f"actions/checkout@{SHA}") and supply_chain.pinned("./x")
    assert not supply_chain.pinned("actions/checkout@v4") and not supply_chain.pinned(SHA)
    assert supply_chain.pinned("docker://alpine@sha256:" + "0" * 64)
    assert not supply_chain.pinned("docker://alpine:3")
    assert supply_chain.grants_write("write-all") and supply_chain.grants_write({"a": "write"})
    assert not supply_chain.grants_write({"a": "read"}) and not supply_chain.grants_write(None)
    script = 'echo "${{ github.head_ref }}" ${{ github.sha }}'
    assert supply_chain.untrusted(script) == (" github.head_ref ",)
    assert supply_chain.pipes_to_shell("wget -qO- x | sh")
    assert not supply_chain.pipes_to_shell("curl x > f")


def test_workflows_are_held_to_scorecards_checks(tmp_path: Path) -> None:
    root = _repo(tmp_path, {})
    _write(root, {".github/workflows/good.yml": GOOD, ".github/workflows/bad.yaml": BAD})
    report = _check(root, "supply-chain")
    assert not report.passed
    found = {(f.path.rsplit("/", 1)[-1], f.rule.split("/")[1], f.line) for f in report.blocking}
    assert found == {
        ("bad.yaml", "token-permissions", 4),
        ("bad.yaml", "pinned-action", 9),
        ("bad.yaml", "untrusted-checkout", 11),
        ("bad.yaml", "script-injection", 13),
        ("bad.yaml", "pipe-to-shell", 14),
        ("bad.yaml", "pinned-action", 16),
    }


def test_a_workflow_with_no_permissions_is_reported_where_its_jobs_start(tmp_path: Path) -> None:
    open_ended = GOOD.replace("permissions:\n  contents: read\n", "")
    root = _repo(tmp_path, {".github/workflows/old.yml": open_ended})
    report = _check(root, "supply-chain")
    assert report.passed  # the base held it
    (finding,) = report.runs[0].findings
    assert (finding.rule, finding.line) == ("supply-chain/token-permissions", 3)


# --- the command line ---------------------------------------------------------------------------


def test_the_command_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _repo(tmp_path, {f"{PKG}/legacy.py": TANGLED})
    sarif = tmp_path / "sarif"
    args = ["standards", "readability", "--root", str(root), "--base", "HEAD"]
    assert main([*args, "--sarif", str(sarif)]) == 0
    assert "readability: passed" in capsys.readouterr().out
    _write(root, {f"{PKG}/new.py": TANGLED.replace("tangled", "again")})
    assert main(args) == 1
    out = capsys.readouterr().out
    assert "FAILED" in out and "`again`: its cognitive complexity is 19, more than 15 (new)" in out
    # With no base the whole tree is new, and the old function blocks too.
    assert main(["standards", "readability", "--root", str(root), "--base", "none"]) == 1
    assert "on the whole tree" in capsys.readouterr().out
    validate_sarif(json.loads((sarif / "readability.sarif").read_text(encoding="utf-8")))


def test_every_group_writes_a_valid_log(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _repo(tmp_path, {"tests/test_x.py": "def test_x():\n    assert True\n"})
    sarif = tmp_path / "sarif"
    args = ["standards", "all", "--root", str(root), "--base", "HEAD", "--sarif", str(sarif)]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert [line.split(":")[1].strip() for line in out.splitlines()[1:]] == list(run.GROUPS)
    for name in run.GROUPS:
        validate_sarif(json.loads((sarif / f"{name}.sarif").read_text(encoding="utf-8")))


def test_what_the_command_line_refuses(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _repo(tmp_path, {})
    assert main(["standards", "design", "--root", str(root), "--base", "no-such-ref"]) == 2
    assert "no-such-ref is not a commit" in capsys.readouterr().out
    assert main(["standards", "design", "--root", str(root)]) == 2  # no remote: auto cannot tell
    assert "pass --base" in capsys.readouterr().out
    (root / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    assert main(["standards", "design", "--root", str(root), "--base", "HEAD"]) == 2
    assert "no [tool.beethoven-qa] table" in capsys.readouterr().out


def test_auto_finds_where_the_branch_left_the_default_branch(tmp_path: Path) -> None:
    root = _repo(tmp_path, {f"{PKG}/legacy.py": TANGLED})
    base = _git(root, "rev-parse", "HEAD").strip()
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    _git(root, "remote", "add", "origin", str(remote))
    _git(root, "push", "-q", "origin", "main")
    _git(root, "fetch", "-q", "origin")
    _git(root, "checkout", "-q", "-b", "feature")
    _write(root, {f"{PKG}/new.py": "X = 1\n"})
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "feature")
    assert run._resolve(root, "auto") == base  # origin/main: the remote names no default
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(remote), str(clone))
    assert run._resolve(clone, "auto") == base  # origin/HEAD, set by the clone


def test_untracked_files_are_new_and_links_are_not_read(tmp_path: Path) -> None:
    root = _repo(tmp_path, {})
    _write(root, {f"{PKG}/fresh.py": "a = 1\nb = 2\n"})
    (root / PKG / "link.py").symlink_to(root / PKG / "fresh.py")
    changes = run._changes(root, run._resolve(root, "HEAD"))
    assert dict(changes.lines)[f"{PKG}/fresh.py"] == ((1, 2),)
    assert f"{PKG}/link.py" not in dict(changes.lines)


# --- coaching (the coach command behind the plugin's PostToolUse hook) -------------------------


def test_which_groups_coach_which_file() -> None:
    assert coach.groups_for(f"{PKG}/mod.py", PKG) == coach.CHEAP
    assert "coverage" not in coach.CHEAP  # it runs the tests: not on every edit
    assert coach.groups_for(".github/workflows/ci.yml", PKG) == coach.WORKFLOW
    assert coach.groups_for(".github/workflows/ci.yaml", PKG) == coach.WORKFLOW
    for elsewhere in (
        "tests/test_x.py",
        f"{PKG}/data.json",
        ".github/workflows/x/y.yml",
        "README.md",
    ):
        assert coach.groups_for(elsewhere, PKG) == ()
    assert coach.groups_for("src/pkgextra/mod.py", PKG) == ()  # a prefix of the name is not it


def test_what_the_session_is_told() -> None:
    assert coach.reason("a.py", (), 3) == ""
    lines = tuple(f"a.py:{n}: rule: message (new)" for n in range(1, 6))
    told = coach.reason("a.py", lines, 3)
    assert told.splitlines()[0].endswith("would block this change in CI, in a.py:")
    assert all(line in told for line in lines[:3]) and lines[3] not in told
    assert "and 2 more" in told and "code-standards skill" in told


def _published(tmp_path: Path, files: dict[str, str]) -> Path:
    # A repository whose base is on origin/main, so the coach's `auto` base is that commit.
    root = _repo(tmp_path, files)
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    _git(root, "remote", "add", "origin", str(remote))
    _git(root, "push", "-q", "origin", "main")
    _git(root, "fetch", "-q", "origin")
    return root


def _coached(
    root: Path, edited: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> str:
    # What the command prints for an Edit of ``edited`` (a path as the hook gives it), with exit 0.
    call = {"tool_name": "Edit", "tool_input": {"file_path": edited, "old_string": "x"}}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(call)))
    assert main(["coach", "--root", str(root)]) == 0
    return capsys.readouterr().out


def test_an_edit_that_breaks_a_limit_is_told_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _published(tmp_path, {f"{PKG}/legacy.py": TANGLED})
    _write(root, {f"{PKG}/new.py": TANGLED.replace("tangled", "again")})
    answer = json.loads(_coached(root, str(root / PKG / "new.py"), monkeypatch, capsys))
    assert answer["decision"] == "block"
    assert f"in {PKG}/new.py:" in answer["reason"]
    assert "`again`: its cognitive complexity is 19, more than 15 (new)" in answer["reason"]
    # What the base already held is not the session's to fix now, and neither is another file's.
    assert _coached(root, str(root / PKG / "legacy.py"), monkeypatch, capsys) == ""
    _write(root, {f"{PKG}/clean.py": "def fine(x):\n    return x\n"})
    assert _coached(root, f"{PKG}/clean.py", monkeypatch, capsys) == ""  # a relative path too


def test_a_workflow_edit_is_held_to_the_supply_chain_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _published(tmp_path, {})
    _write(root, {".github/workflows/bad.yaml": BAD})
    edited = str(root / ".github/workflows/bad.yaml")
    told = json.loads(_coached(root, edited, monkeypatch, capsys))["reason"]
    said = [line for line in told.splitlines() if line.startswith("  .github/workflows/bad.yaml:")]
    assert len(said) == 6 and "supply-chain/pinned-action" in told and " more" not in told


def test_coaching_says_nothing_when_it_cannot_or_should_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _published(tmp_path, {"tests/test_x.py": "def test_x():\n    assert True\n"})
    _write(root, {f"{PKG}/new.py": TANGLED, "notes.txt": "x\n"})
    (root / PKG / "link.py").symlink_to(root / PKG / "new.py")
    outside = tmp_path / "outside.py"
    outside.write_text(TANGLED, encoding="utf-8")
    for edited in (str(root / "notes.txt"), str(root / "tests/test_x.py"), str(outside)):
        assert _coached(root, edited, monkeypatch, capsys) == "", edited
    for edited in (str(root / PKG / "link.py"), str(root / PKG / "gone.py"), ""):
        assert _coached(root, edited, monkeypatch, capsys) == "", edited
    for text in ("not json", "[]", '{"tool_input": {"file_path": 3}}', "{}"):
        monkeypatch.setattr("sys.stdin", io.StringIO(text))
        assert main(["coach", "--root", str(root)]) == 0
        assert capsys.readouterr().out == "", text
    _git(root, "remote", "remove", "origin")  # no base to tell: CI's rule cannot be applied
    assert _coached(root, str(root / PKG / "new.py"), monkeypatch, capsys) == ""


def test_a_group_that_cannot_run_leaves_the_others_to_speak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _published(tmp_path, {})
    _write(root, {f"{PKG}/new.py": TANGLED + "\n\n" + "def run(cmd):\n    return eval(cmd)\n"})

    def broken(context: Context) -> Report:
        raise RuntimeError("complexipy failed")

    monkeypatch.setattr(readability, "_check", broken)
    told = json.loads(_coached(root, str(root / PKG / "new.py"), monkeypatch, capsys))["reason"]
    assert "readability/" not in told and "security/B307" in told
