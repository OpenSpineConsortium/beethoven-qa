"""A stand-in for `gh api`, answering GitHub's REST calls for beethoven-qa from a local repository.

The tests of vendor.py run its real calls of `gh` against this, put first on PATH by a two-line `gh`
wrapper. FAKE_GH_REPO names the git repository that plays OpenSpineConsortium/beethoven-qa (its
branch `main` is the default branch); FAKE_GH_TAMPER names a blob id whose bytes are sent with one
byte changed; FAKE_GH_TRUNCATED=1 sends a tree listing cut short; FAKE_GH_LOG names a file each
call is appended to. Anything else is answered the way gh answers a call GitHub refuses: a line on
stderr and exit 1. Only what vendor.py asks is answered, in the shapes GitHub documents.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import urllib.parse
from collections.abc import Callable

REPOSITORY = "repos/OpenSpineConsortium/beethoven-qa"
REPO = os.environ["FAKE_GH_REPO"]


def _env() -> dict[str, str]:
    # Like vendor.py's scrub: a GIT_DIR a test puts in the environment must not aim git elsewhere.
    local = subprocess.run(
        ["git", "rev-parse", "--local-env-vars"], capture_output=True, text=True, check=True
    ).stdout.split()
    return {key: value for key, value in os.environ.items() if key not in local}


def git(*args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", REPO, *args], capture_output=True, check=True, env=_env()
    ).stdout


def commit(ref: str) -> str:
    return git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").decode().strip()


def ancestor(a: str, b: str) -> bool:
    done = subprocess.run(
        ["git", "-C", REPO, "merge-base", "--is-ancestor", a, b], capture_output=True, env=_env()
    )
    return done.returncode == 0


def compare(base: str, head: str) -> dict[str, str]:
    base, head = commit(base), commit(head)
    if base == head:
        return {"status": "identical"}
    if ancestor(head, base):
        return {"status": "behind"}
    return {"status": "ahead" if ancestor(base, head) else "diverged"}


def matching_tags(prefix: str) -> list[dict[str, object]]:
    lines = git("for-each-ref", "--format=%(refname) %(objecttype) %(objectname)", "refs/tags/")
    found: list[dict[str, object]] = []
    for line in lines.decode().splitlines():
        name, kind, sha = line.split(" ")
        if name.startswith(f"refs/tags/{prefix}"):
            found.append({"ref": name, "object": {"type": kind, "sha": sha}})
    return found


def tag(sha: str) -> dict[str, object]:
    head = git("cat-file", "tag", sha).decode().split("\n\n", 1)[0]
    fields = dict(line.split(" ", 1) for line in head.splitlines())
    return {"object": {"type": fields["type"], "sha": fields["object"]}}


def tree(sha: str) -> dict[str, object]:
    entries = []
    for item in git("ls-tree", "-r", "-t", "-z", "--full-tree", sha).split(b"\x00"):
        if item:
            meta, _, path = item.partition(b"\t")
            mode, kind, oid = meta.decode().split(" ")
            entries.append({"path": path.decode(), "mode": mode, "type": kind, "sha": oid})
    return {"sha": sha, "truncated": os.environ.get("FAKE_GH_TRUNCATED") == "1", "tree": entries}


def blob(oid: str) -> dict[str, object]:
    data = git("cat-file", "blob", oid)
    if os.environ.get("FAKE_GH_TAMPER") == oid:
        data = bytes([data[0] ^ 1]) + data[1:]
    # GitHub wraps the base64 at 60 characters a line.
    text = base64.b64encode(data).decode()
    lines = "\n".join(text[i : i + 60] for i in range(0, len(text), 60))
    return {"sha": oid, "size": len(data), "encoding": "base64", "content": lines + "\n"}


ROUTES: list[tuple[str, Callable[[list[str]], object]]] = [
    (r"", lambda m: {"default_branch": "main", "full_name": REPOSITORY[6:]}),
    (r"/commits/([^/]+)", lambda m: {"sha": commit(m[1])}),
    (r"/compare/([^/]+)\.\.\.([0-9a-f]{40})", lambda m: compare(m[1], m[2])),
    (r"/git/matching-refs/tags/([^/]*)", lambda m: matching_tags(m[1])),
    (r"/git/tags/([0-9a-f]{40})", lambda m: tag(m[1])),
    (r"/git/trees/([0-9a-f]{40})\?recursive=1", lambda m: tree(m[1])),
    (r"/git/blobs/([0-9a-f]{40})", lambda m: blob(m[1])),
]


def answer(path: str) -> object:
    for pattern, reply in ROUTES:
        found = re.fullmatch(re.escape(REPOSITORY) + pattern, path)
        if found:
            parts = [urllib.parse.unquote(g) for g in found.groups()]
            return reply([found[0], *parts])
    raise LookupError(path)


def main(argv: list[str]) -> int:
    if os.environ.get("FAKE_GH_LOG"):
        with open(os.environ["FAKE_GH_LOG"], "a", encoding="utf-8") as log:
            log.write(" ".join(argv) + "\n")
    if len(argv) != 2 or argv[0] != "api":
        print(f"fake gh: not a call vendor.py makes: {argv}", file=sys.stderr)
        return 2
    try:
        print(json.dumps(answer(argv[1])))
    except (LookupError, subprocess.CalledProcessError, ValueError):
        print("gh: Not Found (HTTP 404)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
