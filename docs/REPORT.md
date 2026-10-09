# The evidence report

`python -m beethoven_qa report` (`pixi run --locked report`) makes the file a person hands to
anyone who doubts code written by an AI: one Markdown page, with a JSON twin for programs, that
says what was checked, by which tool at which version, how much of it was a proof and how much a
bounded search, and what makes those checks run before a push and before a merge. It is written
for a reader who will not take "the gate passed" on trust, and it says plainly what it does not
show.

Beethoven runs Claude Code and is not an Anthropic product; "Claude" and "Claude Code" are
Anthropic's marks, used here to say what it runs; "Open OnDemand" is the mark of its publisher.

## What it runs

The report runs every lane the gate runs, and nothing of its own: it reads the tasks `lint`,
`typecheck`, `test`, `boundary`, `symbolic` and `standards` from `pixi.toml`'s `[tasks]` table,
where the pre-push hook (`pixi run --locked gate`) and CI read theirs, and in a consumer that
carries the pinned copy (`vendor/beethoven-qa/` exists) the `vendor-check` task too, or
`python -m beethoven_qa vendor-check` when no task names it. A `pixi.toml` that lacks one of the
six is refused (exit 2), since the report would then stand for something other than the gate; so
is a task written as a table with its own `env` or `cwd`, since the lane would run under
conditions the report does not show (a table's `cmd`, a string or an argv list, is read).

Each command of a task is one process, started from the repository's root in the environment
the report was started in (under `pixi run --locked`, the environment the lock records); a task
written `a && b` gives two lanes, and the second runs even when the first failed, where pixi's
shell would stop, so a report shows every lane's result. The standards lane is given
`--sarif <out>/sarif` so its six logs are kept and counted, and `--base <ref>` when the command
was given one (CI gives it the event's base commit, as `standards.yml` does, so the section says
what the required check said of the same commit; on a push to `main` the lane's own default, the
merge base with `main`, would be the commit itself and nothing new). Everything a lane printed,
both streams as they came, is kept whole in `<out>/lanes/<task>-<n>.txt`; nothing is shown while
it runs but one line per lane with its exit code and wall time. A lane that runs past
`--timeout` (3600 s by default) is stopped and recorded as exit 124, its output ending with a line
that says so, so a hung lane still leaves a report that names it.

On this repository the report takes as long as the gate and the tests together: the symbolic
lane alone is about fourteen minutes on four cores, and the standards lane runs the tests a
second time under coverage.

## How to make it

```sh
pixi run --locked report                       # ./report/report.md, report.json, lanes/, sarif/
pixi run --locked python -m beethoven_qa report --root . --out /somewhere/else
```

`report/` is in `.gitignore`: the report is handed over, never committed, and an untracked
folder would make the pre-push hook refuse the next push. The folder must be empty or not there
yet: one holding an earlier report is refused, since its lane files and SARIF logs would be read
as this run's, and nothing of ours deletes, so move the old report away first. The CI job
`report (evidence)` (`.github/workflows/ci.yml`) makes the same report on GitHub's runner for
every push to `main` and every pull request, with `--base` set to the event's base commit, and
keeps the whole folder as the workflow artifact `evidence-report` for 30 days. It is not a
required check: the gate job already decides whether a change merges, and the report job's exit
code only repeats what its lanes said.

Exit 0 when every lane passed, 1 when one did not or was stopped at the timeout (the report is
written either way and names the lane), 2 when the command cannot run: no `pyproject.toml`, no
git, no `pixi.toml` or one without the lane tasks or with a lane task that sets `env` or `cwd`,
or an `--out` folder that is not empty. A refused run writes nothing.

## How to read it

`report.md` has nine sections, in this order:

1. **The repository.** `HEAD`, the branch, whether the tree was clean when the report began
   (read before the report wrote anything, so its own directory never counts), and the origin
   remote's address with anything before the host removed, so a token in a URL is never copied.
2. **The lanes.** One row per command: the task, the argv exactly as run, the exit code, the
   seconds, and the file holding its output. A failed lane is found here first.
3. **The environment.** First what `pixi.lock` was read as: how many of its records are the
   default environment's for this machine's platform (the lock lists every platform's records
   in one list, and another platform's version is not the one that ran here), or that it is
   missing or could not be read. Then, for each package that decides a verdict (python, ruff,
   mypy, pytest, typeguard, beartype, icontract, pydantic, crosshair-tool, z3-solver, complexipy,
   radon, bandit, coverage, lizard, jscpd, zizmor): the version the lock pins, and the first line
   the tool answers to `--version` where it has one. The two should agree; a tool that is "not
   in the lock" ran from somewhere the lock does not record.
4. **The gate.** The totals the symbolic lane printed: of all conditions, how many were
   *confirmed over all paths* (a proof), how many were *not confirmed* within the bound (a search
   that found no counterexample in its seconds; not a proof), and how many could be given no
   input that meets the precondition (a vacuous contract, or one stated over values CrossHair
   cannot build; either fails the gate). Then the same
   counts per target, and the functions set aside under `[tool.beethoven-qa.unproven]` with the
   reasons the gate printed: those are never targets, and their contracts are checked by the
   tests alone. A gate that printed no counts line (a release before 5.0.0) is said to have
   printed none.
5. **The code standards.** For each group whose SARIF log was written: the verdict the lane
   printed (`passed`, `FAILED`, `could not run`, or `not run`), the tools the log names with
   their versions, and the log's results counted by level (`error`, `warning`, `note`) and by
   `baselineState` against the base commit (`new`, `updated`, `unchanged`). Only a new or
   updated error blocks; `docs/STANDARDS.md` says how the state is decided.
6. **Enforcement.** `git config core.hooksPath` as set in this checkout; whether
   `.githooks/pre-push` exists, is executable, and what its `exec` line hands over to; the
   required status checks of `.github/rulesets/main.json`, its strict flag and who the rule
   lets past its checks (its bypass actors, each with its mode; on this repository organization
   admins, always); every workflow file and the names of its jobs. A reader can match the
   required checks against the jobs.
7. **Who decides each verdict.** One row per check. The lint, typecheck and test rows name the
   programs the lanes ran, read from their argv (a tool at its locked version, such as ruff or
   mypy; a program the lock does not pin as a tool this report knows is called a program), so a
   consumer whose task runs something else is not told a tool ran. The boundary and gate rows
   name home-made code that is itself contracted and under the gate (`beethoven_qa.boundary`,
   `.gate`, `.vendor`), with the gate section saying how far each contract was confirmed, since
   "proven" would claim what only a condition confirmed over all paths is. A standards group's
   row says who found its findings (the tools its log names: the tool itself, or beethoven-qa's
   own reader) and who judged them (home-made code: the limit and the matching against the base,
   in every group). The merge row names GitHub's rule as code, and the bypass it grants where it
   grants one, else no person. The last row is a person: every limit, and what a contract or a
   test means. The rows are built from what ran and from the logs, not from what the code
   believes ran.
8. **Where a language model is involved.** The plugin's workflows, skills and stream agent have
   a model write code and contracts; the coaching hook has a model read a verdict beside its
   edit; no verdict comes from a model.
9. **What this report does not show.** A "not confirmed" condition is a bounded search, not a
   proof. The standards block only code new or worse since the base commit. A green test lane
   says every test passed, but what each means is a person's judgement, as is what each contract
   promises. The pre-push hook runs every lane on the machine that pushes; GitHub's runner runs
   the jobs the Enforcement section lists, and a lane no job runs is checked by the hook alone.
   The report is as true as the machine that made it: its lanes ran in that machine's
   environment.

`report.json` carries the same facts for a program, field for field as the sections above
(`format` 1; `made`, `version`, `repository`, `tools`, `lock`, `lanes`, `gate`, `standards`,
`enforcement`, `decided`, `passed`, `language_model`, `not_shown`); the tuples of the code are
JSON arrays.

## What it proves, and what it does not

The report is evidence, not a proof of its own. What it adds to the gate's verdicts is that each
one is tied to a tool, a version, a command line and a file a reader can open, and that the
counts of the symbolic lane separate a proof from a bounded search. Its own reading of the gate's
and the standards' output is done by contracted functions under the gate (`beethoven_qa.report`:
`split_commands`, `gate_summary`, `group_verdicts`, `count_by`, `exit_code`,
`conda_name_version`, `pinned`, `hook_command`, `strip_credentials`, `table`, `deciders`,
`render`), and `tests/test_report.py` runs the command end to end on a throwaway repository with
stub lanes. Everything that starts a process, reads or writes a file or asks the clock is a
private function, called by no contracted one.

It does not make a lane mean more than it does. A green lint lane says ruff found nothing under
the repository's own settings; a green test lane says the tests passed, not that they are the
right tests; a green symbolic lane with most conditions "not confirmed" says a search found no
counterexample, which is weaker than a proof and is reported as such. It cannot say that the
machine that made it was honest: a report made on a machine whose environment differs from the
lock says so only where a tool's own `--version` disagrees with the lock's line, and a report
made by a person who edited its files afterwards says nothing. The CI artifact, made on a runner
from the lock alone, is the one to hand over when that matters.

## Measured

On 2026-10-08, in a cloud session with 4 cores, on this repository: the module gate over
`beethoven_qa.report` (12 targets, 41 conditions) took 4 min 27 s, every condition "Not
confirmed" at the full 20 s (each quantifies over unbounded strings or tuples), no counterexample
and no unmet precondition. The report's own tests take about 3 s; the end-to-end run on the
throwaway repository is 2.5 s of it, most of it the tools answering `--version`.

On 2026-10-09, the whole report on a fresh clone of 5.0.0's merge commit (8710049), as the CI job
makes it (`--base HEAD^1`), in a cloud session with 4 cores: every lane passed, in 22 min 11 s
(the symbolic lane 886 s, the standards lane 230 s, the tests 205 s).

## Not yet measured

- The report on a consumer's repository (beethoven-agents, beethoven-research): the
  `vendor-check` lane and a `pixi.toml` of their own have been run only against a throwaway
  repository's stub tasks.
- Why the CI job failed on 5.0.0's pull request (2026-10-09): on GitHub's runner it ran 29 min
  and a lane exited non-zero, while the same report made on a fresh clone of the merge commit in
  a cloud session passed every lane; the job's log and its artifact sit behind a download that
  session could not reach. The job now names each failed lane as an annotation of the check, with
  the lines of its output that say why and its last lines (the step `name the failed lanes` in
  `ci.yml`), so the next failure says itself to anyone who can read the check.
