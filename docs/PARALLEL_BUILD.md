# Building in parallel: worktrees and workflows

How a repository that runs the beethoven-qa gate is built in parallel: a checkout of its own for
every parallel editor, and the plugin's workflow scripts for orchestration that repeats. This is the
build half of beethoven-research's `docs/PARALLEL.md` (its sections 1, 2, 3 and 5), taken at the
split on 2026-10-04. Every fact there was checked on 2026-10-04 against the Claude Code pages greg
named (worktrees, workflows, agent-teams, cross-session-messaging) and the overview page that
compares them (`code.claude.com/docs/en/agents`), after his ask of 02:37Z ("incorporate worktrees
and workflows for bootstrapping and writing the parallel parts of the ecosystem"). What the split
changed is where things live: the `stream` agent and the workflows now ship in this repository's
plugin. The plugin facts added here (names, the agent fields a plugin keeps, a plugin's default
settings) were checked the same day against the plugin components page. Where a sentence says "not
yet measured", nothing here has run it.

Agent teams, the engine's masters and satellites, messaging between sessions and the no-orphans
rules are the engine's, not the build's: they belong to beethoven-agents, which carries
beethoven-research's `docs/PARALLEL.md` whole (its section 4 is the engine's parallel parts).

Beethoven runs Claude Code and is not an Anthropic product; "Claude" and "Claude Code" are
Anthropic's marks, used here to say what it runs.

## 1. The five ways Claude Code runs work in parallel, and which one is which here

| Way | What it is (the overview page) | Here |
| - | - | - |
| Subagents | workers inside one session, own context, return a summary | inside a build session and inside a `-p` step; the only fan-out a `-p` step has. The plugin's `stream` agent is one, in a worktree of its own |
| Agent view | one screen to dispatch and watch background sessions on a machine | a person's laptop only |
| Agent teams | a lead session supervising peer sessions | not a way the build runs; beethoven-agents (above) |
| Projects | threads in the cloud or over Remote Control, from claude.ai/code | the build itself (the Beethoven project's threads) |
| Dynamic workflows | a script that runs many subagents and cross-checks them; runs in `-p` too | repeatable orchestration, shipped in `plugin/workflows/` and run as `/beethoven-qa:<name>` |

One tool supports them without being a way to run agents, and it is this file's subject: worktrees
(a separate git checkout per parallel editor). The other the overview names, cross-session
messaging, is beethoven-agents'.

## 2. The build in parallel: worktrees

A git worktree is a second working directory on the same repository, with its own branch. Two
sessions editing the same checkout overwrite each other; two sessions in two worktrees never do.

**What each repository sets.** The plugin cannot set these for it: a plugin's own `settings.json`
takes only the keys `agent` and `subagentStatusLine` and drops every other (plugin components page,
"Default settings"). `docs/CONSUMING.md` step 6 has the file.

- `.gitignore` ignores `.claude/worktrees/`, where Claude Code creates them by default.
- `.claude/settings.json` sets `worktree.baseRef` to `"head"`: a new worktree branches from the
  current HEAD of the checkout it was made from, so a stream on `claude/project-thread-...` carries
  that branch's commits. The default, `"fresh"`, would branch from `origin`'s default branch and
  lose them. Inside a worktree, `"head"` is that worktree's HEAD.
- No `.worktreeinclude`: it copies gitignored files such as `.env` into new worktrees, and a
  repository of ours keeps no such file by rule (secrets never enter it).

**The stream agent**

- `plugin/agents/stream.md` is a subagent definition with `isolation: worktree`, loaded as
  `beethoven-qa:stream`: a plugin agent is named `<plugin>:<name>`, and `isolation` is one of the
  frontmatter fields a plugin agent keeps, with `"worktree"` its only value (plugin components page,
  "Frontmatter fields in plugin agents"; the same page lists `permissionMode`, `hooks`, `mcpServers`
  and `initialPrompt` as ignored there, and the stream sets none of them). Every stream spawned from
  it gets a worktree of its own, automatically removed when it ends without changes and kept (and
  later swept) when it has changes. Ask for "a stream for X" or "use worktrees for your agents".
- A stream checks that it is in a linked worktree and changes nothing if it is not, notes the commit
  it started from, runs `pixi install --locked`, makes the one change it was asked for and nothing
  beyond it, runs `time pixi run --locked gate`, commits on its branch only when the prompt gives it
  the commit trailers, never pushes and never merges, and reports as data: the worktree, the
  branch, the start commit, the commit if any, the files, the gate's verdict with its wall time,
  and what it could not do. The conversation that started it merges the branch and pushes through
  the pre-push gate.
- Where the plugin is not installed (a cloud project thread, `docs/CONSUMING.md` step 7), the same
  instructions are the file `plugin/agents/stream.md` (`vendor/beethoven-qa/plugin/agents/stream.md`
  in a consumer), and a workflow agent takes a worktree of its own with `isolation: 'worktree'`.

**What a stream must know**

- A worktree is a fresh checkout: `.pixi/` is not there. `pixi install --locked` first (the pixi
  cache is shared across worktrees, so it is minutes, not a new solve); `pixi run --locked gate`
  then works in the worktree, and so does the pre-push gate, because `core.hooksPath` is repository
  configuration shared through the one `.git` directory. Git hands a hook run from a linked
  worktree the path of that worktree's own git directory in `GIT_DIR`, and a git that inherits it
  acts on the main repository whatever `-C` names; the hook unsets it, with the rest of
  `git rev-parse --local-env-vars`, before the gate runs, so the throwaway repositories the tests
  make are their own (measured 2026-10-04, git 2.43: without that, the vendor tests' commit and
  `v1.0.0` tag landed on the worktree's branch and in the repository). The vendor tests and
  `vendor.py` scrub the same variables themselves for every process they start (git, the printed
  cleanup line through a shell, the Python helpers), so the gate's own tests were safe in a
  worktree hook even without the unset, which stays as the belt (measured the same day: a push
  from a worktree whose hook ran only `tests/test_vendor.py`, unsetting nothing, was accepted and
  left the main checkout untouched). The hook gates the commit
  that is pushed, not whatever is checked out: every ref it is given must be the worktree's HEAD
  and `git status --porcelain` must print nothing (untracked files count), or the push is refused
  and the message says so; `SKIP_GATE=1` pushes knowingly (`tests/test_hooks.py` pins both).
  Every task runs as `pixi run --locked <task>`: a plain `pixi run` re-solves a manifest that
  drifted from its lock and rewrites `pixi.lock` without a word.
- In a consuming repository the pinned copy is tracked, so every worktree has it and every stream's
  gate is the same pinned gate. A stream never edits `vendor/beethoven-qa/`; `vendor-check` fails a
  copy edited by hand.
- `${CLAUDE_PROJECT_DIR}` in a hook command stays the main checkout; the worktree path arrives as
  `cwd` in the hook's JSON. The session-start hook (the kit's `.claude/hooks/beethoven-qa-session-start.sh`) runs
  against the main checkout, which is what we want (it sets `core.hooksPath` once for every
  worktree).
- A subagent in a worktree does not load the worktree's own `CLAUDE.md` or `.claude/rules/`; it
  carries the instruction files of the conversation that started it.
- Claude Code refuses, inside an isolated session, any edit of a path in the main checkout and any
  git command that would act on it (`git -C`, `GIT_DIR`, a `cd` into it). A refused command is
  rewritten as plain separate commands, never worked around.
- "Yes, don't ask again" answered in a worktree is saved to the main checkout's
  `.claude/settings.local.json` and applies to every worktree.

**A person's own parallel sessions**

```sh
claude --worktree stream-a        # .claude/worktrees/stream-a on branch worktree-stream-a
claude --worktree "#12"           # a worktree on pull request 12's head, at .claude/worktrees/pr-12
claude --worktree stream-a --resume   # back into a kept worktree (printed on exit)
git worktree list                 # what exists
git -C .claude/worktrees/stream-a status --porcelain   # must print nothing before a removal
git worktree remove .claude/worktrees/stream-a      # only when merged and clean; never --force
```

`git worktree remove` refuses a worktree with modified or untracked files, and that refusal is the
point: `--force` would delete that work with no copy left. A worktree that still has changes is
kept, and a person is told which one and what is in it; they commit or discard the work
themselves. "Merged" is not enough on its own: a branch with no commits of its own is listed by
`git branch --merged` even when its worktree holds uncommitted work, so check the worktree's own
`git status` first.

On exit, Claude Code offers to keep or remove a worktree that has work in it and removes a clean
unnamed one by itself. `claude -p --worktree ...` never cleans up: it leaves the worktree and the
lock it took, so a non-interactive run removes its own worktree, when it is clean, with
`git worktree unlock` and then `git worktree remove`, and keeps it and reports it when it is not.
Deleting a worktree someone else made is a deletion outside our own run directories and is not
done.

## 3. The build in parallel: dynamic workflows

A dynamic workflow is a JavaScript script that orchestrates subagents; the script holds the loop,
the branching and the intermediate results, so the conversation holds only the final answer. The
`workflow-authoring` bundled skill is the reference for writing one; what matters here:

- **The build's workflows ship in this plugin**, as `plugin/workflows/<name>.js`, and run as
  `/beethoven-qa:<meta.name>` in any session with the plugin enabled: a plugin's workflows are
  namespaced by the plugin name (workflows page, "Distribute a workflow in a plugin"). Before the
  split they were saved in beethoven-research's `.claude/workflows/` and ran as `/<name>`; a
  repository's own saved workflows still live there and run that way (after `/reload-skills` in a
  session already open). The file starts with a literal
  `export const meta = { name, description, phases? }`; the body uses `agent()`, `pipeline()`,
  `parallel()`, `phase()`, `log()` and the `args` global; `Date.now()`, `Math.random()`, a bare
  `Date()` and a no-argument `new Date()` throw, so that a relaunched run replays the same `agent()`
  calls from cache. A script with `import()` fails before it starts. Claude Code refuses to save
  through a symlinked `.claude`, `.claude/workflows` or target file. `node tools/check_workflows.mjs`
  checks the plugin's scripts against these rules without running an agent: the meta is read as a
  pure literal (no spread, computed key, method, call or name standing for a value, and no
  `__proto__`, `constructor` or `prototype` key) with a non-empty `name` and `description`, and
  nothing but a `;`, a line break and a new statement, or the end of the text may follow it;
  `Date` may appear only as `new Date(<arguments>)` (an empty parenthesis, a comment or a spread
  inside it is no argument) and `Math` only as `Math.<name>` other than `random`, whatever the
  spacing, brackets or optional chaining, and every `\u` escape is refused so an escaped spelling
  cannot hide one; no `import(`, `import.meta`, `require(` or `new.target`; and the whole, wrapped
  as the runtime wraps it, parses under both grammars the runtime uses (it reads the meta as a
  module and runs the body as a strict-mode script).
  `node --test tools/check_workflows.test.mjs` runs the checker against scripts it must refuse and
  accept, and the three workflow scripts under stand-in hooks, so each stops where its comments
  say it stops; CI runs both.
- **In a cloud project thread** the plugin is not installed (a cloud session does not add a
  repository's marketplaces; `docs/CONSUMING.md` step 7). Run a workflow there with the Workflow
  tool's `scriptPath` set to its script, `plugin/workflows/<name>.js` in this checkout or
  `vendor/beethoven-qa/plugin/workflows/<name>.js` in a consumer, with the same `args`.
- **Three ship here.** `gate-until-green` runs `pixi run --locked gate`, fixes what fails one round
  at a time (one fixer, because parallel fixers would edit the same files) and gates every fix again
  before it reports, so its failures are never those of the code before the last edit. It stops
  when green, when the fixer changes nothing, when the same failures (keyed on what the tools print:
  test node ids, file:line of a finding) survive two fixes, when a fix needs a person (a change to
  the gate's configuration, pytest's own files and any `conftest.py` included, the lock, the
  pinned copy, CI, a ruleset or a hook is gated once more and then handed over), or when its gate
  runs are used up; in a consumer the gate includes
  `vendor-check`, and the fixer leaves the copy alone and says so. `verify-module` is the SpecPylot
  loop of the definition of done (step 6): run the gate on that one module (`gate --module ...
  --report-all`, which refuses a module outside the package), feed each counterexample to a repair
  agent, repeat until proven, then write the contracts out in plain words, with each condition's
  verdict and every function set aside under `[tool.beethoven-qa.unproven]` named with its reason,
  never as proven, for a human to review. `improve-module` brings one existing
  module up to the definition of done without changing what it does: it maps the module, pins what
  it does today with characterisation tests that must pass on the unchanged code, adds contracts and
  the layering with pure logic apart from I/O, proves them on the module with one repair per round,
  holds the Pin phase to the module's sha256 at the start of the run and the pinned tests to their
  own sha256 in every proving round (only a repair that names the pinned assertion it corrected may
  move it), never shows a digest to the agent it checks, stops after the Read phase on an exempt
  module and ends without the skeptics when the gate refuses the module, has two independent
  skeptics look for contracts that promise nothing, and reports the contracts in plain
  words; a change only a person may choose (a public function that itself does I/O, a precondition
  that would refuse an input the code accepts today) is listed, not made, and nothing is committed.
  Edits are one at a time in all three; only `improve-module`'s skeptics run side by side, and they
  only read. All take `args`: `verify-module` and `improve-module` need
  `{ module: "src/<package>/<name>.py" }`, and `{ maxRounds: 0 }` runs no agent and is the syntax
  check. Every pixi command in their prompts is `--locked`, and every fixer is told the moves that
  make a gate green without making the code right. None of the three has yet run against a real
  failure: not yet measured.
- **Limits** (the runtime's, not advice): at most 16 concurrent agents by default, fewer in a
  CPU-limited container, changed with `CLAUDE_CODE_WORKFLOW_MAX_CONCURRENT_AGENTS` (1 to 256,
  Claude Code 2.1.269 or later); 4096 items per `parallel()` or `pipeline()` call, refused above;
  1000 agents per run. `workflowSizeGuideline` (a repository's `.claude/settings.json`;
  beethoven-research set `"large"`) is advice to Claude when it writes a script, not a cap.
- **Resume.** A stopped run relaunches from its saved results within the same session; the first
  agent whose prompt changed, and every agent after it, runs again. A cloud session keeps the
  results with its conversation history. A run started fresh has nothing to resume.
- **Cost.** Every agent counts against the plan's usage limits of the account the session runs on.
  An interactive run pauses on a usage limit and resumes when it rolls (twice at most); a `-p` run
  does not pause, the affected agent fails, and the orchestrator's pacing (beethoven-agents) owns
  the retry.
- **Permissions.** In an interactive session the first launch asks once (auto mode) or every run
  (manual). In `-p` and the Agent SDK nothing asks: the Workflow tool call goes through the
  session's permission rules like any tool, so a `dontAsk` step denies it unless `Workflow` is in
  its allow rules. A workflow agent's prompt is text the script computed, not a request from the
  person, and the auto-mode classifier judges that agent's actions accordingly.
- **The `ultracode` switch** (`/effort ultracode`, `claude --effort ultracode`, or the `ultracode`
  settings key) makes Claude plan a workflow for every substantive task; it also lifts the
  concurrent-subagent limit and the large-run warning. The `ultracode` *keyword* in a prompt is an
  opt-in only when a person typed the prompt; through `-p`, a scheduled task, a webhook or a relayed
  comment it starts nothing. Whether the settings key makes a `-p` step plan workflows on its own is
  not yet measured.
- **Prompt cache in a fan-out.** Agents with the same model, effort, agent type, tools, schema and
  working directory share a cached prefix; Claude Code holds all but the first for up to
  `CLAUDE_CODE_WORKFLOW_PREFIX_STAGGER_MS` (5000) so they read it. Their cache holds five minutes on
  a subscription; `subagentPromptCacheTtl: "1h"` lengthens it and is billed as an hour's write,
  which the engine never pays (decision D1: each person's own flat-rate subscription, usage credits
  never used), so it is not set.

## 4. Before writing a parallel part, check

1. Which of the five ways is it, and is that way available where it runs (`-p` has subagents and
   workflows; agent view needs a person's terminal; teams are beethoven-agents')?
2. Who edits which files? Separate editors get separate worktrees or separate clones; never two
   editors in one checkout.
3. Where do the results go: a script variable, a branch in the editor's own worktree, or the
   conversation? The conversation is the last resort.
4. What bounds it: `maxRounds`, the agent caps, the wall time, the window pacing?
5. What is logged when the bound cuts something (never a silent cap)?
6. How does it end, and who removes what it made (its own worktrees, its own temp, nothing else)?
7. What is its progress signal, and when does it commit? For the build it is the gate's verdict and
   the pinned tests; a long-running piece of work declares its own before it starts and commits
   after each meaningful unit (a stream only when its prompt gives the commit trailers; the three
   workflows commit nothing, and `improve-module` ends by saying what to commit, in which pieces).
