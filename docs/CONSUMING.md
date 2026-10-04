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
- **hpcworker's framework** (Python 3.6 standard library only, for the login node's CentOS 7) and
  **the browser and desktop editions** (TypeScript): the repository half only, the pre-push gate,
  the required check, auto-merge and rulesets as code, with their own language's checks. No copy,
  no icontract, no new dependency.

## What an adopted repository holds

```
<repo>/
  pixi.toml, pixi.lock              its environment: the verification stack beside its own needs
  pyproject.toml                    [tool.beethoven-qa] and the tool tables for its own package
  src/<package>/                    its code
  tests/conftest.py                 typeguard's import hook for its package
  vendor/beethoven-qa/              the pinned copy; never edited by hand
    ORIGIN                          where the copy came from and the digest of what it holds
    pyproject.toml  src/beethoven_qa/  plugin/  .claude-plugin/marketplace.json
    kit/                            the kit as the release has it, which vendor-check compares
  .githooks/pre-push                kit: the gate before every push
  .github/workflows/automerge.yml   kit: auto-merge armed on every pull request opened ready
  .github/workflows/rulesets.yml    kit: .github/rulesets/*.json applied to GitHub
  .claude/hooks/session-start.sh    kit: pixi and the locked environment in a cloud session
  .github/workflows/ci.yml          its own: the gate lane (step 5) and any lanes of its own
  .github/rulesets/main.json        its own: names its required checks
  .claude/settings.json             its own: wires the session-start hook, worktree.baseRef
```

The kit files are byte for byte the release's; `vendor-check` fails one that differs. A change to
one is made in beethoven-qa and arrives with the next release.

## 1. Take the copy from a clean, tagged checkout

Clone beethoven-qa beside the consuming repository, so that `../beethoven-qa` names it from the
consumer's root. Then, from the consumer's root:

```sh
git -C ../beethoven-qa fetch --tags
git -C ../beethoven-qa checkout v1.0.0          # the release to take; a detached HEAD is fine
git -C ../beethoven-qa status --porcelain       # must print nothing
pixi run --locked --manifest-path ../beethoven-qa/pixi.toml python -m beethoven_qa vendor --into . --kit
```

- **Where it runs from does not matter.** beethoven-qa's environment puts
  `$PIXI_PROJECT_ROOT/src` on `PYTHONPATH`, an absolute path, so the command imports the checkout's
  own package wherever it is started. Measured 2026-10-04: started from a throwaway consumer's root,
  it wrote 22 files and the kit and exited 0. (An earlier draft set the relative path `src`; started
  from the consumer's root, that stopped with `No module named beethoven_qa`.)
- **`--locked`** takes beethoven-qa's environment from its lock as it is. A re-solve would rewrite
  `pixi.lock`, and the then-dirty checkout would be refused anyway.
- **What it writes.** Into `vendor/beethoven-qa/`: the files of the release commit under
  `pyproject.toml`, `src/beethoven_qa/`, `plugin/` and `.claude-plugin/`, read from git's own
  objects (never the working tree, so no `.pixi/`, no caches, no line-ending conversion and no
  edit git was told to ignore), each with the mode the commit gives it; the four kit files under
  `kit/`; and last `ORIGIN`:

  ```
  repository: OpenSpineConsortium/beethoven-qa
  ref: v1.0.0                       # the tag exactly at HEAD, or "untagged"
  commit: <the full 40-character SHA>
  tree_sha256: <sha256 over the sorted (path, sha256 of the file) pairs>
  files: <how many files the copy holds, ORIGIN not counted>
  ```

  With `--kit` it also writes the four kit files at their own paths in the consumer, with the
  executable bit the commit gives them; without it the live kit files are left as they are, and
  `vendor-check` fails while one is missing or differs. The copy is itself a plugin marketplace (its
  `.claude-plugin/marketplace.json` lists `./plugin`), which is what step 7's image registers.
- **What it refuses, before it writes anything.** The beethoven-qa checkout with any uncommitted
  change, an untracked file included (a copy is of a commit); a source that is not the root of a
  beethoven-qa checkout (run from a consumer, the copy refuses to copy itself); a target that is not
  the root of a git checkout, or is beethoven-qa itself; a link anywhere on a path it would write
  (`vendor/`, `vendor/beethoven-qa/`, `ORIGIN`, any file of the copy, `.github/`, `.claude/`, a kit
  file); a file where the release has a directory, or the reverse; an existing copy that holds a
  link; a link or a submodule in the release itself; and any existing file it would replace that git
  does not track as committed and unchanged, asked of git file by file, so a `.gitignore` entry or
  `status.showUntrackedFiles=no` cannot hide one. A copy that still matches its ORIGIN, and kit
  files equal to that copy's, are rewritten even uncommitted, since nothing in them is lost: taking
  another tag before the first commit just works.
- **It never deletes.** A file the old copy had and the new release does not is left in place and
  listed with one line for the person to run, `rm -- <files> && git add -u -- vendor/beethoven-qa`,
  which works whether or not the old copy was committed; `vendor-check` fails until it is gone, so
  nothing leaves a repository without a reviewed commit.
- **An untagged commit is not refused**; ORIGIN then says `ref: untagged`. Take tagged releases
  only; a reviewer who sees `untagged` asks why.

| Exit | Meaning |
| - | - |
| 0 | The copy (and with `--kit` the kit) is written and nothing of an older copy is left over |
| 1 | The copy is written, but the copy already there held files this release dropped; nothing was deleted; run the printed line, then `vendor-check` |
| 2 | Refused, for one of the reasons above, before anything was written; or git itself failed |
| 3 | Stopped part way by an error of the file system (a full disk, a permission, a link that appeared after the checks); `git status` shows what was written |

Review the result (`git status`, `git diff --stat`, `cat vendor/beethoven-qa/ORIGIN`) and commit the
copy, its ORIGIN and the kit files together, the message naming the release.

## 2. The consumer's `pixi.toml`

The same verification stack as beethoven-qa's own `pixi.toml` (copy its dependency lines; they are
repeated here as they stand at 1.0.0), the repository's own dependencies beside them, the copy on
`PYTHONPATH`, and the five lanes of the gate:

```toml
[workspace]
name = "<repo>"
version = "0.0.0"
channels = ["conda-forge"]
platforms = ["linux-64"]
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
# The repository's own dependencies follow here.

[pypi-dependencies]
crosshair-tool = ">=0.0.60"

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
vendor-check = "python -m beethoven_qa vendor-check"
gate = { depends-on = ["lint", "typecheck", "test", "symbolic", "vendor-check"] }
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

From then on the lock changes only on purpose, by pixi, in a change that says so; never by hand,
never by a stray `pixi install` or `pixi update`, and every `pixi run` carries `--locked` (without
it pixi re-solves a manifest that drifted from its lock, without a word). Measured 2026-10-04: this manifest in a throwaway
consumer locked, installed and ran `pixi run --locked gate` green through all five lanes, and
`pixi run --manifest-path <consumer>/pixi.toml python` started from another directory imported both
the consumer's package and the copy; the gate then failed, as it should, on a
public function without contracts and on a wrong body (`resume_at(0, 0, 1) (which returns 0)`).

The gate's symbolic lane runs one CrossHair process per contracted function and per contracted
class, as many at a time as the allocation's cores; `BEETHOVEN_QA_JOBS` lowers the count on a
laptop busy with other work and never raises it. Both of the gate's checks always run, so an
uncontracted function in one module does not stop CrossHair proving the others. For one module,
`python -m beethoven_qa gate --module <pkg.mod>`; with `--report-all` it prints every condition's
verdict and fails on a precondition no input can meet. Beyond a missing contract, the gate fails
on whatever would keep a function out of CrossHair's sight: a `.py` file under the package that is
not one of its modules (a directory without `__init__.py`), a module that fails or exits while
being imported, a `# crosshair:` directive, a public coroutine (CrossHair would check the coroutine
object, not its result), contracts on a pydantic model (CrossHair does not construct one), and a
package with nothing to prove.

A function CrossHair must not run, because it reads, writes, asks the clock or the environment or
starts a process, or cannot run, because of a fault of CrossHair's own that was measured, is named
in the table below under `unproven` with its reason, never marked with a `# crosshair: off` comment
(the gate refuses every directive: a comment hides what is not proven, a table entry says so on
every run). The function keeps its contracts, which the tests check at run time; the logic it needs
lives in contracted functions CrossHair proves. An entry that names no contracted function fails
the gate, so a renamed or removed function cannot leave an entry behind.

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

# Functions CrossHair never runs, each with its reason; the gate prints them on every run.
[tool.beethoven-qa.unproven]
"<package>.store.load" = "reads the account list from disk"
```

The table is read and validated once (`GateConfig` in `src/beethoven_qa/gate.py`): a repository
without it is a configuration error; `package` is one top-level name, no dots; `src` is a directory
inside the repository; every `exempt` entry is the package or one of its modules, so the copy can
never be exempted from a consumer's gate; every `unproven` key is a function of the package and
every reason says something; no other key is accepted. The gate imports every module
of the package to find its functions, so a module that does work at import time does it during the
gate, and one that calls `sys.exit` at import fails it: guard a command line's call with
`if __name__ == "__main__":`.

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
  # The pixi that produced the committed lock, fetched from its release asset by name.
  PIXI_VERSION: 0.81.0

jobs:
  gate:
    name: gate (lock → lint, typecheck, tests, CrossHair/Z3, vendor-check)
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4
      - name: install pixi
        run: |
          curl -fsSL -o /usr/local/bin/pixi \
            "https://github.com/prefix-dev/pixi/releases/download/v${PIXI_VERSION}/pixi-x86_64-unknown-linux-musl"
          chmod 0755 /usr/local/bin/pixi
          pixi --version
      - name: reconstruct the environment from the lock
        # --locked refuses to re-solve: a manifest that drifted from its lock fails here, on purpose.
        run: pixi install --locked
      - name: the pinned copy of beethoven-qa matches its ORIGIN, and the kit is in place
        run: pixi run --locked vendor-check
      - name: lint
        run: pixi run --locked lint
      - name: typecheck
        run: pixi run --locked typecheck
      - name: tests (typeguard enclave)
        run: pixi run --locked test
      - name: symbolic verification (CrossHair + Z3)
        run: pixi run --locked symbolic
```

`.github/rulesets/main.json` names that job as a required check, its `context` exactly the job's
`name` (a required check that no job reports under that exact name keeps a pull request waiting),
beside any lane of the repository's own (beethoven-agents adds its image lane):

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
        "strict_required_status_checks_policy": false,
        "do_not_enforce_on_create": false,
        "required_status_checks": [
          {
            "context": "gate (lock → lint, typecheck, tests, CrossHair/Z3, vendor-check)",
            "integration_id": 15368
          }
        ]
      }
    }
  ]
}
```

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
2. **In the consumer**, on a branch: step 1 again with the new tag. Exit 1 means the release dropped
   files; run the printed line. Then `pixi run --locked vendor-check` and `pixi run --locked gate`,
   and, with the beethoven-qa checkout beside it,
   `pixi run --locked python -m beethoven_qa vendor-check --source ../beethoven-qa`.
3. If the release changed the dependency lines of beethoven-qa's `pixi.toml`, make the same change
   in the consumer's, run `pixi lock` on purpose, and say so in the same change.
4. One commit for the copy, its ORIGIN, the kit files and any lock change, its message naming the
   old and the new release; then a pull request, ready for review, which merges when the gate is
   green. A red gate after an update is reported red and fixed at its cause, in the consumer's code
   or upstream in beethoven-qa, never in the copy.

## What is never done

- **Hand-editing the copy** or a kit file. `vendor-check` fails both (measured 2026-10-04: one added
  line in the copy, and one in `.githooks/pre-push`, each failed it), and a kit hook that lost its
  executable bit. It is a check in the consumer, not a proof: an edit that also rewrites `ORIGIN`
  passes it, and only `vendor-check --source <beethoven-qa checkout>`, which rebuilds the tree from
  the commit ORIGIN names and checks the tag, catches that (measured the same day, in
  `tests/test_vendor.py`). A reviewer who sees ORIGIN change in a pull request that takes no new
  release asks why. A change is made in beethoven-qa, released, and taken with step 1. `/beethoven-qa:gate-until-green` leaves the copy
  alone and says so, and the stream agent never edits it.
- **Linting or type-checking the copy as the consumer's code.** `lint` names `src tests`, ruff
  excludes `vendor`, `typecheck` names `src`, the typeguard hook names the consumer's package, and
  `exempt` cannot name the copy. The copy is held to all of these by beethoven-qa's own gate, which
  a commit clears before it reaches `main` and a tag.
- **Skipping `vendor-check`.** It is a lane of `pixi run --locked gate` and a step of the CI gate lane.
  `SKIP_GATE=1` skips the pre-push gate only knowingly, with the reason in the commit, and CI runs
  every lane regardless.
- **Taking a copy from a dirty, untagged or vendored checkout**, or running `pixi update` or a plain
  `pixi install` to change a lock.
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
