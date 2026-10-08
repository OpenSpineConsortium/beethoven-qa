"""The plugin's PostToolUse hook: hooks.json and the script it runs, with a stub pixi.

The script is the only thing between Claude Code and ``python -m beethoven_qa coach``, so what it
promises is pinned here with the real file: it asks pixi for the project's environment as it is
(never an install), hands the call on stdin through, passes the answer back unchanged, and ends
quietly with exit 0 and no output whenever anything is missing or fails, so coaching never stands
in a session's way.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
HOOKS = REPO / "plugin" / "hooks"
CALL = '{"tool_name": "Edit", "tool_input": {"file_path": "/x/src/pkg/a.py"}}'
ANSWER = '{"decision": "block", "reason": "told"}'


def _stub(bin_dir: Path, record: Path, body: str) -> None:
    # Stands in for pixi: records its arguments and its stdin, then does what ``body`` says.
    bin_dir.mkdir(parents=True, exist_ok=True)
    pixi = bin_dir / "pixi"
    pixi.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" > '{record / 'args'}'\n"
        f"cat > '{record / 'stdin'}'\n"
        f"{body}\n"
    )
    pixi.chmod(pixi.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _project(tmp_path: Path, installed: bool = True) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pixi.toml").write_text("[workspace]\n", encoding="utf-8")
    if installed:
        (root / ".pixi" / "envs" / "default").mkdir(parents=True)
    return root


def _hook(tmp_path: Path, **env: str) -> subprocess.CompletedProcess[str]:
    path = f"{tmp_path / 'bin'}{os.pathsep}/usr/bin{os.pathsep}/bin"
    return subprocess.run(
        ["bash", str(HOOKS / "coach.sh")],
        input=CALL,
        capture_output=True,
        text=True,
        env={"PATH": path, "HOME": str(tmp_path / "home"), **env},
        check=False,
    )


def test_hooks_json_runs_the_script_after_every_edit() -> None:
    config = json.loads((HOOKS / "hooks.json").read_text(encoding="utf-8"))
    (entry,) = config["hooks"]["PostToolUse"]
    assert {"Edit", "Write"} <= set(entry["matcher"].split("|"))
    (hook,) = entry["hooks"]
    assert hook["type"] == "command" and 0 < hook["timeout"] <= 120
    assert hook["command"] == 'bash "${CLAUDE_PLUGIN_ROOT}"/hooks/coach.sh'
    assert (HOOKS / "coach.sh").is_file()


def test_the_answer_comes_back_unchanged(tmp_path: Path) -> None:
    root, record = _project(tmp_path), tmp_path
    _stub(tmp_path / "bin", record, f"printf '%s\\n' '{ANSWER}'")
    done = _hook(tmp_path, CLAUDE_PROJECT_DIR=str(root))
    assert (done.returncode, done.stdout, done.stderr) == (0, ANSWER + "\n", "")
    # The project's environment as it is (never an install or a re-solve), the call on stdin.
    args = (record / "args").read_text().split()
    assert args == ["run", "--as-is", "python", "-m", "beethoven_qa", "coach", "--root", "."]
    assert (record / "stdin").read_text() == CALL


def test_nothing_to_say_is_said_as_nothing(tmp_path: Path) -> None:
    _stub(tmp_path / "bin", tmp_path, "exit 0")
    done = _hook(tmp_path, CLAUDE_PROJECT_DIR=str(_project(tmp_path)))
    assert (done.returncode, done.stdout) == (0, "")


@pytest.mark.parametrize("failure", ["echo 'unknown command coach' >&2; exit 2", "exit 1"])
def test_a_failure_is_silent_and_exits_0(tmp_path: Path, failure: str) -> None:
    # An older pinned copy has no coach command, and argparse exits 2: Claude Code would show an
    # exit 2's stderr to the session as if it were a finding.
    _stub(tmp_path / "bin", tmp_path, f"printf 'half'; {failure}")
    done = _hook(tmp_path, CLAUDE_PROJECT_DIR=str(_project(tmp_path)))
    assert (done.returncode, done.stdout, done.stderr) == (0, "", "")


def test_without_a_project_or_an_environment_pixi_is_never_called(tmp_path: Path) -> None:
    _stub(tmp_path / "bin", tmp_path, f"printf '%s\\n' '{ANSWER}'")
    plain = tmp_path / "plain"
    plain.mkdir()
    cases = [{}, {"CLAUDE_PROJECT_DIR": str(plain)}]
    cases.append({"CLAUDE_PROJECT_DIR": str(_project(tmp_path, installed=False))})
    for env in cases:
        done = _hook(tmp_path, **env)
        assert (done.returncode, done.stdout) == (0, ""), env
    assert not (tmp_path / "args").exists()


def test_pixi_is_found_where_its_installer_puts_it(tmp_path: Path) -> None:
    # Off the PATH (a cloud session's ~/.pixi/bin is not on it), the installer's place is used.
    _stub(tmp_path / "home" / ".pixi" / "bin", tmp_path, f"printf '%s\\n' '{ANSWER}'")
    done = _hook(tmp_path, CLAUDE_PROJECT_DIR=str(_project(tmp_path)))
    assert (done.returncode, done.stdout) == (0, ANSWER + "\n")
