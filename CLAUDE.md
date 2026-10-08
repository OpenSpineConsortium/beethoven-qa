# beethoven-qa — working context

This repository is **how code for the Beethoven engine is written and proven before it may run**:
greg's "code writing software framework, pixi concolic etc", the software quality assurance
repository of the three-way split of `OpenSpineConsortium/beethoven-research` (greg, 2026-10-04
04:52Z; `/mnt/project-files/plan/REPO_SPLIT.md`). It holds the definition of done, the contracts
and the layering rule, the CrossHair/Z3 gate as a command any repository runs on its own package,
the pinned copy that consuming repositories carry, the code standards that block a change whose
new code breaks them, the repository kit every repository shares, and the Claude Code plugin
through which a session writes new code to that bar and brings existing code up to it. Its
consumers are `beethoven-agents` (the nested agentic framework) and the research pipeline (REFORMS,
CLAIM, the studies); it depends on nothing of ours, and nothing here may import either of them.

Vendor: Gregory Schwing, trading as OpenSpineConsortium. Beethoven runs Claude Code and is not an
Anthropic product; "Claude" and "Claude Code" are Anthropic's marks, used here to say what it runs;
"Open OnDemand" is the mark of its publisher.

## Read first

- `/mnt/project-files/plan/REPO_SPLIT.md`: the boundary between the three repositories, the
  direction of dependence and how this one is consumed. `/mnt/project-files/plan/BUILD_PLAN.md`
  section 3 is the definition of done this repository implements; the plan governs.
- `docs/CONSUMING.md` (adopting beethoven-qa in a repository), `docs/STANDARDS.md` (the code
  standards: the six groups, their limits and sources, the new-code rule, what is not built),
  `docs/PARALLEL_BUILD.md` (worktrees, the stream agent and workflow scripts for building in
  parallel).
- `plugin/skills/`: `definition-of-done`, `write-verified-module`, `improve-existing-module`,
  `review-contracts`, `code-standards`. They are the working instructions for writing code here and
  in every consumer.
- `hpcworker`'s `CLAUDE.md` for the grid facts and the launcher's non-negotiables, which hold here.

## Decisions that are settled (do not reopen)

- **D1 — own subscriptions, no keys.** Everything that calls Claude runs on a flat-rate Claude plan
  held in the person's own name. No pooled or commercial API keys, no gateway, no Bedrock or Vertex,
  no key rotation. The plugin's workflows run in the session that starts them, on that session's own
  sign-in; nothing here starts Claude Code any other way.
- **D2 as refined by the split (greg, 2026-10-04 04:52Z).** The engine is three repositories:
  this one (Goal 1 and the repository rules), `beethoven-agents` (Goals 3, 4 and 5, accounts and
  the super-master, decisions D3 and D4) and the research pipeline (Goals 2, 6 and 7 and the
  studies), with qa <- agents <- research the only direction of dependence. A change here that
  needs anything of theirs is in the wrong repository.

## Non-negotiables (carried from the launcher and the engine; they bind here too)

- **Never touch a Claude credential.** No `setup-token`, no `CLAUDE_CODE_OAUTH_TOKEN`, no
  `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN`, no reading `.credentials.json` under any
  configuration directory, never `claude auth logout`. Nothing in this repository reads, stores,
  copies, prints or relays one; the plugin never asks for one and no skill suggests one.
- **Usage credits are never used**, by anyone, for anything (greg, 2026-10-04; BUILD_PLAN section 2
  C7). Nothing here treats them as an option or a default; a spent window is waited out.
- **No code on claude.ai and no interception of it.** No proxy, no parser of its traffic, nothing.
- **Secrets never appear on a command line, in argv, in logs, in job files or in `sbatch --export`.**
  Never echo one. The only secret any workflow here names is the maintainer's `RULESETS_TOKEN`,
  read from the Actions secret into the environment of the one step that needs it.
- **No remote code.** No `eval`, no `new Function`, no fetched-and-run scripts.
  `tools/check_workflows.mjs` checks the workflow scripts with `node --check` on two copies of
  the wrapped text (the script grammar and the module grammar, the two the runtime uses), never
  by running them;
  `tools/check_workflows.test.mjs` runs them only under stand-in hooks in a child process node
  starts from a file, with no agent, no `eval` and no `vm`.
- **Nothing is deleted outside a run's own directories and node-local temp.** Never the Claude Code
  login directory, the workspace, another run or anything else under `$HOME`. The pinned-copy
  command honours it in its own way: it writes only under `vendor/beethoven-qa/` and the five kit
  paths of the repository it is pointed at, refuses to overwrite any file there whose bytes are not
  HEAD's (compared with the commit's own blob, so an ignore rule or an index flag such as
  `--assume-unchanged` cannot hide an edit), and never deletes; a file a new release dropped is
  listed for the person to remove in a reviewed commit.
- **Grid etiquette**: anything that runs a container, a scheduler call or Claude Code on the grid
  runs inside a Slurm job on a compute node; nothing container-related on the login node; temp on
  the node's local disk (`$TMPDIR` on Wayne State is CephFS, never trusted). The gate runs CrossHair
  as many processes as the allocation has cores (`os.sched_getaffinity`), never more.
- **Names and words.** The Beethoven name and the trademark sentence above stand wherever the
  product is described. Never use the word that starts "prov" and ends "enance" for the history of
  a file or data; say origin, source, lineage or record (the pinned copy's record is `ORIGIN`).
  Versions are integers (1.0.0); `pyproject.toml`, `beethoven_qa.__version__`, the plugin manifest
  and the release tag carry the same one.

## The definition of done (Goal 1) — what this repository exists to enforce

No module is done, and the gate will not let it enter a run or reach `main`, until it clears:

1. **Deterministic environment.** It resolves inside `pixi.lock` (exact versions, build strings,
   hashes, `linux-64`) and runs inside the hashed Apptainer image (built in `beethoven-agents`,
   `--fakeroot`, run `--containall --cleanenv --no-home`).
2. **Design by Contract.** `icontract` `@require`/`@ensure`/invariants on every public function,
   imported from `beethoven_qa.contracts`; subclasses only weaken preconditions and strengthen
   postconditions. `@beartype` outermost, the contracts beneath it (measured, see `contracts.py`).
3. **Layered runtime types.** `pydantic` at external boundaries, `beartype` on internal hot paths,
   `typeguard` only in the test enclave (`tests/conftest.py`'s import hook).
4. **Symbolic proof.** CrossHair + Z3 find no input that satisfies the preconditions and violates a
   postcondition within the declared per-condition bound. Contracted functions are pure; reading,
   importing, writing and starting processes stay in private, uncontracted functions or in the
   exempt command-line module, because CrossHair executes what it checks. A public function that
   must do I/O keeps its contracts (the tests check them at run time) and is named, with its
   reason, under `[tool.beethoven-qa.unproven]` (a function, never a class or a method; its
   contracts are assumed by every proven caller, so the tests must exercise them); so is logic
   CrossHair measurably cannot run (the reason names the fault). The gate prints every entry on
   every run and refuses a stale one.
5. **Schema-aligned parsing** (BAML/SAP) for any LLM-structured output: coercive repair with
   validation feedback, never a hard crash. Not built yet (Linear G1-4); it lands here.
6. **The SpecPylot loop.** Code and contracts, the gate, the counterexample fed back, again until
   proven; humans review the contracts, not the lines (`/beethoven-qa:verify-module`,
   `/beethoven-qa:improve-module`, the `review-contracts` skill). A green gate over vacuous
   contracts proves nothing, which is why `improve-module` ends with two skeptics looking for them.
7. **The gate before every push, and no human gate on a merge** (greg, 2026-10-04 02:11Z).
   `.githooks/pre-push` runs `pixi run --locked gate` (without `--locked`, pixi re-solves a
   manifest that drifted from its lock and gates an environment the commit does not record). The
   hook gates the commit that is pushed, not whatever is checked out: every ref git hands it must
   be the checkout's HEAD and `git status --porcelain` must print nothing (untracked files count),
   else it refuses and says so in plain words; and it unsets the variables git exports to a hook
   (`git rev-parse --local-env-vars`, `GIT_DIR` among them) before the gate runs, so a push from a
   linked worktree never aims the tests' throwaway repositories at this one (`tests/test_hooks.py`
   pins both with the real hook, a stub pixi and a bare remote). `SKIP_GATE=1` skips all of it
   only knowingly, and the commit says why. The same gate is the required
   check on `main` (`.github/rulesets/main.json`, applied by `rulesets.yml` with
   `RULESETS_TOKEN`); pull requests open ready for review, never as drafts; `automerge.yml` arms
   auto-merge and the required checks alone decide.

## The code standards (greg, 2026-10-04 23:26Z; `docs/STANDARDS.md`)

Beside the definition of done, which says whether code is correct, six groups say whether a person
can read it, change it and trust what built it: **readability** (cognitive complexity 15,
cyclomatic 10, 4 parameters, 25 lines of code), **design** (the Chidamber and Kemerer suite with
LCOM4), **duplication** and **coverage** (SonarSource's Sonar way conditions on new code: at most 3%
duplicated, at least 80% covered), **security** (bandit, each finding with its CWE) and **supply
chain** (OpenSSF Scorecard's checks of the workflow files). Each is a module of
`beethoven_qa.standards`, a group of `python -m beethoven_qa standards <group>|all`, a SARIF 2.1.0
log and a job of the kit's `standards.yml`; each passes or fails alone, and the one required check
is `standards (every group green)`. They are in this repository, not in six, because the pinned
copy already carries them to every consumer with one command and a new repository costs greg hand
steps (greg asked for whatever is most logical; the coordinator's default was one package and one
check per group here).

A group blocks only what the change made: findings are matched with the base commit's by rule,
file and anchor (a qualified name or a line's text, never a line number) and only `new` or
`updated` errors block; duplication and coverage read the new lines git reports. No setting,
configuration file or suppression comment of a repository loosens a limit; a limit changes only in
a release. The coaching for a session is the `code-standards` skill and, from 4.0.0, the plugin's
`PostToolUse` hook: after every `Edit` or `Write` it runs `python -m beethoven_qa coach`, which says
beside the edit what CI would block in that file (the cheap groups, never coverage), or nothing;
it never stops an edit and never fails a session (`docs/STANDARDS.md`, "For a session writing
code"). This repository's `.claude/settings.json` names the same script for sessions working here.

Never skip, disable or quarantine a test, loosen a symbolic bound, remove a contract, narrow a
precondition to exclude an input the callers really pass, add a `# crosshair:` directive, exempt
a module that holds logic, or set aside under `unproven` a function CrossHair can run, to get
green; nor raise a standards limit, add a suppression comment the standards ignore anyway, rename a
function to dodge its findings, or weaken a test so coverage counts fewer lines. A precondition
that states the function's real domain, which no caller ever leaves, is not dodging (see
`vendor.stale_files`).

## What this repository provides

- `beethoven_qa.contracts`: the one import point for the contract decorators.
- `beethoven_qa.gate` and `python -m beethoven_qa gate [--module <pkg.mod>] [--report-all]`: both
  checks always run. Check 1: every public function of the package named in `[tool.beethoven-qa]`
  carries contracts, and nothing hides one from CrossHair (a `.py` file that is not one of the
  package's modules, a module that fails or exits on import, a `# crosshair:` directive, a public
  coroutine, a contracted pydantic model, a package with nothing to prove: each fails the gate).
  Check 2: CrossHair + Z3 over every contracted function and every class that carries contracts
  (but the functions set aside, with their reasons, under `[tool.beethoven-qa.unproven]`: an entry
  names a function, never a class or a method, since a class is proven whole as one target, and
  the gate refuses an entry that names one or a member of one; listing keeps a function from being
  a target and nothing more, because CrossHair runs its body when it proves a contracted caller
  and assumes its contracts there, so such a caller does the I/O too and is itself private, exempt
  or listed, and a listed function's contracts are checked by the tests alone),
  one process per target, as many at a time as the allocation has cores (`BEETHOVEN_QA_JOBS` can
  lower it, never raise it), each condition bounded by `per_condition_timeout`. `--module` limits
  both checks to one module (an entry of a deeper module stays that module's, imported or not);
  `--report-all` prints every condition's verdict and fails on a precondition no input can meet.
- `beethoven_qa.vendor`, `python -m beethoven_qa vendor --into <repo> --ref <tag> [--kit]` and
  `python -m beethoven_qa vendor-check [--source <beethoven-qa checkout>]`: the pinned copy at
  `vendor/beethoven-qa/`, with its `ORIGIN` record, and the check that the copy and the kit files
  (bytes and executable bit) are untouched. From 3.0.0 (greg's card of 2026-10-07 18:11Z) `vendor`
  reads the release from GitHub through the person's own `gh` sign-in, like every other copy's
  `pull.sh`: only a commit `main` contains, each file's bytes checked against the blob id the
  commit's tree gives it, no checkout of beethoven-qa read; it runs from the consumer's own copy
  (from a clone of the release the first time, when the copy is older than 3.0.0), and the `cli`
  export is at version 2 for the new required `--ref`. The plain
  check catches an edit that leaves ORIGIN alone; an edit that also rewrites ORIGIN is caught only
  with `--source`, which rebuilds the tree from the commit ORIGIN names (docs/CONSUMING.md). So is
  an edit to the copy's own `vendor.py`, since the consumer's environment runs the copy's code:
  `--source` therefore runs only from beethoven-qa's own environment (`pixi run --locked
  --manifest-path <qa>/pixi.toml python -m beethoven_qa vendor-check --root <consumer> --source
  <qa>`) and is refused anywhere else, because an edited copy would not refuse to vouch for itself
  where an unedited one does. The plain check also fails a byte-code cache git tracks under the
  copy or a hash-based `.pyc` there even untracked (Python loads either in place of the source),
  and `.githooks/pre-push` or `.claude/hooks/session-start.sh` recorded by git as other than
  `100755`, live or under `kit/` (a `core.fileMode=false` checkout does that). A package named
  `beethoven_qa` in a consumer's own `src/` shadows the copy for every check and the gate alike:
  an unedited one fails the plain check, an edited one is seen by neither form, and a reviewer who
  sees `src/beethoven_qa/` in a consumer asks why. Every git call of `vendor.py` and of its tests
  drops the variables `git rev-parse --local-env-vars` lists (`GIT_DIR` among them, which a hook
  inherits) and keeps the two configuration carriers, so `safe.directory` given through the
  environment still reaches git.
- `beethoven_qa.standards` and `python -m beethoven_qa standards <group>|all [--base <ref>|auto|none]
  [--sarif DIR]`: the code standards (`docs/STANDARDS.md`). Exit 0 when every group asked for
  passed, 1 when one did not, 2 when one could not run.
- `beethoven_qa.standards.coach` and `python -m beethoven_qa coach [--root DIR]` (from 4.0.0): the
  command behind the plugin's `PostToolUse` hook (`plugin/hooks/hooks.json`, `coach.sh`). It reads
  Claude Code's call on stdin and prints the hook's JSON (`decision: block` and the findings that
  would block in CI in the edited file, against the `auto` base) or nothing; exit 0 always. The
  script runs it with `pixi run --as-is` in the project's own environment and ends quietly on
  anything missing (no pixi, no environment, an older copy). Measured 2026-10-08: 2.8 s per edit
  on this package, 2.5 s on beethoven-agents'.
- `beethoven_qa.boundary` and `python -m beethoven_qa boundary [--root DIR]` (from 2.0.0): the five
  rules of the Beethoven interface standard
  ([INTERFACES.md](https://github.com/OpenSpineConsortium/beethoven/blob/main/INTERFACES.md)) over
  what git tracks, a lane of this repository's gate and of every consumer's: no file but Markdown
  reaches another checkout (rule 1); `interface/INTERFACE.json` is in the standard's shape and
  names every file it exports and every copy carried (2); each copy holds exactly what its ORIGIN
  lists, and beethoven-qa's own copy is the vendor command's, whose manifest offers what is
  imported (3); imports of the engine's packages name only the provider's `python` export, read
  from the manifest in the pinned copy (4); qa <- agents <- research and never back (5). A finding
  of rule 1 or 4 that must stand is an entry of `[tool.beethoven-qa.boundary]` (file, what
  matched, why), printed on every run and refused when stale; this repository needs none. Exit 0
  when the rules hold, 1 on a finding (`file:line: rule N:` and a sentence), 2 when it cannot run.
- `interface/` (from 2.0.0): this repository's manifest under that standard, `INTERFACE.json`, with
  `python.json` (the modules and names a consumer may import: `beethoven_qa.contracts` and
  `__version__`, nothing else) and `cli.json` (the five commands, their options and exit codes,
  the `[tool.beethoven-qa]` table); exports `python`, `cli`, `kit`, `plugin` (files: Claude Code
  reads it, nobody runs it) and `docs` (text), each at version 1 (`interface/README.md`). The copy
  carries it, so a consumer finds it at `vendor/beethoven-qa/interface/`;
  `tests/test_interface.py` holds it to the code.
- The repository kit, carried byte for byte by every consumer: `.githooks/pre-push`,
  `.github/workflows/automerge.yml`, `.github/workflows/rulesets.yml`,
  `.claude/hooks/session-start.sh`, `.github/workflows/standards.yml`. Change one here and every
  consumer takes it with its next copy (`vendor --kit`); a consumer that re-pins to a release whose
  kit changed without `--kit` fails `vendor-check` on the file that differs until it does, by
  design. Release 2.0.0 adds `standards.yml` and changes `automerge.yml` and `rulesets.yml` (actions
  pinned to a commit, writes granted on the job only).
- `plugin/` and `.claude-plugin/marketplace.json`: the Claude Code plugin `beethoven-qa` (skills,
  the `stream` agent, the workflows `gate-until-green`, `verify-module`, `improve-module`, and from
  4.0.0 the code standards' `PostToolUse` hook under `plugin/hooks/`). On a
  laptop: `/plugin marketplace add OpenSpineConsortium/beethoven-qa`, then
  `/plugin install beethoven-qa@beethoven-qa`; for work inside this checkout,
  `claude --plugin-dir plugin`. On the grid the agents' image registers the copy as a `directory`
  marketplace in its managed settings. A cloud project thread does not add a repository's
  marketplaces (no trust dialog there), so it reads this checkout and runs a workflow with the
  Workflow tool's `scriptPath` (for example `plugin/workflows/verify-module.js`).
- `src/beethoven_qa/example.py`: the worked example that clears steps 1 to 4, so the gate is
  demonstrably real.

## Measured (2026-10-04, this cloud session, 4 cores; CrossHair 0.0.111)

- The gate found real gaps in its own drafts twice: `uncontracted`'s postconditions are false when
  two entries share a qualified name, which a module's namespace never allows (the precondition now
  says so); and `crosshair_command`'s first `--report-all` postcondition held for a target named
  `--report_all` (the postcondition now looks at the flag's position, and a target is never an
  option).
- One CrossHair process over the package took 5 min 43 s; one process per target, four at a time,
  took 2 min 8 s for the same checks. A condition over unbounded inputs (strings, tuples) runs to
  its whole bound when no counterexample exists, so the gate's time grows with the number of such
  postconditions, divided by the cores. The whole `pixi run --locked gate` here (45 tests, 10
  targets) took 2 min 35 s; beethoven-agents' (277 tests, 34 targets: the guard and the account
  switch) 5 min 3 s. Neither is yet measured on a GitHub runner or a grid node.
- The account switch, built in beethoven-research with 53 `# crosshair:` comments (26 switching
  CrossHair off, 27 lowering the bound to 2 to 6 s), clears this gate without them: its 25
  functions that do I/O or that CrossHair cannot run are listed under `unproven` with reasons, one
  it had switched off (`utc_text`) proves, and every other condition holds at the full 20 s.
- CrossHair 0.0.111 raises an internal `assert False` in its string comparison when it sorts
  symbolic strings holding NUL characters; it is reported as an exception in the function under
  check. Stating the real domain as a precondition kept it away (`vendor.stale_files`). A report
  that points inside CrossHair's own `libimpl` is a tool fault to work around and record, never a
  reason to loosen a contract.
- CrossHair 0.0.111 does not construct a pydantic model: a contracted method on one was reported
  "Not confirmed", and a broken one that returned more than the window held passed. The gate now
  refuses contracts on a pydantic model; models stay boundaries and the logic lives in contracted
  functions (`example.Window`).
- An adversarial review of this repository before its first push (four lenses, each finding
  reproduced by a second agent) confirmed 46 findings: among them two ways the copy command could
  write outside its directory through a committed link, files `git status` hides being overwritten,
  and eight ways a function could escape the gate (a directory without `__init__.py`, an import
  that exits 0, a `# crosshair: off` comment, a cached or re-exported function, a coroutine, an
  empty package, a package imported from another checkout). Each has a test that fails on the code
  before the fix (`tests/test_gate.py`, `tests/test_vendor.py`).
- A second adversarial review, after PR #1 merged and before v1.0.0 (four lenses again, each
  finding reproduced by a second agent), confirmed 21 more, each with a test that fails on the
  code before the fix:
  - CrossHair runs the body of a function listed under `unproven` whenever it proves a contracted
    caller, and takes the callee's contracts as given: a listed callee with a false postcondition
    left its caller `Confirmed over all paths` and the gate green, and a listed reader of a file
    read it during the gate (`tests/test_gate.py` pins both). Listing keeps a function from being
    a target, nothing more. Two more ways a function escaped the gate: a public
    `functools.partial` over package code (its `__module__` is `functools` and it has no
    `__name__`, so `_home` counted it as someone else's), and an `unproven` entry naming a class,
    which set the whole class aside, while a method entry matched nothing and under `--module` was
    dropped without a word. Entries now match contracted functions only, `set_aside` refuses a
    class or a member of one with its own message, and under `--module <package>` an entry of a
    submodule that failed to import stays that submodule's instead of being called stale.
  - A pre-push hook run from a linked worktree inherits `GIT_DIR` (that worktree's own git
    directory) and `GIT_PREFIX`; pixi 0.81.0 passes them on to the gate, and a git that inherits
    `GIT_DIR` acts on this repository whatever `-C` names: the vendor tests' `git -C <tmp>
    init/commit/tag` set `core.bare=true` in the developer's checkout and put a `release` commit
    and a `v1.0.0` tag on their branch (git 2.43). Every git call of `vendor.py` and of the tests
    now drops what `git rev-parse --local-env-vars` lists, minus `GIT_CONFIG_PARAMETERS` and
    `GIT_CONFIG_COUNT`; the tests also set `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1`
    and drop the configuration carriers for their own processes (`tag.gpgSign=true` given either
    way had turned the vendor tests red at `git tag`); and the hook unsets the same variables
    before the gate, as the belt (a push from a worktree whose hook ran only the vendor tests,
    unsetting nothing, was accepted and left the main checkout untouched).
  - Python 3.11 loads a hash-based `.pyc` whose header carries the genuine source's hash whatever
    code it holds, checked or unchecked (only the source's hash is compared, never the code), and
    such a file survives a fresh clone where a timestamp `.pyc` is recompiled; the walk behind
    `vendor-check` therefore counts every hash-based `.pyc` under `__pycache__` and leaves only
    timestamp ones out. GitHub's runners carry git 2.55, which refuses `git add -u -- <folder git
    does not track yet>` (exit 128) where 2.43 accepts it; the printed cleanup line no longer
    depends on that. `bash -n a b` parses `a` only and makes `b` a positional parameter, so one
    call over both kit files checked the hook alone; CI now calls it once per file.
  - Read from Claude Code 2.1.289's own code on 2026-10-04: the runtime reads `meta` with acorn as
    a module (one `const` declaration whose value is the object literal; `__proto__`,
    `constructor` and `prototype` refused) and then compiles the body as a strict-mode script, so
    a workflow script must parse under both grammars; `new.target` inside a function passes
    `node --check` on a `.cjs` copy, which is why the checker refuses it by text. The checker's
    own tests, and the three workflow scripts run under stand-in hooks, are
    `tools/check_workflows.test.mjs`.
  - The module gate on `beethoven_qa.gate` (5 targets) took 1 min 48 s on 4 cores and on
    `beethoven_qa.vendor` (3 targets) 1 min 37 s; the conditions that quantify over symbolic
    strings and tuples come back `Not confirmed` at the full 20 s, `is_public` confirmed over all
    paths, no counterexample.
  - An untracked `.claude/settings.local.json`, which Claude Code writes on an "always allow"
    answer, would make the new hook refuse every push from that checkout; `.gitignore` lists it.

- The code standards (2026-10-05): what was measured while building them, the tools' behaviour
  without the flags that disable a repository's suppressions among it, is under "Measured" in
  `docs/STANDARDS.md`. The symbolic gate went from 10 targets in about 2 minutes to 32 in about 7
  on 4 cores.

## Build and test

```sh
pixi install --locked         # reconstruct the exact env from the lock; CI uses --locked too
pixi run --locked gate        # lint (ruff) + typecheck (mypy strict) + tests (pytest) + boundary + symbolic (CrossHair/Z3) + standards
pixi run --locked symbolic    # just the gate: python -m beethoven_qa gate
pixi run --locked standards   # the code standards, every group, against where this branch left main
pixi run --locked boundary    # the interface standard's five rules on this tree
pixi run --locked python -m beethoven_qa gate --module beethoven_qa.vendor --report-all   # one module
pixi run hooks                # point git at .githooks (the session-start hook does this in a cloud session)
```

A Claude Code cloud session with only this repository gets pixi and the locked environment from
`.claude/hooks/session-start.sh`; a project thread with several repositories does not read it and
needs the same commands as the environment's setup script. In this cloud environment `pixi` lands at
`~/.pixi/bin`, which is not on the Bash tool's `PATH`: export it before `git push`, or the pre-push
hook fails. The hook also refuses a push of any ref but the checked-out commit, and any push from a
tree with uncommitted or untracked files: commit everything first.

## Layout

- `pixi.toml` / `pixi.lock` — the verification stack, pinned for `linux-64`.
- `interface/` — the manifest of what this repository offers (`INTERFACE.json`, `python.json`,
  `cli.json`, `README.md`).
- `src/beethoven_qa/` — `contracts.py`, `gate.py`, `vendor.py`, `boundary.py`, `example.py`,
  `__main__.py` (the command line; exempt from the contract rule as wiring and I/O), `standards/`
  (one module per group; `findings.py`, `newcode.py`, `sarif.py` and `group.py` shared; `run.py`
  the runner).
- `tests/` — pytest under typeguard's import hook; `test_hooks.py` runs the kit's real pre-push
  hook with a stub pixi, a bare remote and a linked worktree.
- `plugin/` — the Claude Code plugin (`hooks/` holds the coaching hook); `.claude-plugin/marketplace.json` — the marketplace listing it.
- `tools/check_workflows.mjs` — CI's check of the workflow scripts against the runtime's rules
  (the meta literal and what follows it, the clock and randomness with every `\u` escape refused,
  module loading, `new.target`, both grammars); `tools/check_workflows.test.mjs` — its tests, and
  the workflow scripts' control flow under stand-in hooks (`node --test
  tools/check_workflows.test.mjs`).
- `docs/` — `CONSUMING.md`, `STANDARDS.md`, `PARALLEL_BUILD.md`.
- `tests/data/` — the OASIS SARIF 2.1.0 schema every standards log is validated against, with its
  source in `tests/data/README.md`.
- `.githooks/`, `.github/`, `.claude/` — the kit, the CI lanes, the rule as code, session settings.

## How to be useful here

The recurring failure mode across these projects is a number or a state that is wrong but plausible
— a fabricated hash, a lock that was hand-edited, a "passed" that never ran, a contract that is true
of everything. Check against something outside the computation: regenerate the lock and diff it,
run the gate and read its counterexample, make a contract fail on purpose before trusting that it
passes. Never commit a lockfile or a digest you did not produce with the tool. Say plainly what is
not known; "not yet measured" is the preferred form.

Every unit of build work appends one row to `/mnt/project-files/ledger/LEDGER.md` (format in
`ledger/README.md`): date, thread, actor, activity, wall-minutes, bugs fixed, inputs, outputs, how
it was verified. Re-read the file just before appending — other threads write it too.
