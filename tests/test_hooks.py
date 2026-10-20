"""Runtime checks of the kit's pre-push hook, and of the CI step that parses the kit.

The hook is carried byte for byte by every consumer, so what it does is pinned here with the real
file: a throwaway repository points core.hooksPath at a committed copy of .githooks, a stub `pixi`
on PATH records how the gate was called and what it inherited, and pushes go to a bare remote.
What is pinned: the gate never inherits the repository variables git exports to a hook (from a
linked worktree, GIT_DIR names that worktree's git directory, and a git that inherits it acts on
this repository whatever -C names); the hook gates the commit being pushed, not whatever is
checked out; a red gate refuses the push; SKIP_GATE=1 skips it and says so.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
HOOKS = REPO / ".githooks"
CI = REPO / ".github" / "workflows" / "ci.yml"
KIT_SHELL = (".githooks/pre-push", ".claude/hooks/beethoven-qa-session-start.sh")


@functools.cache
def _git_local_env() -> frozenset[str]:
    # The variables git honours as naming a repository, asked of the git in use rather than kept
    # as a list a newer git could outgrow. The listing needs no repository.
    out = subprocess.run(
        ["git", "rev-parse", "--local-env-vars"], check=True, capture_output=True, text=True
    )
    return frozenset(out.stdout.split())


def _env(bin_dir: Path, **extra: str) -> dict[str, str]:
    # Hermetic: the throwaway repositories see neither the developer's global and system git
    # configuration (a signing key would turn `commit` and `tag` into failures) nor the repository
    # the environment may already name in GIT_DIR (these tests run under the real hook too, from
    # the gate). The stub pixi goes first on PATH, ahead of the real one.
    env = {key: value for key, value in os.environ.items() if key not in _git_local_env()}
    env.update(
        GIT_CONFIG_GLOBAL="/dev/null",
        GIT_CONFIG_NOSYSTEM="1",
        GIT_AUTHOR_NAME="test",
        GIT_AUTHOR_EMAIL="test@example.invalid",
        GIT_COMMITTER_NAME="test",
        GIT_COMMITTER_EMAIL="test@example.invalid",
        PATH=f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
    )
    env.update(extra)
    return env


@dataclass(frozen=True)
class Lab:
    work: Path
    remote: Path
    bin: Path
    record: Path

    def git(self, *args: str, cwd: Path | None = None) -> str:
        return subprocess.run(
            ["git", "-C", str(cwd or self.work), *args],
            check=True,
            capture_output=True,
            text=True,
            env=_env(self.bin),
        ).stdout.strip()

    def push(
        self, *refspecs: str, cwd: Path | None = None, **extra: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(cwd or self.work), "push", "-q", "origin", *refspecs],
            capture_output=True,
            text=True,
            env=_env(self.bin, **extra),
        )

    def stub(self, exit_code: int) -> None:
        # Stands in for pixi: appends its arguments to one file, writes its environment to another,
        # answers as told. The record paths are baked in, so the push's environment carries nothing
        # of the test's own that the hook could be blamed for passing on.
        pixi = self.bin / "pixi"
        pixi.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$*\" >> '{self.record / 'calls'}'\n"
            f"env > '{self.record / 'env'}'\n"
            f"exit {exit_code}\n"
        )
        pixi.chmod(pixi.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def calls(self) -> list[str]:
        path = self.record / "calls"
        return path.read_text().splitlines() if path.exists() else []

    def gate_env(self) -> dict[str, str]:
        pairs = (line.partition("=") for line in (self.record / "env").read_text().splitlines())
        return {key: value for key, _, value in pairs}

    def remote_ref(self, name: str) -> str | None:
        done = subprocess.run(
            ["git", "-C", str(self.remote), "rev-parse", "--verify", "--quiet", name],
            capture_output=True,
            text=True,
            env=_env(self.bin),
        )
        return done.stdout.strip() if done.returncode == 0 else None

    def commit(self, name: str, text: str) -> str:
        (self.work / name).write_text(text)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", name)
        return self.git("rev-parse", "HEAD")


@pytest.fixture
def lab(tmp_path: Path) -> Lab:
    lab = Lab(tmp_path / "work", tmp_path / "remote.git", tmp_path / "bin", tmp_path / "record")
    lab.bin.mkdir()
    lab.record.mkdir()
    lab.remote.mkdir()
    lab.git("init", "-q", "--bare", cwd=lab.remote)
    lab.work.mkdir()
    lab.git("init", "-q", "-b", "main")
    # The hook as the repository ships it: committed under .githooks and named by a relative
    # core.hooksPath, which is what pixi.toml's `hooks` task sets and what a linked worktree
    # resolves against its own checkout.
    shutil.copytree(HOOKS, lab.work / ".githooks")
    lab.git("config", "core.hooksPath", ".githooks")
    lab.commit("one.txt", "one\n")
    lab.git("remote", "add", "origin", str(lab.remote))
    lab.stub(0)
    return lab


def test_a_push_from_a_linked_worktree_hands_the_gate_no_repository_variable(lab: Lab) -> None:
    worktree = lab.work.parent / "stream"
    lab.git("worktree", "add", "-q", "-b", "stream", str(worktree))
    # The worktree moves on and the main checkout holds uncommitted work: the HEAD the hook
    # compares with and the tree it requires clean are the worktree's own.
    (worktree / "two.txt").write_text("two\n")
    lab.git("add", "-A", cwd=worktree)
    lab.git("commit", "-q", "-m", "two", cwd=worktree)
    (lab.work / "untracked.txt").write_text("not the worktree's\n")
    done = lab.push("HEAD:refs/heads/stream", cwd=worktree)
    assert done.returncode == 0, done.stderr
    assert lab.calls() == ["run --locked gate"]
    leaked = _git_local_env() & lab.gate_env().keys()
    assert not leaked, sorted(leaked)
    assert lab.remote_ref("refs/heads/stream") == lab.git("rev-parse", "HEAD", cwd=worktree)


@pytest.mark.parametrize("change", ["untracked", "modified"])
def test_a_push_from_a_tree_with_uncommitted_changes_is_refused(lab: Lab, change: str) -> None:
    (lab.work / ("new.txt" if change == "untracked" else "one.txt")).write_text("changed\n")
    done = lab.push("HEAD:refs/heads/main")
    assert done.returncode != 0
    assert "pre-push: refused" in done.stderr
    assert "SKIP_GATE=1" in done.stderr
    assert lab.calls() == []
    assert lab.remote_ref("refs/heads/main") is None


def test_a_push_of_a_commit_that_is_not_checked_out_is_refused(lab: Lab) -> None:
    older = lab.git("rev-parse", "HEAD")
    lab.commit("two.txt", "two\n")
    done = lab.push(f"{older}:refs/heads/x")
    assert done.returncode != 0
    assert "pre-push: refused" in done.stderr
    assert "refs/heads/x" in done.stderr
    assert "SKIP_GATE=1" in done.stderr
    assert lab.calls() == []
    assert lab.remote_ref("refs/heads/x") is None


def test_a_clean_push_of_head_runs_the_gate_once_and_goes_through(lab: Lab) -> None:
    head = lab.git("rev-parse", "HEAD")
    done = lab.push("HEAD:refs/heads/main", "HEAD:refs/heads/other")
    assert done.returncode == 0, done.stderr
    assert "pre-push: running the gate" in done.stderr
    assert lab.calls() == ["run --locked gate"]
    assert lab.remote_ref("refs/heads/main") == head
    assert lab.remote_ref("refs/heads/other") == head


def test_an_annotated_tag_of_head_and_a_deletion_go_through(lab: Lab) -> None:
    # A tag's local sha is the tag object, not the commit it names; a deletion's is all zeros.
    # Neither is "a commit other than HEAD".
    lab.git("tag", "-a", "v1.0.0", "-m", "release")
    assert lab.push("HEAD:refs/heads/x", "refs/tags/v1.0.0").returncode == 0
    assert lab.remote_ref("refs/tags/v1.0.0^{commit}") == lab.git("rev-parse", "HEAD")
    assert lab.push(":refs/heads/x").returncode == 0
    assert lab.remote_ref("refs/heads/x") is None


def test_a_red_gate_refuses_the_push(lab: Lab) -> None:
    lab.stub(1)
    done = lab.push("HEAD:refs/heads/main")
    assert done.returncode != 0
    assert lab.calls() == ["run --locked gate"]
    assert lab.remote_ref("refs/heads/main") is None


def test_skip_gate_skips_the_gate_and_says_so(lab: Lab) -> None:
    (lab.work / "new.txt").write_text("not committed\n")
    done = lab.push("HEAD:refs/heads/main", SKIP_GATE="1")
    assert done.returncode == 0, done.stderr
    assert "gate skipped (SKIP_GATE=1)" in done.stderr
    assert lab.calls() == []
    assert lab.remote_ref("refs/heads/main") == lab.git("rev-parse", "HEAD")


def _step_command(workflow: Path, name: str) -> str:
    # The `run:` of the named step, read from the workflow text as written: a plain value, or a
    # literal block (`|`) dedented. Enough for the kit's steps, and no YAML library in the lock.
    lines = workflow.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == f"- name: {name}")
    for j in range(start + 1, len(lines)):
        text = lines[j].strip()
        if text.startswith("- name:"):
            break
        if not text.startswith("run:"):
            continue
        value = text.removeprefix("run:").strip()
        if value != "|":
            return value
        indent = len(lines[j]) - len(lines[j].lstrip())
        block: list[str] = []
        for line in lines[j + 1 :]:
            if line.strip() and len(line) - len(line.lstrip()) <= indent:
                break
            block.append(line)
        return textwrap.dedent("\n".join(block))
    raise AssertionError(f"{workflow.name} has no run line for the step {name!r}")


@pytest.mark.parametrize("broken", [None, *KIT_SHELL])
def test_ci_checks_the_syntax_of_every_kit_shell_file(tmp_path: Path, broken: str | None) -> None:
    # The step runs on a copy of the kit, as CI's bash would run it, with one file broken at a
    # time: a syntax error in the second file passed when both were operands of one bash -n.
    for name in KIT_SHELL:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / name, tmp_path / name)
    if broken is not None:
        with (tmp_path / broken).open("a") as handle:
            handle.write("\nif then fi (((\n")
    done = subprocess.run(
        ["bash", "-eo", "pipefail", "-c", _step_command(CI, "shell syntax of the kit")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert (done.returncode == 0) == (broken is None), (broken, done.stderr)


def _stub_curl(bin_dir: Path, body: bytes) -> Path:
    # Stands in for curl: writes ``body`` to the file named after -o, records the call, exits 0.
    # So the file arrives, and only the digest check can tell it is not the one measured.
    record = bin_dir / "curl-calls"
    curl = bin_dir / "curl"
    curl.write_text(
        "#!/bin/bash\n"
        f"printf '%s\\n' \"$*\" >> '{record}'\n"
        'out=""\n'
        'while [ $# -gt 0 ]; do if [ "$1" = -o ]; then out="$2"; shift; fi; shift; done\n'
        f"[ -n \"$out\" ] && printf '%s' '{body.decode('ascii')}' > \"$out\"\n"
        "exit 0\n"
    )
    curl.chmod(curl.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return record


def test_main_is_merged_only_from_a_branch_that_carries_it() -> None:
    # Measured 2026-10-08: with the strict flag off, the tree main held after a merge was never
    # the one the checks had run on. The rule file is what rulesets.yml applies.
    rule = json.loads((REPO / ".github" / "rulesets" / "main.json").read_text(encoding="utf-8"))
    checks = next(r for r in rule["rules"] if r["type"] == "required_status_checks")
    assert checks["parameters"]["strict_required_status_checks_policy"] is True
    required = {c["context"] for c in checks["parameters"]["required_status_checks"]}
    assert "standards (every group green)" in required
    assert "scorecard (report)" not in required  # it reports, it never blocks


@pytest.mark.parametrize(
    ("workflow", "step", "target"),
    [
        ("ci.yml", "install pixi", "/usr/local/bin/pixi"),
        ("standards.yml", "install pixi", "/usr/local/bin/pixi"),
        ("standards.yml", "fetch scorecard and check its digest", "scorecard.tar.gz"),
    ],
)
def test_a_download_that_is_not_the_one_measured_fails_the_step_and_is_removed(
    tmp_path: Path, workflow: str, step: str, target: str
) -> None:
    # The step as the kit writes it, under a curl that delivers the wrong bytes, with the install
    # path pointed at a throwaway folder: the digest check refuses, the file is gone, and nothing
    # after it (chmod, the run, the unpacking) happens.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_curl(bin_dir, b"not the file measured\n")
    script = _step_command(REPO / ".github" / "workflows" / workflow, step)
    script = script.replace("/usr/local/bin/pixi", str(tmp_path / "pixi"))
    env = {
        **_env(bin_dir),
        "RUNNER_TEMP": str(tmp_path),
        "PIXI_VERSION": "0.81.0",
        "PIXI_SHA256": "0" * 64,
        "SCORECARD_VERSION": "5.5.0",
        "SCORECARD_SHA256": "0" * 64,
    }
    argv = ["bash", "-eo", "pipefail", "-c", script]
    done = subprocess.run(argv, cwd=tmp_path, capture_output=True, text=True, env=env)
    assert done.returncode != 0
    assert "FAILED" in done.stdout + done.stderr
    assert not (tmp_path / Path(target).name).exists()
    assert not (tmp_path / "scorecard").exists()
    assert len((bin_dir / "curl-calls").read_text().splitlines()) == 1


@pytest.mark.parametrize(
    ("workflow", "step"), [("ci.yml", "install pixi"), ("standards.yml", "install pixi")]
)
def test_a_download_that_is_the_one_measured_is_kept_and_run(
    tmp_path: Path, workflow: str, step: str
) -> None:
    # The step's other branch: the bytes whose digest the workflow names arrive, the check
    # passes, the file is made executable and runs (the stand-in answers its --version).
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    body = b"#!/bin/sh\necho pixi 0.81.0\n"
    record = _stub_curl(bin_dir, body)
    script = _step_command(REPO / ".github" / "workflows" / workflow, step)
    script = script.replace("/usr/local/bin/pixi", str(tmp_path / "pixi"))
    digest = hashlib.sha256(body).hexdigest()
    env = {**_env(bin_dir), "PIXI_VERSION": "0.81.0", "PIXI_SHA256": digest}
    env["PATH"] = f"{tmp_path}{os.pathsep}{env['PATH']}"
    argv = ["bash", "-eo", "pipefail", "-c", script]
    done = subprocess.run(argv, cwd=tmp_path, capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "pixi 0.81.0" in done.stdout and os.access(tmp_path / "pixi", os.X_OK)
    assert len(record.read_text().splitlines()) == 1


def _hook(tmp_path: Path, bin_dir: Path) -> subprocess.CompletedProcess[str]:
    # The session-start hook as a cloud session runs it, on a throwaway home and a throwaway
    # repository as the project, whose hooks path the hook sets.
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    env = {**_env(bin_dir), "HOME": str(home), "CLAUDE_CODE_REMOTE": "true"}
    env["CLAUDE_PROJECT_DIR"] = str(project)
    subprocess.run(["git", "init", "-q", str(project)], check=True, env=env)
    return subprocess.run(
        ["bash", str(REPO / ".claude" / "hooks" / "beethoven-qa-session-start.sh")],
        capture_output=True,
        text=True,
        env=env,
    )


def _hooks_path(tmp_path: Path, bin_dir: Path) -> str:
    argv = ["git", "-C", str(tmp_path / "project"), "config", "--get", "core.hooksPath"]
    done = subprocess.run(argv, capture_output=True, text=True, env=_env(bin_dir), check=False)
    return done.stdout.strip()


def _stub_curl_copying(bin_dir: Path, source: Path) -> Path:
    # Stands in for curl with a file of this machine: the bytes arrive whole, and the digest
    # check alone decides what becomes of them.
    record = bin_dir / "curl-calls"
    curl = bin_dir / "curl"
    curl.write_text(
        "#!/bin/bash\n"
        f"printf '%s\\n' \"$*\" >> '{record}'\n"
        'out=""\n'
        'while [ $# -gt 0 ]; do if [ "$1" = -o ]; then out="$2"; shift; fi; shift; done\n'
        f'[ -n "$out" ] && cp \'{source}\' "$out"\n'
        "exit 0\n"
    )
    curl.chmod(curl.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return record


def test_the_hook_keeps_no_pixi_whose_digest_differs(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record = _stub_curl(bin_dir, b"not pixi\n")
    done = _hook(tmp_path, bin_dir)
    assert done.returncode == 0, done.stderr  # the hook never fails a session
    assert "sha256 differs" in done.stdout and "removed" in done.stdout
    assert sorted(p.name for p in (tmp_path / "home" / ".pixi" / "bin").iterdir()) == []
    assert len(record.read_text().splitlines()) == 1
    # The push gate was pointed at before the download, so a session with no pixi still has it.
    assert _hooks_path(tmp_path, bin_dir) == ".githooks"


def test_the_hook_keeps_a_pixi_whose_digest_is_the_one_measured(tmp_path: Path) -> None:
    # The other branch, with the bytes of this machine's own pixi: kept, executable and under its
    # final name when its digest is the measured one (a GitHub runner's pixi is, installed by the
    # workflow's own checked download), else refused like any other file. Which branch this
    # machine takes is decided by the digest here, never assumed; with no pixi on the machine the
    # hook file itself stands in, and that is the refusing branch.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    hook = REPO / ".claude" / "hooks" / "beethoven-qa-session-start.sh"
    measured = re.search(r"^PIXI_SHA256=([0-9a-f]{64})$", hook.read_text(), re.MULTILINE)
    assert measured is not None
    found = shutil.which("pixi") or str(Path.home() / ".pixi" / "bin" / "pixi")
    source = Path(found) if Path(found).is_file() else hook
    record = _stub_curl_copying(bin_dir, source)
    done = _hook(tmp_path, bin_dir)
    assert done.returncode == 0, done.stderr
    assert len(record.read_text().splitlines()) == 1
    assert _hooks_path(tmp_path, bin_dir) == ".githooks"
    kept = tmp_path / "home" / ".pixi" / "bin" / "pixi"
    if hashlib.sha256(source.read_bytes()).hexdigest() == measured.group(1):
        assert kept.is_file() and os.access(kept, os.X_OK), done.stdout
        assert "sha256 differs" not in done.stdout and not (kept.parent / "pixi.tmp").exists()
    else:
        assert "sha256 differs" in done.stdout and not kept.exists()


def test_the_hook_fetches_nothing_when_its_pixi_is_already_there(tmp_path: Path) -> None:
    # A pixi of the right version is kept as it is: no download, so no digest to check; the hook
    # goes on to the locked install, which the stub records.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record = _stub_curl(bin_dir, b"never written\n")
    pixi_bin = tmp_path / "home" / ".pixi" / "bin"
    pixi_bin.mkdir(parents=True)
    pixi = pixi_bin / "pixi"
    pixi.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> '{tmp_path / 'pixi-calls'}'\n"
        '[ "$1" = --version ] && echo "pixi 0.81.0"\n'
        "exit 0\n"
    )
    pixi.chmod(pixi.stat().st_mode | stat.S_IXUSR)
    done = _hook(tmp_path, bin_dir)
    assert done.returncode == 0, done.stderr
    assert not record.exists()
    assert "install --locked" in (tmp_path / "pixi-calls").read_text()
