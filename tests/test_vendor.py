"""Runtime checks of the pinned copy.

CrossHair proves the pure parts (a safe path never leaves its directory; the digest is a sha256;
the stale list is exactly what the release dropped). These tests run the whole round trip in
throwaway git checkouts: a copy is written, recorded and checked; a hand edit, a missing kit file
and a dropped file are each caught; and nothing is ever deleted by the command.
"""

from __future__ import annotations

import importlib.util
import marshal
import os
import py_compile
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from beethoven_qa import vendor
from beethoven_qa.contracts import ViolationError
from beethoven_qa.vendor import (
    COPY_ROOTS,
    KIT,
    KIT_EXECUTABLE,
    ORIGIN_NAME,
    VENDOR_DIR,
    Origin,
    _check,
    _git_env,
    _git_local_env,
    _measure,
    _sync,
    is_safe_relpath,
    stale_files,
    tree_digest,
)

REPO = Path(__file__).resolve().parent.parent
H = "a" * 64


def _env(**extra: str) -> dict[str, str]:
    # The environment of everything this file spawns that may run git: hermetic, so the throwaway
    # repositories see neither the developer's global and system git configuration (a signing key
    # or tag.gpgSign would turn `commit` and `tag` into failures) nor the repository a hook's
    # environment names in GIT_DIR (a pre-push hook of a linked worktree carries one, and git
    # would act on that repository whatever -C names). vendor.py's own scrub is the base, so the
    # tests and the code drop the same repository names. The two configuration carriers the code
    # keeps (a container lane gives safe.directory that way) are dropped here all the same: the
    # throwaway repositories are the running user's own and need no safe.directory, and a push
    # made as `git -c tag.gpgSign=true push` would otherwise carry that setting through the
    # pre-push hook into every release tag written below. Python is told not to write byte-code
    # caches: one written into a throwaway checkout makes it dirty, and the next copy from it is
    # refused.
    env = {
        key: value
        for key, value in _git_env().items()
        if key not in vendor._GIT_CONFIG_ENV
        and not key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))
    }
    env.update(
        GIT_CONFIG_GLOBAL="/dev/null",
        GIT_CONFIG_NOSYSTEM="1",
        GIT_AUTHOR_NAME="test",
        GIT_AUTHOR_EMAIL="test@example.invalid",
        GIT_COMMITTER_NAME="test",
        GIT_COMMITTER_EMAIL="test@example.invalid",
        PYTHONDONTWRITEBYTECODE="1",
    )
    env.update(extra)
    return env


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True, env=_env()
    ).stdout


def _vendor_check(code: Path | str, root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    # `python -m beethoven_qa vendor-check` the way an environment runs it: ``code`` is the src/
    # directory whose package does the checking (a consumer's copy, or a beethoven-qa checkout),
    # or a PYTHONPATH of several, in the order the environment would put them.
    return subprocess.run(
        [sys.executable, "-m", "beethoven_qa", "vendor-check", "--root", str(root), *args],
        env=_env(PYTHONPATH=str(code)),
        capture_output=True,
        text=True,
    )


def _printed_line(out: str) -> str:
    # The `rm -- ... && git rm ...` line vendor prints for files a release dropped.
    return next(text.strip() for text in out.splitlines() if text.strip().startswith("rm --"))


def _run_line(consumer: Path, line: str) -> subprocess.CompletedProcess[str]:
    # The printed line, run in the consumer the way the person would.
    return subprocess.run(
        ["bash", "-c", line], cwd=consumer, env=_env(), capture_output=True, text=True
    )


def _init(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    return path


def _source(tmp_path: Path) -> Path:
    # A throwaway beethoven-qa checkout holding exactly what a copy is taken from, committed and
    # tagged, so the test never depends on the state of the checkout it runs in.
    src = _init(tmp_path / "qa")
    for rel in COPY_ROOTS + KIT:
        origin = REPO / rel
        if origin.is_dir():
            shutil.copytree(origin, src / rel, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            (src / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origin, src / rel)
    _git(src, "add", "-A")
    _git(src, "commit", "-q", "-m", "release")
    _git(src, "tag", "v1.0.0")
    return src


def test_tree_digest_is_a_property_of_the_tree() -> None:
    a = (("a.py", "1" * 64), ("b/c.py", "2" * 64))
    assert tree_digest(a) == tree_digest(tuple(reversed(a)))
    assert tree_digest(a) != tree_digest((("a.py", "1" * 64), ("b/c.py", "3" * 64)))
    assert tree_digest(a) != tree_digest((("a.py", "1" * 64), ("b/d.py", "2" * 64)))
    with pytest.raises(ViolationError):
        tree_digest((("../escape", H),))
    with pytest.raises(ViolationError):
        tree_digest((("a.py", "XYZ"),))
    with pytest.raises(ViolationError):
        tree_digest((("a.py", H), ("a.py", H)))


def test_is_safe_relpath() -> None:
    for good in ("a", "src/beethoven_qa/gate.py", "kit/.githooks/pre-push", ".claude-plugin/x"):
        assert is_safe_relpath(good), good
    for bad in ("", "/etc/passwd", "../x", "a/../../x", "a//b", "./a", "a/.", "a\\b", "a\x00b"):
        assert not is_safe_relpath(bad), bad


def test_stale_files() -> None:
    assert stale_files(frozenset({"a", "b", "c"}), frozenset({"b"})) == ["a", "c"]
    assert stale_files(frozenset(), frozenset({"a"})) == []


def test_origin_round_trip_and_boundary() -> None:
    origin = Origin(
        repository="OpenSpineConsortium/beethoven-qa",
        ref="v1.0.0",
        commit="0" * 40,
        tree_sha256=H,
        files=3,
    )
    assert Origin.parse(origin.render()) == origin
    for text in (
        origin.render().replace("0" * 40, "abc"),  # a short SHA is not a record
        origin.render().replace(H, "x" * 64),
        origin.render() + "extra: 1\n",
        origin.render() + "files: 4\n",  # a key twice
        "repository OpenSpineConsortium/beethoven-qa\n",
    ):
        with pytest.raises((ValidationError, ValueError)):
            Origin.parse(text)


def test_round_trip_and_tamper(tmp_path: Path) -> None:
    src = _source(tmp_path)
    consumer = _init(tmp_path / "consumer")
    assert _sync(consumer, install_kit=True, source=src) == 0
    origin = Origin.parse((consumer / VENDOR_DIR / ORIGIN_NAME).read_text(encoding="utf-8"))
    assert origin.ref == "v1.0.0"
    assert origin.commit == _git(src, "rev-parse", "HEAD").strip()
    assert (consumer / VENDOR_DIR / "src/beethoven_qa/gate.py").is_file()
    assert (consumer / VENDOR_DIR / ".claude-plugin/marketplace.json").is_file()
    assert os.access(consumer / ".githooks/pre-push", os.X_OK)
    assert _check(consumer) == 0

    # Python's byte-code caches appear when the copy is imported; they are not part of it.
    cache = consumer / VENDOR_DIR / "src/beethoven_qa/__pycache__"
    cache.mkdir()
    (cache / "gate.cpython-311.pyc").write_bytes(b"\x00")
    assert _check(consumer) == 0

    target = consumer / VENDOR_DIR / "src/beethoven_qa/gate.py"
    target.write_text(target.read_text(encoding="utf-8") + "\n# edited by hand\n", encoding="utf-8")
    assert _check(consumer) == 1


def test_kit_is_checked(tmp_path: Path) -> None:
    src = _source(tmp_path)
    consumer = _init(tmp_path / "consumer")
    assert _sync(consumer, install_kit=False, source=src) == 0
    assert _check(consumer) == 1  # the kit is not in place
    assert _sync(consumer, install_kit=True, source=src) == 0
    assert _check(consumer) == 0
    (consumer / KIT[1]).write_text("name: drifted\n", encoding="utf-8")
    assert _check(consumer) == 1


def test_a_dropped_file_is_listed_never_deleted(tmp_path: Path) -> None:
    src = _source(tmp_path)
    consumer = _init(tmp_path / "consumer")
    assert _sync(consumer, install_kit=True, source=src) == 0
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-q", "-m", "vendor beethoven-qa v1.0.0")

    _git(src, "rm", "-q", "src/beethoven_qa/example.py")
    _git(src, "commit", "-q", "-m", "drop the example")
    assert _sync(consumer, install_kit=True, source=src) == 1
    dropped = consumer / VENDOR_DIR / "src/beethoven_qa/example.py"
    assert dropped.is_file()  # listed for a reviewed commit, not removed
    assert _check(consumer) == 1
    _git(consumer, "rm", "-q", f"{VENDOR_DIR}/src/beethoven_qa/example.py")
    assert _check(consumer) == 0


@pytest.mark.parametrize("first_copy", ["untracked", "staged", "committed"])
def test_the_printed_line_removes_a_dropped_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], first_copy: str
) -> None:
    # Taking a second release before committing the first, or after: the printed line must work on
    # any git (2.55 refuses `git add -u` on a folder it does not track yet; 2.43 did not).
    src = _source(tmp_path)
    consumer = _init(tmp_path / "consumer")
    assert _sync(consumer, install_kit=True, source=src) == 0
    if first_copy != "untracked":
        _git(consumer, "add", "-A")
    if first_copy == "committed":
        _git(consumer, "commit", "-q", "-m", "the first copy")
    _git(src, "rm", "-q", "src/beethoven_qa/example.py")
    _git(src, "commit", "-q", "-m", "drop the example")
    capsys.readouterr()
    assert _sync(consumer, install_kit=True, source=src) == 1
    done = _run_line(consumer, _printed_line(capsys.readouterr().out))
    assert done.returncode == 0, done.stderr
    dropped = f"{VENDOR_DIR}/src/beethoven_qa/example.py"
    assert not (consumer / dropped).exists()
    assert _git(consumer, "ls-files", "--", dropped) == ""
    assert _check(consumer) == 0


def _committed_link(consumer: Path, link: str, target: str) -> None:
    path = consumer / link
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(target)
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-q", "-m", "a link")


def test_links_on_the_way_are_refused_before_anything_is_written(tmp_path: Path) -> None:
    src = _source(tmp_path)
    # ORIGIN itself a committed link out of the consumer, live and dangling.
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "victim.txt"
    victim.write_text("important\n", encoding="utf-8")
    consumer = _init(tmp_path / "c1")
    _committed_link(consumer, f"{VENDOR_DIR}/ORIGIN", str(victim))
    assert _sync(consumer, install_kit=False, source=src) == 2
    assert victim.read_text(encoding="utf-8") == "important\n"
    assert not (consumer / VENDOR_DIR / "pyproject.toml").exists()
    consumer = _init(tmp_path / "c2")
    _committed_link(consumer, f"{VENDOR_DIR}/ORIGIN", str(outside / "absent.txt"))
    assert _sync(consumer, install_kit=False, source=src) == 2
    assert not (outside / "absent.txt").exists()

    # The copy's directory a link onto the consumer's own root, with unsaved work there.
    consumer = _init(tmp_path / "c3")
    (consumer / "pyproject.toml").write_text("mine\n", encoding="utf-8")
    _committed_link(consumer, VENDOR_DIR, "..")
    (consumer / "pyproject.toml").write_text("mine, edited\n", encoding="utf-8")
    assert _sync(consumer, install_kit=False, source=src) == 2
    assert (consumer / "pyproject.toml").read_text(encoding="utf-8") == "mine, edited\n"

    # A kit file's directory a link elsewhere in the consumer.
    consumer = _init(tmp_path / "c4")
    (consumer / "shared" / "github" / "workflows").mkdir(parents=True)
    _committed_link(consumer, ".github", "shared/github")
    assert _sync(consumer, install_kit=True, source=src) == 2
    assert not (consumer / "shared/github/workflows/automerge.yml").exists()
    assert not (consumer / VENDOR_DIR).exists()


def test_unsaved_files_git_status_would_hide_are_kept(tmp_path: Path) -> None:
    src = _source(tmp_path)
    mine = "#!/bin/sh\necho mine\n"
    # Ignored by the consumer's .gitignore.
    consumer = _init(tmp_path / "c1")
    (consumer / ".gitignore").write_text(".claude/\n", encoding="utf-8")
    hook = consumer / ".claude/hooks/session-start.sh"
    hook.parent.mkdir(parents=True)
    hook.write_text(mine, encoding="utf-8")
    assert _sync(consumer, install_kit=True, source=src) == 2
    assert hook.read_text(encoding="utf-8") == mine
    # Hidden by status.showUntrackedFiles=no.
    consumer = _init(tmp_path / "c2")
    _git(consumer, "config", "status.showUntrackedFiles", "no")
    hook = consumer / ".githooks/pre-push"
    hook.parent.mkdir(parents=True)
    hook.write_text(mine, encoding="utf-8")
    assert _sync(consumer, install_kit=True, source=src) == 2
    assert hook.read_text(encoding="utf-8") == mine


def test_the_copy_is_of_the_commit_not_the_working_tree(tmp_path: Path) -> None:
    src = _source(tmp_path)
    gate = src / "src/beethoven_qa/gate.py"
    _git(src, "update-index", "--assume-unchanged", "src/beethoven_qa/gate.py")
    gate.write_text(gate.read_text(encoding="utf-8") + "\n# not committed\n", encoding="utf-8")
    consumer = _init(tmp_path / "consumer")
    assert _sync(consumer, install_kit=False, source=src) == 0
    copied = (consumer / VENDOR_DIR / "src/beethoven_qa/gate.py").read_text(encoding="utf-8")
    assert copied == _git(src, "show", "HEAD:src/beethoven_qa/gate.py")


def test_a_release_that_turns_a_file_into_a_directory_is_refused_first(tmp_path: Path) -> None:
    src = _source(tmp_path)
    consumer = _init(tmp_path / "consumer")
    assert _sync(consumer, install_kit=True, source=src) == 0
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-q", "-m", "vendor v1")
    before = (consumer / VENDOR_DIR / ORIGIN_NAME).read_text(encoding="utf-8")
    stream = src / "plugin/agents/stream.md"
    body = stream.read_bytes()
    _git(src, "rm", "-q", "plugin/agents/stream.md")
    stream.mkdir(parents=True)
    (stream / "AGENT.md").write_bytes(body)
    _git(src, "add", "-A")
    _git(src, "commit", "-q", "-m", "a file becomes a directory")
    assert _sync(consumer, install_kit=True, source=src) == 2
    assert (consumer / VENDOR_DIR / ORIGIN_NAME).read_text(encoding="utf-8") == before


def test_check_sees_links_caches_and_the_executable_bit(tmp_path: Path) -> None:
    src = _source(tmp_path)
    consumer = _init(tmp_path / "consumer")
    assert _sync(consumer, install_kit=True, source=src) == 0
    assert _check(consumer) == 0
    extra = tmp_path / "elsewhere"
    extra.mkdir()
    (extra / "SKILL.md").write_text("not from the release\n", encoding="utf-8")
    link = consumer / VENDOR_DIR / "plugin/skills/extra"
    link.symlink_to(extra)
    assert _check(consumer) == 1
    link.unlink()
    cache = consumer / VENDOR_DIR / "src/beethoven_qa/__pycache__"
    cache.mkdir(exist_ok=True)
    (cache / "notes.txt").write_text("hidden?\n", encoding="utf-8")
    assert _check(consumer) == 1
    (cache / "notes.txt").unlink()
    assert _check(consumer) == 0
    (consumer / ".githooks/pre-push").chmod(0o644)  # git would skip the hook without a word
    assert _check(consumer) == 1


def test_a_rewritten_origin_is_caught_only_against_the_source(tmp_path: Path) -> None:
    # The limit vendor-check states: an edit that also rewrites ORIGIN passes the plain check, and
    # the check against a beethoven-qa checkout, run from that checkout's own code, catches it.
    src = _source(tmp_path)
    consumer = _init(tmp_path / "consumer")
    assert _sync(consumer, install_kit=True, source=src) == 0
    anchored = _vendor_check(src / "src", consumer, "--source", str(src))
    assert anchored.returncode == 0 and "and the commit it names" in anchored.stdout
    target = consumer / VENDOR_DIR / "src/beethoven_qa/gate.py"
    target.write_text(target.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
    origin_file = consumer / VENDOR_DIR / ORIGIN_NAME
    origin = Origin.parse(origin_file.read_text(encoding="utf-8"))
    measured = _measure(consumer / VENDOR_DIR)
    assert measured is not None
    forged = origin.model_copy(update={"tree_sha256": measured[0], "files": measured[1]})
    origin_file.write_text(forged.render(), encoding="utf-8")
    assert _check(consumer) == 0
    anchored = _vendor_check(src / "src", consumer, "--source", str(src))
    assert anchored.returncode == 1 and "is not the tree of commit" in anchored.stdout


def test_the_anchored_check_runs_only_from_the_checkout_it_is_given(tmp_path: Path) -> None:
    # The consumer's environment imports the copy, so `vendor-check --source` run there is the
    # copy checking itself, and a copy whose vendor.py vouches for itself passes with ORIGIN
    # untouched. The anchored form therefore runs only from the source checkout's own code.
    src = _source(tmp_path)
    consumer = _init(tmp_path / "consumer")
    assert _sync(consumer, install_kit=True, source=src) == 0
    assert _check(consumer, source=src) == 1  # this process runs REPO's code, not src's
    copy = consumer / VENDOR_DIR / "src"
    refused = _vendor_check(copy, consumer, "--source", str(src))
    assert refused.returncode == 1 and "not the checkout this command runs from" in refused.stdout
    assert _vendor_check(copy, consumer).returncode == 0  # the plain check is the copy's to run

    vouching = (
        "\n\ndef _check(root, source=None):\n    print('vouching for myself')\n    return 0\n"
    )
    with (copy / "beethoven_qa/vendor.py").open("a", encoding="utf-8") as fh:
        fh.write(vouching)
    assert _vendor_check(copy, consumer).returncode == 0  # the limit the plain check states
    # Nor does an edited copy refuse the anchored form, which is why the documented line runs it
    # from beethoven-qa's own environment and never the consumer's.
    assert _vendor_check(copy, consumer, "--source", str(src)).returncode == 0
    assert _vendor_check(src / "src", consumer).returncode == 1
    assert _vendor_check(src / "src", consumer, "--source", str(src)).returncode == 1
    # The checks above ran Python on the checkout's own src/; a byte-code cache written there
    # would make the checkout dirty and refuse the next copy taken from it.
    assert _git(src, "status", "--porcelain", "--untracked-files=all") == ""
    assert _sync(_init(tmp_path / "another"), install_kit=True, source=src) == 0


def test_a_package_that_shadows_the_copy_is_refused(tmp_path: Path) -> None:
    # The consumer's PYTHONPATH is its own src/ first, then the copy's: a package of the same name
    # there is what every check and the gate import, with the copy untouched behind it. One that
    # is not edited says so; an edited one is seen by neither form, the limit vendor.py states.
    src = _source(tmp_path)
    consumer = _init(tmp_path / "consumer")
    assert _sync(consumer, install_kit=True, source=src) == 0
    shutil.copytree(src / "src/beethoven_qa", consumer / "src/beethoven_qa")
    own = f"{consumer / 'src'}{os.pathsep}{consumer / VENDOR_DIR / 'src'}"
    shadowed = _vendor_check(own, consumer)
    assert shadowed.returncode == 1 and "shadows the copy" in shadowed.stdout
    assert _vendor_check(consumer / VENDOR_DIR / "src", consumer).returncode == 0  # the copy's own
    assert _check(consumer) == 0  # this process runs REPO's code, which is in neither place
    assert _vendor_check(src / "src", consumer, "--source", str(src)).returncode == 0  # not seen


def test_configuration_given_to_git_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Of what `git rev-parse --local-env-vars` lists, the two configuration carriers name no
    # repository and stay: a container lane commonly gives safe.directory through GIT_CONFIG_COUNT,
    # and without it every git call of vendor-check there would fail.
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "vendor.counted")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "yes")
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'vendor.dashc=yes'")
    src = _source(tmp_path)
    assert vendor._git(src, "config", "--get", "vendor.counted") == "yes\n"
    assert vendor._git(src, "config", "--get", "vendor.dashc") == "yes\n"


@pytest.mark.parametrize("given", ["dash-c", "counted"])
def test_configuration_given_to_git_does_not_reach_the_tests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, given: str
) -> None:
    # The code keeps both carriers (the test above), and a push made as `git -c tag.gpgSign=true
    # push` runs the pre-push hook, and so this suite, with that setting in GIT_CONFIG_PARAMETERS:
    # before _env() dropped them, the release tag of every throwaway checkout became a signed one
    # and `git tag v1.0.0` failed with exit 128 (measured 2026-10-04, git 2.43).
    if given == "dash-c":
        monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'tag.gpgSign=true'")
    else:
        monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
        monkeypatch.setenv("GIT_CONFIG_KEY_0", "tag.gpgSign")
        monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")
    src = _source(tmp_path)
    assert _git(src, "cat-file", "-t", "v1.0.0") == "commit\n"  # lightweight, as written
    consumer = _init(tmp_path / "consumer")
    assert _sync(consumer, install_kit=True, source=src) == 0
    assert _check(consumer) == 0


def test_a_repository_named_in_the_environment_is_never_touched(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # A pre-push hook in a linked worktree runs with GIT_DIR=<main>/.git/worktrees/<name> in its
    # environment, and pixi passes it on to the tests. Before the scrub, `git -C <tmp> init` here
    # set core.bare=true in the developer's checkout, and `commit` and `tag v1.0.0` landed on their
    # branch (measured 2026-10-04, git 2.43).
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        assert name in _git_local_env()
    developer = _init(tmp_path / "developer")
    (developer / "work.txt").write_text("the developer's work\n", encoding="utf-8")
    _git(developer, "add", "-A")
    _git(developer, "commit", "-q", "-m", "stream work")
    _git(developer, "worktree", "add", "-q", str(tmp_path / "wt"), "-b", "stream-1")
    monkeypatch.setenv("GIT_DIR", str(developer / ".git/worktrees/wt"))
    # The premise: git itself, with that environment, acts on the worktree's branch whatever -C
    # names (and, GIT_WORK_TREE being unset, takes that directory for its work tree).
    named = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert named == "stream-1"

    src = _source(tmp_path)
    consumer = _init(tmp_path / "consumer")
    assert _sync(consumer, install_kit=True, source=src) == 0
    assert _check(consumer) == 0
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-q", "-m", "vendor v1.0.0")
    # The printed cleanup line too, run through a shell as the person would: with GIT_DIR reaching
    # it, its `git rm --cached` matched nothing in the developer's index, exited 0 and left the
    # consumer's entry in place (measured 2026-10-04, in this file's own printed-line test).
    _git(src, "rm", "-q", "src/beethoven_qa/example.py")
    _git(src, "commit", "-q", "-m", "drop the example")
    capsys.readouterr()
    assert _sync(consumer, install_kit=True, source=src) == 1
    done = _run_line(consumer, _printed_line(capsys.readouterr().out))
    assert done.returncode == 0, done.stderr
    assert _git(consumer, "ls-files", "--", f"{VENDOR_DIR}/src/beethoven_qa/example.py") == ""
    assert _check(consumer) == 0
    assert _git(developer, "log", "--format=%s", "stream-1") == "stream work\n"
    assert _git(developer, "tag", "-l") == ""
    assert _git(developer, "config", "core.bare") == "false\n"
    assert _git(tmp_path / "wt", "status", "--porcelain") == ""


def test_the_developers_git_configuration_does_not_reach_the_tests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # tag.gpgSign turns the lightweight release tag into a signed one with no message, and
    # commit.gpgSign without a key fails every commit: 13 of 18 tests, for nothing in the code.
    config = tmp_path / "gitconfig"
    config.write_text("[tag]\n\tgpgSign = true\n[commit]\n\tgpgSign = true\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    src = _source(tmp_path)
    assert _git(src, "tag", "-l") == "v1.0.0\n"
    assert _git(src, "cat-file", "-t", "v1.0.0") == "commit\n"  # lightweight, as written


@pytest.mark.parametrize("flag", ["--skip-worktree", "--assume-unchanged"])
def test_an_edit_behind_an_index_flag_is_kept(tmp_path: Path, flag: str) -> None:
    # The usual way to keep a local edit to a tracked file: git then reports the file unchanged,
    # and the bytes on disk are not the commit's.
    src = _source(tmp_path)
    consumer = _init(tmp_path / "consumer")
    assert _sync(consumer, install_kit=True, source=src) == 0
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-q", "-m", "vendor v1.0.0")
    mine = "#!/bin/sh\necho MY UNCOMMITTED WORK\n"
    kept = [KIT[0], f"{VENDOR_DIR}/pyproject.toml"]
    for rel in kept:
        _git(consumer, "update-index", flag, "--", rel)
        (consumer / rel).write_text(mine, encoding="utf-8")
    assert _git(consumer, "status", "--porcelain") == ""  # what git says of it
    assert _sync(consumer, install_kit=True, source=src) == 2
    for rel in kept:
        assert (consumer / rel).read_text(encoding="utf-8") == mine


def _forged_pyc(source: Path, flags: int) -> bytes:
    # PEP 552's header (magic, flags, the genuine source's hash) over code the source never held.
    code = compile("FORGED = True\n", str(source), "exec")
    header = importlib.util.MAGIC_NUMBER + struct.pack("<I", flags)
    return header + importlib.util.source_hash(source.read_bytes()) + marshal.dumps(code)


def _forged(code: Path) -> bool:
    # Reading a cache is unaffected by PYTHONDONTWRITEBYTECODE, so a forged one still loads.
    out = subprocess.run(
        [sys.executable, "-c", "import beethoven_qa.example as m; print(hasattr(m, 'FORGED'))"],
        env=_env(PYTHONPATH=str(code)),
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip() == "True"


def test_a_byte_code_cache_that_carries_code_is_caught(tmp_path: Path) -> None:
    src = _source(tmp_path)
    consumer = _init(tmp_path / "consumer")
    assert _sync(consumer, install_kit=True, source=src) == 0
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-q", "-m", "vendor v1.0.0")
    code = consumer / VENDOR_DIR / "src"
    example = code / "beethoven_qa/example.py"
    pyc = Path(importlib.util.cache_from_source(str(example)))
    rel = pyc.relative_to(consumer).as_posix()
    # Python's own cache, a timestamp pyc, is not part of the copy.
    py_compile.compile(str(example), cfile=str(pyc), doraise=True)
    assert not _forged(code)
    assert _check(consumer) == 0
    # A hash-based pyc (unchecked, then checked) with the genuine source's hash: Python runs it in
    # place of the source, and a fresh clone would too, so it is counted and the digest fails.
    for flags in (0b01, 0b11):
        pyc.write_bytes(_forged_pyc(example, flags))
        assert _forged(code)
        assert _check(consumer) == 1
    # Once git tracks a cache it reaches every clone; refused by name whatever its kind.
    _git(consumer, "add", "-f", "--", rel)
    _git(consumer, "commit", "-q", "-m", "a cache")
    assert _check(consumer) == 1
    py_compile.compile(str(example), cfile=str(pyc), doraise=True)
    assert _check(consumer) == 1
    _git(consumer, "rm", "-q", "--cached", "--", rel)
    assert _check(consumer) == 0


def test_a_hook_git_records_without_the_executable_bit_is_caught(tmp_path: Path) -> None:
    # A checkout whose file system git does not trust for modes (WSL on /mnt/c, NTFS) commits a
    # new 0755 hook as 100644; every clone then has a hook git skips, and the live file and the
    # copy's agree with each other, both without the bit.
    src = _source(tmp_path)
    consumer = _init(tmp_path / "consumer")
    _git(consumer, "config", "core.fileMode", "false")
    assert _sync(consumer, install_kit=True, source=src) == 0
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-q", "-m", "vendor v1.0.0")
    assert _git(consumer, "ls-files", "-s", "--", KIT_EXECUTABLE[0]).startswith("100644 ")
    assert os.access(consumer / KIT_EXECUTABLE[0], os.X_OK)  # this disk still says yes
    assert _check(consumer) == 1
    fresh = tmp_path / "fresh"
    _git(tmp_path, "clone", "-q", str(consumer), str(fresh))
    assert not os.access(fresh / KIT_EXECUTABLE[0], os.X_OK)  # what every clone gets
    assert _check(fresh) == 1
    hooks = [*KIT_EXECUTABLE, *(f"{VENDOR_DIR}/kit/{rel}" for rel in KIT_EXECUTABLE)]
    _git(consumer, "update-index", "--chmod=+x", "--", *hooks)
    assert _check(consumer) == 0


def test_the_mode_is_set_on_the_open_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # glibc before 2.32 refuses fchmodat(AT_SYMLINK_NOFOLLOW) with ENOTSUP, which CPython raises as
    # NotImplementedError, not an OSError: a path-based chmod stopped vendor after its first file,
    # with a traceback and exit 1, the code for "files dropped".
    src = _source(tmp_path)
    consumer = _init(tmp_path / "consumer")

    def unsupported(*args: object, **kwargs: object) -> None:
        raise NotImplementedError("chmod: follow_symlinks unavailable on this platform")

    monkeypatch.setattr(os, "chmod", unsupported)
    assert _sync(consumer, install_kit=True, source=src) == 0
    assert (consumer / KIT_EXECUTABLE[0]).stat().st_mode & 0o777 == 0o755
    assert (consumer / VENDOR_DIR / "pyproject.toml").stat().st_mode & 0o777 == 0o644
    monkeypatch.setattr(os, "fchmod", unsupported)
    assert _sync(consumer, install_kit=True, source=src) == 3


def test_refusals(tmp_path: Path) -> None:
    src = _source(tmp_path)
    consumer = _init(tmp_path / "consumer")
    (src / "untracked.txt").write_text("dirty\n", encoding="utf-8")
    assert _sync(consumer, install_kit=False, source=src) == 2  # a copy is of a commit
    (src / "untracked.txt").unlink()

    assert _sync(consumer / "not-a-root", install_kit=False, source=src) == 2
    assert _sync(src, install_kit=False, source=src) == 2  # never into itself

    assert _sync(consumer, install_kit=False, source=src) == 0
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-q", "-m", "vendor")
    edited = consumer / VENDOR_DIR / "pyproject.toml"
    edited.write_text("edited\n", encoding="utf-8")
    assert _sync(consumer, install_kit=False, source=src) == 2  # would overwrite uncommitted work
    assert edited.read_text(encoding="utf-8") == "edited\n"


def test_check_without_a_copy(tmp_path: Path) -> None:
    assert _check(_init(tmp_path / "empty")) == 1
