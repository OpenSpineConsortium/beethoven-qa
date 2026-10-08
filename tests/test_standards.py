"""The standards' shared parts: matching a finding with the base's, reading git's account of the
new lines, and the SARIF log.

CrossHair proves the contracted functions within their bound. These tests pin the cases a reader
checks first (a finding that only moved, one made worse, one the base never had), read what real
git prints for file names that need care (a space, a tab, a non-ASCII letter, a renamed file), and
validate every SARIF log against the OASIS schema kept in ``tests/data``.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import jsonschema
import pytest

from beethoven_qa.contracts import ViolationError
from beethoven_qa.standards.findings import (
    Finding,
    Measure,
    blocking,
    classify,
    fingerprint,
    moved,
    over_limit,
)
from beethoven_qa.standards.newcode import Changes, changed_lines, new_lines, renamed_files
from beethoven_qa.standards.sarif import FINGERPRINT, Rule, Run, sarif_log

SCHEMA = Path(__file__).parent / "data" / "sarif-schema-2.1.0.json"


def _f(
    anchor: str, value: int, *, path: str = "a.py", level: str = "error", line: int = 1
) -> Finding:
    return Finding("r/x", path, line, line, anchor, value, 10, level, f"{anchor} is {value}")


def _git(cwd: Path, *args: str, env: dict[str, str]) -> str:
    out = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True, env=env
    )
    return out.stdout


@pytest.fixture
def git_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    # Hermetic git for the throwaway repositories: no global or system configuration (a signing
    # key or diff.noPrefix would change what is measured), a fixed author, and no repository named
    # by the environment (a pre-push hook's GIT_DIR).
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CONFIG_PARAMETERS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("GIT_CONFIG_COUNT", raising=False)
    values = {
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return dict(os.environ)


# --- matching with the base ---------------------------------------------------------------------


def test_a_finding_the_base_never_had_is_new() -> None:
    assert classify((_f("f", 20),), ()) == ("new",)
    assert classify((_f("f", 20),), (_f("g", 20),)) == ("new",)
    assert classify((_f("f", 20),), (_f("f", 20, path="b.py"),)) == ("new",)


def test_a_finding_that_only_moved_is_unchanged_and_a_worse_one_is_updated() -> None:
    assert classify((_f("f", 20, line=90),), (_f("f", 20, line=3),)) == ("unchanged",)
    assert classify((_f("f", 18),), (_f("f", 20),)) == ("unchanged",)  # better still counts
    assert classify((_f("f", 21),), (_f("f", 20),)) == ("updated",)


def test_findings_that_share_a_key_pair_worst_with_worst() -> None:
    # Two functions of one name (in two branches of an if): one made worse, one added.
    head = (_f("f", 12), _f("f", 30), _f("f", 25))
    base = (_f("f", 20), _f("f", 12))
    assert classify(head, base) == ("new", "updated", "updated")
    assert classify(base, base) == ("unchanged", "unchanged")


def test_only_new_or_worse_errors_block() -> None:
    head = (_f("a", 20), _f("b", 20), _f("c", 20, level="warning"), _f("d", 20, level="note"))
    states = ("new", "unchanged", "new", "updated")
    assert blocking(head, states) == (head[0],)
    with pytest.raises(ViolationError):
        blocking(head, ("new",))


def test_a_renamed_file_keeps_its_findings() -> None:
    base = (_f("f", 20, path="old.py"), _f("g", 20, path="kept.py"))
    head = (_f("f", 20, path="new.py"), _f("g", 20, path="kept.py"))
    assert classify(head, base) == ("new", "unchanged")
    assert classify(head, moved(base, (("old.py", "new.py"),))) == ("unchanged", "unchanged")


def test_the_fingerprint_ignores_the_line_and_keeps_the_parts_apart() -> None:
    assert fingerprint("r", "a.py", "f") == fingerprint("r", "a.py", "f")
    assert fingerprint("r", "ab", "c") != fingerprint("r", "a", "bc")
    assert fingerprint("r", "a.py", "f") != fingerprint("s", "a.py", "f")


def test_over_limit() -> None:
    m = Measure("readability/x", "a.py", "C.f", 3, 9, 16)
    assert over_limit(m, 16, "size") is None
    found = over_limit(m, 15, "size", ("t",))
    assert found is not None
    got = (found.anchor, found.line, found.end_line, found.value, found.limit)
    assert got == ("C.f", 3, 9, 16, 15)
    assert found.message == "`C.f`: its size is 16, more than 15" and found.tags == ("t",)


# --- the new lines, as git says -----------------------------------------------------------------


def test_hunk_headers_and_the_header_of_a_file() -> None:
    diff = "\n".join(
        [
            "diff --git a/x.py b/x.py",
            "--- a/x.py",
            "+++ b/x.py",
            "@@ -1,0 +2,3 @@",
            "+one",
            "+++ b/evil.py",  # a content line that looks like a header: a line of x.py's hunk
            "+three",
            "@@ -9 +11 @@",
            "@@ -20,4 +22,0 @@",  # removes only
            "diff --git a/gone.py b/gone.py",
            "--- a/gone.py",
            "+++ /dev/null",
            "@@ -1,2 +0,0 @@",
        ]
    )
    assert changed_lines(diff) == (("x.py", ((2, 4), (11, 11))),)


def test_a_billion_line_hunk_costs_nothing() -> None:
    diff = "diff --git a/x b/x\n+++ b/x\n@@ -1 +1,999999999 @@\n"
    assert changed_lines(diff) == (("x", ((1, 999999999),)),)
    changes = Changes(changed_lines(diff), (), False)
    assert new_lines(changes, "x", frozenset({5, 10**9 + 1})) == frozenset({5})


def test_new_lines() -> None:
    changes = Changes((("a.py", ((2, 3), (7, 7))),), (), False)
    lines = frozenset(range(1, 10))
    assert new_lines(changes, "a.py", lines) == frozenset({2, 3, 7})
    assert new_lines(changes, "b.py", lines) == frozenset()
    assert new_lines(Changes((), (), True), "b.py", lines) == lines


def test_what_real_git_prints_for_names_that_need_care(
    tmp_path: Path, git_env: dict[str, str]
) -> None:
    # A space (git ends the header with a tab), a tab (git quotes the name) and a non-ASCII letter
    # (left alone under core.quotePath=false), and a file renamed with one line changed.
    names = ["my file.py", "tab\there.py", "é.py"]
    _git(tmp_path, "init", "-q", "-b", "main", env=git_env)
    body = "".join(f"line {n}\n" for n in range(1, 21))
    (tmp_path / "old.py").write_text(body, encoding="utf-8")
    _git(tmp_path, "add", "-A", env=git_env)
    _git(tmp_path, "commit", "-q", "-m", "base", env=git_env)
    for name in names:
        (tmp_path / name).write_text("a\nb\n", encoding="utf-8")
    (tmp_path / "old.py").rename(tmp_path / "new.py")
    (tmp_path / "new.py").write_text(body.replace("line 7\n", "line seven\n"), encoding="utf-8")
    _git(tmp_path, "add", "-A", env=git_env)
    quiet = ("-c", "core.quotePath=false")
    plain = ("--no-color", "--src-prefix=a/", "--dst-prefix=b/", "-M")
    cached = (*quiet, "diff", "--cached")
    diff = _git(tmp_path, *cached, "--unified=0", *plain, "HEAD", env=git_env)
    status = _git(tmp_path, *cached, "--name-status", "-M", "-z", "HEAD", env=git_env)
    found = dict(changed_lines(diff))
    assert {name: found[name] for name in names} == {name: ((1, 2),) for name in names}
    assert found["new.py"] == ((7, 7),)
    assert renamed_files(status) == (("old.py", "new.py"),)


def test_renamed_files_reads_renames_only() -> None:
    fields = ["M", "a.py", "R087", "old.py", "new.py", "C100", "src.py", "copy.py", "D", "gone.py"]
    text = "\x00".join([*fields, "R100", "cut"])  # the last entry cut short
    assert renamed_files(text) == (("old.py", "new.py"),)
    assert renamed_files("") == ()


# --- SARIF --------------------------------------------------------------------------------------


def _validator() -> jsonschema.protocols.Validator:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    return jsonschema.Draft4Validator(schema)


def validate_sarif(log: dict[str, object]) -> None:
    """Raise if ``log`` is not a valid SARIF 2.1.0 log, by the OASIS schema."""
    _validator().validate(json.loads(json.dumps(log)))


def test_a_log_validates_and_carries_what_is_matched_on() -> None:
    rule = Rule("r/x", "X", "x is at most 10.", "a standard", "https://example.invalid/x", ("t",))
    head = (_f("f", 20, line=4), _f("g", 20, level="note"))
    states = ("new", "unchanged")
    run = Run("group", "tool", "1.0.0", "https://example.invalid", (rule,), head, states)
    log = sarif_log((run, run._replace(findings=(), states=())))
    validate_sarif(log)
    result = log["runs"][0]["results"][0]  # type: ignore[index]
    assert result["baselineState"] == "new" and result["ruleIndex"] == 0
    assert result["partialFingerprints"] == {FINGERPRINT: fingerprint("r/x", "a.py", "f")}
    assert result["locations"][0]["physicalLocation"]["region"] == {"startLine": 4, "endLine": 4}
    assert log["runs"][1]["results"] == []  # type: ignore[index]


def test_a_finding_of_a_rule_the_run_does_not_list_is_refused() -> None:
    rule = Rule("r/y", "Y", "y.", "s", "https://example.invalid/y")
    run = Run("group", "tool", "1.0.0", "https://example.invalid", (rule,), (_f("f", 1),), ("new",))
    with pytest.raises(ViolationError):
        sarif_log((run,))


def test_a_tool_version_is_the_first_dotted_number_of_its_first_line() -> None:
    # coverage.py says "Coverage.py, version 7.16.2 with C extension", so the last word is not the
    # version (the log said "extension" until 5.0.0); ruff, bandit, mypy and jscpd put it last or
    # before a parenthesis; radon and lizard print it alone. A tool that says none is "unknown".
    from beethoven_qa.standards.group import _version_word

    assert _version_word("Coverage.py, version 7.16.2 with C extension") == "7.16.2"
    assert _version_word("ruff 0.16.10") == "0.16.10"
    assert _version_word("mypy 2.4.0 (compiled: yes)") == "2.4.0"
    assert _version_word("6.0.1") == "6.0.1"
    assert _version_word("Changes") == "unknown"
    assert _version_word("") == "unknown"
