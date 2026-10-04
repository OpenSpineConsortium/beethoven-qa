"""Runtime checks of the pinned copy.

CrossHair proves the pure parts (a safe path never leaves its directory; the digest is a sha256;
the stale list is exactly what the release dropped). These tests run the whole round trip in
throwaway git checkouts: a copy is written, recorded and checked; a hand edit, a missing kit file
and a dropped file are each caught; and nothing is ever deleted by the command.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from beethoven_qa.contracts import ViolationError
from beethoven_qa.vendor import (
    COPY_ROOTS,
    KIT,
    ORIGIN_NAME,
    VENDOR_DIR,
    Origin,
    _check,
    _measure,
    _sync,
    is_safe_relpath,
    stale_files,
    tree_digest,
)

REPO = Path(__file__).resolve().parent.parent
H = "a" * 64


def _git(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
    }
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True, env=env
    ).stdout


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
    line = next(
        text.strip()
        for text in capsys.readouterr().out.splitlines()
        if text.strip().startswith("rm --")
    )
    done = subprocess.run(["bash", "-c", line], cwd=consumer, capture_output=True, text=True)
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
    # the check against a beethoven-qa checkout catches it.
    src = _source(tmp_path)
    consumer = _init(tmp_path / "consumer")
    assert _sync(consumer, install_kit=True, source=src) == 0
    assert _check(consumer, source=src) == 0
    target = consumer / VENDOR_DIR / "src/beethoven_qa/gate.py"
    target.write_text(target.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
    origin_file = consumer / VENDOR_DIR / ORIGIN_NAME
    origin = Origin.parse(origin_file.read_text(encoding="utf-8"))
    measured = _measure(consumer / VENDOR_DIR)
    assert measured is not None
    forged = origin.model_copy(update={"tree_sha256": measured[0], "files": measured[1]})
    origin_file.write_text(forged.render(), encoding="utf-8")
    assert _check(consumer) == 0
    assert _check(consumer, source=src) == 1


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
