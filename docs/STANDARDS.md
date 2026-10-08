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
| `readability` | a function over a limit | cognitive complexity 15 (Campbell, SonarSource S3776); cyclomatic complexity 10 (McCabe, 1976); 4 parameters, `self` and `cls` not counted, and 25 lines of code in a body (Code Climate's defaults), which is 26 as lizard counts them (its NLOC takes the `def` line and each line of the signature with the body) | complexipy, ruff (C901, PLR0913) and lizard (NLOC) |
| `design` | a class over a limit | WMC 50, NOC 10, RFC 50 (chosen here); DIT 5 (the Drive file); CBO 14 (Sahraoui, Boukadoum and Lounis, 2000); LCOM4 1 (Hitz and Montazeri, 1995) | the Chidamber and Kemerer suite computed here from the syntax tree, McCabe weights from radon |
| `duplication` | more than 3% of the new lines in a copied block, or a comment holding `jscpd:ignore` | Sonar way: duplicated lines on new code at most 3%; a block is 10 lines (SonarSource's own) holding 40 of jscpd's tokens (derived below) | jscpd |
| `coverage` | less than 80% of the new lines that can run are run by the tests | Sonar way: coverage on new code at least 80% (line coverage) | coverage.py over the repository's own tests |
| `security` | a new weakness of medium or high severity found with medium or high confidence | Sonar way: no new vulnerability; each finding carries its CWE (the catalogue ISO/IEC 5055 builds on) | bandit |
| `supply-chain` | a workflow that breaks one of OpenSSF Scorecard's file checks, or gets a High or Medium finding from zizmor | Pinned-Dependencies (actions by full commit, no download piped into a shell, no download that the step runs, installs, unpacks or chmods without a sha256 check), Token-Permissions (top-level permissions grant no write), Dangerous-Workflow (no checkout of a pull request's code under `pull_request_target` or `workflow_run`, no outsider-controlled expression in a `run` script); zizmor's audits at its regular persona (its Low and Informational findings are recorded, never blocking) | a reader of `.github/workflows/*.yml` here, and zizmor |

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
(a string literal and a docstring among the places; a comment holding the marker is an error
finding of its own rule, `duplication/ignore-marker`, and a string holding it is not), zizmor
`--no-ignores --no-config` (an inline `# zizmor: ignore[...]` is reported like any other finding,
and a `zizmor.yml` or `.github/zizmor.yml`, which could also loosen an audit's policy, is never
read). A `# noqa`, a `# nosec`, a `# jscpd:ignore-start`, a `.bandit` file, a `ruff.toml`, a
`.jscpd.json`, a `[tool.complexipy]` table, a `complexipy.toml` or a zizmor ignore changes nothing
(`tests/test_standards_groups.py` pins each). There is no `[tool.beethoven-qa]` key for the
standards: a limit changes only in a
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
needs no change to the rule. The SARIF logs are kept as artifacts of the run for 30 days (the
supply-chain log holds two runs, the reader's and zizmor's). Nothing is uploaded to GitHub's code
scanning: on a private repository it needs GitHub Code Security, which is not bought. The evidence
report (`docs/REPORT.md`) runs the `standards` task with `--sarif` and counts each log's results
by level and by `baselineState`, beside the tools the log names.

The same file has one job more, `scorecard (report)`, which runs OpenSSF Scorecard 5.5.0 over the
checked-out tree (`--local`), prints the scores in its log and keeps the JSON as an artifact. It is
not among the jobs the required check waits for and the ruleset does not name it: it reports, and
blocks nothing. Why only a report: Scorecard scores whatever we do the two things this repository
accepts with open eyes, the `npm install -g` of the Claude Code CLI, which has no lockfile to pin
it (Pinned-Dependencies 8 of 10 on 2026-10-08, the one warning), and the pixi download-then-run,
which the reader now holds to a digest check where a digest exists (pixi publishes none, so the
digest is this repository's own measurement, written beside the download); and most of its other
file checks look for what this repository does not have by design (a licence file on a private
repository, a Dependabot configuration, a SAST service, fuzzing, a security policy, a publishing
workflow). The checks that ask GitHub's API (Branch-Protection, CI-Tests, CII-Best-Practices,
Code-Review, Contributors, Maintained, Signed-Releases) refuse a local tree and are not run. The
job installs no pixi environment: Binary-Artifacts counts the environment's binaries as artifacts
of the repository (measured 2026-10-08).

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
  `.github/rulesets/main.json`. Scorecard itself runs in CI as a report only ("Running it" above).
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
- On 2026-10-08, zizmor 1.30.1 and Scorecard 5.5.0 (the same session size). zizmor over this
  repository's four workflows takes 0.2 s a run, JSON or plain, and the whole supply-chain group
  with no base 0.6 s. Its JSON counts rows from 0 where its plain output counts lines from 1
  (`ci.yml:73:14` in plain was row 72, column 13); the Primary location is the one anchored to,
  the Hidden and Related ones are the step and the key around it. Left to itself it honoured an
  inline `# zizmor: ignore[unpinned-uses]` and the ignore rules of a `zizmor.yml` (a High and a Low
  disappeared); with `--no-ignores --no-config` both came back, the inline one marked `ignored:
  true` and reported all the same. With `--no-exit-codes` a non-zero exit means it did not run: a
  file it cannot read as a workflow (a YAML list, a syntax error) is passed over with a warning,
  and when every file given is such it exits 3 saying it collected no input, which the group reads
  as no finding. At the regular persona on this repository: one Low, `adhoc-packages` for the
  `npm install -g` of the Claude Code CLI; the auditor persona adds `undocumented-permissions`,
  `anonymous-definition` and `concurrency-limits`, style opinions, and `secrets-outside-env`,
  which would have `RULESETS_TOKEN` read through a GitHub Environment where `rulesets.yml` reads
  it as a plain Actions secret; the persona is not used. Scorecard `--local` on a clone of this
  tree runs
  Binary-Artifacts 10, Dangerous-Workflow 10, Token-Permissions 10, Vulnerabilities 10,
  Pinned-Dependencies 8 (the npm install, `0 out of 1 npmCommand dependencies pinned`; the pixi
  download it does not see), Dependency-Update-Tool 0, Fuzzing 0, License 0, SAST 0,
  Security-Policy 0 and Packaging without a score, aggregate 5.8, in 0.6 s; the other seven checks
  end with `Unsupported RequestType [0]`. A worktree's `.pixi` link it skipped; a real `.pixi/`
  holding one binary scored Binary-Artifacts 9. The tarball's sha256 equals the release's
  `scorecard_checksums.txt`; pixi 0.81.0's asset has no such file, and its digest was measured
  twice from two downloads at different times and matched the binary this session runs.

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

- On 2026-10-08, lizard 1.24.1 (pinned beside jscpd and zizmor) took over the lines of code per
  function from the count the group made itself out of the tokens. lizard's start line is the
  `def` line for a decorated function (the decorators above it not counted) and for a signature
  over several lines alike, the line the syntax tree gives, so each of its rows is matched by file
  and start line and anchored by the tree's qualified name (lizard's own name has no class and
  reads `outer.inner` for a nested function); the anchors did not change, and old findings still
  match. Its NLOC counts the `def` line, each further line of the signature and every line of a
  multi-line string or a bracketed expression, and leaves out the docstring, comments, blank lines
  and the body of a nested function, whose `def` line it counts in the function around it. Over
  this package's 256 functions at ddd8c19, NLOC was the body count plus one for 235 of them; the 21
  others differ by the signature's lines (`sig`, the `def` line included) and the nested bodies
  left out (`nest`), NLOC = body + sig − nest in every case:

  | function | body | NLOC | sig | nest |
  | - | -: | -: | -: | -: |
  | `boundary.compare_copy` | 6 | 9 | 3 | 0 |
  | `boundary.unpublished` | 11 | 14 | 3 | 0 |
  | `boundary.stale_exceptions` | 1 | 4 | 3 | 0 |
  | `boundary._off_surface` | 13 | 16 | 3 | 0 |
  | `gate.crosshair_command` | 10 | 13 | 3 | 0 |
  | `gate.set_aside` | 9 | 12 | 3 | 0 |
  | `gate._modules` | 43 | 34 | 1 | 10 |
  | `gate._run` | 102 | 101 | 1 | 2 |
  | `standards.coverage.new_line_coverage` | 1 | 4 | 3 | 0 |
  | `standards.coverage._tally` | 11 | 14 | 3 | 0 |
  | `standards.design._components` | 12 | 10 | 1 | 3 |
  | `standards.duplication.repeated` | 6 | 9 | 3 | 0 |
  | `standards.duplication._marked` | 7 | 10 | 3 | 0 |
  | `standards.findings.over_limit` | 14 | 17 | 3 | 0 |
  | `standards.group._definitions` | 15 | 9 | 1 | 7 |
  | `standards.group._compare` | 3 | 6 | 3 | 0 |
  | `standards.newcode._unquote` | 10 | 7 | 1 | 4 |
  | `standards.readability._defs` | 3 | 6 | 3 | 0 |
  | `standards.readability._check` | 23 | 20 | 1 | 4 |
  | `vendor._write_copy` | 17 | 23 | 6 | 0 |
  | `vendor._preflight` | 24 | 31 | 7 | 0 |

  The limit is therefore 26 in NLOC, the Drive file's 25 lines in the body and one for the `def`
  line, and a signature over several lines spends part of the 25: `vendor._preflight`, 24 lines of
  body under a 7-line signature, is the one old finding this added (15 where there were 14, all
  unchanged). The readability group took 2.0 s before and 2.2 to 2.4 s after; lizard runs twice,
  at the head and at the base, 0.3 s each with its interpreter's start. The oracle for a plain
  body of N statements, where the two counts must agree, is the old count kept in
  `tests/test_standards_groups.py` (`body_length` in the module is its definition).
- What lizard 1.24.1 does not do, and how the group answers (measured the same day, from its
  `PythonIndents.set_nesting` and `FileInfoBuilder.end_of_function`). A function written on its
  `def` line (`def f(x): return x`, or with the body on the line that closes the parameters) never
  gets the deeper-indented line lizard waits for and is not listed; it holds one line of code and
  the group leaves it. lizard's own `#lizard forgive` comment drops the function it closes (the
  one before it, not the one after), and a comment holding `GENERATED CODE` drops the rest of the
  file, which this module's own comments did when they named the two phrases and had to be
  reworded (a string literal is no comment to lizard); a function the syntax tree has, whose body
  begins on a line of its own and which lizard leaves out, is refused by name and the group does
  not run, since nothing in a repository's files may loosen a limit. Any line after the
  parameters' `)` at the `def`'s indentation or less whose first token is not a `)` ends the
  function to lizard: a return annotation over several lines (`-> dict[`, `str, int`, `]:`), which
  ruff format does write for a long one, left a function of 30 lines listed with 2, and a dedented
  continuation line does the same; such a function is refused by name and line rather than counted
  short. A multi-line string is one token and its lines at column 0 do no such thing. lizard's
  `whitelizard.txt` filters its warnings only, never its CSV, and is looked for in the working
  directory, which is empty; a file given by name is read with or without `.gitignore`
  (`--no-gitignore` is passed all the same); its exit code is 0 under `--csv` whatever its own
  thresholds see; a module with a syntax error is listed as one function to its end, and the tree
  has no function at that line, so nothing is recorded for it, as before.

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
- The `scorecard (report)` job on a GitHub runner: its steps were run here under a stub curl that
  delivers the wrong bytes (the digest check refuses and removes the file) and Scorecard itself on
  a clone of the tree, never the whole job on a runner; nor whether its Vulnerabilities and Fuzzing
  checks, which ask OSV and OSS-Fuzz rather than GitHub, answer from a runner as they did here.
- The strict policy in action: that a pull request behind `main` is held, and merges once the
  branch carries `main`, has been read in GitHub's description of the flag, not yet seen on a
  pull request of this repository.
