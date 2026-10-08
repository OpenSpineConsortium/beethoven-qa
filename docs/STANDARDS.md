# The code standards

`python -m beethoven_qa standards <group>|all` holds a repository's new code to the standards of
greg's Drive file "Auditable Code Quality Standards" (2026-10-04): readability, design, duplication,
test coverage, security weaknesses and the build pipeline itself. They sit beside the definition of
done (contracts, types and the CrossHair/Z3 proof, `CLAUDE.md`), which says whether code is
correct; these say whether a person can read it, change it and trust what built it.

Each group is its own module (`src/beethoven_qa/standards/<group>.py`), runs alone, passes or fails
alone, writes its own SARIF 2.1.0 log and is its own job in CI, so one can be red while the others
are green and each can be changed without touching the rest. They live in this repository, not in
six: every consumer already takes beethoven-qa as a pinned copy with one `vendor` command, a new
repository costs greg hand steps each time (`plan/REPO_SPLIT.md` section 5), and the copy, the
kit and the version move together. A group that ever needs a life of its own can leave by the same
road the split of 2026-10-04 took.

Beethoven runs Claude Code and is not an Anthropic product; "Claude" and "Claude Code" are
Anthropic's marks, used here to say what it runs.

## The six groups

| Group | What blocks a change | Limit and where it comes from | Tool (from the lock) |
| - | - | - | - |
| `readability` | a function over a limit | cognitive complexity 15 (Campbell, SonarSource S3776); cyclomatic complexity 10 (McCabe, 1976); 4 parameters, `self` and `cls` not counted, and 25 lines of code in a body (Code Climate's defaults) | complexipy, ruff (C901, PLR0913), and a count of lines of code here |
| `design` | a class over a limit | WMC 50, NOC 10, RFC 50 (chosen here); DIT 5 (the Drive file); CBO 14 (Sahraoui, Boukadoum and Lounis, 2000); LCOM4 1 (Hitz and Montazeri, 1995) | the Chidamber and Kemerer suite computed here from the syntax tree, McCabe weights from radon |
| `duplication` | more than 3% of the new lines in a copied block, or a comment holding `jscpd:ignore` | Sonar way: duplicated lines on new code at most 3%; a block is 10 lines (SonarSource's own) holding 40 of jscpd's tokens (derived below) | jscpd |
| `coverage` | less than 80% of the new lines that can run are run by the tests | Sonar way: coverage on new code at least 80% (line coverage) | coverage.py over the repository's own tests |
| `security` | a new weakness of medium or high severity found with medium or high confidence | Sonar way: no new vulnerability; each finding carries its CWE (the catalogue ISO/IEC 5055 builds on) | bandit |
| `supply-chain` | a workflow that breaks one of OpenSSF Scorecard's file checks | Pinned-Dependencies (actions by full commit, no download piped into a shell), Token-Permissions (top-level permissions grant no write), Dangerous-Workflow (no checkout of a pull request's code under `pull_request_target` or `workflow_run`, no outsider-controlled expression in a `run` script) | a reader of `.github/workflows/*.yml` here |

Each module's docstring gives the exact definitions (what a line of code is, which methods LCOM4
joins, which GitHub contexts are untrusted) and every limit's source.

The duplication group's token floor is this package's own derivation. SonarSource's block is 10
lines and 100 tokens, which assumes about ten tokens a line, and Python has fewer: measured on
2026-10-08 with jscpd 5.4.0 on this package, 7.4 of jscpd's tokens a line (40946 over 5569 lines,
blank and comment lines among them), so a 10-line copy of average code holds about 74, while the
13-line function that 4.0.0's token-window detector let through (its floor was 100 of its own
tokens) holds 57, 48 in its first ten lines. So jscpd runs with `--min-lines 10 --min-tokens 40`:
a 10-line copy of ordinary code is a clone (those ten lines are one; jscpd's own default of 50
would miss them), a column of assignments or literals is not (ten `name = value` lines hold 35),
and both must hold, so nine dense lines are not one either. Imports and docstrings are compared
with nothing and counted as nothing, as before (compared, this package's two pairs of identical
import blocks, 11 and 15 lines at 40 and 42 tokens, would be its only clones): jscpd reads a
staged copy of the package's files with those lines blank, in its weak mode (comments set aside),
and every module is compared with every other and with itself. A clone the base already held is
`unchanged` and blocks nothing; when the density is over the limit, the copies that hold new lines
block, and the other copy of a block newly copied is in the record as new, an error to no one.

## Only the new code blocks

Measured on 2026-10-04, this package's own command line and git code break the readability limits
(`gate._run`: cognitive complexity 67, cyclomatic 22, 102 lines; `vendor._check`: 36, 19, 82; 17
findings in all), and so would most code written before the standards existed. Gated over the
whole tree, every pull request in every repository would be red from its first day. So a group
blocks only what the change made, SonarSource's "clean as you code":

- **Findings of a function, a class or a line** (readability, design, security, supply chain) are
  computed at the head and at the base commit, and matched by rule, file and anchor (the function's
  or class's qualified name, or a line's own text), never by line number. Matched findings are
  paired worst with worst. A head finding with no match is `new`, one worse than its match is
  `updated`, one no worse is `unchanged`; `new` and `updated` errors block, `unchanged` ones are
  reported and pass. Moving a function changes nothing; making it worse, or writing a new one over
  a limit, blocks. A renamed file keeps its findings (`git diff -M`). Changing a function's name,
  or a workflow step's, makes its findings new: touched code is held to the limits.
- **Measures of the new lines** (duplication, coverage) take the lines `git diff` adds or changes
  between the base and the working tree, plus every line of a file git does not track yet.

These are the fields SARIF defines for the purpose (`baselineState`, matched through
`partialFingerprints`), so a reader of the logs sees the same verdict the command printed.

The base, from `--base`: a commit or ref as given; `auto` (the default, and the pre-push hook's):
where the checkout left the remote's default branch (`git merge-base HEAD origin/HEAD`, else
`origin/main` or `origin/master`); `none`: no base, every line and every finding new. CI gives a
pull request's `HEAD^1` (the base its merge commit was made on) and a push the commit it replaced.
The base's files are read from git's own objects into a temporary directory (`git ls-tree`,
`git cat-file`), never checked out over the working tree, and never through `git archive`, which
applies `export-ignore`: a file left out of the base would make every old finding in it look new.

## Nothing in the repository loosens a limit

The tools run as their own processes from an empty directory with every setting on their command
line, and their suppression comments are not honoured: ruff `--isolated --ignore-noqa`, bandit
`--ignore-nosec` with `--ini` naming an empty section, complexipy `--no-ignore`, coverage.py with a
configuration file of its own whose exclusion pattern matches nothing (so `# pragma: no cover`
leaves no line out), jscpd over a staged copy of the package's files in which its `jscpd:ignore`
marker is defused, since it has no flag to ignore the marker and reads it wherever the text stands
(a string literal and a docstring among the places); a comment holding the marker is an error
finding of its own rule, `duplication/ignore-marker`, and a string holding it is not. A `# noqa`,
a `# nosec`, a `# jscpd:ignore-start`, a `.bandit` file, a `ruff.toml`, a `.jscpd.json`, a
`[tool.complexipy]` table or a `complexipy.toml` changes nothing (`tests/test_standards_groups.py`
pins each). There is no `[tool.beethoven-qa]` key for the standards: a limit changes only in a
release of beethoven-qa, reviewed like any other change. The coverage group runs the repository's
tests as the repository runs them (`pytest` from its root, its own pytest settings); if they fail
under coverage, the group fails and says so.

## Running it

```sh
pixi run --locked standards                                   # every group, against `auto`
pixi run --locked python -m beethoven_qa standards readability --base origin/main
pixi run --locked python -m beethoven_qa standards all --base none --sarif out/   # the whole tree
```

Each group prints one line, `standards: <group>: passed|FAILED: <summary>`, and one line per finding
that blocks, with its file, line, rule, the measure against the limit and whether it is new or worse
than before. Exit 0 when every group asked for passed, 1 when one did not, 2 when one could not run
(a tool failed, git could not tell the base, the `[tool.beethoven-qa]` table is unusable).
`--sarif DIR` writes `DIR/<group>.sarif`; nothing is written into the checkout otherwise, so the
pre-push hook's clean-tree rule holds. The command reads the package `[tool.beethoven-qa]` names
(`src/<package>`) and `.github/workflows/`; tests, the pinned copy and anything else are not
measured.

The `gate` task runs `standards` after the other lanes, so the pre-push hook holds every push to
them. In CI the kit's `.github/workflows/standards.yml` runs one job per group (`standards /
<group>`) and one job, `standards (every group green)`, which is green only when every group's is:
that one is the required check in `.github/rulesets/main.json`, so a group added in a later release
needs no change to the rule. The SARIF logs are kept as artifacts of the run for 30 days. Nothing
is uploaded to GitHub's code scanning: on a private repository it needs GitHub Code Security, which
is not bought. The evidence report (`docs/REPORT.md`) runs the `standards` task with `--sarif`
and counts each log's results by level and by `baselineState`, beside the tools the log names.

## For a session writing code

The plugin's `code-standards` skill (`plugin/skills/code-standards/SKILL.md`) says how to write a
function that clears these limits the first time, and how to read and answer a failure: run the
one group that failed, read its blocking lines, change the code, never the limit.

From 4.0.0 the plugin also says it at once. Its `PostToolUse` hook (`plugin/hooks/hooks.json`)
runs `plugin/hooks/coach.sh` after every `Edit` or `Write`, which runs
`python -m beethoven_qa coach` in the project's own environment (`pixi run --as-is`, never an
install). The command reads Claude Code's call on stdin, runs the groups cheap enough for every
edit (readability, design, duplication and security for a module of the package, supply chain for
a workflow file; never coverage, which runs the tests) against the base CI uses (`auto`), and
answers with the findings that would block in CI **in the file just edited**, as the hook's JSON:
`decision: block` with a `reason` listing them (twelve at most, the rest counted). Claude Code puts
that beside the edit's result for the session to read; the edit has already happened and stays,
and nothing is stopped. A file nothing would block, a file outside the package and the workflows,
an older pinned copy without `coach`, no pixi, no installed environment, no base to tell (no
remote): each gets no answer at all and exit 0, so the hook never stands in a session's way. The
limits are the pinned copy's, the same CI applies; CI still decides.

Where the hook runs: wherever the plugin is enabled (a laptop's `/plugin install`, `claude
--plugin-dir plugin`). This repository's own `.claude/settings.json` names the same script for a
session working here, which does not load the plugin; a consumer whose sessions do not load the
plugin can name `vendor/beethoven-qa/plugin/hooks/coach.sh` the same way (`docs/CONSUMING.md`).
Under managed settings with `allowManagedHooksOnly` (the agents' image), Claude Code blocks plugin
hooks unless the plugin is force-enabled in the managed `enabledPlugins` (the hooks reference, read
2026-10-08); whether the image's registration does that is not yet measured.

## What is not here, and why

- **Readability models** (Buse and Weimer, 2010; Scalabrino et al., 2018), which the Drive file
  describes, are classifiers trained on people's ratings. No maintained open implementation of
  either was found to run (looked for 2026-10-04), and none is imitated: the readability group is
  the measurable limits above.
- **The full ISO/IEC 5055 measure** is computed by commercial tools against its own list of
  weaknesses, which is not reproduced here. The security group reports each finding's CWE and
  blocks on severity and confidence; whether a CWE is on the standard's list is not checked.
- **Scorecard's other checks** (branch protection, code review, signed releases, a security policy
  and others) read GitHub's settings, not files. This project requires no human review by design
  (greg, 2026-10-04: the checks alone decide); its branch rule is code in
  `.github/rulesets/main.json`.
- **SonarQube, Code Climate and GitHub Advanced Security** are services, paid or hosted, and need a
  token; this repository holds none. Their published conditions are applied here with open tools.
- **Nesting depth and boolean-expression limits** exist in ruff only as preview rules, which change
  without notice. Cognitive complexity already charges for both.

## Measured

On 2026-10-04 and 2026-10-05, in a cloud session with 4 cores; complexipy 8.0.1, radon 6.0.1,
bandit 1.9.4, coverage.py 7.16.2, ruff 0.16.10, CrossHair 0.0.111, git 2.43.

- bandit left to itself honours a `.bandit` file in the directory it reads (a `skips` line there
  hid a high-severity `shell=True` and an `eval`) and a `# nosec` comment; with `--ini` naming an
  empty `[bandit]` section and `--ignore-nosec`, both came back.
- complexipy reports no line numbers; a function is found by its qualified name in the module's
  syntax tree. A `[tool.complexipy]`, `complexipy.toml` or `.complexipy.toml` with `failed = true`,
  `exclude` and a high `max-complexity-allowed` beside the package changed nothing, since it runs
  from an empty directory.
- ruff's PLR0913 does not count `self`; a method of four more parameters is within the limit.
- git ends a `+++ b/<path>` header with a tab when the path holds a space, and quotes a path holding
  a tab (`"b/tab\there.py"`), under `core.quotePath=false` too; both are read correctly
  (`tests/test_standards.py` runs real git). A person's `diff.noPrefix` would remove the `b/`, so
  the prefixes are given on the command line.
- On beethoven-qa itself, against `main` before this change: every group passes on the new code,
  the coverage group with 1101 of 1142 new lines run by the tests; 17 readability findings in the old
  code are recorded as unchanged. `standards all` took about a minute, 45 s of it the coverage
  group running the test suite again under coverage.
- The symbolic gate grew from 10 targets to 32 and from about 2 to about 7 minutes on 4 cores; the
  conditions over unbounded tuples and strings run to their whole 20 s bound when no counterexample
  exists. CrossHair 0.0.111's symbolic frozenset refused `frozenset.intersection` called unbound
  (`design.lcom4`'s condition is written with `&` instead, same meaning).
- Two functions were broken on purpose before their contracts were trusted. `classify` marking an
  equal finding `updated`: CrossHair found the counterexample. `changed_lines` reading one line too
  many from each hunk: CrossHair found none within its bound (the input must be a well-formed diff),
  and the tests failed on the postcondition that ties every range to a hunk header in the text.
- On 2026-10-08 (same session size, the same tools; beethoven-qa 3.0.0's package): the readability
  group took 5 s, nine tenths of it parsing each module again for every finding in it
  (`dict.setdefault` builds its default before it looks: 518 parses where about 25 do); parsed once
  per module, 1.6 s. The security group's anchors did the same per bandit finding and now parse
  once per module too. `coach` on an edit of this package, the four cheap groups each in a thread
  of its own, took 2.8 s from the hook's `pixi run --as-is` to the answer (4.1 s one group after
  another); on beethoven-agents' package at its `main` (nine modules, 48 old readability
  findings), 2.5 s.
- The hook script was broken on purpose before its tests were trusted: without `|| exit 0` an
  older copy's argparse refusal (exit 2) came through, and with the last test inverted an empty
  answer printed an empty line; the tests failed on each (`tests/test_coach_hook.py`).

On 2026-10-08, with jscpd 5.4.0 (the same session size):

- 4.0.0's token-window detector (a block of 10 lines holding 100 of its own tokens, about 8.6 a
  line of code) let a 13-line verbatim copy of a plain function through; jscpd found it, as 57 of
  its tokens. On this package jscpd counts 7.4 tokens a line over all lines and 10.3 a line of code
  (40946 tokens, 5569 lines, 3978 of them code), about a fifth more than Python's `tokenize` gives
  for the same text (34366 names, operators, numbers and strings). Its weak mode leaves comments
  out (two comments: two tokens fewer); its strict mode counts the whitespace too (79 against 45
  for an 11-line function).
- At the floor (10 lines, 40 tokens) the package holds no clone once imports and docstrings are
  set aside; with them compared, two pairs of identical import blocks and nothing else
  (`design`/`readability`, 11 lines, 40 tokens; `readability`/`security`, 15 lines, 42): 0.46% of
  its lines. A run at a floor of one token lists clones differently (one of the two pairs, at 31
  tokens), so the counts above come from runs at the floor.
- jscpd reads a `.jscpd.json` from the directory it is run in and from nowhere else: one planted
  at the repository root, beside the package and in the scanned directory changed nothing, and one
  in the working directory set the floors out of reach (`--debug` showed it merged). It reads
  `jscpd:ignore-start` wherever the text stands: a comment with or without a space after the `#`,
  a trailing comment, a string literal and a docstring each hid the rest of the file;
  `JSCPD:IGNORE-START`, `jscpd:ignore` alone, `jscpd:ignore-end` alone and `jscpd-ignore-start`
  hid nothing, which is why the staged copy holds the last. A symlinked `.py` file is not
  followed; a file that does not parse stops nothing; a directory with no file leaves a report
  with no source, a missing directory no report and exit 1.
- The report's `fragment` holds the first side's whole lines from the clone's first to its last,
  the text before the copy on its first line among them (`def first(items):` for a clone that
  starts at `(items):`), so it is not what the two sides share; a side is anchored by its own
  text cut at the report's start and end columns instead, and swapping two copied functions' names
  left their clone matched (`updated`, 2 new lines in it). The end column can lie one token past
  the copied text: a block whose copies part at `return` and `raise` was reported one line longer
  than the copy, and that line counts as duplicated, as jscpd counts it. A clone class of three
  copies is reported as two pairs, each later copy with the first.
- The group took 274 ms in-process on this package before (one tokenize and one parse of every
  module, no process) and about 300 ms after (jscpd once per tree, 40 ms a run, and a module
  tokenized only when the diff names it or it holds the marker; with no base, every module); from
  the command line 1.1 s against a base and 0.8 s with none, before and after alike, Python's
  start-up being most of it.

## Not yet measured

- The groups in beethoven-agents and the research pipeline on GitHub's runners: both took 2.0.0
  and then 3.0.0 on 2026-10-07 through pull requests that merged, but their runs were not read
  here, nor their tests under coverage (agents' 277).
- The hook inside a live Claude Code session: the script and the command are tested with the
  hook's input shape and a stub pixi, and `claude plugin validate` passes, but no session has yet
  been seen reading an answer beside its edit.
- How long the coverage group takes on a large test suite, and whether the pre-push gate should
  leave it to CI there.
- Whether jscpd's columns count characters or bytes on a line that holds a non-ASCII character
  before a copy starts; a side's anchor is cut by them, and is stable either way.
