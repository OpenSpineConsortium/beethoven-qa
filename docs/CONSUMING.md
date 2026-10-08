# Consuming beethoven-qa

How a repository adopts beethoven-qa, as steps a person or a Claude session can follow exactly. A
consuming repository (beethoven-agents, the research pipeline, any Python repository that may take
dependencies) gets two things: a pinned copy of the package and its gate, which its own `pixi run
gate` runs on every push and in CI, and the Claude Code plugin, which gives a session the skills,
the `stream` agent and the workflows for writing code to the bar and bringing existing code up to
it. Why a copy and not a dependency fetched from GitHub is in `src/beethoven_qa/vendor.py` and the
project's `plan/REPO_SPLIT.md` section 3: the repositories are private, and a copy needs no secret
anywhere.

The settings and plugin facts below were checked on 2026-10-04 against the Claude Code pages on
settings, plugin loading, plugin components, marketplaces and workflows; the commands were run that
day against a throwaway tagged copy of this repository and a throwaway consumer (pixi 0.81.0; from
the lock: Python 3.11.16, ruff 0.16.10, mypy 2.4.0, CrossHair 0.0.111, typeguard 4.6.0, icontract
2.7.3). Where a sentence says "not yet measured", nothing here has run it.

Beethoven runs Claude Code and is not an Anthropic product; "Claude" and "Claude Code" are
Anthropic's marks, used here to say what it runs.

## Who takes which half

- **Python that may take dependencies** (beethoven-agents, the research pipeline): every step
  below. Each public function carries contracts, the gate proves them, the copy is checked on every
  push.
- **hpcworker's framework** (the standard library only, of the oldest Python on the login node of a
  site it supports: 3.6 today, Wayne State's CentOS 7, as that site's module records) and
  **the browser and desktop editions** (TypeScript): the repository half only, the pre-push gate,
  the required check, auto-merge and rulesets as code, with their own language's checks. No copy,
  no icontract, no new dependency. The boundary lane comes with the copy, so these repositories
  keep their own manifest and check the standard's rules with their own tests.

## What an adopted repository holds

```
<repo>/
  pixi.toml, pixi.lock              its environment: the verification stack beside its own needs
  pyproject.toml                    [tool.beethoven-qa] and the tool tables for its own package
  src/<package>/                    its code
  tests/conftest.py                 typeguard's import hook for its package
  interface/INTERFACE.json          its own manifest: what it offers, and what it takes (step 3)
  vendor/beethoven-qa/              the pinned copy; never edited by hand
    ORIGIN                          where the copy came from and the digest of what it holds
    pyproject.toml  src/beethoven_qa/  plugin/  .claude-plugin/marketplace.json
    interface/                      beethoven-qa's manifest, which the boundary lane reads
    kit/                            the kit as the release has it, which vendor-check compares
  .githooks/pre-push                kit: the gate before every push
  .github/workflows/automerge.yml   kit: auto-merge armed on every pull request opened ready
  .github/workflows/rulesets.yml    kit: .github/rulesets/*.json applied to GitHub
  .claude/hooks/session-start.sh    kit: pixi and the locked environment in a cloud session
  .github/workflows/standards.yml   kit: the code standards, one job per group, one required check
  .github/workflows/ci.yml          its own: the gate lane (step 5) and any lanes of its own
  .github/rulesets/main.json        its own: names its required checks
  .claude/settings.json             its own: wires the session-start hook, worktree.baseRef
```

The kit files are byte for byte the release's; `vendor-check` fails one that differs. A change to
one is made in beethoven-qa and arrives with the next release: a consumer that takes that release
with `--kit` has the new file in the same commit as the new copy, and one that takes it without
`--kit` keeps its old kit and fails `vendor-check` until it does, by design.

## 1. Take the copy from GitHub

The copy is read from GitHub at a release tag, through `gh` and your own sign-in, the way every other
repository's copies are pulled (the interface standard's rule 3); no checkout of beethoven-qa is
read. `gh auth status` must show an account that can read `OpenSpineConsortium/beethoven-qa`. From
the consumer's root, once it carries a copy of 3.0.0 or later:

```sh
pixi run --locked python -m beethoven_qa vendor --into . --ref v3.0.0 --kit
```

That is the consumer's own pinned copy taking the release, and every release after the first is
taken this way. The first time, when the consumer has no copy yet or one older than 3.0.0 (whose
`vendor` read a checkout instead), the same command runs once from a clone of beethoven-qa at the
release, since only code of 3.0.0 or later can read GitHub:

```sh
gh repo clone OpenSpineConsortium/beethoven-qa /tmp/beethoven-qa-v3 -- --quiet --branch v3.0.0 --depth 1
pixi run --locked --manifest-path /tmp/beethoven-qa-v3/pixi.toml python -m beethoven_qa vendor --into . --ref v3.0.0 --kit
```

Only the clone's code runs; the files of the copy still come from GitHub, so nothing in the clone
can change them. (beethoven-qa's environment puts `$PIXI_PROJECT_ROOT/src` on `PYTHONPATH`, an
absolute path, so the command imports the clone's package wherever it is started.)

- **Where the files come from.** `gh api` against `repos/OpenSpineConsortium/beethoven-qa`: the
  commit the ref names, refused unless `main` contains it (GitHub's comparison of `main` with it
  says `identical` or `behind`), then the commit's tree, then each file the copy takes as its blob.
  The bytes of each blob are hashed the way git names objects (SHA-1 over `blob <size>`, a NUL and
  the bytes) and must give the id the commit's tree lists for that file, so they are the commit's
  bytes whatever carried them. Nothing holds a token: `gh` uses the sign-in of whoever runs it, and
  nothing reaches an argument. Measured 2026-10-07 from a cloud session: 2.0.0 taken this way into
  a throwaway consumer, 41 files in 19 s, wrote the same `ORIGIN` and the same bytes as the copy
  beethoven-agents had taken from a checkout.
- **`--locked`** takes the environment from its lock as it is; a re-solve would rewrite
  `pixi.lock`.
- **What it writes.** Into `vendor/beethoven-qa/`: the files of the release commit under
  `pyproject.toml`, `src/beethoven_qa/`, `plugin/`, `.claude-plugin/` and (from 2.0.0)
  `interface/`, as the commit holds them (never a working tree, so no `.pixi/`, no caches, no
  line-ending conversion and no edit git was told to ignore), each with the mode the commit gives
  it; the five kit files under `kit/`; and last `ORIGIN`:

  ```
  repository: OpenSpineConsortium/beethoven-qa
  ref: v3.0.0                       # the tag given, when it is a tag of that commit; else "untagged"
  commit: <the full 40-character SHA>
  tree_sha256: <sha256 over the sorted (path, sha256 of the file) pairs>
  files: <how many files the copy holds, ORIGIN not counted>
  ```

  With `--kit` it also writes the five kit files at their own paths in the consumer, with the
  executable bit the commit gives them; without it the live kit files are left as they are, and
  `vendor-check` fails while one is missing or differs. The copy is itself a plugin marketplace (its
  `.claude-plugin/marketplace.json` lists `./plugin`), which is what step 7's image registers.
- **What it refuses, before it writes anything.** A ref GitHub does not know, or a commit `main`
  does not contain (a pull request's branch); bytes that are not the blob the tree names, a tree
  listing GitHub cut short, an answer not in the shape GitHub documents, a `gh` that fails or is not
  installed (gh's own last line says why); a target that is not the root of a git checkout, or is
  the beethoven-qa checkout the command runs from; a link anywhere on a path it would write
  (`vendor/`, `vendor/beethoven-qa/`, `ORIGIN`, any file of the copy, `.github/`, `.claude/`, a kit
  file); a file where the release has a directory, or the reverse; an existing copy that holds a
  link; a link or a submodule in the release itself; and any existing file it would replace whose
  bytes are not the ones HEAD holds for it, asked of git file by file and compared with the
  commit's own blob rather than with the index, so neither a `.gitignore` entry nor
  `status.showUntrackedFiles=no` nor an edit kept behind `git update-index --assume-unchanged` or
  `--skip-worktree` (which `git status` and `git diff` report as no change) can hide one. A copy
  that still matches its ORIGIN, and kit files equal to that copy's, are rewritten even
  uncommitted, since nothing in them is lost: taking another tag before the first commit just
  works.
- **It never deletes.** A file the old copy had and the new release does not is left in place and
  listed with one line for the person to run,
  `rm -- <files> && git rm -q --cached --ignore-unmatch -- <files>`, which works whether or not the
  old copy was committed (an earlier `git add -u -- vendor/beethoven-qa` did not: git 2.55 on
  GitHub's runners refuses it for a folder git does not track yet, where 2.43 accepted it);
  `vendor-check` fails until it is gone, so nothing leaves a repository without a reviewed commit.
- **An untagged commit is not refused**: a commit `main` contains, given by its id or by a branch,
  is taken, and ORIGIN then says `ref: untagged`. Take tagged releases only; a reviewer who sees
  `untagged` asks why.

| Exit | Meaning |
| - | - |
| 0 | The copy (and with `--kit` the kit) is written and nothing of an older copy is left over |
| 1 | The copy is written, but the copy already there held files this release dropped; nothing was deleted; run the printed line, then `vendor-check` |
| 2 | Refused, for one of the reasons above (gh's failures among them), before anything was written; or git itself failed |
| 3 | Stopped part way by an error of the file system (a full disk, a permission, a link that appeared after the checks); `git status` shows what was written |

Review the result (`git status`, `git diff --stat`, `cat vendor/beethoven-qa/ORIGIN`) and commit the
copy, its ORIGIN and the kit files together, the message naming the release.

## 2. The consumer's `pixi.toml`

The same verification stack as beethoven-qa's own `pixi.toml` (copy its dependency lines; they are
repeated here as they stand at 2.0.0, but `jsonschema`, which only beethoven-qa's own tests use),
the repository's own dependencies beside them, the copy on `PYTHONPATH`, and the seven lanes of the
gate:

```toml
[workspace]
name = "<repo>"
version = "0.0.0"
channels = ["conda-forge"]
# One platform, named apart from its subdirectory because it states a glibc floor: jscpd (the
# duplication detector, from PyPI) ships manylinux_2_34 wheels only. A `[target.linux-64.…]` table
# still applies under the named entry (measured with pixi 0.81.0, 2026-10-08).
platforms = [{ name = "linux-64-glibc234", platform = "linux-64", glibc = "2.34" }]
# Only while no package comes from both conda-forge and PyPI; beethoven-qa's pixi.toml says why.
conda-pypi-map = false

[dependencies]
# The verification stack, the same lines as beethoven-qa's pixi.toml.
python = "3.11.*"
pip = "*"
icontract = ">=2.6"
pydantic = ">=2"
beartype = ">=0.18"
typeguard = ">=4"
pytest = ">=8"
ruff = ">=0.6"
mypy = ">=1.10"
# The code standards (docs/STANDARDS.md of beethoven-qa), each tool run as its own process.
complexipy = ">=8"
radon = ">=6"
bandit = ">=1.9"
coverage = ">=7.10"
pyyaml = ">=6"
types-pyyaml = ">=6"
zizmor = ">=1.30"
lizard = ">=1.24"
# The repository's own dependencies follow here.

[pypi-dependencies]
crosshair-tool = ">=0.0.60"
jscpd = ">=5.4"

[activation]
# The repository's package and the pinned copy, imported without a build step. Absolute, so an
# ad-hoc `pixi run` started outside the repository's root imports them too.
env = { PYTHONPATH = "$PIXI_PROJECT_ROOT/src:$PIXI_PROJECT_ROOT/vendor/beethoven-qa/src" }

[tasks]
# Ruff and mypy are named the repository's own directories only, never the copy.
lint = "ruff check src tests && ruff format --check src tests"
typecheck = "mypy src"
test = "pytest -q"
symbolic = "python -m beethoven_qa gate"
standards = "python -m beethoven_qa standards all"
vendor-check = "python -m beethoven_qa vendor-check"
boundary = "python -m beethoven_qa boundary"
# The evidence report (docs/REPORT.md of beethoven-qa): every lane above run and written up.
report = "python -m beethoven_qa report"
gate = { depends-on = ["lint", "typecheck", "test", "boundary", "symbolic", "standards", "vendor-check"] }
hooks = "git config core.hooksPath .githooks"
```

Then, once and on purpose, solve the lock with pixi 0.81.0 (the version beethoven-qa's lock, the
kit's session-start hook and CI use) and commit it in the change that adds the stack, saying so:

```sh
pixi lock
pixi install --locked
pixi run hooks                      # the pre-push gate is on from here
pixi run --locked gate
```

The hook gates the commit being pushed, not whatever is checked out: every ref it is given must
be the checkout's HEAD and `git status --porcelain` must print nothing (untracked files count), or
it refuses and says to check out and commit what is pushed, or to push knowingly with
`SKIP_GATE=1`; and before the gate runs it unsets the variables git exports to a hook (`GIT_DIR`
among them), so a push from a linked worktree never aims the tests' throwaway repositories at the
repository itself. A file Claude Code writes beside the checkout, such as
`.claude/settings.local.json`, is listed in `.gitignore` so it never counts as untracked.

From then on the lock changes only on purpose, by pixi, in a change that says so; never by hand,
never by a stray `pixi install` or `pixi update`, and every `pixi run` carries `--locked` (without
it pixi re-solves a manifest that drifted from its lock, without a word). Measured 2026-10-04: this manifest in a throwaway
consumer locked, installed and ran `pixi run --locked gate` green through all five lanes of 1.0.0, and
`pixi run --manifest-path <consumer>/pixi.toml python` started from another directory imported both
the consumer's package and the copy; the gate then failed, as it should, on a
public function without contracts and on a wrong body (`resume_at(0, 0, 1) (which returns 0)`).

The gate's symbolic lane runs one CrossHair process per contracted function and per contracted
class, as many at a time as the allocation's cores; `BEETHOVEN_QA_JOBS` lowers the count on a
laptop busy with other work and never raises it. Both of the gate's checks always run, so an
uncontracted function in one module does not stop CrossHair proving the others. For one module,
`python -m beethoven_qa gate --module <pkg.mod>`. Every run prints every condition's verdict,
counts them under each target and over the run, and fails on a precondition no input can meet
(`--report-all` is accepted and changes nothing). Beyond a missing contract, the gate fails
on whatever would keep a function out of CrossHair's sight: a `.py` file under the package that is
not one of its modules (a directory without `__init__.py`), a module that fails or exits while
being imported, a `# crosshair:` directive, a public coroutine (CrossHair would check the coroutine
object, not its result), contracts on a pydantic model (CrossHair does not construct one), and a
package with nothing to prove; and on what would hide a finding from the standards or a test
from the run, in the package and in `tests/`: a suppression comment (`# noqa`, `# type: ignore`,
`# nosec`, `# pragma: no cover`, `# jscpd:ignore`, `# zizmor: ignore`, `# complexipy: ignore`) or
a test skipped or expected to fail (pytest's and unittest's marks and calls).

A function CrossHair must not run, because it reads, writes, asks the clock or the environment or
starts a process, or cannot run, because of a fault of CrossHair's own that was measured, is named
in the table below under `unproven` with its reason, never marked with a `# crosshair: off` comment
(the gate refuses every directive: a comment hides what is not proven, a table entry says so on
every run). The function keeps its contracts, which the tests check at run time; the logic it needs
lives in contracted functions CrossHair proves. An entry that names no contracted function fails
the gate, so a renamed or removed function cannot leave an entry behind; so does one that names a
class or a method, since a class is proven whole, as one target, and I/O stays out of contracted
classes. Listing keeps the gate from checking the function as a target, and nothing more: CrossHair
runs the body of every function a contracted target calls, listed or not, so a contracted function
that calls a listed one does the I/O too while it is being proven and is itself kept private,
exempt or listed; and a listed function's contracts are assumed by every proven caller, never
checked through it, so the tests are their only check and must exercise them.

The standards lane holds the package's new code to the six groups of `docs/STANDARDS.md`
(readability, design, duplication, coverage, security, supply chain), against where the branch left
the remote's default branch. It reads the package `[tool.beethoven-qa]` names and
`.github/workflows/`, needs no table of its own (its limits are the release's, and no setting of the
repository loosens them), and blocks only what the change made: the findings the old code already
holds are reported and pass. Its coverage group runs the repository's tests a second time, under
coverage.py, as `pytest` from the root with the repository's own pytest settings.

The boundary lane checks the five rules of the Beethoven interface standard
([INTERFACES.md](https://github.com/OpenSpineConsortium/beethoven/blob/main/INTERFACES.md)) on the
files git tracks, with no network: no file but Markdown reaches another checkout (`../<repo>`, a
path built from a parent and a repository's name, a checkout variable such as
`HPCWORKER_DIR`, a submodule, a dependency by git URL or by a path outside the tree, a clone of one
of the organisation's repositories, a workflow step that checks another repository out); the
repository's own `interface/INTERFACE.json` exists in the standard's shape and names every file it
exports and every pinned copy it carries; each copy holds exactly what its `ORIGIN` lists, byte for
byte; Python imports of `beethoven_qa`, `beethoven_agents` and `beethoven_research` name only what
the provider's `python` export lists, read from the provider's manifest in this repository's pinned
copy of it; and they go one way, beethoven-qa <- beethoven-agents <- beethoven-research. It reads
beethoven-qa's own manifest in `vendor/beethoven-qa/interface/`, which is why it needs a copy of
2.0.0 or later, and leaves the copy's files to `vendor-check`. Exit 0 when the rules hold, 1 with
each finding as `file:line: rule N:` and a sentence, 2 when it cannot run (not the root of a git
checkout, or a `[tool.beethoven-qa]` table it cannot use). Step 3 says what the manifest holds.

## 3. `pyproject.toml`: the gate's table and the tools

```toml
[tool.pytest.ini_options]
pythonpath = ["src", "vendor/beethoven-qa/src"]
testpaths = ["tests"]
addopts = "-ra"

[tool.ruff]
line-length = 100
target-version = "py311"
src = ["src", "tests"]
# A bare `ruff check --fix .` or `ruff format .` must never rewrite the copy: vendor-check would fail.
extend-exclude = ["vendor"]

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP"]
ignore = ["E731"]

[tool.mypy]
python_version = "3.11"
mypy_path = "src:vendor/beethoven-qa/src"
packages = ["<package>"]
strict = true
warn_unused_ignores = true

# What `python -m beethoven_qa gate` checks in this repository.
[tool.beethoven-qa]
package = "<package>"               # the top-level package the gate imports and checks
src = "src"                         # its directory, inside the repository
exempt = ["<package>.__main__"]     # wiring and the command line only, never logic
per_condition_timeout = 20          # seconds per condition, 1 to 600: the declared bound

# Functions the gate never checks as targets, each with its reason; it prints them on every run.
[tool.beethoven-qa.unproven]
"<package>.store.load" = "reads the account list from disk"
```

The table is read and validated once (`GateConfig` in `src/beethoven_qa/gate.py`): a repository
without it is a configuration error; `package` is one top-level name, no dots; `src` is a directory
inside the repository; every `exempt` entry is the package or one of its modules, so the copy can
never be exempted from a consumer's gate; every `unproven` key is a function of the package (never
a class or a method; the gate refuses an entry that names one) and every reason says something; no
other key is accepted. The gate imports every module
of the package to find its functions, so a module that does work at import time does it during the
gate, and one that calls `sys.exit` at import fails it: guard a command line's call with
`if __name__ == "__main__":`.

### `interface/INTERFACE.json` and the boundary table

The boundary lane reads the repository's own manifest, `interface/INTERFACE.json`, in the shape the
standard gives: what the repository offers (its `exports`, each with an integer `version` and the
files it covers) and what it takes, one entry per export of another repository it carries. A
consumer of the copy names beethoven-qa's exports it takes, all into `vendor/beethoven-qa`, with
the copy's own `ORIGIN` as the record (a research-pipeline repository that imports the contract
decorators, runs the commands, carries the kit and registers the plugin):

```json
{
  "format": 1,
  "name": "<repo>",
  "repository": "OpenSpineConsortium/<repo>",
  "summary": "<one sentence>",
  "standard": "https://github.com/OpenSpineConsortium/beethoven/blob/main/INTERFACES.md",
  "exports": [],
  "imports": [
    {"repository": "OpenSpineConsortium/beethoven-qa", "export": "python", "version": 1,
     "into": "vendor/beethoven-qa", "origin": "vendor/beethoven-qa/ORIGIN"},
    {"repository": "OpenSpineConsortium/beethoven-qa", "export": "cli", "version": 1,
     "into": "vendor/beethoven-qa", "origin": "vendor/beethoven-qa/ORIGIN"},
    {"repository": "OpenSpineConsortium/beethoven-qa", "export": "kit", "version": 1,
     "into": "vendor/beethoven-qa", "origin": "vendor/beethoven-qa/ORIGIN"},
    {"repository": "OpenSpineConsortium/beethoven-qa", "export": "plugin", "version": 1,
     "into": "vendor/beethoven-qa", "origin": "vendor/beethoven-qa/ORIGIN"}
  ]
}
```

What beethoven-qa offers is its own manifest, which the copy carries
(`vendor/beethoven-qa/interface/INTERFACE.json`, explained in `interface/README.md` there): `python`
(`beethoven_qa.contracts` and `beethoven_qa.__version__`, nothing else of the package), `cli` (the
five commands, their options and exit codes, and this table), `kit`, `plugin`, and `docs`, the two
documents, which the copy does not carry and an import of which the lane refuses. An import of
beethoven-qa into any other folder, or of an export or version the copy's manifest does not offer,
is a finding of rule 3. Which exports a repository takes is the person's to write; the lane checks
that each one is there, and that the code imports nothing the `python` export does not list.

A finding of rule 1 or rule 4 that must stand for now (a test that still reads a sibling checkout
until the provider publishes what it needs, say) is named in a table of its own, each entry a file,
what matched there, and why:

```toml
[tool.beethoven-qa.boundary]
"tests/test_profiles.py" = { "HPCWORKER_DIR" = "compares the copy with a sibling checkout on a maintainer's machine; goes when the provider's export is pulled" }
```

The lane prints every entry on every run, refuses one that no longer matches anything, and never
excuses rules 2, 3 or 5. beethoven-qa itself needs no entry.

Two things measured on 2026-10-04 that a consumer meets at once:

- Ruff treats `beethoven_qa` as third-party in a consumer (it is not under `src`), so
  `from beethoven_qa.contracts import ensure, require` sits in the same import block as
  `from beartype import beartype`, with no blank line between them. Inside beethoven-qa itself the
  package is first-party and the two are separate blocks.
- With the copy on `mypy_path`, `mypy src` checks the consumer's code against the copy's types and
  does not report an error planted inside the copy; naming the copy as a target does. So the
  typecheck task names `src` and nothing else.

## 4. `tests/conftest.py`

```python
# typeguard's import hook is the test-enclave layer of the typing rule: deeper and slower than
# beartype, so it runs only here. It must be installed before the package is first imported, which
# is why it lives in conftest rather than in a test module.
from typeguard import install_import_hook

install_import_hook("<package>")
```

The hook names the consumer's own package; the copy was tested under its own hook in beethoven-qa.

## 5. The CI gate lane and the required check

`.github/workflows/ci.yml` carries the gate lane in the shape of beethoven-research's (lock first,
then each lane as its own step so a failure names itself), with `vendor-check` added right after the
environment is reconstructed: the gate's code comes from the copy, so the copy is checked before
anything it reports is believed. No step holds a secret.

```yaml
name: ci
on:
  push:
    branches: [main]
  pull_request:
  workflow_dispatch:

env:
  # The pixi that produced the committed lock, fetched from its release asset by name, and the
  # sha256 of that asset: beethoven-qa's own measurement of 2026-10-08 (pixi publishes no
  # checksum asset). A new pixi version means a new digest, measured again.
  PIXI_VERSION: 0.81.0
  PIXI_SHA256: 248510f5f75460a2ae4ec62d761e15c0191dc35db3b1da6f82d9f17c6017051d

# Nothing writes; the supply-chain standard refuses a workflow without this, or one that grants a
# write at the top.
permissions:
  contents: read

jobs:
  gate:
    name: gate (lock → lint, typecheck, tests, CrossHair/Z3, vendor-check)
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      # Pinned to a commit, the tag beside it (the supply-chain standard refuses a tag alone).
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0
        with:
          persist-credentials: false
      - name: install pixi
        # Checked against its digest before it is made executable or run (the supply-chain
        # standard refuses a download a step runs, installs or chmods without such a check).
        run: |
          curl -fsSL -o /usr/local/bin/pixi \
            "https://github.com/prefix-dev/pixi/releases/download/v${PIXI_VERSION}/pixi-x86_64-unknown-linux-musl"
          echo "${PIXI_SHA256}  /usr/local/bin/pixi" | sha256sum -c - || { rm -f /usr/local/bin/pixi; exit 1; }
          chmod 0755 /usr/local/bin/pixi
          pixi --version
      - name: reconstruct the environment from the lock
        # --locked refuses to re-solve: a manifest that drifted from its lock fails here, on purpose.
        run: pixi install --locked
      - name: the pinned copy of beethoven-qa matches its ORIGIN, and the kit is in place
        run: pixi run --locked vendor-check
      - name: the interface standard's rules (boundary)
        run: pixi run --locked boundary
      - name: lint
        run: pixi run --locked lint
      - name: typecheck
        run: pixi run --locked typecheck
      - name: tests (typeguard enclave)
        run: pixi run --locked test
      - name: symbolic verification (CrossHair + Z3)
        run: pixi run --locked symbolic
```

The code standards run in the kit's `.github/workflows/standards.yml`, not in this file: one job
per group, and `standards (every group green)`, green only when every group's is. The standards
lane of the gate task runs them before a push too; CI runs them per group so a red one names itself.

`.github/rulesets/main.json` names the gate job and `standards (every group green)` as required
checks, each `context` exactly the job's `name` (a required check that no job reports under that
exact name keeps a pull request waiting), beside any lane of the repository's own (beethoven-agents
adds its image lane):

```json
{
  "name": "main: checks before merge",
  "target": "branch",
  "enforcement": "active",
  "bypass_actors": [
    { "actor_id": 1, "actor_type": "OrganizationAdmin", "bypass_mode": "always" }
  ],
  "conditions": { "ref_name": { "include": ["~DEFAULT_BRANCH"], "exclude": [] } },
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    {
      "type": "required_status_checks",
      "parameters": {
        "strict_required_status_checks_policy": true,
        "do_not_enforce_on_create": false,
        "required_status_checks": [
          {
            "context": "gate (lock → lint, typecheck, tests, CrossHair/Z3, vendor-check)",
            "integration_id": 15368
          },
          {
            "context": "standards (every group green)",
            "integration_id": 15368
          }
        ]
      }
    }
  ]
}
```

The policy is strict: a pull request whose branch fell behind `main` is not merged until it
carries `main`, since the checks ran on a tree `main` would never hold (beethoven-qa measured on
2026-10-08 that with the flag off, `main`'s tree after a merge was never what the checks ran on).
Bringing the branch up to date is the session's own step (merge `main` in, gate, push); a push a
bot makes with `GITHUB_TOKEN` starts no workflow, so no automation does it. The kit's
`scorecard (report)` job is named by no rule: it reports and never blocks.

The kit's `rulesets.yml` applies that file with the `RULESETS_TOKEN` secret, and the kit's
`automerge.yml` arms auto-merge only once `main` carries a required-checks rule. So a new repository
needs greg's hand once (`plan/REPO_SPLIT.md` section 5): **Allow auto-merge** ticked, the secret
shared with the repository, and pull request #1 merged by hand when green, which applies the rule.
Every later pull request is opened ready for review, never as a draft, and merges itself when the
required checks are green; the checks alone decide.

## 6. The repository's own `.claude/settings.json`

The kit carries the session-start hook's script, not its wiring, and two settings belong to the
repository: a plugin's own `settings.json` takes only the keys `agent` and `subagentStatusLine` and
drops every other (plugin components page, "Default settings"). Beside the repository's own deny
rules (beethoven-research's are the model):

```json
{
  "permissions": {
    "deny": [
      "Read(**/.credentials.json)",
      "Read(~/.claude/.credentials.json)",
      "Read(**/.env)",
      "Read(**/.env.*)",
      "Bash(claude setup-token*)",
      "Bash(claude auth logout*)"
    ]
  },
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|resume",
        "hooks": [
          {
            "type": "command",
            "command": "bash \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/session-start.sh",
            "timeout": 900
          }
        ]
      }
    ]
  },
  "worktree": { "baseRef": "head" }
}
```

`worktree.baseRef: "head"` makes a stream's worktree branch from the current HEAD rather than from
`origin`'s default branch (`docs/PARALLEL_BUILD.md` section 2).

## 7. The plugin in Claude Code sessions

The plugin is `plugin/` (name `beethoven-qa`), listed by `.claude-plugin/marketplace.json`
(marketplace `beethoven-qa`, plugin source `"./plugin"`). Its skills run as
`/beethoven-qa:definition-of-done`, `/beethoven-qa:write-verified-module`,
`/beethoven-qa:improve-existing-module` and `/beethoven-qa:review-contracts`; its agent is
`beethoven-qa:stream`; its workflows run as `/beethoven-qa:gate-until-green`,
`/beethoven-qa:verify-module` and `/beethoven-qa:improve-module` (a plugin workflow runs as
`/<plugin>:<meta.name>`, workflows page, "Distribute a workflow in a plugin").

**greg's laptop.** In a Claude Code session:

```
/plugin marketplace add OpenSpineConsortium/beethoven-qa
/plugin install beethoven-qa@beethoven-qa
```

The repository is private, so Claude Code reaches it with the git credentials already on the
machine; no marketplace field holds a token (marketplace hosting page, "Work around what a
marketplace can't do"). The laptop's plugin follows what beethoven-qa's default branch lists, which
can be ahead of a consumer's pinned copy: the copy's gate decides what lands, the plugin only helps
write it.

**On the grid, in the agents' image.** The image (beethoven-agents builds it) puts a consumer's
pinned copy at one absolute path, `/opt/beethoven-qa` in `plan/REPO_SPLIT.md` section 3, and its
managed settings (`/etc/claude-code/managed-settings.json`) register that copy as a `directory`
marketplace and force-enable the plugin:

```json
{
  "extraKnownMarketplaces": {
    "beethoven-qa": {
      "source": { "source": "directory", "path": "/opt/beethoven-qa" }
    }
  },
  "enabledPlugins": { "beethoven-qa@beethoven-qa": true }
}
```

What the pages say, and where (all 2026-10-04):

- A `directory` source takes the absolute path of a directory containing
  `.claude-plugin/marketplace.json`, and is meant for a marketplace an organization deploys to each
  machine: settings reference, `extraKnownMarketplaces`, "Marketplace source types", and the
  `directory` row of the source table under `strictKnownMarketplaces`. The same page's
  `prependPlugins` and `appendPlugins` entries show a managed-settings file with exactly this pair
  of keys, a `directory` marketplace and its plugin set to `true`.
- A relative-path plugin from a marketplace whose files Claude Code has (`github`, `git`, `file`,
  `directory`) resolves (marketplace reference, "Relative path plugin source"), loads from the
  marketplace itself with no install record (plugin loading reference, "Enabled in project settings
  but not installed"), and is read in place from a directory marketplace, not copied (marketplace
  hosting page, "Edit plugins in place on a shared directory").
- `true` in managed `enabledPlugins` force-enables, and no other source overrides it (plugin loading
  reference, "Find where a plugin is enabled"; settings reference, `enabledPlugins`).
- **Under `allowManagedHooksOnly`, which the image's managed settings set, the hooks of a plugin
  that managed settings force-enable still run**: settings reference, `allowManagedHooksOnly`, and
  its subsection "What runs under `allowManagedHooksOnly`": "Force-enabled plugin hooks run: hooks
  from plugins your managed settings force-enable through `enabledPlugins`". It matches on the full
  `plugin@marketplace` id, so a same-named plugin from another marketplace stays blocked; plugins
  with a `command` source are disabled under that key, and this one is a relative path. The plugin
  ships no hooks at 1.0.0; this is what lets it carry vetted ones later.
- Why managed settings and not the repository's: a repository's `extraKnownMarketplaces` is honoured
  only after the workspace trust dialog, and in a folder not trusted, a `-p` run there included, it
  is ignored without a message (settings reference, `extraKnownMarketplaces`). Managed settings are
  not a repository's file.
- A `-p` step that starts one of the plugin's workflows needs `Workflow` in its allow rules: in `-p`
  the launch goes through the session's permission rules like any tool call, so `dontAsk` denies it
  otherwise (workflows page, "Approve the plan before it runs"). How a step is shaped is
  beethoven-agents'.

Whether this directory marketplace loads the plugin in a `-p` step or a master inside the image on
the grid is **not yet measured**: it is what the pages say, and no image has run it.

**In a cloud project thread.** A cloud session does not add the marketplaces a repository lists
under `extraKnownMarketplaces`, because that needs the workspace trust dialog, which a cloud session
never shows (plugin loading reference, "Plugins shared through a repository"); the image's managed
settings file does not reach it either (only server-managed settings reach a cloud session, settings
page). So a thread uses the attached checkout as files:

- it reads the skills as files: `plugin/skills/<name>/SKILL.md` in a beethoven-qa checkout,
  `vendor/beethoven-qa/plugin/skills/<name>/SKILL.md` in a consumer;
- it runs a workflow with the Workflow tool's `scriptPath` set to the script,
  `plugin/workflows/<name>.js` or `vendor/beethoven-qa/plugin/workflows/<name>.js`, with the same
  `args` (`{ module: "src/<package>/<name>.py" }` for `verify-module` and `improve-module`);
- for a stream it follows `plugin/agents/stream.md`, giving each parallel editor its own worktree;
- a thread with several repositories does not run the session-start hook (the hook's own header),
  so it runs `pixi install --locked` in each checkout before the gate.

Whether a repository's `.claude/skills/` skills-directory plugin loads in a cloud thread is **not
yet measured**. The plugin loading reference says such a plugin loads only after the workspace trust
dialog, which a cloud session never shows; no thread has tried it.

## Updating to a new release

1. **In beethoven-qa**, a release is a tag on `main`, an integer version (`v2.0.0`), with
   `__version__` in `src/beethoven_qa/__init__.py`, `version` in `pyproject.toml` and `version` in
   `plugin/.claude-plugin/plugin.json` changed together. The last one matters on laptops: a plugin
   installed from a git marketplace stays on its cached copy until that string changes (marketplace
   hosting page, "Release a new version"), and the update arrives with
   `/plugin marketplace update beethoven-qa` or `claude plugin update beethoven-qa@beethoven-qa`.
2. **In the consumer**, on a branch: step 1 again with the new tag, from the consumer's own copy.
   Exit 1 means the release dropped files; run the printed line. Then `pixi run --locked
   vendor-check` and `pixi run --locked gate`, and, with a beethoven-qa checkout at the release
   beside it (`../beethoven-qa`), the anchored check from that checkout's own environment:
   `pixi run --locked --manifest-path ../beethoven-qa/pixi.toml python -m beethoven_qa vendor-check --root . --source ../beethoven-qa`.
   Run from the consumer's environment instead, `--source` is refused with exit 1: that
   environment imports the copy, so the check would be the copy's own code vouching for itself.
   An unedited copy refuses; an edited one would not, which is exactly why the line runs from
   beethoven-qa's own environment.
3. If the release changed the dependency lines of beethoven-qa's `pixi.toml`, make the same change
   in the consumer's, run `pixi lock` on purpose, and say so in the same change.

   **From 1.0.0 to 2.0.0**: the release adds the code standards. Take it with `--kit` (the kit
   gains `.github/workflows/standards.yml`, and `automerge.yml` and `rulesets.yml` now pin their
   actions and grant writes only on the job that needs them); add the six dependency lines and the
   `standards` task of step 2 and re-lock; add `standards (every group green)` to
   `.github/rulesets/main.json`; and give the repository's own workflows top-level
   `permissions: contents: read` and actions pinned to a commit, or the supply-chain group reports
   them (as findings the old workflows held, they pass, but any line the change touches is new).
   The rule is applied by the next daily run of `rulesets.yml` after the merge, or by hand with
   "Run workflow". 2.0.0 is also the first release with the `boundary` command and with
   `interface/` in the copy: write the repository's own `interface/INTERFACE.json` and, if it
   needs one, the `[tool.beethoven-qa.boundary]` table (step 3), add the `boundary` task to the
   gate (step 2) and its step to the CI gate lane (step 5), and run `pixi run --locked boundary`
   until it says the rules hold. A copy of 1.0.0 carries no manifest, so the lane says to take
   2.0.0.
   **From 2.0.0 to 3.0.0**: `vendor` reads the release from GitHub and takes `--ref`. A copy of
   2.0.0 cannot do that (its `vendor` reads a checkout), so this once step 1's second form runs it
   from a clone of 3.0.0; every release after is taken by the consumer's own copy. The `cli`
   export is at version 2, because `--ref` is new and required: change the consumer's import of
   `cli` in `interface/INTERFACE.json` to version 2 in the same change, or the boundary lane says
   the copy no longer offers `cli` version 1. The kit and the dependency lines are unchanged.
   **From 3.0.0 to 4.0.0**: the release adds the `coach` command and the plugin's `PostToolUse`
   hook that runs it (`docs/STANDARDS.md`, "For a session writing code"), and makes the
   readability and security groups several times faster. Take it with `--ref v4.0.0 --kit`; the
   kit, the dependency lines and the `cli` export's version (2: a command added) are unchanged.
   Sessions that load the plugin get the hook with the copy. A repository whose sessions do not
   load it (a cloud session, which reads only the repository's own settings) may name the copy's
   script in its own `.claude/settings.json`, the way beethoven-qa's does:
   `{"hooks": {"PostToolUse": [{"matcher": "Edit|Write|MultiEdit", "hooks": [{"type": "command",
   "command": "bash \"$CLAUDE_PROJECT_DIR\"/vendor/beethoven-qa/plugin/hooks/coach.sh",
   "timeout": 60}]}]}}`. That file is the repository's own, not the kit's.
   **From 4.0.0 to 5.0.0**: the release makes the gate stricter, puts three established tools
   beside the home-made code, and adds the `report` command. Take it with `--ref v5.0.0 --kit`,
   then, in the same change:
   - **The gate.** It now runs CrossHair with every condition's verdict on every run (the old
     `--report-all` is the default; the flag stays accepted and changes nothing), prints under
     each target and at the end how many conditions were confirmed over all paths and how many
     were not confirmed within the bound, and fails on a precondition no input can meet: either a
     vacuous contract, or one stated over values CrossHair 0.0.111 cannot build (a `str` checked
     against a set of names, measured 2026-10-08 in beethoven-qa's own `security.level_for` and
     in four functions of beethoven-agents: `bee.accounts.host_transcript`,
     `bee.handoff.extraction_prompt`, `bee.handoff.hydration_prompt`, `guard.check_path`).
     Restate such a contract over an enum (a `Literal` makes CrossHair 0.0.111 raise), or list the
     function under `[tool.beethoven-qa.unproven]` with that reason. The gate also refuses, in the
     package and in `tests/`, every suppression comment (`# noqa`, `# type: ignore`, `# nosec`,
     `# pragma: no cover`, `# jscpd:ignore`, `# zizmor: ignore`, `# complexipy: ignore`, the
     file-level forms, lizard's `forgive` and `GENERATED CODE`) and every pytest or unittest skip
     or expected failure: remove them and fix what they hid.
   - **The dependency lines.** Add `zizmor` and `lizard` (conda-forge) and `jscpd` (PyPI) as
     step 2 now shows them, and give the platform entry jscpd's glibc floor
     (`platforms = [{ name = "linux-64-glibc234", platform = "linux-64", glibc = "2.34" }]`); a
     `[target.linux-64.…]` table still applies under the named entry, and so does one named
     `linux-64-glibc234` (measured with pixi 0.81.0, 2026-10-08). Re-lock on purpose and say so.
     The readability group now refuses to run on a function lizard cannot count (a return
     annotation over several lines, a dedented continuation line), naming it; put the signature
     on one line. The duplication group's floor is jscpd's: 10 lines and 40 of its tokens.
   - **The kit is at version 2.** All five files are still the kit: `.githooks/pre-push`,
     `.github/workflows/automerge.yml`, `.github/workflows/rulesets.yml`,
     `.claude/hooks/session-start.sh` and `.github/workflows/standards.yml`; three of them change
     (`standards.yml` checks the pixi download against its digest and gains the `scorecard
     (report)` job, which is not a required check; `session-start.sh` checks the same download;
     `automerge.yml` says what the strict policy below means). `--kit` takes all five. The
     repository's own `ci.yml` is not the kit's: give its pixi download the same digest check
     (step 5 shows it; the supply-chain group now reports a download a step goes on to run without
     one) and, if the report is wanted on every run, the `report (evidence)` job from
     beethoven-qa's `ci.yml`, which is not a required check either; add the `report` task to
     `pixi.toml` as step 2 shows.
   - **The ruleset.** Set `strict_required_status_checks_policy` to `true` in
     `.github/rulesets/main.json` (step 5): a pull request whose branch fell behind `main` then
     waits until its branch carries `main`. A session updates its own branch (merge `main` in,
     gate, push); nothing automates it, because a push made with `GITHUB_TOKEN` starts no checks.
   - **The `cli` export is at version 3** (`report` added; `--report-all` the default): change
     the import in the repository's `interface/INTERFACE.json`, or the boundary lane says the copy
     no longer offers `cli` version 2.
4. One commit for the copy, its ORIGIN, the kit files and any lock change, its message naming the
   old and the new release; then a pull request, ready for review, which merges when the gate is
   green. A red gate after an update is reported red and fixed at its cause, in the consumer's code
   or upstream in beethoven-qa, never in the copy.

## What is never done

- **Hand-editing the copy** or a kit file. `vendor-check`, run at the consumer's root (it asks git
  there, and tells git which repository by the directory alone: `GIT_DIR` and its kin from a
  hook's environment are dropped, configuration given with `git -c` or `GIT_CONFIG_COUNT`, the
  way a container lane gives `safe.directory`, is kept), fails both (measured 2026-10-04: one
  added line in the copy, and one in `.githooks/pre-push`, each failed it); a kit hook that lost
  its executable bit on disk, and one
  that git records without it (`.githooks/pre-push` and `.claude/hooks/session-start.sh` must be
  `100755` in git's own index, live and under `kit/`: a checkout with `core.fileMode=false`, such
  as WSL on `/mnt/c`, commits a new hook as `100644`, every clone then has a hook git skips with
  only a hint, and the printed `git update-index --chmod=+x` line fixes the record); a byte-code
  cache git tracks anywhere under the copy, and a hash-based `.pyc` there even untracked (Python
  loads one in place of the source, after comparing only the source's hash or not at all, so one
  compiled from other code survives a fresh clone and runs; the timestamp caches importing writes
  are left out of the digest and are recompiled on a clone). It is a check in the consumer, not a
  proof: an edit that also rewrites `ORIGIN` passes it, and so does an edit to the copy's own
  `vendor.py`, since the consumer's environment runs the copy's code. Only
  `vendor-check --source <beethoven-qa checkout>`, which rebuilds the tree from the commit ORIGIN
  names and checks the tag, catches those, and it is run from that checkout's own environment
  (the line under "Updating", step 2): an unedited copy refuses to run it from anywhere else, and
  an edited one would not, which is exactly why the line runs from beethoven-qa's own environment
  (measured the same day, in `tests/test_vendor.py`). A package named `beethoven_qa` in the
  consumer's own `src/` comes first on the `PYTHONPATH` and shadows the copy for `vendor-check`
  and the gate alike: an unedited one fails the plain check (its code is in the consumer and not
  in the copy), an edited one is seen by neither form, and a reviewer who sees `src/beethoven_qa/`
  in a consumer asks why. A reviewer who sees ORIGIN change in a pull request that takes no new
  release asks why. A change is made in beethoven-qa, released, and taken with step 1.
  `/beethoven-qa:gate-until-green` leaves the copy alone and says so, and the stream agent never
  edits it.
- **Linting or type-checking the copy as the consumer's code.** `lint` names `src tests`, ruff
  excludes `vendor`, `typecheck` names `src`, the typeguard hook names the consumer's package, and
  `exempt` cannot name the copy. The copy is held to all of these by beethoven-qa's own gate, which
  a commit clears before it reaches `main` and a tag.
- **Importing what the `python` export does not list**, such as `beethoven_qa.gate` or
  `beethoven_qa.vendor`: those are run through the commands, and can change inside a release. The
  boundary lane refuses the import with its file and line.
- **Skipping `vendor-check`.** It is a lane of `pixi run --locked gate` and a step of the CI gate lane.
  `SKIP_GATE=1` skips the pre-push gate only knowingly, with the reason in the commit; it also skips
  the hook's own checks (that the pushed ref is HEAD and the tree clean), so such a push is unproven
  on every count, and CI runs every lane regardless.
- **Taking a copy from anywhere but GitHub**, from a commit `main` does not contain, or without a
  tag, or running `pixi update` or a plain `pixi install` to change a lock.
- **Skipping, disabling or weakening a test, a contract or a symbolic bound to get green**,
  listing a module with logic in `exempt`, or setting aside under `unproven` a function CrossHair
  can run.

## Not yet measured

- Whether the managed-settings `directory` marketplace loads the plugin in a `-p` step, or in a
  master, inside the agents' image on the grid.
- Whether a repository's `.claude/skills/` plugin loads in a cloud project thread.
- The plugin installed from the private GitHub marketplace on greg's laptop, and its update after a
  version change.
- The CI gate lane and ruleset above in a consuming repository on GitHub: the shape is
  beethoven-research's, which ran there; the `vendor-check` step and the longer check name have not.
- The standards workflow and its required check on GitHub, in beethoven-qa or in a consumer, and the
  standards lane over a consumer's package and tests (beethoven-agents' 277 tests under coverage).
- The boundary lane in a real consumer: it ran in throwaway consumers written by the tests
  (`tests/test_boundary.py`, `tests/test_vendor.py`) and on beethoven-qa itself, and in no
  consumer's gate or CI yet.
