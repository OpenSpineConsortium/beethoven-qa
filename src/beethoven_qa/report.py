"""The evidence report: ``python -m beethoven_qa report [--root DIR] [--out DIR]``, what a person
hands to anyone who doubts code written by an AI.

Why it exists: the gate's verdicts are spread over a hook's output, a CI log and six SARIF files,
and each says "passed" without saying who decided, with which tool at which version, and how much
of it was a proof and how much a bounded search. The report runs every lane the gate runs, each
command as its own process in the environment the report was started in, keeps every output
whole, and writes two files a person can hand over: ``report.md`` for a reader and ``report.json``
for a program. The lane commands are read from ``pixi.toml``'s ``[tasks]`` table, where the hook
and CI read theirs, so the report runs what they run and not a list of its own; the standards lane
is also given ``--sarif`` so its logs are kept and counted. Each command of a task runs even when
an earlier one of the same task failed, where ``&&`` would stop: a report shows every lane.

What it records: the repository (HEAD, branch, whether the tree was clean when the report began,
the origin's address with any credential removed); the environment (the versions pixi.lock pins
for the tools that decide, and what each tool says of itself); every lane's argv exactly as run,
exit code, wall time and output file; the gate's counts (per target, the totals, the functions
set aside with their reasons), so a reader sees how many conditions were confirmed over all paths
and how many were a bounded search that found nothing; each standards group's verdict and the
counts of its SARIF log by level and by state against the base; the enforcement facts (the hook
git runs, the required checks of the branch rule, the workflows and their jobs); who decides each
verdict; where a language model is involved at all; and what the report does not show.

Exit 0 when every lane passed, 1 when one did not (the report is written either way and says
which), 2 when the command cannot run (no ``pyproject.toml``, no git, no lane tasks in
``pixi.toml``). The parsing, counting, table building and rendering are public, pure and
contracted, and CrossHair proves them; every process, read, write and look at the clock is a
private function, called by no contracted one.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

import yaml
from beartype import beartype
from pydantic import BaseModel, ValidationError

from beethoven_qa import __version__
from beethoven_qa.contracts import ensure, require
from beethoven_qa.standards.group import _bin
from beethoven_qa.vendor import VENDOR_DIR, _git

__all__ = [
    "LANES",
    "LANGUAGE_MODEL",
    "NOT_IN_LOCK",
    "NOT_SHOWN",
    "PINNED",
    "SECTIONS",
    "VENDOR_LANE",
    "Enforcement",
    "Evidence",
    "GateSummary",
    "GroupSummary",
    "Lane",
    "Repository",
    "Target",
    "Tool",
    "Totals",
    "conda_name_version",
    "count_by",
    "deciders",
    "exit_code",
    "gate_summary",
    "group_verdicts",
    "hook_command",
    "pinned",
    "render",
    "split_commands",
    "strip_credentials",
    "table",
]

# The tasks of pixi.toml the gate depends on, in the order the gate runs them; the report refuses
# a pixi.toml that lacks one, since it would then run something other than the gate.
LANES: tuple[str, ...] = ("lint", "typecheck", "test", "boundary", "symbolic", "standards")
# A consumer's seventh lane, run when the pinned copy is there; its default when no task names it.
VENDOR_LANE = "vendor-check"
_VENDOR_COMMAND = "python -m beethoven_qa vendor-check"
# The packages of the lock that decide a verdict, in the order the environment table lists them.
PINNED: tuple[str, ...] = (
    "python",
    "ruff",
    "mypy",
    "pytest",
    "typeguard",
    "beartype",
    "icontract",
    "pydantic",
    "crosshair-tool",
    "z3-solver",
    "complexipy",
    "radon",
    "bandit",
    "coverage",
    "lizard",
    "jscpd",
    "zizmor",
)
NOT_IN_LOCK = "not in the lock"
# The tools that answer --version with their first line; the others say nothing of themselves.
_VERSION_FLAG: tuple[str, ...] = (
    "python",
    "ruff",
    "mypy",
    "pytest",
    "complexipy",
    "radon",
    "bandit",
    "coverage",
    "lizard",
    "jscpd",
    "zizmor",
)
# The headings of report.md, in order.
SECTIONS: tuple[str, ...] = (
    "The repository",
    "The lanes",
    "The environment",
    "The gate",
    "The code standards",
    "Enforcement",
    "Who decides each verdict",
    "Where a language model is involved",
    "What this report does not show",
)
LANGUAGE_MODEL: tuple[str, ...] = (
    "The plugin's workflows (gate-until-green, verify-module, improve-module), its skills and its "
    "stream agent have a language model write code and contracts; the gate then proves the "
    "contracts, and a person reviews what they mean.",
    "The plugin's coaching hook has a language model read a verdict: after an edit it is shown "
    "what the code standards would block in that file, and the edit stays as it is.",
    "No verdict in this report comes from a model. Each is a tool's, the gate's contracted "
    "code's, or GitHub's rule's, as the table above says.",
)
NOT_SHOWN: tuple[str, ...] = (
    'A condition "not confirmed" is a bounded search that found no counterexample within its '
    "seconds; it is not a proof. Only a condition confirmed over all paths is one.",
    "The code standards block only code new or worse since the base commit; the findings of "
    "older code are recorded as unchanged and pass.",
    "The tests pass; what each test means, and whether it is the right test, is a person's "
    "judgement, and so is what each contract promises.",
    "The gate runs on the machine that pushes and again on GitHub's runner; this report is as "
    "true as the machine that made it, and no truer. Its lanes ran in that machine's environment.",
)

_GATE = "gate: "
_COUNTS = "gate: conditions: "
_UNPROVEN = "gate: not proven, contracts checked at run time only: "
_STANDARDS = "standards: "
# What the gate prints after the counts of a condition: the totals line and a target's line
# differ only in the "of N" after the first count and in "s per condition" against "s".
_TOTALS_PHRASES = (
    " of ",
    " confirmed over all paths, ",
    " not confirmed within ",
    " s per condition, ",
    " unable to meet a precondition",
)
_TARGET_PHRASES = (
    " confirmed over all paths, ",
    " not confirmed within ",
    " s, ",
    " unable to meet a precondition",
)
_VERDICTS = ("passed", "FAILED", "could not run")


class Repository(NamedTuple):
    """The checkout the report is about, as git told it before anything was written."""

    head: str
    branch: str  # "HEAD" when detached
    clean: bool  # git status --porcelain printed nothing
    origin: str  # the origin remote's address without any credential; "" when there is none


class Tool(NamedTuple):
    """One package that decides a verdict: what the lock pins and what the tool says of itself."""

    name: str
    locked: str  # the version in pixi.lock, or NOT_IN_LOCK
    reported: str  # the first line of its --version, "" when it has none, or why it did not answer


class Lane(NamedTuple):
    """One command of one task, as it ran."""

    task: str
    argv: tuple[str, ...]
    exit: int
    seconds: float
    output: str  # the file holding what it printed, relative to the report directory


class Target(NamedTuple):
    """The gate's counts for one contracted function or class."""

    name: str
    confirmed: int
    not_confirmed: int
    bound: int  # the seconds each condition was given
    unable: int


class Totals(NamedTuple):
    """The gate's counts line over every target."""

    confirmed: int
    conditions: int
    not_confirmed: int
    bound: int
    unable: int


class GateSummary(NamedTuple):
    """What the symbolic lane's output says, read back."""

    totals: Totals | None  # None when the gate printed no counts line (a release before 5.0.0)
    targets: tuple[Target, ...]
    unproven: tuple[tuple[str, str], ...]  # (function, the reason it is not a target)


class GroupSummary(NamedTuple):
    """One standards group: its printed verdict and what its SARIF log holds."""

    group: str
    verdict: str  # passed, FAILED, could not run, or "not run" when the lane said nothing
    tools: tuple[tuple[str, str], ...]  # (name, version) of each run's tool
    levels: tuple[tuple[str, int], ...]  # results by level
    states: tuple[tuple[str, int], ...]  # results by baselineState


class Enforcement(NamedTuple):
    """What makes the gate run before a push and before a merge, as the files say."""

    hooks_path: str  # git config core.hooksPath, "" when unset
    hook_present: bool  # .githooks/pre-push exists
    hook_executable: bool
    hook_command: str  # what the hook hands over to, "" when it cannot be told
    required: tuple[str, ...]  # the required status checks of .github/rulesets/main.json
    strict: bool | None  # its strict flag; None when the file or the rule is missing
    workflows: tuple[tuple[str, tuple[str, ...]], ...]  # (file, its jobs' names)


class Evidence(NamedTuple):
    """Everything the two report files are written from."""

    made: str  # when, UTC, to the second
    version: str  # beethoven-qa's
    repository: Repository
    tools: tuple[Tool, ...]
    lanes: tuple[Lane, ...]
    gate: GateSummary
    standards: tuple[GroupSummary, ...]
    enforcement: Enforcement
    decided: tuple[tuple[str, str], ...]  # (check, who decides it)
    passed: bool


# --- parsing -------------------------------------------------------------------------------------


@beartype
@ensure(
    lambda result: all(c and "&&" not in c and c == c.strip() for c in result),
    "each command stands alone, trimmed",
)
@ensure(lambda result, task: all(c in task for c in result), "every command is a piece of the task")
@ensure(
    lambda result, task: len(result) <= task.count("&&") + 1, "no more pieces than the task has"
)
@ensure(
    lambda result, task: (len(result) == 0) == all(not p.strip() for p in task.split("&&")),
    "nothing to run only when the task says nothing",
)
def split_commands(task: str) -> tuple[str, ...]:
    """The commands of a pixi task string, one per ``&&``; the report runs each as its own process
    (pixi's shell would stop at the first failure, a report shows every one)."""
    return tuple(p.strip() for p in task.split("&&") if p.strip())


def _numbers(text: str, phrases: tuple[str, ...]) -> tuple[int, ...] | None:
    # ``text`` as "<n>phrase<n>phrase...<n>phrase" with nothing after the last phrase; each number
    # a plain decimal as the gate prints it (no sign, no leading zero, ASCII), so what is read
    # prints back as the same text. None for anything else.
    found: list[int] = []
    rest = text
    for phrase in phrases:
        digits, sep, rest = rest.partition(phrase)
        if not sep or not digits.isascii() or not digits.isdecimal() or digits != str(int(digits)):
            return None
        found.append(int(digits))
    return tuple(found) if rest == "" else None


def _totals_line(line: str) -> Totals | None:
    if not line.startswith(_COUNTS):
        return None
    numbers = _numbers(line[len(_COUNTS) :], _TOTALS_PHRASES)
    return Totals(*numbers) if numbers is not None else None


def _target_line(line: str) -> Target | None:
    if not line.startswith(_GATE):
        return None
    name, sep, rest = line[len(_GATE) :].partition(": ")
    if not sep or not name or " " in name or ":" in name:
        return None
    numbers = _numbers(rest, _TARGET_PHRASES)
    return Target(name, *numbers) if numbers is not None else None


def _unproven_line(line: str) -> tuple[str, str] | None:
    if not line.startswith(_UNPROVEN):
        return None
    name, sep, why = line[len(_UNPROVEN) :].partition(": ")
    return (name, why) if sep else None


def _printed_target(t: Target) -> str:
    return (
        f"{_GATE}{t.name}: {t.confirmed} confirmed over all paths, {t.not_confirmed} not confirmed "
        f"within {t.bound} s, {t.unable} unable to meet a precondition"
    )


def _printed_totals(t: Totals) -> str:
    return (
        f"{_COUNTS}{t.confirmed} of {t.conditions} confirmed over all paths, {t.not_confirmed} "
        f"not confirmed within {t.bound} s per condition, {t.unable} unable to meet a precondition"
    )


@beartype
@ensure(
    lambda result, lines: result.totals is None or _printed_totals(result.totals) in lines,
    "the totals, printed back, are a line of the output",
)
@ensure(
    lambda result, lines: all(_printed_target(t) in lines for t in result.targets),
    "every target, printed back, is a line of the output",
)
@ensure(
    lambda result, lines: all(f"{_UNPROVEN}{n}: {why}" in lines for n, why in result.unproven),
    "every function set aside, printed back with its reason, is a line of the output",
)
@ensure(
    lambda result, lines: (
        len(result.unproven)
        == sum(1 for x in lines if x.startswith(_UNPROVEN) and ": " in x[len(_UNPROVEN) :])
    ),
    "every line that sets a function aside is read",
)
@ensure(
    lambda result, lines: (
        len(result.targets) + len(result.unproven) + (result.totals is not None) <= len(lines)
    ),
    "no line is read as two things",
)
def gate_summary(lines: tuple[str, ...]) -> GateSummary:
    """What the gate's output says: its counts line (the last one, when it printed one), each
    target's counts, and the functions set aside under ``unproven`` with their reasons."""
    totals = None
    targets: list[Target] = []
    unproven: list[tuple[str, str]] = []
    for line in lines:
        if (whole := _totals_line(line)) is not None:
            totals = whole
        elif (target := _target_line(line)) is not None:
            targets.append(target)
        elif (entry := _unproven_line(line)) is not None:
            unproven.append(entry)
    return GateSummary(totals, tuple(targets), tuple(unproven))


def _verdict_line(line: str) -> tuple[str, str] | None:
    # "standards: <group>: passed|FAILED|could not run: <summary>", as run.py prints it.
    if not line.startswith(_STANDARDS):
        return None
    group, sep, rest = line[len(_STANDARDS) :].partition(": ")
    verdict, said, _ = rest.partition(": ")
    if not (sep and said and group) or " " in group or verdict not in _VERDICTS:
        return None
    return group, verdict


@beartype
@ensure(
    lambda result: all(g and " " not in g and v in _VERDICTS for g, v in result),
    "a group's name and one of the verdicts the standards print",
)
@ensure(
    lambda result, lines: all(
        any(x.startswith(f"{_STANDARDS}{g}: {v}: ") for x in lines) for g, v in result
    ),
    "every verdict read is one a line of the output printed",
)
@ensure(lambda result, lines: len(result) <= len(lines), "at most one verdict per line")
def group_verdicts(lines: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    """The ``(group, verdict)`` of every verdict line the standards lane printed, in order."""
    return tuple(v for line in lines if (v := _verdict_line(line)) is not None)


# --- counting ------------------------------------------------------------------------------------


@beartype
@ensure(lambda result, items: sum(n for _, n in result) == len(items), "every item counted once")
@ensure(
    lambda result, items: [k for k, _ in result] == list(dict.fromkeys(items)),
    "each distinct item once, in the order first seen",
)
@ensure(
    lambda result, items: all(n == items.count(k) for k, n in result), "the count is the item's"
)
def count_by(items: tuple[str, ...]) -> tuple[tuple[str, int], ...]:
    """How many times each distinct item occurs, in the order first seen."""
    counts: dict[str, int] = {}
    for item in items:
        counts[item] = counts.get(item, 0) + 1
    return tuple(counts.items())


@beartype
@ensure(lambda result: result in (0, 1), "the command's own exit code for the lanes' verdict")
@ensure(lambda result, exits: (result == 0) == all(e == 0 for e in exits), "0 only when all passed")
def exit_code(exits: tuple[int, ...]) -> int:
    """The report command's exit code from the lanes': 0 when every lane passed, else 1."""
    return 0 if all(e == 0 for e in exits) else 1


# --- the environment -----------------------------------------------------------------------------


@beartype
@ensure(
    lambda result, filename: result is None or filename.startswith(f"{result[0]}-{result[1]}-"),
    "the name and the version are the file name's first two parts, dash-separated",
)
@ensure(
    lambda result: result is None or (len(result[0]) > 0 and len(result[1]) > 0),
    "neither part is empty",
)
@ensure(lambda result: result is None or "-" not in result[1], "a version holds no dash")
@ensure(
    lambda result, filename: result is None or filename.endswith((".conda", ".tar.bz2")),
    "only a conda package file is read",
)
def conda_name_version(filename: str) -> tuple[str, str] | None:
    """``(name, version)`` of a conda package file, ``<name>-<version>-<build>.conda`` or
    ``.tar.bz2``; None for any other file name. A name may hold dashes (``pydantic-core``), a
    version and a build never do, so the parts are taken from the right."""
    stem = filename.removesuffix(".conda").removesuffix(".tar.bz2")
    if stem == filename:
        return None
    rest, dash, build = stem.rpartition("-")
    name, dash2, version = rest.rpartition("-")
    if not (dash and dash2 and name and version):
        return None
    return name, version


@beartype
@require(lambda wanted: len(set(wanted)) == len(wanted), "each package asked for once")
@ensure(
    lambda result, wanted: [n for n, _ in result] == list(wanted),
    "one answer per package asked for, in that order",
)
@ensure(
    lambda result, entries: all(v == dict(entries).get(n, NOT_IN_LOCK) for n, v in result),
    "the version is the lock's, or the package is said to be missing from it",
)
def pinned(
    entries: tuple[tuple[str, str], ...], wanted: tuple[str, ...]
) -> tuple[tuple[str, str], ...]:
    """The version the lock pins for each package in ``wanted``, from the lock's ``(name,
    version)`` entries; ``NOT_IN_LOCK`` for one the lock does not hold."""
    found = dict(entries)
    return tuple((name, found.get(name, NOT_IN_LOCK)) for name in wanted)


# --- enforcement ---------------------------------------------------------------------------------


@beartype
@ensure(
    lambda result, text: (
        result is None
        or any(
            x.strip().startswith("exec ") and x.strip()[5:].strip() == result
            for x in text.splitlines()
        )
    ),
    "the command is what an exec line of the hook hands over to",
)
@ensure(
    lambda result, text: (
        result is not None
        or not any(
            x.strip().startswith("exec ") and x.strip()[5:].strip() for x in text.splitlines()
        )
    ),
    "a hook with an exec line has a command",
)
@ensure(lambda result: result is None or result.strip() == result, "trimmed")
def hook_command(text: str) -> str | None:
    """The command the pre-push hook hands the shell over to: its last ``exec`` line's."""
    found = None
    for line in text.splitlines():
        word = line.strip()
        if word.startswith("exec ") and word[5:].strip():
            found = word[5:].strip()
    return found


@beartype
@ensure(lambda result, url: "://" in url or result == url, "an address without a scheme is kept")
@ensure(
    lambda result, url: "://" not in url or "@" not in result.partition("://")[2].partition("/")[0],
    "nothing stands before the host",
)
@ensure(
    lambda result, url: (
        result.partition("://")[2].partition("/")[2] == url.partition("://")[2].partition("/")[2]
    ),
    "the path is kept",
)
@ensure(lambda result, url: len(result) <= len(url), "nothing is added")
def strip_credentials(url: str) -> str:
    """``url`` with whatever stands before its host removed (``https://user:token@host/x`` becomes
    ``https://host/x``), so a remote's address can be written down. An scp-like address
    (``git@host:path``) names a user and no secret, and is kept."""
    scheme, sep, rest = url.partition("://")
    if not sep:
        return url
    authority, slash, path = rest.partition("/")
    return f"{scheme}{sep}{authority.rpartition('@')[2]}{slash}{path}"


# --- tables and rendering ------------------------------------------------------------------------


def _cell(text: str) -> str:
    return text.replace("\n", " ").replace("|", "\\|")


def _row(cells: tuple[str, ...]) -> str:
    return "| " + " | ".join(_cell(c) for c in cells) + " |"


@beartype
@require(lambda header: len(header) > 0, "at least one column")
@require(lambda header, rows: all(len(r) == len(header) for r in rows), "a cell per column")
@ensure(lambda result, rows: result.count("\n") == len(rows) + 1, "the header, a rule, each row")
@ensure(lambda result: result.startswith("| ") and result.endswith(" |"), "a Markdown table")
def table(header: tuple[str, ...], rows: tuple[tuple[str, ...], ...]) -> str:
    """A Markdown table; a cell's own pipes are escaped and its line breaks become spaces, so a
    cell never breaks the table."""
    rule = "| " + " | ".join("-" for _ in header) + " |"
    return "\n".join([_row(header), rule, *(_row(r) for r in rows)])


def _tool(versions: dict[str, str], name: str) -> str:
    return f"{name} {versions.get(name, NOT_IN_LOCK)}"


def _before_groups(v: dict[str, str]) -> tuple[tuple[str, str], ...]:
    proven = "contracted and proven by the gate"
    return (
        ("lint", f"a tool, {_tool(v, 'ruff')}"),
        ("typecheck", f"a tool, {_tool(v, 'mypy')}"),
        (
            "test",
            f"a tool, {_tool(v, 'pytest')}, runs the tests under {_tool(v, 'typeguard')}; what "
            "each test asserts is a person's judgement",
        ),
        ("boundary", f"home-made code, `beethoven_qa.boundary`, {proven}"),
        (
            "symbolic, check 1: every public function carries contracts",
            f"home-made code, `beethoven_qa.gate`, {proven}",
        ),
        (
            "symbolic, check 2: no counterexample within the bound",
            f"a tool, {_tool(v, 'crosshair-tool')} with {_tool(v, 'z3-solver')}",
        ),
    )


def _group_row(group: GroupSummary) -> tuple[str, str]:
    # Its SARIF log names each run's tool: beethoven-qa for the home-made reader, the tool's own
    # name otherwise, so the row says what the log says and nothing the code believes about it.
    who = [
        f"home-made code, beethoven-qa {v}, contracted and proven by the gate"
        if n == "beethoven-qa"
        else f"a tool, {n} {v}"
        for n, v in group.tools
    ]
    return f"standards: {group.group}", "; ".join(who) or "no tool recorded in its log"


def _after_groups(vendor: bool) -> tuple[tuple[str, str], ...]:
    copy = (VENDOR_LANE, "home-made code, `beethoven_qa.vendor`, contracted and proven by the gate")
    return (
        *((copy,) if vendor else ()),
        (
            "whether a change merges",
            "GitHub's required checks, the rule as code in `.github/rulesets/main.json`; no person",
        ),
        (
            "every limit, and what a contract or a test means",
            "a person, in a reviewed release of beethoven-qa",
        ),
    )


@beartype
@ensure(
    lambda result, groups: all(
        any(c == f"standards: {g.group}" for c, _ in result) for g in groups
    ),
    "every group that ran has its row",
)
@ensure(
    lambda result, vendor: any(c == VENDOR_LANE for c, _ in result) == vendor,
    "the copy's check has a row exactly when it ran",
)
@ensure(lambda result: all(c and w for c, w in result), "no empty cell")
@ensure(lambda result, groups: len(result) >= len(groups) + 8, "the fixed rows are always there")
def deciders(
    tools: tuple[Tool, ...], groups: tuple[GroupSummary, ...], vendor: bool
) -> tuple[tuple[str, str], ...]:
    """The table "who decides each verdict": for each check, a tool at its locked version,
    home-made code the gate proved, or a person."""
    versions = {t.name: t.locked for t in tools}
    return (*_before_groups(versions), *map(_group_row, groups), *_after_groups(vendor))


def _md_head(e: Evidence) -> list[str]:
    where = e.repository.origin or "a repository with no origin remote"
    verdict = (
        "**Every lane passed.**" if e.passed else "**A lane failed; the lanes table says which.**"
    )
    return [
        f"# Evidence report: {where}",
        "",
        f"Made {e.made} by beethoven-qa {e.version} (`python -m beethoven_qa report`). {verdict}",
        "",
        'Beethoven runs Claude Code and is not an Anthropic product; "Claude" and "Claude '
        "Code\" are Anthropic's marks, used here to say what it runs.",
    ]


def _md_repository(e: Evidence) -> list[str]:
    r = e.repository
    clean = "clean" if r.clean else "NOT clean (it held uncommitted changes)"
    return [
        f"- HEAD `{r.head}` on `{r.branch}`",
        f"- the tree was {clean} when the report began",
        f"- origin: {r.origin or 'none'}",
    ]


def _md_lanes(e: Evidence) -> list[str]:
    rows = tuple(
        (lane.task, " ".join(lane.argv), str(lane.exit), f"{lane.seconds:.1f}", lane.output)
        for lane in e.lanes
    )
    return [
        "Each command of each task of `pixi.toml` ran as its own process, in the environment the "
        "report was started in; the output file holds everything it printed.",
        "",
        table(("task", "command", "exit", "seconds", "output"), rows),
    ]


def _md_environment(e: Evidence) -> list[str]:
    rows = tuple((t.name, t.locked, t.reported or "says nothing of itself") for t in e.tools)
    return [
        "What `pixi.lock` pins, and what each tool answers to `--version`.",
        "",
        table(("package", "pinned in pixi.lock", "reports itself as"), rows),
    ]


def _md_gate_totals(t: Totals | None) -> list[str]:
    if t is None:
        return ["The gate printed no counts line, so what follows is only what it did print."]
    return [
        f"Of {t.conditions} conditions, **{t.confirmed} were confirmed over all paths** (a "
        f"proof), {t.not_confirmed} were not confirmed within {t.bound} s each (a bounded search "
        f"that found no counterexample, not a proof), and {t.unable} could be given no input "
        "that meets the precondition (a vacuous contract, which fails the gate).",
    ]


def _md_gate(e: Evidence) -> list[str]:
    g = e.gate
    targets = tuple(
        (t.name, str(t.confirmed), str(t.not_confirmed), str(t.unable)) for t in g.targets
    )
    unproven = tuple((n, why) for n, why in g.unproven)
    return [
        *_md_gate_totals(g.totals),
        "",
        table(
            ("target", "confirmed over all paths", "not confirmed", "precondition unmet"), targets
        ),
        "",
        "Functions set aside under `[tool.beethoven-qa.unproven]`, never targets; their contracts "
        "are checked by the tests alone:",
        "",
        table(("function", "reason"), unproven),
    ]


def _md_group(g: GroupSummary) -> str:
    levels = ", ".join(f"{n} {k}" for k, n in g.levels) or "no results"
    states = ", ".join(f"{n} {k}" for k, n in g.states) or "none"
    tools = ", ".join(f"{n} {v}" for n, v in g.tools) or "no tool recorded"
    return f"- **{g.group}**: {g.verdict}; {tools}; results by level: {levels}; by state: {states}"


def _md_standards(e: Evidence) -> list[str]:
    if not e.standards:
        return ["No SARIF log was written, so no group is summarised."]
    return [
        "Each group's verdict as printed, the tools its SARIF log names, and its results by "
        "level and by `baselineState` against the base commit (`new` and `updated` errors block).",
        "",
        *map(_md_group, e.standards),
    ]


def _md_enforcement(e: Evidence) -> list[str]:
    f = e.enforcement
    hook = "present" if f.hook_present else "MISSING"
    hook += ", executable" if f.hook_executable else ", not executable"
    strict = "unknown" if f.strict is None else str(f.strict).lower()
    jobs = [f"  - `{name}`: {', '.join(js) or 'no jobs'}" for name, js in f.workflows]
    return [
        f"- `git config core.hooksPath`: `{f.hooks_path or 'unset'}`",
        f"- `.githooks/pre-push`: {hook}; hands over to `{f.hook_command or 'nothing it says'}`",
        f"- required checks of `.github/rulesets/main.json`: "
        f"{', '.join(f'`{c}`' for c in f.required) or 'none'} (strict: {strict})",
        "- workflows and their jobs:",
        *(jobs or ["  - none"]),
    ]


def _md_decided(e: Evidence) -> list[str]:
    return [table(("check", "decided by"), e.decided)]


def _bullets(lines: tuple[str, ...]) -> list[str]:
    return [f"- {line}" for line in lines]


@beartype
@ensure(lambda result: result.startswith("# Evidence report: "), "titled")
@ensure(
    lambda result: all(f"\n## {s}\n" in result for s in SECTIONS), "every section, in its place"
)
@ensure(lambda result, evidence: evidence.repository.head in result, "the commit is named")
@ensure(
    lambda result, evidence: all(_cell(lane.task) in result for lane in evidence.lanes),
    "every lane is named",
)
@ensure(lambda result: result.endswith("\n"), "a text file")
def render(evidence: Evidence) -> str:
    """``report.md``: the evidence as a person reads it."""
    sections = (
        _md_repository,
        _md_lanes,
        _md_environment,
        _md_gate,
        _md_standards,
        _md_enforcement,
        _md_decided,
        lambda _: _bullets(LANGUAGE_MODEL),
        lambda _: _bullets(NOT_SHOWN),
    )
    lines = _md_head(evidence)
    for heading, section in zip(SECTIONS, sections, strict=True):
        lines += ["", f"## {heading}", "", *section(evidence)]
    return "\n".join(lines) + "\n"


# --- the command: processes, files, git and the clock ---------------------------------------------


class _CannotRun(Exception):
    pass


def _tasks(root: Path) -> dict[str, str]:
    # pixi.toml's [tasks], the string ones and a table's own cmd; a task that only depends on
    # others (gate) names nothing to run.
    path = root / "pixi.toml"
    if not path.is_file():
        raise _CannotRun(f"{path} is missing")
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    tasks = data.get("tasks")
    if not isinstance(tasks, dict):
        raise _CannotRun(f"{path} has no [tasks] table")
    found: dict[str, str] = {}
    for name, value in tasks.items():
        command = value.get("cmd") if isinstance(value, dict) else value
        if isinstance(command, str):
            found[name] = command
    return found


def _lane_tasks(root: Path) -> list[tuple[str, str]]:
    if not (root / "pyproject.toml").is_file():
        raise _CannotRun(f"{root / 'pyproject.toml'} is missing")
    tasks = _tasks(root)
    missing = [name for name in LANES if name not in tasks]
    if missing:
        raise _CannotRun(f"{root / 'pixi.toml'} has no task {', '.join(missing)}")
    lanes = [(name, tasks[name]) for name in LANES]
    if (root / VENDOR_DIR).is_dir():
        lanes.append((VENDOR_LANE, tasks.get(VENDOR_LANE, _VENDOR_COMMAND)))
    return lanes


class _Done(NamedTuple):
    exit: int
    seconds: float
    text: str


def _execute(root: Path, argv: list[str]) -> _Done:
    # The lane as its own process from the repository's root, in the environment the report was
    # started in, its output (both streams, as they came) kept whole for the file the report
    # names. A command that cannot start is a failed lane, never a crashed report.
    started = time.monotonic()
    try:
        done = subprocess.run(
            argv,
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            check=False,
        )
    except OSError as exc:
        return _Done(127, time.monotonic() - started, f"could not start: {exc}\n")
    return _Done(done.returncode, time.monotonic() - started, done.stdout)


class _Ran(NamedTuple):
    lanes: tuple[Lane, ...]
    outputs: dict[str, str]  # task -> everything its commands printed

    def lines(self, task: str) -> tuple[str, ...]:
        return tuple(self.outputs.get(task, "").splitlines())


def _lanes(root: Path, out: Path, tasks: list[tuple[str, str]]) -> _Ran:
    lanes: list[Lane] = []
    outputs: dict[str, str] = {}
    for task, command in tasks:
        for index, piece in enumerate(split_commands(command), 1):
            argv = shlex.split(piece)
            if task == "standards":
                argv += ["--sarif", str(out / "sarif")]
            done = _execute(root, argv)
            rel = f"lanes/{task}-{index}.txt"
            (out / rel).write_text(done.text, encoding="utf-8")
            outputs[task] = outputs.get(task, "") + done.text
            lanes.append(Lane(task, tuple(argv), done.exit, done.seconds, rel))
            print(f"report: {task}: {shlex.join(argv)}: exit {done.exit} ({done.seconds:.1f} s)")
    return _Ran(tuple(lanes), outputs)


def _repository(root: Path) -> Repository:
    head = _git(root, "rev-parse", "HEAD").strip()
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    clean = _git(root, "status", "--porcelain").strip() == ""
    try:
        origin = strip_credentials(_git(root, "remote", "get-url", "origin").strip())
    except subprocess.CalledProcessError:  # no origin: a local repository
        origin = ""
    return Repository(head, branch, clean, origin)


class _Package(BaseModel):
    # One record of the lock's packages list: a conda one names its file in its URL, a PyPI one
    # carries its name and version.
    conda: str | None = None
    name: str | None = None
    version: str | None = None


class _Lock(BaseModel):
    packages: list[_Package] = []


def _lock_entries(root: Path) -> tuple[tuple[str, str], ...]:
    path = root / "pixi.lock"
    if not path.is_file():
        return ()
    try:
        lock = _Lock.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (yaml.YAMLError, ValidationError):
        return ()
    found: list[tuple[str, str]] = []
    for package in lock.packages:
        if package.conda is not None:
            pair = conda_name_version(package.conda.rpartition("/")[2])
            if pair is not None:
                found.append(pair)
        elif package.name is not None and package.version is not None:
            found.append((package.name, package.version))
    return tuple(found)


def _reported(name: str) -> str:
    # The tool's own first line of --version, from the environment the report runs in (python is
    # this interpreter, a tool the one beside it); a tool that has no --version says nothing.
    if name not in _VERSION_FLAG:
        return ""
    argv = [sys.executable if name == "python" else _bin(name), "--version"]
    try:
        done = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"did not answer: {type(exc).__name__}"
    first = (done.stdout or done.stderr).strip().splitlines()[:1]
    return first[0].strip() if first else "answered nothing"


def _tools(root: Path) -> tuple[Tool, ...]:
    return tuple(
        Tool(name, locked, _reported(name)) for name, locked in pinned(_lock_entries(root), PINNED)
    )


class _StatusCheck(BaseModel):
    context: str


class _Parameters(BaseModel):
    strict_required_status_checks_policy: bool | None = None
    required_status_checks: list[_StatusCheck] = []


class _Rule(BaseModel):
    type: str
    parameters: _Parameters | None = None


class _Ruleset(BaseModel):
    rules: list[_Rule] = []


def _ruleset(path: Path) -> tuple[tuple[str, ...], bool | None]:
    # The required checks of the branch rule and its strict flag; nothing when the file is
    # missing or not a ruleset, since a reader must see that there is no rule.
    if not path.is_file():
        return (), None
    try:
        ruleset = _Ruleset.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError:
        return (), None
    for rule in ruleset.rules:
        if rule.type == "required_status_checks" and rule.parameters is not None:
            contexts = tuple(c.context for c in rule.parameters.required_status_checks)
            return contexts, rule.parameters.strict_required_status_checks_policy
    return (), None


def _jobs(path: Path) -> tuple[str, ...]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return ()
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, dict):
        return ()
    return tuple(
        str(job.get("name", name)) if isinstance(job, dict) else str(name)
        for name, job in jobs.items()
    )


def _workflows(root: Path) -> tuple[tuple[str, tuple[str, ...]], ...]:
    home = root / ".github" / "workflows"
    if not home.is_dir():
        return ()
    files = sorted(p for p in home.iterdir() if p.suffix in (".yml", ".yaml") and p.is_file())
    return tuple((p.name, _jobs(p)) for p in files)


def _enforcement(root: Path) -> Enforcement:
    try:
        hooks_path = _git(root, "config", "--get", "core.hooksPath").strip()
    except subprocess.CalledProcessError:  # unset: git exits 1
        hooks_path = ""
    hook = root / ".githooks" / "pre-push"
    present = hook.is_file()
    command = hook_command(hook.read_text(encoding="utf-8", errors="replace")) if present else None
    required, strict = _ruleset(root / ".github" / "rulesets" / "main.json")
    return Enforcement(
        hooks_path,
        present,
        present and os.access(hook, os.X_OK),
        command or "",
        required,
        strict,
        _workflows(root),
    )


class _Driver(BaseModel):
    name: str
    version: str = ""


class _SarifTool(BaseModel):
    driver: _Driver


class _Result(BaseModel):
    level: str = ""
    baselineState: str = ""


class _SarifRun(BaseModel):
    tool: _SarifTool
    results: list[_Result] = []


class _Log(BaseModel):
    runs: list[_SarifRun] = []


def _group(path: Path, verdicts: dict[str, str]) -> GroupSummary | None:
    try:
        log = _Log.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        return None
    tools = tuple((run.tool.driver.name, run.tool.driver.version) for run in log.runs)
    results = [r for run in log.runs for r in run.results]
    verdict = verdicts.get(path.stem, "not run")
    levels = count_by(tuple(r.level for r in results))
    states = count_by(tuple(r.baselineState for r in results))
    return GroupSummary(path.stem, verdict, tools, levels, states)


def _groups(sarif: Path, verdicts: dict[str, str]) -> tuple[GroupSummary, ...]:
    if not sarif.is_dir():
        return ()
    read = (_group(p, verdicts) for p in sorted(sarif.glob("*.sarif")))
    return tuple(g for g in read if g is not None)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _plain(value: object) -> object:
    # NamedTuples as objects, tuples as lists, so report.json reads the way the fields are named.
    if isinstance(value, tuple) and hasattr(value, "_asdict"):
        return {k: _plain(v) for k, v in value._asdict().items()}
    if isinstance(value, (tuple, list)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    return value


def _write(out: Path, evidence: Evidence) -> None:
    data: dict[str, object] = {"format": 1}
    data.update({k: _plain(v) for k, v in evidence._asdict().items()})
    data["language_model"] = list(LANGUAGE_MODEL)
    data["not_shown"] = list(NOT_SHOWN)
    text = json.dumps(data, indent=2, ensure_ascii=False)
    (out / "report.json").write_text(text + "\n", encoding="utf-8")
    (out / "report.md").write_text(render(evidence), encoding="utf-8")


def _evidence(root: Path, out: Path, repository: Repository, ran: _Ran) -> Evidence:
    tools = _tools(root)
    groups = _groups(out / "sarif", dict(group_verdicts(ran.lines("standards"))))
    vendor = any(lane.task == VENDOR_LANE for lane in ran.lanes)
    return Evidence(
        _now(),
        __version__,
        repository,
        tools,
        ran.lanes,
        gate_summary(ran.lines("symbolic")),
        groups,
        _enforcement(root),
        deciders(tools, groups, vendor),
        exit_code(tuple(lane.exit for lane in ran.lanes)) == 0,
    )


def _run(root: Path, out: Path) -> int:
    # The repository's facts are read before anything is written, so the report directory, which
    # may sit inside the checkout, never makes the tree look changed by the report itself.
    root, out = root.resolve(), out.resolve()
    try:
        tasks = _lane_tasks(root)
        repository = _repository(root)
    except _CannotRun as exc:
        print(f"report: cannot run: {exc}")
        return 2
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"report: cannot run: git failed: {exc}")
        return 2
    (out / "lanes").mkdir(parents=True, exist_ok=True)
    ran = _lanes(root, out, tasks)
    _write(out, _evidence(root, out, repository, ran))
    failed = sorted({lane.task for lane in ran.lanes if lane.exit != 0})
    verdict = "every lane passed" if not failed else f"failed: {', '.join(failed)}"
    print(f"report: {verdict}; report.md and report.json in {out}")
    return exit_code(tuple(lane.exit for lane in ran.lanes))
