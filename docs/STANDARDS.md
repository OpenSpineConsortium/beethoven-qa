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
| `duplication` | more than 3% of the new lines in a copied block | Sonar way: duplicated lines on new code at most 3%; a block is 10 lines and 100 tokens | a token-level detector here |
| `coverage` | less than 80% of the new lines that can run are run by the tests | Sonar way: coverage on new code at least 80% (line coverage) | coverage.py over the repository's own tests |
| `security` | a new weakness of medium or high severity found with medium or high confidence | Sonar way: no new vulnerability; each finding carries its CWE (the catalogue ISO/IEC 5055 builds on) | bandit |
| `supply-chain` | a workflow that breaks one of OpenSSF Scorecard's file checks, or gets a High or Medium finding from zizmor | Pinned-Dependencies (actions by full commit, no download piped into a shell, no download that the step runs, installs, unpacks or chmods without a sha256 check), Token-Permissions (top-level permissions grant no write), Dangerous-Workflow (no checkout of a pull request's code under `pull_request_target` or `workflow_run`, no outsider-controlled expression in a `run` script); zizmor's audits at its regular persona (its Low and Informational findings are recorded, never blocking) | a reader of `.github/workflows/*.yml` here, and zizmor |

Each module's docstring gives the exact definitions (what a line of code is, which methods LCOM4
joins, which GitHub contexts are untrusted) and every limit's source.

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
leaves no line out), zizmor `--no-ignores --no-config` (an inline `# zizmor: ignore[...]` is
reported like any other finding, and a `zizmor.yml` or `.github/zizmor.yml`, which could also
loosen an audit's policy, is never read). A `# noqa`, a `# nosec`, a `.bandit` file, a
`ruff.toml`, a `[tool.complexipy]` table, a `complexipy.toml` or a zizmor ignore changes nothing
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
scanning: on a private repository it needs GitHub Code Security, which is not bought.

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

## Not yet measured

- The groups in beethoven-agents and the research pipeline on GitHub's runners: both took 2.0.0
  and then 3.0.0 on 2026-10-07 through pull requests that merged, but their runs were not read
  here, nor their tests under coverage (agents' 277).
- The hook inside a live Claude Code session: the script and the command are tested with the
  hook's input shape and a stub pixi, and `claude plugin validate` passes, but no session has yet
  been seen reading an answer beside its edit.
- How long the coverage group takes on a large test suite, and whether the pre-push gate should
  leave it to CI there.
- The `scorecard (report)` job on a GitHub runner: its steps were run here under a stub curl that
  delivers the wrong bytes (the digest check refuses and removes the file) and Scorecard itself on
  a clone of the tree, never the whole job on a runner; nor whether its Vulnerabilities and Fuzzing
  checks, which ask OSV and OSS-Fuzz rather than GitHub, answer from a runner as they did here.
- The strict policy in action: that a pull request behind `main` is held, and merges once the
  branch carries `main`, has been read in GitHub's description of the flag, not yet seen on a
  pull request of this repository.
