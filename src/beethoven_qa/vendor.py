"""The pinned copy of beethoven-qa that every consuming repository carries (plan/REPO_SPLIT.md, 3).

Why a copy and not a dependency fetched from GitHub: the repositories are private, a GitHub Actions
run of one repository cannot read another private repository with its own token, and the grid's
image build has no GitHub token at all. A copy needs no secret anywhere, which is the project's
rule, and it is the pattern the launcher already uses for the site profiles. What keeps a copy
honest is its record: ``vendor/beethoven-qa/ORIGIN`` names the repository, the release and the
commit it was taken from and the sha256 of the copied tree, and ``vendor-check`` recomputes that
digest in the consumer's CI, so an edit to the copy or a kit file that leaves ORIGIN alone (drift,
a formatter, a hand fix) fails. It is a check in the consumer, not a proof: an edit that also
rewrites ORIGIN passes it. ``vendor-check --source <beethoven-qa checkout>`` closes that gap where
both checkouts are present, by rebuilding the digest from the commit ORIGIN names.

Two commands, both run through ``python -m beethoven_qa``:

- ``vendor --into <consumer>`` runs from a clean beethoven-qa checkout and writes the copy (the
  package, the plugin and its marketplace file, and the repository kit: the pre-push hook, the
  auto-merge and rulesets workflows and the cloud-session hook every repository carries alike),
  read from the commit's own objects, never from the working tree. With ``--kit`` it also puts the
  kit files in place in the consumer. Before it writes anything it refuses a link anywhere on a
  path it would write, a file in the way of a directory, and any existing file it would replace
  that is not committed and unchanged (except a copy that still matches its ORIGIN). It never
  deletes: a file the new release no longer has is listed with a line for the person to run in a
  reviewed commit, and vendor-check fails until they have.
- ``vendor-check`` runs in the consumer (its CI and its gate) and fails on any difference between
  the copy and its ORIGIN, or between the live kit files and the copy's.

The functions that carry contracts are pure; the ones that read, write and run git are private and
carry none, so the CrossHair gate never runs them with symbolic arguments.
"""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import subprocess
from pathlib import Path

from beartype import beartype
from pydantic import BaseModel, ConfigDict, NonNegativeInt, field_validator

from beethoven_qa.contracts import ensure, require

__all__ = [
    "COPY_ROOTS",
    "KIT",
    "ORIGIN_NAME",
    "REPOSITORY",
    "VENDOR_DIR",
    "Origin",
    "is_safe_relpath",
    "stale_files",
    "tree_digest",
]

REPOSITORY = "OpenSpineConsortium/beethoven-qa"
# Where the copy lives in a consumer, relative to its repository root.
VENDOR_DIR = "vendor/beethoven-qa"
ORIGIN_NAME = "ORIGIN"
# What the copy holds: the package (imported through the consumer's PYTHONPATH), the plugin and the
# marketplace file that lists it (so the copied directory is itself a plugin marketplace the image
# can register), and pyproject.toml for the package's declared dependencies.
COPY_ROOTS: tuple[str, ...] = ("pyproject.toml", "src/beethoven_qa", "plugin", ".claude-plugin")
# The repository kit: files every consuming repository carries byte for byte. They are copied under
# kit/ in the copy, and vendor-check compares the live files against them.
KIT: tuple[str, ...] = (
    ".githooks/pre-push",
    ".github/workflows/automerge.yml",
    ".github/workflows/rulesets.yml",
    ".claude/hooks/session-start.sh",
)

_HEX64 = re.compile(r"[0-9a-f]{64}")
_HEX40 = re.compile(r"[0-9a-f]{40}")


@beartype
@ensure(lambda result, path: not result or len(path) > 0, "never empty")
@ensure(lambda result, path: not result or not path.startswith("/"), "never absolute")
@ensure(lambda result, path: not result or "\\" not in path, "forward slashes only")
@ensure(
    lambda result, path: not result or all(part not in ("", ".", "..") for part in path.split("/")),
    "no empty, current or parent component, so it cannot leave the directory it is joined to",
)
def is_safe_relpath(path: str) -> bool:
    """``True`` when ``path`` is a relative path that stays inside the directory it is joined to."""
    if len(path) == 0 or path.startswith("/") or "\\" in path or "\x00" in path:
        return False
    return all(part not in ("", ".", "..") for part in path.split("/"))


@beartype
@require(
    lambda entries: all(is_safe_relpath(p) for p, _ in entries), "every path is a safe relpath"
)
@require(
    lambda entries: all(_HEX64.fullmatch(h) is not None for _, h in entries),
    "every file hash is a sha256 in lower-case hex",
)
@require(lambda entries: len({p for p, _ in entries}) == len(entries), "each path appears once")
@ensure(lambda result: _HEX64.fullmatch(result) is not None, "a sha256 in lower-case hex")
def tree_digest(entries: tuple[tuple[str, str], ...]) -> str:
    """The sha256 of a tree given as (path, sha256 of the file's bytes) pairs, in any order.

    Sorting first makes the digest a property of the tree, not of the order a directory walk
    happened to return. Read from the start, each entry is its path up to the first NUL (a safe
    path holds none), then exactly 64 hex characters and a newline, so two different trees never
    produce the same text.
    """
    text = "".join(f"{path}\x00{digest}\n" for path, digest in sorted(entries))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# The precondition is the real domain (``_sync`` refuses any other name before it gets here), and it
# also keeps CrossHair 0.0.111 away from a fault of its own: sorting symbolic strings that hold NUL
# characters trips an internal ``assert False`` in its string comparison, reported as an exception
# in this function although the function is fine on those inputs (measured 2026-10-04).
@beartype
@require(
    lambda old, new: all(is_safe_relpath(p) for p in old | new),
    "file names of a copy, which are always safe relative paths",
)
@ensure(lambda result: list(result) == sorted(result), "listed in a stable order")
@ensure(lambda result, old, new: set(result) == old - new, "exactly the files the release dropped")
def stale_files(old: frozenset[str], new: frozenset[str]) -> list[str]:
    """Files present in the old copy that the new release no longer has."""
    return sorted(old - new)


class Origin(BaseModel):
    """The record of where a copy came from: the boundary where an ORIGIN file is read back."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    repository: str
    ref: str
    commit: str
    tree_sha256: str
    files: NonNegativeInt

    @field_validator("commit")
    @classmethod
    def _commit(cls, value: str) -> str:
        if _HEX40.fullmatch(value) is None:
            raise ValueError("commit must be a full 40-character git SHA in lower-case hex")
        return value

    @field_validator("tree_sha256")
    @classmethod
    def _tree(cls, value: str) -> str:
        if _HEX64.fullmatch(value) is None:
            raise ValueError("tree_sha256 must be a sha256 in lower-case hex")
        return value

    @field_validator("repository", "ref")
    @classmethod
    def _one_line(cls, value: str) -> str:
        if not value or "\n" in value or ":" in value:
            raise ValueError("must be one non-empty line without a colon")
        return value

    def render(self) -> str:
        return "".join(f"{key}: {value}\n" for key, value in self.model_dump().items())

    @classmethod
    def parse(cls, text: str) -> Origin:
        fields: dict[str, str] = {}
        for line in text.splitlines():
            if not line.strip():
                continue
            key, sep, value = line.partition(": ")
            if not sep or key in fields:
                raise ValueError(f"ORIGIN line {line!r} is not one 'key: value' pair")
            fields[key] = value
        return cls.model_validate(fields)


# --- I/O: private, uncontracted, never run symbolically -------------------------------------------

# The modes a copied file may have at the commit: a regular file, executable or not. A link (120000)
# or a submodule (160000) is refused; a copy holds files only.
_MODES = {"100644": 0o644, "100755": 0o755}


class _Refused(Exception):
    """A reason to write nothing, found before the first write."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git(cwd: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)
    return out.stdout


def _git_bytes(cwd: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True).stdout


def _toplevel(path: Path) -> Path | None:
    try:
        return Path(_git(path, "rev-parse", "--show-toplevel").strip()).resolve()
    except (subprocess.CalledProcessError, FileNotFoundError, NotADirectoryError):
        return None


def _walk(root: Path) -> tuple[dict[str, Path], list[str]]:
    # Every regular file under root, by its path relative to root, and every link found (a copy
    # written by `vendor` never holds one). Python's byte-code caches (.pyc files directly inside a
    # __pycache__ directory) are not part of a copy: importing the vendored package creates them.
    # Anything else in a __pycache__ directory is counted, so the cache cannot hide content.
    found: dict[str, Path] = {}
    links: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            full = Path(dirpath) / name
            if full.is_symlink():
                links.append(full.relative_to(root).as_posix())
        for name in filenames:
            full = Path(dirpath) / name
            if full.is_symlink() or (Path(dirpath).name == "__pycache__" and name.endswith(".pyc")):
                continue
            found[full.relative_to(root).as_posix()] = full
    return found, sorted(links)


def _source_root() -> Path:
    # src/beethoven_qa/vendor.py -> the repository root of the checkout this module runs from.
    return Path(__file__).resolve().parents[2]


def _blobs(source: Path, commit: str) -> dict[str, tuple[int, bytes]]:
    # What a copy of ``commit`` holds, read from git's objects: COPY_ROOTS at their own paths and
    # the kit under kit/, each with the mode the commit gives it. The working tree is never read,
    # so neither a line-ending conversion nor an assume-unchanged edit can enter a copy.
    listing = _git_bytes(
        source, "ls-tree", "-r", "-z", "--full-tree", commit, "--", *COPY_ROOTS, *KIT
    )
    files: dict[str, tuple[int, bytes]] = {}
    for item in listing.split(b"\x00"):
        if not item:
            continue
        meta, _, raw = item.partition(b"\t")
        mode, kind, oid = meta.decode("ascii").split(" ")
        try:
            path = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _Refused(f"a file name at {commit[:12]} is not UTF-8: {raw!r}") from exc
        if kind != "blob" or mode not in _MODES:
            raise _Refused(f"{path} is a {mode} entry at {commit[:12]} (a link or a submodule)")
        rel = f"kit/{path}" if path in KIT else path
        if not is_safe_relpath(rel):
            raise _Refused(f"refusing an unsafe path {rel!r}")
        files[rel] = (_MODES[mode], _git_bytes(source, "cat-file", "blob", oid))
    missing = [rel for rel in KIT if f"kit/{rel}" not in files]
    if missing:
        raise _Refused(f"the kit is incomplete at {commit[:12]}: {', '.join(missing)}")
    return files


def _blocked(base: Path, rel: str, directory: bool = False) -> str | None:
    # Why writing base/rel could reach somewhere else or replace the wrong thing, or None. ``base``
    # is a resolved directory; every component below it must be a real directory or not exist yet,
    # and the last one a regular file (a directory when ``directory``) or not exist yet.
    probe = base
    parts = rel.split("/")
    for i, part in enumerate(parts):
        probe = probe / part
        if probe.is_symlink():
            return f"{probe} is a link"
        if not probe.exists():
            return None
        last = i == len(parts) - 1
        if (not last or directory) and not probe.is_dir():
            return f"{probe} is a file where a directory belongs"
        if last and not directory and not probe.is_file():
            return f"{probe} is not a regular file"
    return None


def _committed(into: Path, paths: list[str]) -> set[str]:
    # The paths (relative to ``into``) that git tracks and that are unchanged from HEAD. Asked of
    # git directly, file by file, so neither .gitignore nor status.showUntrackedFiles can hide one.
    if not paths:
        return set()
    tracked = set(_git(into, "--literal-pathspecs", "ls-files", "-z", "--", *paths).split("\x00"))
    try:
        _git(into, "rev-parse", "--verify", "--quiet", "HEAD")
    except subprocess.CalledProcessError:
        return set()  # nothing is committed yet
    changed = _git(into, "--literal-pathspecs", "diff", "--name-only", "-z", "HEAD", "--", *paths)
    return (tracked - {""}) - set(changed.split("\x00"))


def _write(path: Path, data: bytes, mode: int) -> None:
    # O_NOFOLLOW: a link that appeared after the checks is refused here rather than followed.
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.chmod(path, mode, follow_symlinks=False)


def _sync(into: Path, install_kit: bool, source: Path | None = None) -> int:
    # ``source`` is only ever given by the tests, which copy from a throwaway checkout.
    source = (source or _source_root()).resolve()
    if _toplevel(source) != source:
        print(
            f"vendor: {source} is not a beethoven-qa checkout (a vendored copy cannot copy itself)"
        )
        return 2
    if _git(source, "status", "--porcelain", "--untracked-files=all"):
        print("vendor: the beethoven-qa checkout has uncommitted changes; a copy is of a commit")
        return 2
    into = into.resolve()
    if _toplevel(into) != into:
        print(f"vendor: {into} is not the root of a git checkout")
        return 2
    if into == source:
        print("vendor: refusing to copy beethoven-qa into itself")
        return 2

    commit = _git(source, "rev-parse", "HEAD").strip()
    try:
        ref = _git(source, "describe", "--tags", "--exact-match", "HEAD").strip()
    except subprocess.CalledProcessError:
        ref = "untagged"
    dest = into / VENDOR_DIR
    try:
        files = _blobs(source, commit)
        origin = Origin(
            repository=REPOSITORY,
            ref=ref,
            commit=commit,
            tree_sha256=tree_digest(
                tuple((rel, _sha256(data)) for rel, (_, data) in files.items())
            ),
            files=len(files),
        )
        old = _existing(into, dest)
        kit = {rel: files[f"kit/{rel}"] for rel in KIT} if install_kit else {}
        _preflight(into, dest, files, kit, old)
    except _Refused as refusal:
        print(f"vendor: {refusal}")
        return 2
    except ValueError as exc:  # an Origin the record format cannot hold, such as a 64-hex commit
        print(f"vendor: this commit cannot be recorded in ORIGIN: {exc}")
        return 2

    try:
        for rel, (mode, data) in sorted(files.items()):
            _write(dest / rel, data, mode)
        _write(dest / ORIGIN_NAME, origin.render().encode("utf-8"), 0o644)
        print(f"vendor: {len(files)} files of {REPOSITORY} {ref} ({commit[:12]}) into {dest}")
        for rel, (mode, data) in kit.items():
            _write(into / rel, data, mode)
        if kit:
            print(f"vendor: kit files in place in {into}: {', '.join(KIT)}")
    except OSError as exc:
        print(f"vendor: stopped part way ({exc}); `git status` shows what was written")
        return 3

    gone = stale_files(old, frozenset(files)) if old is not None else []
    if gone:
        paths = " ".join(shlex.quote(f"{VENDOR_DIR}/{rel}") for rel in gone)
        print("vendor: the new release no longer has these files; nothing was deleted. Remove them")
        print("        in a reviewed commit (this works whether or not they were committed), then")
        print("        run vendor-check:")
        print(f"        rm -- {paths} && git rm -q --cached --ignore-unmatch -- {paths}")
        return 1
    return 0


def _existing(into: Path, dest: Path) -> frozenset[str] | None:
    # The files of the copy already there, or None when there is none. A link anywhere in it, or a
    # name a copy can never hold, is refused: a copy written by `vendor` holds neither.
    reason = _blocked(into, VENDOR_DIR, directory=True)
    if reason is not None:
        raise _Refused(reason)
    if not dest.exists():
        return None
    present, links = _walk(dest)
    if links:
        raise _Refused(f"{dest} holds links a copy never has: {', '.join(links)}")
    old = frozenset(rel for rel in present if rel != ORIGIN_NAME)
    if not all(is_safe_relpath(rel) for rel in old):
        raise _Refused(f"{dest} holds a file name a copy can never hold; clean it up by hand")
    return old


def _preflight(
    into: Path,
    dest: Path,
    files: dict[str, tuple[int, bytes]],
    kit: dict[str, tuple[int, bytes]],
    old: frozenset[str] | None,
) -> None:
    # Every reason to write nothing, before the first write: a link or a file in the way on any path
    # to be written, and any existing file to be replaced that is not committed and unchanged.
    # Replacing is allowed without that only where nothing can be lost: a copy that still matches
    # its ORIGIN, and a kit file equal to that copy's kit file, are rewritten from a commit.
    targets = [f"{VENDOR_DIR}/{rel}" for rel in sorted(files)] + [f"{VENDOR_DIR}/{ORIGIN_NAME}"]
    targets += list(kit)
    for rel in targets:
        reason = _blocked(into, rel)
        if reason is not None:
            # A file the old copy had where the release now has a directory, or the reverse: the
            # old files in the way, for the person to remove in a reviewed commit.
            inside = rel.removeprefix(f"{VENDOR_DIR}/")
            way = sorted(
                r for r in old or () if inside.startswith(f"{r}/") or r.startswith(f"{inside}/")
            )
            hint = f"; remove {', '.join(way)} in a reviewed commit first" if way else ""
            raise _Refused(f"{reason}{hint}")
    pristine = old is not None and _pristine(dest)
    existing = [rel for rel in targets if (into / rel).is_file()]
    safe = _committed(into, existing)
    for rel in existing:
        if rel in safe:
            continue
        if pristine and rel.startswith(f"{VENDOR_DIR}/"):
            continue
        if pristine and rel in kit:
            kept = dest / "kit" / rel
            if kept.is_file() and (into / rel).read_bytes() == kept.read_bytes():
                continue
        raise _Refused(f"{rel} has uncommitted changes in {into}; commit or restore it first")


def _measure(dest: Path) -> tuple[str, int] | None:
    # The digest and file count of a copy as it is on disk, or None when it holds a link or a file
    # name a copy can never hold.
    present, links = _walk(dest)
    present.pop(ORIGIN_NAME, None)
    if links or not all(is_safe_relpath(rel) for rel in present):
        return None
    digest = tree_digest(tuple((rel, _sha256(p.read_bytes())) for rel, p in present.items()))
    return digest, len(present)


def _read_origin(dest: Path) -> Origin:
    path = dest / ORIGIN_NAME
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{path} is not a regular file")
    return Origin.parse(path.read_text(encoding="utf-8"))


def _pristine(dest: Path) -> bool:
    try:
        origin = _read_origin(dest)
    except (OSError, ValueError):
        return False
    return _measure(dest) == (origin.tree_sha256, origin.files)


def _check(root: Path, source: Path | None = None) -> int:
    root = root.resolve()
    dest = root / VENDOR_DIR
    reason = _blocked(root, VENDOR_DIR, directory=True)
    if reason is not None or not dest.is_dir():
        print(f"vendor-check: no copy at {VENDOR_DIR} in {root}{f' ({reason})' if reason else ''}")
        return 1
    try:
        origin = _read_origin(dest)
    except (OSError, ValueError) as exc:
        print(f"vendor-check: {VENDOR_DIR}/{ORIGIN_NAME} cannot be read: {exc}")
        return 1
    failures: list[str] = []
    if origin.repository != REPOSITORY:
        failures.append(f"ORIGIN names {origin.repository}, not {REPOSITORY}")
    measured = _measure(dest)
    if measured is None:
        failures.append("the copy holds a link or a file name a copy can never hold")
    elif measured != (origin.tree_sha256, origin.files):
        digest, count = measured
        failures.append(
            f"the copy differs from its ORIGIN ({count} files, tree {digest[:12]}; ORIGIN "
            f"says {origin.files} files, tree {origin.tree_sha256[:12]}): it was edited by hand or "
            "a file the release dropped is still there"
        )
    for rel in KIT:
        live, kept = root / rel, dest / "kit" / rel
        if live.is_symlink() or not live.is_file():
            failures.append(f"kit file {rel} is missing (python -m beethoven_qa vendor --kit)")
        elif not kept.is_file() or live.read_bytes() != kept.read_bytes():
            failures.append(f"kit file {rel} differs from the copy of {origin.ref}")
        elif os.access(live, os.X_OK) != os.access(kept, os.X_OK):
            # git skips a hook that is not executable, without a word: the gate would not run.
            failures.append(f"kit file {rel} is executable in one place and not the other")
    if source is not None:
        failures += _against(source.resolve(), dest, origin)
    for failure in failures:
        print(f"vendor-check: {failure}")
    if not failures:
        anchored = " and the commit it names" if source is not None else ""
        print(
            f"vendor-check: {VENDOR_DIR} matches its ORIGIN{anchored} "
            f"({origin.repository} {origin.ref}, {origin.commit[:12]})"
        )
    return 1 if failures else 0


def _against(source: Path, dest: Path, origin: Origin) -> list[str]:
    # The copy rebuilt from the commit ORIGIN names, in a beethoven-qa checkout: the anchor that a
    # rewritten ORIGIN cannot forge.
    try:
        _git(source, "cat-file", "-e", f"{origin.commit}^{{commit}}")
    except subprocess.CalledProcessError:
        return [f"{source} has no commit {origin.commit[:12]}; fetch it, or the ORIGIN is wrong"]
    try:
        files = _blobs(source, origin.commit)
    except _Refused as refusal:
        return [str(refusal)]
    failures: list[str] = []
    digest = tree_digest(tuple((rel, _sha256(data)) for rel, (_, data) in files.items()))
    if (digest, len(files)) != (origin.tree_sha256, origin.files):
        failures.append(
            f"ORIGIN's tree {origin.tree_sha256[:12]} is not the tree of commit "
            f"{origin.commit[:12]} ({digest[:12]})"
        )
    if origin.ref != "untagged":
        try:
            tagged = _git(source, "rev-parse", "--verify", f"{origin.ref}^{{commit}}").strip()
        except subprocess.CalledProcessError:
            tagged = ""
        if tagged != origin.commit:
            failures.append(f"{origin.ref} in {source} is not commit {origin.commit[:12]}")
    for rel, (mode, _) in files.items():
        on_disk = dest / rel
        if on_disk.is_file() and os.access(on_disk, os.X_OK) != bool(mode & 0o111):
            failures.append(f"{rel} is executable in one place and not the other")
    return failures
