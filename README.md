# beethoven-qa

How code for the Beethoven engine is written and proven before it may run. It holds the definition
of done, the contract decorators, the CrossHair/Z3 gate as a command any repository runs on its own
package, the pinned copy that consuming repositories carry, the code standards that block a change
whose new code breaks them (readability, design, duplication, coverage, security, supply chain;
`docs/STANDARDS.md`), the boundary check of the Beethoven interface standard, the repository kit
they share, and the Claude Code plugin through which a session writes new code to that bar and
brings existing code up to it. What another repository may take from this one is
`interface/INTERFACE.json` (`interface/README.md`).

It is one of three repositories: this one, `beethoven-agents` (the nested agentic framework) and the
research pipeline. They depend in one direction only, qa <- agents <- research, and nothing here
imports either of the others.

Beethoven runs Claude Code and is not an Anthropic product; "Claude" and "Claude Code" are
Anthropic's marks, used here to say what it runs; "Open OnDemand" is the mark of its publisher.
Vendor: Gregory Schwing, trading as OpenSpineConsortium. Private; the code is proprietary.

## Use

```sh
pixi install --locked     # the exact environment from the lock
pixi run --locked gate    # lint, typecheck, tests, boundary, CrossHair + Z3 over the package, the code standards
pixi run --locked python -m beethoven_qa standards readability --base origin/main   # one group
pixi run hooks            # the pre-push hook runs the same gate before anything leaves the machine
```

In a consuming repository (`docs/CONSUMING.md` has the whole of it):

```sh
python -m beethoven_qa vendor --into . --ref <tag> --kit   # the release, read from GitHub with your gh sign-in
python -m beethoven_qa vendor-check                       # at the consumer's root: the copy and the kit are untouched
                                                          # (`--source <qa>` only from this repository's own environment)
python -m beethoven_qa gate                               # the consumer's own package, as [tool.beethoven-qa] names it
python -m beethoven_qa boundary                           # the interface standard's rules, against the copy's manifest
```

The Claude Code plugin, on a laptop:

```
/plugin marketplace add OpenSpineConsortium/beethoven-qa
/plugin install beethoven-qa@beethoven-qa
```

It brings the skills `definition-of-done`, `write-verified-module`, `improve-existing-module`,
`review-contracts` and `code-standards`, the `stream` agent, the workflows `/beethoven-qa:gate-until-green`,
`/beethoven-qa:verify-module` and `/beethoven-qa:improve-module`, and a hook that says at once what
the code standards would block in each file a session edits (from 4.0.0).

## The definition of done

A module is done when it resolves inside `pixi.lock`, carries `icontract` contracts on every public
function, types its boundaries with `pydantic` and its hot paths with `beartype`, and CrossHair with
Z3 finds no counterexample within the declared bound. `CLAUDE.md` states it in full, with what was
measured while building the gate.
