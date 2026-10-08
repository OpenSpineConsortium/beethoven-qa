"""Each standards group on throwaway repositories: what the change made blocks, what the base
already held is recorded and passes, and nothing in the repository's own files loosens a limit.

Every repository here is a real git checkout with a package at ``src/pkg``; the base is a commit
of it and the head is its working tree, so the groups read the same ``git diff`` the command line
and CI read. The command line itself is run in-process (``__main__.main``), its exit codes and its
SARIF logs checked, every log against the OASIS schema.
"""

from __future__ import annotations

import ast
import io
import json
import os
import re
import subprocess
import tempfile
import textwrap
import tokenize
from pathlib import Path

import pytest

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
from beethoven_qa.standards.newcode import Changes
from beethoven_qa.standards.sarif import sarif_log
from test_standards import validate_sarif

REPO = Path(__file__).resolve().parent.parent
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
    files["whitelizard.txt"] = "long\n"  # lizard's whitelist, looked for in the working directory
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


LENGTH = readability.LENGTH


# The count readability.py made itself before lizard, kept as the oracle lizard's NLOC is held to
# where the two must agree: a plain body of N statements is N lines of code, N + 1 with the def.


def _code_lines(source: str) -> frozenset[int]:
    skip = {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT}
    skip |= {tokenize.ENCODING, tokenize.ENDMARKER}
    lines: set[int] = set()
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type not in skip:
            lines.update(range(tok.start[0], tok.end[0] + 1))
    return frozenset(lines)


def _first_statement(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    body = node.body
    docstring = (
        isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    )
    rest = body[1:] if docstring else body
    return rest[0].lineno if rest else (node.end_lineno or node.lineno) + 1


def _home_made(source: str) -> dict[int, int]:
    # Each function's body length by its def line, as the group counted it before lizard.
    code = _code_lines(source)
    found = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            last = node.end_lineno or node.lineno
            found[node.lineno] = readability.body_length(code, _first_statement(node), last)
    return found


def _statements(n: int, indent: str = "    ") -> str:
    return "".join(f"{indent}x{i} = {i}\n" for i in range(n))


def _lengths(report: Report) -> dict[str, tuple[int, str]]:
    # Each function-length finding's anchor, with its value and its state against the base.
    pairs = (zip(r.findings, r.states, strict=True) for r in report.runs)
    return {f.anchor: (f.value, s) for pair in pairs for f, s in pair if f.rule == LENGTH}


def test_twenty_five_lines_of_body_pass_and_twenty_six_fail(tmp_path: Path) -> None:
    # The Drive file's 25 lines in the body are 26 of lizard's NLOC, which counts the def line.
    root = _repo(tmp_path, {})
    full, over = "def full():\n" + _statements(25), "def over():\n" + _statements(26)
    _write(root, {f"{PKG}/new.py": full + "\n\n" + over})
    report = _check(root, "readability")
    assert _blocked(report) == {(LENGTH, "over")} and _lengths(report) == {"over": (27, "new")}
    said = "`over`: its length in lines of code (the def line counted) is 27, more than 26"
    assert [f.message for f in report.blocking] == [said]


def test_length_findings_are_anchored_by_qualified_name(tmp_path: Path) -> None:
    # lizard names a method without its class and a nested function outer.inner; the anchors are
    # the syntax tree's, as before lizard, so old findings still match. A nested function's body
    # is its own, not the body of the function around it, whose def line alone counts there.
    method = "class K:\n    def m(self):\n" + _statements(26, " " * 8)
    nested = "def outer():\n    def inner():\n" + _statements(26, " " * 8) + "    return inner()\n"
    root = _repo(tmp_path, {})
    _write(root, {f"{PKG}/new.py": method + "\n\n" + nested})
    report = _check(root, "readability")
    assert _blocked(report) == {(LENGTH, "K.m"), (LENGTH, "outer.inner")}
    assert _lengths(report) == {"K.m": (27, "new"), "outer.inner": (27, "new")}


def test_decorators_are_not_counted_and_the_lines_of_a_signature_are(tmp_path: Path) -> None:
    # lizard starts a function at its def line, below any decorator, and counts each line of a
    # signature spread over several with the body: 1 + 3 + 23 = 27 is over, 1 + 3 + 22 = 26 is
    # not. A docstring, a comment and a blank line are not lines of code to it.
    decorated = "@functools.lru_cache\n@functools.wraps(len)\ndef decorated():\n" + _statements(25)
    spread = "def spread(\n    a,\n    b,\n):\n" + _statements(23)
    documented = 'def documented(a):\n    """Three lines\n\n    of docstring."""\n'
    documented += "    # a comment\n\n" + _statements(25)
    root = _repo(tmp_path, {})
    module = "import functools\n\n\n" + "\n\n".join((decorated, spread, documented))
    _write(root, {f"{PKG}/new.py": module})
    report = _check(root, "readability")
    assert _blocked(report) == {(LENGTH, "spread")} and _lengths(report) == {"spread": (27, "new")}
    _write(root, {f"{PKG}/new.py": module.replace(_statements(23), _statements(22))})
    assert _check(root, "readability").passed


def test_lizard_agrees_with_the_count_it_replaced_on_plain_bodies(tmp_path: Path) -> None:
    # On a plain body of N statements the two counts agree, lizard's one more for the def line:
    # here a function, an async function, a method and a nested function, for seven N. The
    # function around the nested one is the documented exception: its def line, the nested def
    # line and its return are 3, whatever N, where the count it replaced took the nested body too.
    parts = []
    for n in (1, 2, 5, 24, 25, 26, 40):
        parts.append(f"def f{n}():\n" + _statements(n))
        parts.append(f"async def a{n}():\n" + _statements(n))
        parts.append(f"class C{n}:\n    def m(self):\n" + _statements(n, " " * 8))
        inner = "    def inner():\n" + _statements(n, " " * 8) + "    return inner\n"
        parts.append(f"def o{n}():\n" + inner)
    source, rel = "\n\n".join(parts), f"{PKG}/plain.py"
    _write(tmp_path, {rel: source})
    measured = readability._lizard(tmp_path, [rel])
    home = _home_made(source)
    assert len(measured) == 7 * 5 == len(home)
    for m in measured:
        if m.name.startswith("o") and "." not in m.name:
            assert m.value == 3 and home[m.line] == int(m.name[1:]) + 2, m.name
        else:
            assert m.value == home[m.line] + 1, m.name


def test_what_lizard_leaves_out_or_would_cut_short(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A function written on its def line is not listed by lizard and holds one line of code: no
    # finding and no refusal, as for a module that does not parse (lizard lists a function there,
    # the syntax tree has none at its line). lizard's forgiveness comment, its generated-code
    # marker and a line at the def's indentation after the parameters (a return annotation over
    # several lines, which lizard takes for the function's end) are refused by name instead:
    # nothing in the repository's files loosens the limit, and no short count is recorded.
    fine = "def one(a): return a\n\n\ndef two(\n    a,\n): return a\n\n\n"
    files = {f"{PKG}/fine.py": fine + "def three(a):\n    return a\n"}
    files[f"{PKG}/broken.py"] = "def broken(a:\n    return a\n"
    _write(tmp_path, files)
    assert [m.name for m in readability._lizard(tmp_path, sorted(files))] == ["three"]
    root = _repo(tmp_path, {})
    forgiven = "def hidden(a):\n    return a\n\n\n#lizard forgive\ndef kept(a):\n    return a\n"
    generated = "# GENERATED CODE\ndef gone(a):\n    return a\n"
    annotated = "def cut(a) -> dict[\n    str, int\n]:\n" + _statements(30)
    refused = {
        "`hidden` is not listed": forgiven,
        "`gone` is not listed": generated,
        "`cut` has a line at its def's indentation \\(line 3\\)": annotated,
    }
    for said, text in refused.items():
        _write(root, {f"{PKG}/new.py": text})
        with pytest.raises(RuntimeError, match=f"cannot trust {PKG}/new.py: {said}"):
            _check(root, "readability")
    assert main(["standards", "readability", "--root", str(root), "--base", "HEAD"]) == 2
    assert "readability: could not run: the length check cannot trust" in capsys.readouterr().out


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
    # Fifteen lines of about twelve of jscpd's tokens each: a clone under any floor.
    body = "".join(f"    total = total + values[{n}] * 3 - offset // {n + 2}\n" for n in range(12))
    return f"def {name}(values, offset):\n    total = 0\n{body}    return total\n"


# The function the token-window detector let through on 2026-10-08: 13 lines holding 57 of
# jscpd's tokens (4.4 a line) against its floor of 100. The first ten lines hold 48, fewer than
# jscpd's own default floor of 50.
SPARSE = """\
def NAME(items):
    total = 0
    for item in items:
        if item is None:
            continue
        total += int(item) * 3 + 1
        if total > 1000:
            total -= 500
        elif total < -1000:
            total += 500
        else:
            total = total % 997
    return total
"""

# Twelve lines of two or three tokens each, 35 in all: under the floor, so not a clone.
COLUMN = "def NAME():\n" + "".join(f"    value_{n} = {n}\n" for n in range(10)) + "    return 0\n"

COPIES = {f"{PKG}/copies.py": _block("first") + "\n\n" + _block("second")}
SMALL = {f"{PKG}/other.py": "def small(x):\n    return x\n"}


def _pair(text: str, tail_a: str = "", tail_b: str = "") -> dict[str, str]:
    # ``text`` once per file, under two function names, each file with its own ending.
    return {
        f"{PKG}/a.py": text.replace("NAME", "first") + tail_a,
        f"{PKG}/b.py": text.replace("NAME", "second") + tail_b,
    }


def _commit_all(root: Path, message: str) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)


def test_duplication_measures() -> None:
    assert duplication.within_limit(3, 100) and not duplication.within_limit(4, 100)
    lines = frozenset(range(1, 21))
    assert duplication.in_blocks(lines, ((5, 8), (30, 40))) == frozenset({5, 6, 7, 8})
    assert duplication.in_blocks(lines, ()) == frozenset()


def test_copied_new_code_blocks_and_a_clone_the_base_held_is_unchanged(tmp_path: Path) -> None:
    root = _repo(tmp_path, {})
    _write(root, COPIES)
    report = _check(root, "duplication")
    assert not report.passed and "30 of 30 new lines" in report.summary
    assert [(f.line, f.end_line, f.value) for f in report.blocking] == [(1, 15, 15), (18, 32, 15)]
    where = f"lines 1-15 repeat a block also found at {PKG}/copies.py:18, 15 of them new"
    assert report.blocking[0].message == where and set(_states(report).values()) == {"new"}
    (run,) = report.runs
    assert run.tool == "jscpd" and run.version.count(".") == 2, run.version

    _commit_all(root, "copies")
    _write(root, SMALL)
    report = _check(root, "duplication")
    assert report.passed and not report.blocking, report.summary
    assert set(_states(report).values()) == {"unchanged"}
    assert [f.level for f in report.runs[0].findings] == ["warning", "warning"]

    # The two names swapped: the two changed lines both lie in a clone the base held, which is the
    # same clone (the same text, copied from the same file) now holding new lines.
    _write(root, {f"{PKG}/copies.py": _block("second") + "\n\n" + _block("first")})
    report = _check(root, "duplication")
    assert not report.passed and "2 of 4 new lines" in report.summary  # with other.py's two
    assert set(_states(report).values()) == {"updated"} and len(report.blocking) == 2


def test_the_old_copy_of_a_block_newly_copied_is_recorded_and_does_not_block(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path, COPIES)
    _write(root, {f"{PKG}/third.py": _block("third")})
    report = _check(root, "duplication")
    assert not report.passed and "15 of 15 new lines" in report.summary
    assert {(f.path, f.line) for f in report.blocking} == {(f"{PKG}/third.py", 1)}
    # The old blocks' own clone is unchanged; the first of them, which jscpd pairs the new copy
    # with (each copy is paired with the first it saw), is new to the record and an error to no one.
    recorded = {
        (f.path, f.line, f.level, s)
        for f, s in zip(report.runs[0].findings, report.runs[0].states, strict=True)
        if f.path == f"{PKG}/copies.py"
    }
    assert recorded == {
        (f"{PKG}/copies.py", 1, "warning", "unchanged"),
        (f"{PKG}/copies.py", 18, "warning", "unchanged"),
        (f"{PKG}/copies.py", 1, "warning", "new"),
    }


def test_a_block_with_its_names_changed_is_not_a_copy(tmp_path: Path) -> None:
    root = _repo(tmp_path, {})
    other = _block("second").replace("total", "acc")
    _write(root, {f"{PKG}/copies.py": _block("first") + "\n\n" + other})
    assert _check(root, "duplication").passed


def test_the_floor_finds_ten_sparse_lines_and_not_a_column(tmp_path: Path) -> None:
    root = _repo(tmp_path, {})
    _write(root, _pair(SPARSE))
    report = _check(root, "duplication")
    assert not report.passed
    assert {(f.path, f.line, f.end_line) for f in report.blocking} == {
        (f"{PKG}/a.py", 1, 13),
        (f"{PKG}/b.py", 1, 13),
    }

    # Its first ten lines, each copy ending in its own way (jscpd may count the line where the
    # copies part as the block's last).
    ten = "\n".join(SPARSE.splitlines()[:10]) + "\n"
    _write(root, _pair(ten, "    return total\n", "    raise ValueError(total)\n"))
    report = _check(root, "duplication")
    assert not report.passed and len(report.blocking) == 2
    assert all(f.line == 1 and f.end_line in (10, 11) for f in report.blocking)

    # Under the floor: too few tokens in twelve lines, then too few lines however dense.
    _write(root, _pair(COLUMN))
    report = _check(root, "duplication")
    assert report.passed and not report.runs[0].findings, report.summary
    nine = "\n".join(_block("NAME").splitlines()[:9]) + "\n"
    _write(root, _pair(nine, "    return 1\n", "    raise ValueError(total)\n"))
    report = _check(root, "duplication")
    assert report.passed and not report.runs[0].findings, report.summary


def test_imports_and_docstrings_are_compared_with_nothing_and_counted_as_nothing(
    tmp_path: Path,
) -> None:
    # Twenty-two import lines (47 tokens) and a thirteen-line docstring: a clone each, were they
    # compared; and the only lines of code are the two assignments.
    imports = "from pkg.other import (\n" + "".join(f"    name_{n},\n" for n in range(20)) + ")\n"
    prose = "".join(f"Line {n} of the docstring, with a few words on it.\n" for n in range(11))
    doc = f'"""A module.\n{prose}"""\n'
    root = _repo(tmp_path, {})
    _write(root, {f"{PKG}/a.py": doc + imports + "\nA = 1\n"})
    _write(root, {f"{PKG}/b.py": doc + imports + "\nB = 2\n"})
    report = _check(root, "duplication")
    assert report.passed and not report.runs[0].findings, report.summary
    assert "0 of 2 new lines" in report.summary


def test_the_ignore_marker_is_refused_and_hides_nothing(tmp_path: Path) -> None:
    start, end = f"# {duplication.MARKER}-start\n", f"# {duplication.MARKER}-end\n"
    marked = {f"{PKG}/marked.py": start + _block("first") + end + "\n\n" + _block("second")}
    root = _repo(tmp_path, {})
    _write(root, marked)
    report = _check(root, "duplication")
    assert not report.passed and "2 comments hold" in report.summary
    assert {(f.rule, f.line) for f in report.blocking} == {
        (duplication.IGNORE.id, 1),
        (duplication.IGNORE.id, 17),
        (duplication.RULE.id, 2),
        (duplication.RULE.id, 20),
    }
    assert _states(report)[(duplication.IGNORE.id, start.strip())] == "new"

    # A string holding the text is not a comment: defused for jscpd, which would otherwise read it
    # there too, and not refused.
    strung = f'TEXT = "{duplication.MARKER}-start"\n' + _block("first") + "\n\n" + _block("second")
    _write(root, {f"{PKG}/marked.py": strung})
    report = _check(root, "duplication")
    rules = {(f.rule, f.line) for f in report.blocking}
    assert rules == {(duplication.RULE.id, 2), (duplication.RULE.id, 19)}

    # A marker the base held is unchanged and does not block, and the clone behind it is seen.
    _write(root, marked)
    _commit_all(root, "marked")
    _write(root, SMALL)
    report = _check(root, "duplication")
    assert report.passed and set(_states(report).values()) == {"unchanged"}
    assert len(report.runs[0].findings) == 4


def test_a_jscpd_configuration_file_changes_nothing(tmp_path: Path) -> None:
    # Were it read, the file would ignore the copies, set the floors out of reach and allow 100%.
    config = json.dumps(
        {
            "threshold": 100,
            "ignore": ["**/copies.py"],
            "minTokens": 100000,
            "min_tokens": 100000,
            "minLines": 1000,
            "min_lines": 1000,
            "mode": "strict",
        }
    )
    planted = {".jscpd.json": config, "src/.jscpd.json": config, f"{PKG}/.jscpd.json": config}
    root = _repo(tmp_path, planted)
    _write(root, COPIES)
    report = _check(root, "duplication")
    assert not report.passed and [f.line for f in report.blocking] == [1, 18]


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
    assert security.level_for("HIGH", "MEDIUM") == "error"
    assert security.level_for("MEDIUM", "LOW") == "note"
    assert security.level_for("LOW", "HIGH") == "note"


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
        with:
          persist-credentials: false
      - uses: ./local-action
      - run: echo "$TITLE"
        env:
          TITLE: ${{{{ github.event.issue.title }}}}
      - run: |
          curl -fsSL -o "$RUNNER_TEMP/tool" https://example.invalid/tool
          echo "{"0" * 64}  $RUNNER_TEMP/tool" | sha256sum -c -
          chmod +x "$RUNNER_TEMP/tool"
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
      - run: |
          wget -q https://example.invalid/tool.tgz
          tar xzf tool.tgz && ./tool --version
  reuse:
    uses: some/repo/.github/workflows/x.yml@main
"""

# A third party's action and an install outside a lockfile, with zizmor told to look away in every
# way it accepts: an inline comment (spelled from its parts, since the gate refuses one written
# out), and an ignore rule in both places a configuration file lives.
LOOK_AWAY_COMMENT = "# " + "zizmor: " + "ignore[unpinned-uses]"
THIRD = f"""\
name: third
on: [push]
permissions:
  contents: read
jobs:
  j:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@{SHA}
        with:
          persist-credentials: false
      - uses: someone/thing@v1 {LOOK_AWAY_COMMENT}
      - run: npm install -g foo
"""
LOOK_AWAY = "rules:\n  unpinned-uses:\n    ignore:\n      - third.yml\n"
LOOK_AWAY += "  adhoc-packages:\n    ignore:\n      - third.yml\n"


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
    assert supply_chain.level_for("High") == supply_chain.level_for("Medium") == "error"
    assert supply_chain.level_for("Low") == supply_chain.level_for("Informational") == "note"


def test_a_download_the_step_goes_on_to_use_needs_a_digest_check() -> None:
    fetch = 'curl -fsSL -o /usr/local/bin/pixi \\\n  "https://example.invalid/v${V}/pixi"\n'
    use = "chmod 0755 /usr/local/bin/pixi\npixi --version\n"
    assert supply_chain.unchecked_download(fetch + use)
    assert supply_chain.unchecked_download("wget https://example.invalid/a.tgz\ntar xzf a.tgz\n")
    assert supply_chain.unchecked_download("curl -O https://example.invalid/a.sh && bash a.sh\n")
    assert not supply_chain.unchecked_download(fetch)  # fetched and left alone
    # Checked against a digest, in either form the rule names, before the use.
    digest = "0" * 64
    check = f'echo "{digest}  /usr/local/bin/pixi" | sha256sum -c -\n'
    assert not supply_chain.unchecked_download(fetch + check + use)
    check = 'echo "$PIXI_SHA256  /usr/local/bin/pixi" | sha256sum --status --check -\n'
    assert not supply_chain.unchecked_download(fetch + check + use)
    check = f'test "$(sha256sum /usr/local/bin/pixi | cut -c1-64)" = {digest} || exit 1\n'
    assert not supply_chain.unchecked_download(fetch + check + use)
    # A download piped into a shell is the pipe rule's, and a pipe to stdout fetches no file.
    assert not supply_chain.unchecked_download("curl -fsSL https://example.invalid/i.sh | sh\n")
    assert not supply_chain.unchecked_download("wget -qO- https://example.invalid/i.sh | sh\n")
    assert not supply_chain.unchecked_download("wget -O - https://example.invalid/i | bash\n")


def test_workflows_are_held_to_scorecards_checks(tmp_path: Path) -> None:
    root = _repo(tmp_path, {})
    _write(root, {".github/workflows/good.yml": GOOD, ".github/workflows/bad.yaml": BAD})
    report = _check(root, "supply-chain")
    assert not report.passed
    found = {(f.path.rsplit("/", 1)[-1], f.rule, f.line) for f in report.blocking}
    assert {f for f in found if f[1].startswith("supply-chain/")} == {
        ("bad.yaml", "supply-chain/token-permissions", 4),
        ("bad.yaml", "supply-chain/pinned-action", 9),
        ("bad.yaml", "supply-chain/untrusted-checkout", 11),
        ("bad.yaml", "supply-chain/script-injection", 13),
        ("bad.yaml", "supply-chain/pipe-to-shell", 14),
        ("bad.yaml", "supply-chain/unchecked-download", 16),
        ("bad.yaml", "supply-chain/pinned-action", 19),
    }
    # zizmor's own reading of the same file, each at the line of its primary location, Medium and
    # High both errors; good.yml gives it nothing above Low.
    assert {f for f in found if f[1].startswith("zizmor/")} == {
        ("bad.yaml", "zizmor/dangerous-triggers", 2),
        ("bad.yaml", "zizmor/excessive-permissions", 4),
        ("bad.yaml", "zizmor/artipacked", 9),
        ("bad.yaml", "zizmor/unpinned-uses", 9),
        ("bad.yaml", "zizmor/template-injection", 13),
        ("bad.yaml", "zizmor/unpinned-uses", 19),
    }
    reader, audits = report.runs
    assert (reader.tool, audits.tool) == ("beethoven-qa", "zizmor")
    assert re.fullmatch(r"\d+\.\d+\.\d+", audits.version), audits.version


def test_zizmor_is_read_at_the_regular_persona_and_nothing_tells_it_to_look_away(
    tmp_path: Path,
) -> None:
    files = {"zizmor.yml": LOOK_AWAY, ".github/zizmor.yml": LOOK_AWAY}
    root = _repo(tmp_path, files)
    _write(root, {".github/workflows/third.yml": THIRD})
    report = _check(root, "supply-chain")
    assert not report.passed
    # The reader refuses the tag too; zizmor's own reading sits beside it.
    rules = [f.rule for f in report.blocking]
    assert rules == ["supply-chain/pinned-action", "zizmor/unpinned-uses"]
    stop = report.blocking[1]
    assert (stop.rule, stop.line, stop.level) == ("zizmor/unpinned-uses", 12, "error")
    assert stop.anchor == f"- uses: someone/thing@v1 {LOOK_AWAY_COMMENT}"
    assert "action is not pinned to a hash" in stop.message and "high severity" in stop.message
    audits = report.runs[1]
    notes = [f for f in audits.findings if f.level == "note"]
    assert [(f.rule, f.line) for f in notes] == [("zizmor/adhoc-packages", 13)]  # recorded only
    rule = next(r for r in audits.rules if r.id == "zizmor/adhoc-packages")
    assert rule.help_uri == "https://docs.zizmor.sh/audits/#adhoc-packages"
    log = json.loads(json.dumps(sarif_log(report.runs)))
    validate_sarif(log)
    result = log["runs"][1]["results"][0]
    assert result["locations"][0]["physicalLocation"]["region"]["startLine"] == 12
    # Committed, moved down a line, the finding is the base's and passes.
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "third")
    _write(root, {".github/workflows/third.yml": "# moved down\n" + THIRD})
    report = _check(root, "supply-chain")
    assert report.passed
    assert _states(report)[("zizmor/unpinned-uses", stop.anchor)] == "unchanged"


def test_a_zizmor_that_fails_fails_the_group_in_its_own_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path, {})
    _write(root, {".github/workflows/good.yml": GOOD})
    stub = tmp_path / "zizmor"
    stub.write_text("#!/bin/sh\necho 'boom' >&2\nexit 1\n", encoding="utf-8")
    stub.chmod(0o755)
    monkeypatch.setattr(supply_chain, "_bin", lambda name: str(stub))
    with pytest.raises(RuntimeError, match="zizmor failed: boom"):
        _check(root, "supply-chain")


def test_a_file_zizmor_cannot_read_as_a_workflow_is_passed_over(tmp_path: Path) -> None:
    # The reader passes over what does not parse as a mapping; zizmor, told so by its own exit,
    # collected nothing, which is no finding and no failure.
    root = _repo(tmp_path, {})
    _write(root, {".github/workflows/notes.yml": "- not a workflow\n"})
    report = _check(root, "supply-chain")
    assert report.passed and all(not run.findings for run in report.runs)


def test_this_repositorys_own_workflows_pass_the_reader_and_zizmor() -> None:
    # Every finding new (no base): the tree passes only if nothing of error level is in it. The
    # one note is zizmor's Low for the npm install of the Claude Code CLI, which has no lockfile
    # to pin it (recorded, accepted; supply_chain's docstring).
    context = Context(REPO, None, "src/beethoven_qa", Changes((), (), True))
    report = supply_chain._check(context)
    assert report.passed, report.summary
    recorded = {(f.rule, f.level) for run in report.runs for f in run.findings}
    assert recorded == {("zizmor/adhoc-packages", "note")}
    # The hook is no workflow, so the reader never sees it; its download keeps the rule anyway.
    hook = (REPO / ".claude" / "hooks" / "session-start.sh").read_text(encoding="utf-8")
    assert supply_chain._FETCH.search(hook) and not supply_chain.unchecked_download(hook)


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
    # The reader's seven and zizmor's six: one over the answer's cap, so twelve are said and the
    # last is counted.
    assert len(said) == 12 and "supply-chain/pinned-action" in told and "and 1 more" in told
    assert "zizmor/template-injection" in told


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
