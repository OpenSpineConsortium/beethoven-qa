"""The boundary check, the interface standard's rules 1 to 5, on throwaway repositories.

CrossHair proves the pure parts (what a line reaches, what a pattern covers, how a record reads, how
a copy differs from it, what an import takes that is not published, what goes against the
direction). These tests build a consumer that holds every rule in a throwaway git checkout, break
each rule on purpose and see the command say so, with the file, the line and the rule, and run it
on this repository itself. This file is tracked, so rule 1 reads it too: every string here that
would reach another checkout is spelled from its parts, and none is written out whole.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from beethoven_qa import boundary
from beethoven_qa.__main__ import main
from beethoven_qa.boundary import (
    CHECKOUT_VARIABLES,
    STANDARD,
    Manifest,
    against_direction,
    compare_copy,
    covered,
    line_reaches,
    read_origin,
    stale_exceptions,
    unpublished,
)
from beethoven_qa.contracts import ViolationError
from beethoven_qa.gate import GateConfig
from beethoven_qa.vendor import VENDOR_DIR, _git_env

REPO = Path(__file__).resolve().parent.parent
ORG = "OpenSpineConsortium"
UP = ".." + "/"  # the parent directory, never written next to a repository's name here
AGENTS = "beethoven-agents"
HPC = "hpcworker"
DIR_VARIABLE = "HPCWORKER" + "_DIR"
COMMIT = "c" * 40


def _env() -> dict[str, str]:
    # Hermetic git, as tests/test_vendor.py explains: neither the developer's configuration nor a
    # repository a hook's environment names reaches the throwaway checkouts.
    env = dict(_git_env())
    env.update(
        GIT_CONFIG_GLOBAL="/dev/null",
        GIT_CONFIG_NOSYSTEM="1",
        GIT_AUTHOR_NAME="test",
        GIT_AUTHOR_EMAIL="test@example.invalid",
        GIT_COMMITTER_NAME="test",
        GIT_COMMITTER_EMAIL="test@example.invalid",
    )
    return env


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True, env=_env()
    ).stdout


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _import(repository: str, export: str, into: str, version: int = 1) -> dict[str, Any]:
    return {
        "repository": f"{ORG}/{repository}",
        "export": export,
        "version": version,
        "into": into,
        "origin": f"{into}/ORIGIN",
    }


def _manifest(name: str, imports: list[dict[str, Any]], exports: list[Any] | None = None) -> str:
    return json.dumps(
        {
            "format": 1,
            "name": name,
            "repository": f"{ORG}/{name}",
            "summary": "a throwaway repository",
            "standard": STANDARD,
            "exports": exports or [],
            "imports": imports,
        },
        indent=1,
    )


PROFILE = '{"id": "wayne"}\n'


def _origin(files: dict[str, str], export: str = "export profiles 1") -> str:
    lines = [f"repository {ORG}/{HPC}", f"commit {COMMIT}", export]
    lines += [f"sha256 {_sha(text)} {rel}" for rel, text in files.items()]
    return "\n".join(lines) + "\n"


def _consumer(tmp_path: Path, name: str = "beethoven-research") -> Path:
    # A repository that holds every rule: beethoven-qa's pinned copy (its manifest is what the
    # check reads of it), a standard copy of hpcworker's profiles, code that imports only what
    # beethoven-qa publishes, and prose that may name another checkout.
    root = tmp_path / name
    root.mkdir()
    _git(root, "init", "-q")
    imports = [
        _import("beethoven-qa", "python", VENDOR_DIR),
        _import("beethoven-qa", "cli", VENDOR_DIR, 3),
        _import(HPC, "profiles", "profiles"),
    ]
    _write(root, "interface/INTERFACE.json", _manifest(name, imports))
    shutil.copytree(REPO / "interface", root / VENDOR_DIR / "interface")
    _write(root, f"{VENDOR_DIR}/ORIGIN", "repository: OpenSpineConsortium/beethoven-qa\n")
    _write(root, "profiles/wayne.json", PROFILE)
    _write(root, "profiles/ORIGIN", _origin({"wayne.json": PROFILE}))
    _write(root, "profiles/pull.sh", "#!/bin/bash\n")
    _write(root, "profiles/README.md", f"Refreshed from {UP}{HPC} once, by hand.\n")
    _write(
        root,
        "src/beethoven_research/__init__.py",
        "import beethoven_qa\nimport beethoven_qa.contracts as c\n"
        "from beethoven_qa import __version__, contracts\n"
        "from beethoven_qa.contracts import ensure, require\nfrom . import splits\n",
    )
    _write(root, "src/beethoven_research/splits.py", "VALUE = 1\n")
    _write(root, "README.md", f"Clone beside {UP}{AGENTS} to compare by hand.\n")
    _write(root, "pyproject.toml", '[tool.beethoven-qa]\npackage = "beethoven_research"\n')
    (root / "data.bin").write_bytes(b"\xff\xfe\x00 not text")
    _stage(root)
    return root


def _stage(root: Path) -> None:
    _git(root, "add", "-A")


def _run(root: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    capsys.readouterr()
    code = boundary._run(root)
    return code, capsys.readouterr().out


# --- the pure parts ------------------------------------------------------------------------------


def test_line_reaches() -> None:
    assert line_reaches(f"cp -r {UP}{HPC}/framework .", "") == ((HPC, boundary._LINE_RULES[0][1]),)
    assert line_reaches(f'X="$(dirname "$HERE")/{AGENTS}"', "")[0][0] == AGENTS
    assert line_reaches(f"test -d ${{{DIR_VARIABLE}}}", "")[0][0] == DIR_VARIABLE
    assert line_reaches(f"git clone git@github.com:{ORG}/{HPC}.git", "")[0][0] == HPC
    assert line_reaches(f"pip install git+https://github.com/{ORG}/{AGENTS}", "")[0][0] == AGENTS
    # GitHub reads an address in any case, so a clone or a git URL counts in any case too; a dot of
    # a name is a dot, never any character.
    upper = f"GIT Clone https://github.com/{ORG.lower()}/{HPC.upper()}"
    assert line_reaches(upper, "") == ((HPC.upper(), boundary._LINE_RULES[3][1]),)
    assert line_reaches(f"x @ GIT+SSH://github.com/{ORG.upper()}/{AGENTS.title()}", "") != ()
    assert line_reaches(f"git clone {ORG}/openspineconsortiumXgithubXio", "") == ()
    assert line_reaches(f"cp {UP}{AGENTS}/x .", AGENTS) == ()  # its own name
    assert line_reaches("   ", "") == ()
    # The hub's name is the product's too: an address under it is no checkout; its parent form is.
    assert line_reaches("fetch(`${origin}/beethoven/services.json`)", "") == ()
    assert line_reaches(f"ls {UP}beethoven/plan", "")[0][0] == "beethoven"
    # Not another repository: a longer name, a home folder, the shared lab folder.
    for line in (f"{UP}{HPC}-notes", "~/.hpcworker/bin", "/rs/x/shared/beethoven/hpcworker/v1"):
        assert line_reaches(line, "") == (), line
    assert all(v.endswith("_DIR") for v in CHECKOUT_VARIABLES)


def test_covered() -> None:
    assert covered("plugin/skills/a/SKILL.md", ("plugin/",))
    assert covered("framework/profiles/wayne.json", ("framework/profiles/*.json",))
    assert covered("interface/python.json", ("interface/python.json",))
    assert covered("a/b-x-c.json", ("a/b-*-*.json",))
    assert not covered("framework/profiles/x/wayne.json", ("framework/profiles/*.json",))
    assert not covered("framework/profiles/wayne.txt", ("framework/profiles/*.json",))
    assert not covered("a/bc.json", ("a/b*c*d.json",))
    assert not covered("a/b.json", ("a/c*",))
    assert not covered("plugin", ("plugin/",))


def test_read_origin() -> None:
    files = {"wayne.json": PROFILE, "labs/x.json": "{}\n"}
    record = read_origin(_origin(files))
    assert record.repository == f"{ORG}/{HPC}"
    assert record.commit == COMMIT
    assert record.exports == (("profiles", "1"),)
    assert dict(record.files) == {rel: _sha(text) for rel, text in files.items()}
    assert record.unread == ()
    odd = read_origin(
        f"repository {ORG}/{HPC}\ncommit abc\npath framework/profiles\nexport profiles\n"
        f"sha256 xyz a.json\nsha256 {'a' * 64} {UP}x\nsha256 {'b' * 64} a\nsha256 {'b' * 64} a\n"
    )
    assert odd.commit == ""  # not a full SHA
    assert odd.files == (("a", "b" * 64),)
    assert len(odd.unread) == 4  # the export without a version, two bad sha256 lines, a repeat
    assert read_origin("") == ("", "", (), (), ())


def test_compare_copy() -> None:
    h, g = "1" * 64, "2" * 64
    diff = compare_copy((("a", h), ("b", h), ("c", h)), (("b", h), ("c", g), ("d", h)))
    assert diff == (("a",), ("d",), ("c",))
    with pytest.raises(ViolationError):
        compare_copy((("a", h), ("a", g)), ())


def test_unpublished() -> None:
    surface = {"beethoven_qa": ("__version__",), "beethoven_qa.contracts": ("ensure", "require")}
    assert unpublished("beethoven_qa.contracts", (), surface) == ()
    assert unpublished("beethoven_qa.gate", (), surface) == ("beethoven_qa.gate",)
    assert unpublished("beethoven_qa", ("contracts", "__version__"), surface) == ()
    assert unpublished("beethoven_qa", ("gate", "vendor"), surface) == (
        "beethoven_qa.gate",
        "beethoven_qa.vendor",
    )
    assert unpublished("beethoven_qa.contracts", ("*",), surface) == ("beethoven_qa.contracts.*",)
    assert unpublished("beethoven_qa.gate", ("GateConfig",), surface) == (
        "beethoven_qa.gate.GateConfig",
    )


def test_against_direction() -> None:
    qa, agents, research = (name for name, _ in boundary.ENGINE)
    assert against_direction(qa, (agents, HPC, agents)) == (agents, HPC)
    assert against_direction(agents, (qa, HPC, research)) == (research,)
    assert against_direction(research, (qa, agents, HPC)) == ()
    assert against_direction(HPC, (qa, agents, research)) == ()
    assert against_direction(qa, (qa,)) == ()


def test_stale_exceptions() -> None:
    found = frozenset({("a.sh", HPC)})
    assert stale_exceptions(found, frozenset({("a.sh", HPC), ("b.sh", AGENTS)})) == [
        ("b.sh", AGENTS)
    ]
    assert stale_exceptions(found, frozenset()) == []


@pytest.mark.parametrize(
    "change",
    [
        {"repository": f"{ORG}/someone-else"},
        {"standard": "https://example.invalid/standard"},
        {"format": 2},
        {"extra": True},
        {"exports": [{"id": "x"}]},
        {
            "exports": [
                {
                    "id": "Bad",
                    "kind": "data",
                    "version": 1,
                    "summary": "",
                    "paths": ["a"],
                    "consumers": [],
                }
            ]
        },
        {
            "exports": [
                {
                    "id": "x",
                    "kind": "library",
                    "version": 1,
                    "summary": "",
                    "paths": ["a"],
                    "consumers": [],
                }
            ]
        },
        {
            "exports": [
                {
                    "id": "x",
                    "kind": "data",
                    "version": "1",
                    "summary": "",
                    "paths": ["a"],
                    "consumers": [],
                }
            ]
        },
        {
            "exports": [
                {
                    "id": "x",
                    "kind": "data",
                    "version": 0,
                    "summary": "",
                    "paths": ["a"],
                    "consumers": [],
                }
            ]
        },
        {
            "exports": [
                {
                    "id": "x",
                    "kind": "data",
                    "version": 1,
                    "summary": "",
                    "paths": [],
                    "consumers": [],
                }
            ]
        },
        {
            "exports": [
                {
                    "id": "x",
                    "kind": "data",
                    "version": 1,
                    "summary": "",
                    "paths": [UP + "x"],
                    "consumers": [],
                }
            ]
        },
        {
            "exports": [
                {
                    "id": "x",
                    "kind": "data",
                    "version": 1,
                    "summary": "",
                    "paths": ["a"],
                    "consumers": ["nobody"],
                }
            ]
        },
        {
            "exports": [
                {
                    "id": "x",
                    "kind": "data",
                    "version": 1,
                    "summary": "",
                    "paths": ["a"],
                    "consumers": [],
                }
            ]
            * 2
        },
        {"imports": [{**_import(HPC, "profiles", "profiles"), "origin": "elsewhere/ORIGIN"}]},
        {"imports": [{**_import(HPC, "profiles", "profiles"), "repository": "no owner"}]},
        {"imports": [{**_import(HPC, "profiles", "profiles"), "export": "Profiles"}]},
    ],
)
def test_the_manifest_shape(change: dict[str, Any]) -> None:
    good = json.loads(_manifest("beethoven-research", [_import(HPC, "profiles", "profiles")]))
    Manifest.model_validate(good)
    with pytest.raises(ValidationError):
        Manifest.model_validate({**good, **change})


@pytest.mark.parametrize(
    "table",
    [
        {"a.sh": {}},
        {UP + "a.sh": {HPC: "why"}},
        {"a.sh": {HPC: "  "}},
        {"a.sh": {"": "why"}},
    ],
)
def test_the_exception_table_is_refused_when_it_says_nothing(table: dict[str, Any]) -> None:
    GateConfig.model_validate({"package": "p", "boundary": {"a.sh": {HPC: "the reason"}}})
    with pytest.raises(ValidationError):
        GateConfig.model_validate({"package": "p", "boundary": table})


# --- the command ----------------------------------------------------------------------------------


def test_beethoven_qa_itself_holds_every_rule(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = _run(REPO, capsys)
    assert code == 0, out
    assert main(["boundary", "--root", str(REPO)]) == 0


def test_a_consumer_that_holds_every_rule_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _consumer(tmp_path)
    code, out = _run(root, capsys)
    assert code == 0, out
    assert f"{VENDOR_DIR} is beethoven-qa's pinned copy" in out
    # beethoven-qa's own copy is vendor-check's: what is in it is not read here.
    _write(root, f"{VENDOR_DIR}/kit/x.sh", f"cp {UP}{AGENTS}/y .\n")
    _stage(root)
    assert _run(root, capsys)[0] == 0


@pytest.mark.parametrize(
    ("rel", "text", "what"),
    [
        ("tools/pull.sh", f"echo\ncp -r {UP}{HPC}/framework .\n", HPC),
        ("tools/env.sh", f'echo\nFROM="$(dirname "$HERE")/{AGENTS}"\n', AGENTS),
        ("tools/run.sh", f'echo\ntest -d "${DIR_VARIABLE}"\n', DIR_VARIABLE),
        ("tools/fetch.sh", f"echo\ngit clone https://github.com/{ORG}/{AGENTS} x\n", AGENTS),
        ("requirements.txt", f"x\ny @ git+https://github.com/{ORG}/{HPC}.git\n", HPC),
    ],
)
def test_rule_1_a_line_that_reaches_another_checkout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], rel: str, text: str, what: str
) -> None:
    root = _consumer(tmp_path)
    _write(root, rel, text)
    _stage(root)
    code, out = _run(root, capsys)
    assert code == 1
    assert f"boundary: {rel}:2: rule 1: " in out and what in out
    assert "rule 1, a sealed tree" in out


def test_rule_1_a_link_into_another_checkout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _consumer(tmp_path)
    (root / "tools").mkdir()
    (root / "tools" / "theirs").symlink_to(f"{UP}{UP}{AGENTS}/src")
    _stage(root)
    code, out = _run(root, capsys)
    assert code == 1 and "tools/theirs:1: rule 1:" in out


def test_rule_1_dependencies_and_checkouts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _consumer(tmp_path)
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    _write(
        root,
        "pyproject.toml",
        '[project]\ndependencies = ["pydantic>=2", "x @ git+https://example.invalid/x.git"]\n'
        '[tool.uv.sources]\nown = { path = "src" }\nfar = { path = "/opt/elsewhere" }\n'
        + pyproject,
    )
    _write(
        root,
        "pixi.toml",
        '[pypi-dependencies]\nz = { git = "https://example.invalid/z.git" }\n'
        'own = { path = ".", editable = true }\n[tasks]\nlint = "ruff check"\n',
    )
    checkout = "actions/checkout@" + "1" * 40
    _write(
        root,
        ".github/workflows/ci.yml",
        "on: push\njobs:\n  a:\n    steps:\n"
        f"      - uses: {checkout}\n"
        f"      - uses: {checkout}\n        with:\n          repository: {ORG}/{AGENTS}\n"
        "      - run: echo\n  b: nothing\n",
    )
    _stage(root)
    code, out = _run(root, capsys)
    assert code == 1
    assert "pyproject.toml:2: rule 1: a dependency by git URL" in out
    assert "pyproject.toml:5: rule 1: a dependency by git URL or by a path outside" in out
    assert "pixi.toml:2: rule 1:" in out
    assert out.count("pyproject.toml") == 2 and out.count("pixi.toml:") == 1
    assert ".github/workflows/ci.yml:8: rule 1: an actions/checkout step" in out


def test_rule_1_files_that_cannot_be_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _consumer(tmp_path)
    _write(root, "pixi.toml", "[tasks\n")
    _write(root, ".github/workflows/ci.yml", "jobs: [\n")
    _stage(root)
    code, out = _run(root, capsys)
    assert code == 1
    assert "pixi.toml: rule 1: cannot be read as TOML" in out
    assert ".github/workflows/ci.yml: rule 1: cannot be read as YAML" in out


def test_rule_1_submodules(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _consumer(tmp_path)
    _write(root, ".gitmodules", '[submodule "x"]\n\tpath = theirs\n')
    _stage(root)  # before the gitlink: `add -A` drops a gitlink with no folder behind it
    _git(root, "update-index", "--add", "--cacheinfo", f"160000,{COMMIT},theirs")
    code, out = _run(root, capsys)
    assert code == 1
    assert "theirs: rule 1: a submodule" in out
    assert ".gitmodules: rule 1: declares submodules" in out
    assert "boundary: 2 findings in" in out


def test_known_exceptions_are_printed_and_go_stale(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _consumer(tmp_path)
    _write(root, "tools/pull.sh", f"cp -r {UP}{HPC}/framework .\n")
    _write(root, "src/beethoven_research/old.py", "from beethoven_qa.gate import GateConfig\n")
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    known = (
        '[tool.beethoven-qa.boundary."tools/pull.sh"]\n'
        f'{HPC} = "the default source, until its owner takes the pinned copy"\n'
        '[tool.beethoven-qa.boundary."src/beethoven_research/old.py"]\n'
        '"beethoven_qa.gate.GateConfig" = "read until the next release publishes it"\n'
    )
    _write(root, "pyproject.toml", pyproject + known)
    _stage(root)
    code, out = _run(root, capsys)
    assert code == 0, out
    assert f"known exception, tools/pull.sh: {HPC}: the default source" in out
    assert "(2 known exceptions)" in out
    (root / "tools/pull.sh").unlink()
    _stage(root)
    code, out = _run(root, capsys)
    assert code == 1
    assert f"tools/pull.sh '{HPC}' matches nothing any more: remove the entry" in out


def test_an_exception_never_excuses_rules_2_3_and_5(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _consumer(tmp_path)
    _write(root, "profiles/wayne.json", '{"id": "edited"}\n')
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    _write(
        root,
        "pyproject.toml",
        pyproject + '[tool.beethoven-qa.boundary."profiles/wayne.json"]\n"wayne.json" = "no"\n',
    )
    _stage(root)
    code, out = _run(root, capsys)
    assert code == 1
    assert "profiles/wayne.json: rule 3: differs" in out
    assert "matches nothing any more" in out


def test_rule_2_the_manifest(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _consumer(tmp_path)
    manifest = root / "interface/INTERFACE.json"
    manifest.unlink()
    _stage(root)
    code, out = _run(root, capsys)
    assert code == 1
    assert "interface/INTERFACE.json: rule 2: is missing" in out
    assert "rules 3 to 5 read the manifest" in out
    _write(root, "interface/INTERFACE.json", "{ not json")
    code, out = _run(root, capsys)
    assert code == 1 and "interface/INTERFACE.json: rule 2: the file: Invalid JSON" in out
    data = json.loads(_manifest("beethoven-research", []))
    data["exports"] = [
        {"id": "x", "kind": "library", "version": 1, "summary": "", "paths": ["a"], "consumers": []}
    ]
    _write(root, "interface/INTERFACE.json", json.dumps(data))
    code, out = _run(root, capsys)
    assert code == 1 and "rule 2: exports.0.kind: Input should be" in out


def test_rule_2_what_the_manifest_lists_and_holds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _consumer(tmp_path)
    data = json.loads((root / "interface/INTERFACE.json").read_text(encoding="utf-8"))
    entry = {"kind": "data", "version": 1, "summary": "s", "consumers": [f"{ORG}/{AGENTS}"]}
    data["exports"] = [
        {**entry, "id": "rules", "paths": ["interface/rules.json", "data/*.json", "docs/"]},
        {**entry, "id": "missing", "paths": ["data/none.json", "empty/", "data/*.csv"]},
    ]
    _write(root, "interface/INTERFACE.json", json.dumps(data))
    _write(root, "interface/rules.json", "{}")
    _write(root, "interface/README.md", "what this repository offers\n")
    _write(root, "interface/stray.json", "{}")
    _write(root, "data/a.json", "{}")
    _write(root, "docs/x.md", "x\n")
    (root / "empty").mkdir()
    _write(root, "elsewhere/ORIGIN", "repository x\n")
    _stage(root)
    code, out = _run(root, capsys)
    assert code == 1
    for pattern in ("data/none.json", "empty/", "data/*.csv"):
        assert f"rule 2: export missing lists {pattern}, which names no file" in out
    assert "interface/stray.json: rule 2: is in interface/ but no export lists it" in out
    assert "elsewhere/ORIGIN: rule 2: records a copy" in out
    assert out.count("rule 2:") == 5


@pytest.mark.parametrize(
    ("change", "said"),
    [
        (lambda root: _write(root, "profiles/wayne.json", "{}\n"), "differs from the bytes"),
        (lambda root: _write(root, "profiles/extra.json", "{}\n"), "not recorded in ORIGIN"),
        (lambda root: (root / "profiles/wayne.json").unlink(), "is recorded in ORIGIN and missing"),
        (
            lambda root: _write(
                root, "profiles/ORIGIN", _origin({"wayne.json": PROFILE}, "export profiles 2")
            ),
            "ORIGIN says export profiles 2, and the import is profiles 1",
        ),
        (
            lambda root: _write(
                root,
                "profiles/ORIGIN",
                _origin({"wayne.json": PROFILE}).replace(f"{ORG}/{HPC}", f"{ORG}/x"),
            ),
            f"ORIGIN names {ORG}/x, not {ORG}/{HPC}",
        ),
        (
            lambda root: _write(
                root, "profiles/ORIGIN", _origin({"wayne.json": PROFILE}).replace(COMMIT, "main")
            ),
            "ORIGIN names no commit",
        ),
        (
            lambda root: _write(
                root, "profiles/ORIGIN", _origin({"wayne.json": PROFILE}) + "sha256 bad x\n"
            ),
            "ORIGIN cannot read the line 'sha256 bad x'",
        ),
        (lambda root: shutil.rmtree(root / "profiles"), "has no folder with its ORIGIN"),
    ],
)
def test_rule_3_a_copy_that_differs_from_its_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], change: Any, said: str
) -> None:
    root = _consumer(tmp_path)
    change(root)
    code, out = _run(root, capsys)
    assert code == 1
    assert "rule 3: " in out and said in out
    assert "take a new copy with its pull.sh" in out


def test_rule_3_a_copy_taken_before_its_provider_published(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _consumer(tmp_path)
    _write(root, "profiles/ORIGIN", _origin({"wayne.json": PROFILE}, "path framework/profiles"))
    code, out = _run(root, capsys)
    assert code == 0, out  # no export line yet, and a key of its own is left alone


def test_rule_3_beethoven_qa_is_taken_only_as_its_copy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _consumer(tmp_path)
    data = json.loads((root / "interface/INTERFACE.json").read_text(encoding="utf-8"))
    data["imports"] += [
        _import("beethoven-qa", "kit", "third_party/qa"),
        _import("beethoven-qa", "python", VENDOR_DIR, version=2),
        _import("beethoven-qa", "docs", VENDOR_DIR),
    ]
    _write(root, "interface/INTERFACE.json", json.dumps(data))
    code, out = _run(root, capsys)
    assert code == 1
    assert f"taken only with its vendor command, into {VENDOR_DIR}, never into third_party" in out
    assert "does not offer python version 2 (it offers cli 3, docs 1, kit 2, plugin 1" in out
    assert "does not carry the docs export: docs/CONSUMING.md, docs/STANDARDS.md" in out
    shutil.rmtree(root / VENDOR_DIR / "interface")  # a copy of 1.0.0, from before the manifest
    code, out = _run(root, capsys)
    assert code == 1 and "take beethoven-qa 2.0.0 or later" in out


@pytest.mark.parametrize(
    ("source", "what"),
    [
        ("from beethoven_qa.gate import GateConfig\n", "beethoven_qa.gate.GateConfig"),
        ("import os\nimport beethoven_qa.vendor\n", "beethoven_qa.vendor"),
        ("from beethoven_qa import gate, contracts\n", "beethoven_qa.gate"),
        ("from beethoven_qa.contracts import *\n", "beethoven_qa.contracts.*"),
    ],
)
def test_rule_4_an_import_of_what_is_not_published(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], source: str, what: str
) -> None:
    root = _consumer(tmp_path)
    _write(root, "tests/test_x.py", "x = 1\n\n" + source)
    _stage(root)
    code, out = _run(root, capsys)
    assert code == 1
    line = 4 if source.startswith("import os") else 3
    said = (
        f"tests/test_x.py:{line}: rule 4: imports {what}, which the python export of beethoven-qa"
    )
    assert said in out
    assert out.count("rule 4: imports") == 1


def test_rule_4_another_provider_through_its_pinned_copy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _consumer(tmp_path)
    _write(root, "src/beethoven_research/run.py", "from beethoven_agents.site import load\n")
    _stage(root)
    code, out = _run(root, capsys)
    assert code == 1
    assert "run.py:1: rule 4: imports beethoven_agents.site, and there is no pinned copy" in out
    assert "boundary: 1 finding in" in out

    # A pinned copy of beethoven-agents' python export that carries its manifest.
    agents_export = {
        "id": "python",
        "kind": "python",
        "version": 1,
        "summary": "s",
        "paths": ["interface/INTERFACE.json", "interface/python.json"],
        "consumers": [],
    }
    files = {
        "interface/INTERFACE.json": _manifest(AGENTS, [], [agents_export]),
        "interface/python.json": json.dumps({"beethoven_agents.site": ["load"]}),
    }
    for rel, text in files.items():
        _write(root, f"vendor/{AGENTS}/{rel}", text)
    origin = [f"repository {ORG}/{AGENTS}", f"commit {COMMIT}", "export python 1"]
    origin += [f"sha256 {_sha(text)} {rel}" for rel, text in files.items()]
    _write(root, f"vendor/{AGENTS}/ORIGIN", "\n".join(origin) + "\n")
    data = json.loads((root / "interface/INTERFACE.json").read_text(encoding="utf-8"))
    data["imports"].append(_import(AGENTS, "python", f"vendor/{AGENTS}"))
    _write(root, "interface/INTERFACE.json", json.dumps(data))
    _stage(root)
    code, out = _run(root, capsys)
    assert code == 0, out

    _write(root, "src/beethoven_research/run.py", "from beethoven_agents.site import save\n")
    _stage(root)
    code, out = _run(root, capsys)
    assert code == 1
    assert "imports beethoven_agents.site.save, which the python export of beethoven-agents" in out


@pytest.mark.parametrize(
    ("python", "said"),
    [
        (None, "which lists no python export with one file under interface/"),
        ("[1, 2]", "which cannot be read"),
    ],
)
def test_rule_4_a_provider_copy_without_a_usable_surface(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], python: str | None, said: str
) -> None:
    root = _consumer(tmp_path)
    surface = root / VENDOR_DIR / "interface/python.json"
    if python is None:
        data = json.loads((root / VENDOR_DIR / "interface/INTERFACE.json").read_text("utf-8"))
        data["exports"] = [e for e in data["exports"] if e["kind"] != "python"]
        _write(root, f"{VENDOR_DIR}/interface/INTERFACE.json", json.dumps(data))
        _write(root, "interface/INTERFACE.json", _manifest("beethoven-research", []))
    else:
        surface.write_text(python, encoding="utf-8")
    code, out = _run(root, capsys)
    assert code == 1
    assert "rule 4: imports beethoven_qa" in out and said in out


def test_rule_4_a_file_that_is_not_python(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _consumer(tmp_path)
    _write(root, "tools/broken.py", "def (:\n")
    _stage(root)
    code, out = _run(root, capsys)
    assert code == 1 and "tools/broken.py: rule 4: cannot be read as Python" in out


def test_rule_5_the_direction(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _consumer(tmp_path, AGENTS)
    _write(root, "src/beethoven_research/__init__.py", "from beethoven_research import splits\n")
    _write(root, "tools/use.py", "import beethoven_research.splits\n")
    data = json.loads((root / "interface/INTERFACE.json").read_text(encoding="utf-8"))
    data["imports"].append(_import("beethoven-research", "python", "vendor/research"))
    _write(root, "interface/INTERFACE.json", json.dumps(data))
    _stage(root)
    code, out = _run(root, capsys)
    assert code == 1
    assert f"tools/use.py:1: rule 5: {AGENTS} imports beethoven_research.splits" in out
    assert f"interface/INTERFACE.json: rule 5: {AGENTS} imports beethoven-research" in out
    assert "rule 4: imports beethoven_research" not in out  # the direction is the cause

    qa = tmp_path / "qa"
    qa.mkdir()
    _git(qa, "init", "-q")
    _write(qa, "interface/INTERFACE.json", _manifest("beethoven-qa", [_import(HPC, "data", "d")]))
    _write(qa, "d/a.json", "{}\n")
    _write(
        qa,
        "d/ORIGIN",
        f"repository {ORG}/{HPC}\ncommit {COMMIT}\nsha256 {_sha('{}' + chr(10))} a.json\n",
    )
    _write(
        qa, "src/beethoven_qa/x.py", f"import beethoven_agents\nfrom {'beethoven_qa'} import y\n"
    )
    _stage(qa)
    code, out = _run(qa, capsys)
    assert code == 1
    assert f"interface/INTERFACE.json: rule 5: beethoven-qa imports {HPC}" in out
    assert "src/beethoven_qa/x.py:1: rule 5: beethoven-qa imports beethoven_agents" in out
    assert out.count("rule 5:") == 2


def test_when_the_rules_cannot_be_checked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _consumer(tmp_path)
    code, out = _run(root / "src", capsys)
    assert code == 2 and "is not the root of a git checkout" in out
    assert main(["boundary", "--root", str(tmp_path)]) == 2
    _write(root, "pyproject.toml", "[tool.beethoven-qa\n")
    code, out = _run(root, capsys)
    assert code == 2 and "the [tool.beethoven-qa] table cannot be used" in out
    (root / "pyproject.toml").unlink()
    assert _run(root, capsys)[0] == 0  # no table, no exceptions

    def fails(*args: object, **kwargs: object) -> str:
        raise subprocess.CalledProcessError(128, ["git"])

    monkeypatch.setattr(boundary, "_git", fails)
    assert main(["boundary", "--root", str(root)]) == 2
    assert "boundary: git failed" in capsys.readouterr().out


def test_the_copy_carries_the_manifest_the_check_reads(tmp_path: Path) -> None:
    # What a consumer runs: the copy's own code, from the copy's own src/, on the consumer.
    root = _consumer(tmp_path)
    copy = root / VENDOR_DIR / "src"
    shutil.copytree(REPO / "src/beethoven_qa", copy / "beethoven_qa")
    _stage(root)
    env = {**_env(), "PYTHONPATH": str(copy), "PYTHONDONTWRITEBYTECODE": "1"}
    import sys

    done = subprocess.run(
        [sys.executable, "-m", "beethoven_qa", "boundary", "--root", str(root)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert os.path.exists(root / VENDOR_DIR / "interface/INTERFACE.json")
