"""Supply chain: the GitHub Actions workflows that build, test and merge the code.

The Drive file's OpenSSF Scorecard section: a flawless codebase means little if the pipeline that
forges it can be subverted. Scorecard itself scores a whole repository against GitHub's API and
counts human review, which this project has chosen not to require (greg, 2026-10-04: the checks
alone decide a merge); so its checks that read the workflow files are applied here, to every file
under ``.github/workflows/``, the way Scorecard defines them:

- **Pinned-Dependencies**: every action a workflow uses, step or reusable workflow, is pinned to a
  full 40-character commit, never a tag or a branch that can be moved; a local action
  (``./path``) and a container pinned by its ``sha256`` digest are pinned. A download piped into a
  shell (``curl ... | sh``) runs whatever the server sends that day and is refused; so is a file a
  step fetches with curl or wget and then runs, installs, unpacks or chmods without a sha256 check
  in the same step (``sha256sum -c``, or a comparison with a 64-hex digest written there).
- **Token-Permissions**: every workflow declares top-level ``permissions`` that grant no write; a
  job that needs to write says so on the job.
- **Dangerous-Workflow**: a workflow started by ``pull_request_target`` or ``workflow_run`` runs
  with the repository's secrets and write token, so it never checks out the pull request's code;
  and no ``run`` script expands an expression an outsider controls (an issue's or a pull request's
  title or body, a branch name, a commit message), which would let that text run as shell.

Beside this reader, zizmor (the established auditor of workflow files) runs over the same files at
its regular persona, offline, with its ignore comments and configuration files not honoured
(``--no-ignores --no-config``: an inline ``zizmor: ignore[...]`` comment or a ``zizmor.yml``
silences and loosens nothing, the rule every group keeps). Each of its findings is a result of its
own run in the
group's log, ``zizmor/<audit>``, at the line of the finding's primary location and anchored to
that line's text; one of **High or Medium severity** is an error and blocks when the change made
it, a Low or Informational one is a note. Measured 2026-10-08 with zizmor 1.30.1: its JSON counts
rows from 0 where its plain output and GitHub count lines from 1; a file it cannot read as a
workflow it passes over with a warning, as the reader passes over one that does not parse; and on
this repository's own workflows it reports one Low, ``adhoc-packages`` for the ``npm install -g``
of the Claude Code CLI, which has no lockfile to pin it (recorded, accepted).

Scorecard's other checks (branch protection, code review, signed releases, a security policy and
others) read GitHub's settings, not files; this repository's rulesets as code
(``.github/rulesets/main.json``) are its own record of the first. Scorecard itself runs over the
tree in CI as a report that never blocks (the job ``scorecard (report)`` of ``standards.yml``;
``docs/STANDARDS.md`` says why).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import NamedTuple

import yaml
from beartype import beartype
from pydantic import BaseModel, ConfigDict

from beethoven_qa import __version__
from beethoven_qa.contracts import ensure
from beethoven_qa.standards.findings import Finding, blocking
from beethoven_qa.standards.group import (
    Context,
    Group,
    Report,
    _bin,
    _compare,
    _tool,
    _version,
)
from beethoven_qa.standards.sarif import Rule, Run

__all__ = [
    "BLOCKING",
    "GROUP",
    "RULES",
    "grants_write",
    "level_for",
    "pinned",
    "pipes_to_shell",
    "unchecked_download",
    "untrusted",
]

GROUP = Group(
    "supply-chain",
    "the workflows that build, test and merge",
    "OpenSSF Scorecard's checks of workflow files, and zizmor's audits",
)

WORKFLOWS = ".github/workflows"
BLOCKING = ("Medium", "High")  # zizmor's severities that make a finding an error
_SCORECARD = "https://github.com/ossf/scorecard/blob/main/docs/checks.md"
_ZIZMOR = "https://docs.zizmor.sh/audits/"
_TAGS = ("security", "supply-chain")


def _rule(key: str, name: str, text: str, check: str) -> Rule:
    return Rule(
        f"supply-chain/{key}",
        name,
        text,
        f"OpenSSF Scorecard, {check}.",
        f"{_SCORECARD}#{check.lower()}",
        _TAGS,
    )


RULES: dict[str, Rule] = {
    r.id: r
    for r in (
        _rule(
            "pinned-action",
            "PinnedAction",
            "An action is pinned to a full commit.",
            "Pinned-Dependencies",
        ),
        _rule(
            "pipe-to-shell",
            "PipeToShell",
            "No download is piped into a shell.",
            "Pinned-Dependencies",
        ),
        _rule(
            "unchecked-download",
            "UncheckedDownload",
            "A file a step fetches and then runs, installs, unpacks or chmods is checked against a "
            "sha256 digest in that step.",
            "Pinned-Dependencies",
        ),
        _rule(
            "token-permissions",
            "TokenPermissions",
            "A workflow declares top-level permissions that grant no write.",
            "Token-Permissions",
        ),
        _rule(
            "untrusted-checkout",
            "UntrustedCheckout",
            "A pull_request_target or workflow_run workflow never checks out the pull request.",
            "Dangerous-Workflow",
        ),
        _rule(
            "script-injection",
            "ScriptInjection",
            "No run script expands an expression an outsider controls.",
            "Dangerous-Workflow",
        ),
    )
}

_SHA = re.compile(r"[0-9a-f]{40}")
_DIGEST = re.compile(r"docker://[^@\s]+@sha256:[0-9a-f]{64}")
_PIPE = re.compile(r"\b(curl|wget)\b[^|\n;&]*\|\s*(sudo\s+)?(-?\w+\s+)*(ba|da|z|k)?sh\b")
# A curl or wget whose output is a file: curl with -o, -O, --output or --remote-name among its
# flags or its output redirected, wget unless it writes to stdout (-O-, -qO-, -O -). A command
# runs to a pipe, a semicolon, an ampersand or the end of its line, a backslash-newline included.
_FETCH = re.compile(
    r"\bcurl\b(?:[^\n|;&]|\\\n)*(?:\s-[A-Za-z]*[oO]\b|\s--(?:output|remote-name)\b|\s>)"
    r"|\bwget\b(?!(?:[^\n|;&]|\\\n)*-[A-Za-z]*O\s*-(?=\s|$))"
)
_COMMAND_END = re.compile(r"[|;&]|(?<!\\)\n")
# What a step does with the file it fetched: makes it executable, installs, moves or unpacks it,
# or runs it, by a path or through a shell or an interpreter.
_USE = re.compile(
    r"\b(?:chmod|install|mv|cp|tar|unzip|gunzip|unxz|sudo|bash|sh|zsh|python3?|node|perl)\b"
    r"|(?:^|[\s;&|(])\.?/\S+",
    re.MULTILINE,
)
# A digest check: sha256sum or shasum -a 256 told to check (-c, --check), or a 64-hex digest
# written in the step for a comparison.
_CHECKED = re.compile(
    r"\b(?:sha256sum|shasum\s+-a\s*256)\b[^\n|;&]*\s(?:-\w*c\w*|--check)\b|\b[0-9a-f]{64}\b"
)
_EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)
# GitHub's own list of untrusted input, from its security hardening guide for Actions.
_UNTRUSTED = re.compile(
    r"\bgithub\s*\.\s*(head_ref\b|event\s*\.\s*("
    r"(issue|pull_request|discussion)\s*\.\s*(title|body)\b"
    r"|(comment|review|review_comment)\s*\.\s*body\b"
    r"|pages\s*\.[^.\s]+\.\s*page_name\b"
    r"|(commits\s*\.[^.\s]+|head_commit|workflow_run\s*\.\s*head_commit)\s*\.\s*"
    r"(message|author\s*\.\s*(email|name))\b"
    r"|pull_request\s*\.\s*head\s*\.\s*(ref|label|repo\s*\.\s*default_branch)\b"
    r"|workflow_run\s*\.\s*head_branch\b))"
)


@beartype
@ensure(
    lambda result, uses: (
        not result
        or uses.startswith("./")
        or (_DIGEST.fullmatch(uses) is not None)
        or (_SHA.fullmatch(uses.rsplit("@", 1)[-1]) is not None and "@" in uses)
    ),
    "pinned means a local path, a container by digest, or a full commit after the @",
)
def pinned(uses: str) -> bool:
    """Whether the ``uses:`` value of a step or a job names one fixed thing."""
    if uses.startswith("./"):
        return True
    if uses.startswith("docker://"):
        return _DIGEST.fullmatch(uses) is not None
    name, at, ref = uses.rpartition("@")
    return bool(name) and bool(at) and _SHA.fullmatch(ref) is not None


@beartype
@ensure(
    lambda result, permissions: (
        result
        == (
            permissions == "write-all"
            or (isinstance(permissions, dict) and "write" in permissions.values())
        )
    ),
    "a write is write-all, or a scope set to write",
)
def grants_write(permissions: str | dict[str, str] | None) -> bool:
    """Whether a ``permissions:`` value grants any write."""
    if isinstance(permissions, dict):
        return "write" in permissions.values()
    return permissions == "write-all"


@beartype
@ensure(
    lambda result, script: all(f"${{{{{e}}}}}" in script for e in result),
    "every expression reported is one the script holds",
)
@ensure(lambda result: all(_UNTRUSTED.search(e) for e in result), "and it reads untrusted input")
def untrusted(script: str) -> tuple[str, ...]:
    """The ``${{ }}`` expressions in ``script`` that read input an outsider controls."""
    return tuple(e for e in _EXPRESSION.findall(script) if _UNTRUSTED.search(e))


@beartype
@ensure(lambda result, script: not result or "|" in script, "a pipe needs a |")
def pipes_to_shell(script: str) -> bool:
    """Whether ``script`` pipes a ``curl`` or ``wget`` download into a shell."""
    return _PIPE.search(script) is not None


@beartype
@ensure(
    lambda result, script: not result or _FETCH.search(script) is not None,
    "a report needs a curl or wget that writes a file",
)
@ensure(
    lambda result, script: not result or _CHECKED.search(script) is None,
    "a step that checks a digest is never reported",
)
def unchecked_download(script: str) -> bool:
    """Whether ``script`` fetches a file with curl or wget and goes on to run, install, unpack or
    chmod something, with no sha256 check anywhere in the same step."""
    fetch = _FETCH.search(script)
    if fetch is None or _CHECKED.search(script) is not None:
        return False
    end = _COMMAND_END.search(script, fetch.start())
    return _USE.search(script, end.start() if end else len(script)) is not None


@beartype
@ensure(lambda result: result in ("error", "note"), "an error or a note")
@ensure(
    lambda result, severity: (result == "error") == (severity in BLOCKING),
    "an error exactly for a High or Medium severity; any other word, a note",
)
def level_for(severity: str) -> str:
    """The SARIF level of a zizmor finding of this severity."""
    return "error" if severity in BLOCKING else "note"


class _Where(NamedTuple):
    path: str
    text: str
    job: str = ""  # the job being read, if any
    place: str = ""  # the job, or the job and its step, a finding's anchor starts with


def _line(where: _Where, needle: str) -> int:
    # The first line holding ``needle`` that is not a comment, after the job's own key when a job
    # is being read; YAML's loader keeps no line numbers.
    lines = where.text.splitlines()
    key = re.compile(rf"\s+['\"]?{re.escape(where.job)}['\"]?\s*:")
    start = next((i for i, t in enumerate(lines) if where.job and key.match(t)), 0)
    for n in range(start, len(lines)):
        if needle in lines[n] and not lines[n].lstrip().startswith("#"):
            return n + 1
    return start + 1


def _finding(where: _Where, key: str, needle: str, message: str) -> Finding:
    # ``needle`` is what the finding is about, as the file writes it: it finds the line and, after
    # the place, makes the anchor.
    rule, line = f"supply-chain/{key}", _line(where, needle)
    anchor = f"{where.place}: {needle}" if where.place else needle
    return Finding(rule, where.path, line, line, anchor, 0, 0, "error", message, RULES[rule].tags)


def _triggers(doc: dict[object, object]) -> set[str]:
    # PyYAML reads the key `on:` as the boolean True (YAML 1.1).
    on = doc.get("on", doc.get(True))
    if isinstance(on, str):
        return {on}
    return {str(k) for k in on} if isinstance(on, (dict, list)) else set()


def _permissions(where: _Where, doc: dict[object, object]) -> list[Finding]:
    if "permissions" not in doc:
        why = "the workflow declares no top-level permissions"
        return [_finding(where, "token-permissions", "jobs:", why)]
    value = doc["permissions"]
    clean = value if isinstance(value, (str, dict)) else None
    if isinstance(clean, dict):
        clean = {str(k): str(v) for k, v in clean.items()}
    if grants_write(clean):
        why = "the top-level permissions grant a write; grant it on the job that needs it"
        return [_finding(where, "token-permissions", "permissions:", why)]
    return []


def _unpinned(where: _Where, uses: object) -> list[Finding]:
    if isinstance(uses, str) and not pinned(uses):
        why = f"{uses} is not pinned to a full commit"
        return [_finding(where, "pinned-action", uses, why)]
    return []


def _download(script: str) -> str:
    # The download command as the step writes it, to the end of its line: the finding's needle.
    fetch = _FETCH.search(script)
    return script[fetch.start() if fetch else 0 :].split("\n", 1)[0].strip()


def _script(where: _Where, run: object) -> list[Finding]:
    if not isinstance(run, str):
        return []
    found = [
        _finding(
            where,
            "script-injection",
            expression.strip(),
            f"the run script expands ${{{{{expression}}}}}, which an outsider controls; "
            "pass it through env",
        )
        for expression in untrusted(run)
    ]
    if pipes_to_shell(run):
        why = "a download is piped into a shell"
        found.append(_finding(where, "pipe-to-shell", "|", why))
    if unchecked_download(run):
        why = (
            "a download is run, installed, unpacked or chmodded without a digest check in its "
            "step; check it with sha256sum -c first"
        )
        found.append(_finding(where, "unchecked-download", _download(run), why))
    return found


def _checkout(where: _Where, step: dict[object, object]) -> list[Finding]:
    # Called for privileged workflows only: a checkout of the pull request's head or merge ref.
    uses, given = step.get("uses"), step.get("with")
    ref = str(given.get("ref", "")) if isinstance(given, dict) else ""
    if isinstance(uses, str) and "checkout" in uses and re.search(r"head|merge", ref):
        why = "a privileged workflow checks out the pull request's code"
        return [_finding(where, "untrusted-checkout", ref, why)]
    return []


def _steps(where: _Where, steps: list[object], dangerous: bool) -> list[Finding]:
    found = []
    for i, step in enumerate(s for s in steps if isinstance(s, dict)):
        label = step.get("name") or step.get("uses") or f"step {i + 1}"
        inside = where._replace(place=f"{where.job}/{label}")
        found += _unpinned(inside, step.get("uses"))
        found += _script(inside, step.get("run"))
        found += _checkout(inside, step) if dangerous else []
    return found


def _workflow(where: _Where) -> list[Finding]:
    try:
        doc = yaml.safe_load(where.text)
    except yaml.YAMLError:
        return []
    if not isinstance(doc, dict):
        return []
    dangerous = bool(_triggers(doc) & {"pull_request_target", "workflow_run"})
    found = _permissions(where, doc)
    jobs = doc.get("jobs")
    for job, body in jobs.items() if isinstance(jobs, dict) else []:
        if isinstance(body, dict):
            steps = body.get("steps")
            inside = where._replace(job=str(job), place=str(job))
            found += _unpinned(inside, body.get("uses"))
            found += _steps(inside, steps if isinstance(steps, list) else [], dangerous)
    return found


def _texts(tree: Path) -> dict[str, str]:
    # The workflow files, by their path relative to the tree, in a stable order; a link is never
    # read.
    home = tree / WORKFLOWS
    if not home.is_dir():
        return {}
    files = sorted(p for p in home.glob("*.y*ml") if p.is_file() and not p.is_symlink())
    return {p.relative_to(tree).as_posix(): p.read_text("utf-8", errors="replace") for p in files}


def _findings(tree: Path) -> tuple[Finding, ...]:
    return tuple(f for rel, text in _texts(tree).items() for f in _workflow(_Where(rel, text)))


class _Point(BaseModel):
    model_config = ConfigDict(extra="ignore")

    row: int  # from 0


class _Span(BaseModel):
    model_config = ConfigDict(extra="ignore")

    start_point: _Point
    end_point: _Point


class _Concrete(BaseModel):
    model_config = ConfigDict(extra="ignore")

    location: _Span


class _Symbolic(BaseModel):
    model_config = ConfigDict(extra="ignore")

    key: dict[str, dict[str, object]]  # {"Local": {"verbatim_path": ...}} for a file
    annotation: str = ""
    kind: str = "Primary"  # Primary, Related or Hidden


class _Location(BaseModel):
    model_config = ConfigDict(extra="ignore")

    symbolic: _Symbolic
    concrete: _Concrete


class _Determinations(BaseModel):
    model_config = ConfigDict(extra="ignore")

    severity: str
    confidence: str
    persona: str


class _Audit(BaseModel):
    # One finding of zizmor's JSON (its v1 format): the fields read.
    model_config = ConfigDict(extra="ignore")

    ident: str
    desc: str
    url: str = _ZIZMOR
    determinations: _Determinations
    locations: list[_Location]


def _zizmor(tree: Path, paths: list[str]) -> list[_Audit]:
    # zizmor over the workflow files, offline, at the regular persona, its ignore comments and
    # configuration files not honoured, and its exit code saying only whether it ran. A file it
    # cannot read as a workflow it passes over with a warning; when every file is such it says it
    # collected no input, which is no finding, not a failure.
    if not paths:
        return []
    argv = [_bin("zizmor"), "--offline", "--no-ignores", "--no-config", "--no-exit-codes"]
    argv += ["--no-progress", "--quiet", "--persona", "regular", "--format", "json", *paths]
    out = _tool(argv, cwd=tree)
    if out.returncode != 0:
        if "no inputs collected" in out.stderr:
            return []
        raise RuntimeError(f"zizmor failed: {(out.stderr or out.stdout).strip()}")
    raw = json.loads(out.stdout)
    return [_Audit.model_validate(item) for item in raw] if isinstance(raw, list) else []


def _audit_rule(audit: _Audit) -> Rule:
    name = "".join(w.capitalize() for w in audit.ident.split("-") if w)
    return Rule(
        f"zizmor/{audit.ident}",
        name or audit.ident,
        f"zizmor {audit.ident}: {audit.desc}.",
        "An audit of zizmor at its regular persona; a finding of High or Medium severity blocks "
        "when the change made it, a Low or Informational one is recorded.",
        audit.url,
        _TAGS,
    )


def _audited(audit: _Audit, texts: dict[str, str]) -> Finding | None:
    # The finding at the audit's primary location (the first location when none is marked so),
    # anchored to the text of its line; one in a file that was not given is passed over.
    place = next((loc for loc in audit.locations if loc.symbolic.kind == "Primary"), None)
    place = place or (audit.locations[0] if audit.locations else None)
    if place is None:
        return None
    local = place.symbolic.key.get("Local", {})
    rel = Path(str(local.get("verbatim_path", ""))).as_posix()
    if rel not in texts:
        return None
    span = place.concrete.location
    line, end = span.start_point.row + 1, max(span.start_point.row, span.end_point.row) + 1
    lines = texts[rel].splitlines()
    anchor = " ".join(lines[line - 1].split()) if line <= len(lines) else ""
    severity = audit.determinations.severity
    message = f"{audit.desc}: {place.symbolic.annotation} ({severity.lower()} severity)"
    rule = f"zizmor/{audit.ident}"
    return Finding(rule, rel, line, end, anchor, 0, 0, level_for(severity), message, _TAGS)


def _audits(tree: Path, rules: dict[str, Rule]) -> tuple[Finding, ...]:
    # zizmor's findings, and in ``rules`` the rule each of them names.
    texts = _texts(tree)
    found = []
    for audit in _zizmor(tree, sorted(texts)):
        finding = _audited(audit, texts)
        if finding is not None:
            rules[finding.rule] = _audit_rule(audit)
            found.append(finding)
    return tuple(found)


def _check(context: Context) -> Report:
    head, states = _compare(context, _findings)
    seen: dict[str, Rule] = {}
    audited, audit_states = _compare(context, lambda tree: _audits(tree, seen))
    rules = tuple(seen[r] for r in sorted({f.rule for f in audited}))
    version = _version([_bin("zizmor"), "--version"])
    reader = tuple(RULES.values())
    runs = (
        Run(GROUP.name, "beethoven-qa", __version__, _SCORECARD, reader, head, states),
        Run(GROUP.name, "zizmor", version, _ZIZMOR, rules, audited, audit_states),
    )
    stops = blocking(head + audited, states + audit_states)
    notes = sum(1 for f in audited if f.level == "note")
    summary = (
        f"{len(head)} workflow findings of the reader and {len(audited) - notes} of zizmor's of "
        f"medium or high severity in the tree, {len(stops)} new or worse; {notes} low ones recorded"
    )
    return Report(GROUP.name, runs, stops, not stops, summary)
