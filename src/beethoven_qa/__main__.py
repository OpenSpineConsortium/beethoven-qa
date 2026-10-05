"""The command line: ``python -m beethoven_qa gate | standards | vendor | vendor-check``.

Wiring and I/O only, so the gate exempts this module from the contract rule (pyproject.toml); the
logic it calls lives in contracted, symbolically checked functions of gate.py, vendor.py and the
standards groups.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from beethoven_qa import __version__, gate, vendor
from beethoven_qa.standards import run as standards


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m beethoven_qa",
        description="beethoven-qa: the verification gate and the pinned copy of this package.",
    )
    parser.add_argument("--version", action="version", version=f"beethoven-qa {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    _gate_parser(
        sub.add_parser(
            "gate",
            help="every public function has contracts, and CrossHair + Z3 find no counterexample",
        )
    )
    _standards_parser(
        sub.add_parser(
            "standards",
            help="the code standards, each group on its own, on the code new since a base commit",
        )
    )
    _vendor_parser(
        sub.add_parser("vendor", help="write the pinned copy of this checkout into a repository")
    )
    _vendor_check_parser(
        sub.add_parser("vendor-check", help="the copy matches its ORIGIN and the kit is in place")
    )
    return parser


def _gate_parser(g: argparse.ArgumentParser) -> None:
    g.add_argument("--root", type=Path, default=Path("."), help="repository root (default: .)")
    g.add_argument(
        "--module",
        metavar="DOTTED",
        help="only this module of the package (for example beethoven_qa.vendor)",
    )
    g.add_argument(
        "--report-all",
        action="store_true",
        help="print every condition's verdict; a precondition no input can meet fails the gate",
    )


def _standards_parser(s: argparse.ArgumentParser) -> None:
    s.add_argument("group", choices=[*standards.GROUPS, "all"], help="one group, or all of them")
    s.add_argument("--root", type=Path, default=Path("."), help="repository root (default: .)")
    s.add_argument(
        "--base",
        default="auto",
        help="the commit the new code starts from: a ref, 'auto' (where HEAD left the remote's "
        "default branch; the default) or 'none' (the whole tree)",
    )
    s.add_argument("--sarif", type=Path, help="write each group's SARIF 2.1.0 log into DIR")


def _vendor_parser(v: argparse.ArgumentParser) -> None:
    v.add_argument("--into", type=Path, required=True, help="root of the consuming repository")
    v.add_argument("--kit", action="store_true", help="also put the repository kit files in place")


def _vendor_check_parser(c: argparse.ArgumentParser) -> None:
    c.add_argument("--root", type=Path, default=Path("."), help="repository root (default: .)")
    c.add_argument(
        "--source",
        type=Path,
        help=(
            "the beethoven-qa checkout this command runs from (its own environment): also rebuild "
            "the copy from the commit its ORIGIN names; refused from anywhere else"
        ),
    )


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    if args.command == "gate":
        try:
            config = gate.GateConfig.from_pyproject(args.root.resolve())
        except (OSError, ValueError) as exc:  # a missing file, bad TOML, or pydantic's refusal
            print(f"gate: the [tool.beethoven-qa] table cannot be used: {exc}")
            return 2
        if args.module is not None and not args.module.startswith(config.package):
            print(f"gate: {args.module} is not part of {config.package}")
            return 2
        return gate._run(args.root.resolve(), args.module, args.report_all)
    if args.command == "standards":
        names = list(standards.GROUPS) if args.group == "all" else [args.group]
        return standards._run(args.root, names, args.base, args.sarif)
    try:
        if args.command == "vendor":
            return vendor._sync(args.into, args.kit)
        return vendor._check(args.root, args.source)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        # git itself failed or is missing; a traceback is not an answer.
        print(f"{args.command}: git failed: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
