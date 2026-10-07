"""What interface/ says this repository offers is what the code ships.

A consumer reads interface/INTERFACE.json and the two surface files beside it from its pinned copy
to know what it may import and run (the boundary command does, for rule 4). These tests hold them to
the code, so a change to the package that is not a change to the manifest, or the other way round,
fails here and not in a consumer's gate.
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import tomllib
from pathlib import Path
from typing import Any

from beethoven_qa import __version__, vendor
from beethoven_qa.__main__ import _parser
from beethoven_qa.boundary import MANIFEST, Export, Manifest, covered
from beethoven_qa.gate import GateConfig

REPO = Path(__file__).resolve().parent.parent
HELP = ("-h", "--help")


def _manifest() -> Manifest:
    return Manifest.model_validate_json((REPO / MANIFEST).read_text(encoding="utf-8"))


def _export(export_id: str) -> Export:
    return next(e for e in _manifest().exports if e.id == export_id)


def _json(rel: str) -> Any:
    return json.loads((REPO / rel).read_text(encoding="utf-8"))


def _options(parser: argparse.ArgumentParser) -> set[str]:
    return {o for a in parser._actions for o in a.option_strings if o not in HELP}


def test_the_manifest_names_what_is_here() -> None:
    manifest = _manifest()
    assert manifest.name == "beethoven-qa"
    assert manifest.imports == ()  # the bottom of the engine's order takes nothing of ours
    assert [e.id for e in manifest.exports] == ["python", "cli", "kit", "plugin", "docs"]
    for export in manifest.exports:
        for rel in export.paths:
            path = REPO / rel.rstrip("/")
            assert path.is_dir() if rel.endswith("/") else path.is_file(), rel


def test_every_published_module_and_name_is_importable() -> None:
    surface: dict[str, list[str]] = _json("interface/python.json")
    assert set(surface) == {"beethoven_qa", "beethoven_qa.contracts"}
    paths = _export("python").paths
    for module, names in surface.items():
        loaded = importlib.import_module(module)
        assert sorted(loaded.__all__) == sorted(names), module  # nothing more, nothing less
        assert all(hasattr(loaded, name) for name in names), module
        assert Path(str(loaded.__file__)).relative_to(REPO).as_posix() in paths, module


def test_the_commands_are_the_ones_main_accepts() -> None:
    cli = _json("interface/cli.json")
    parser = _parser()
    assert _options(parser) == set(cli["options"])
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    assert set(sub.choices) == set(cli["commands"])
    for name, command in sub.choices.items():
        declared = cli["commands"][name]
        assert _options(command) == set(declared["options"]), name
        arguments = {
            a.dest: list(a.choices or ()) for a in command._actions if not a.option_strings
        }
        assert arguments == declared["arguments"], name
        assert set(declared["exit"]) >= {"0", "2"}, name


def test_the_table_the_commands_read_is_the_gates() -> None:
    configuration = _json("interface/cli.json")["configuration"]
    assert configuration["table"] == "tool.beethoven-qa"
    assert set(configuration["keys"]) == set(GateConfig.model_fields)


def test_the_kit_export_is_the_kit() -> None:
    assert _export("kit").paths == vendor.KIT


def test_a_consumer_finds_each_export_in_its_copy() -> None:
    # The python, cli and plugin exports are read from the pinned copy (COPY_ROOTS), the kit from
    # kit/ inside it; the docs are text a person reads at the release, not something a copy needs.
    assert "interface" in vendor.COPY_ROOTS
    roots = tuple(f"{root}/" for root in vendor.COPY_ROOTS)
    for export_id in ("python", "cli", "plugin"):
        for rel in _export(export_id).paths:
            assert rel in vendor.COPY_ROOTS or covered(rel, roots), rel
    assert covered(MANIFEST, roots)


def test_one_version_everywhere() -> None:
    with (REPO / "pyproject.toml").open("rb") as fh:
        project = tomllib.load(fh)["project"]["version"]
    plugin = _json("plugin/.claude-plugin/plugin.json")["version"]
    assert project == __version__ == plugin
    assert re.fullmatch(r"[1-9][0-9]*\.0\.0", __version__)  # integer versions only
