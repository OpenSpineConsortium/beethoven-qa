// Tests of tools/check_workflows.mjs and of the workflow scripts it checks, run by
// `node --test tools/check_workflows.test.mjs` (node's own runner; no dependency).
//
// The checker is tested on small scripts written to a scratch tree with a copy of the checker beside
// them, so it reads them exactly as it reads plugin/workflows/, and nothing of the real plugin is
// touched. The workflow scripts are run in a child process under stand-ins for the runtime's hooks:
// agent() answers from a scripted list, in order, and records every prompt, which is the only way to
// watch a script's control flow without an agent. The body is run by node from a file, as the
// checker parses it: no eval, no new Function, no vm.
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { after, test } from 'node:test'
import { fileURLToPath } from 'node:url'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const CHECKER = path.join(ROOT, 'tools', 'check_workflows.mjs')
const WORKFLOWS = path.join(ROOT, 'plugin', 'workflows')
const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'beethoven-qa-workflow-tests-'))
after(() => fs.rmSync(tmpRoot, { recursive: true, force: true }))
let made = 0
function scratch() {
  const dir = path.join(tmpRoot, String(++made))
  fs.mkdirSync(dir)
  return dir
}

// --- the checker ----------------------------------------------------------------------------------

const META = "export const meta = { name: 'x', description: 'y' }\n"

function checkScript(source) {
  const tree = scratch()
  fs.mkdirSync(path.join(tree, 'tools'))
  fs.mkdirSync(path.join(tree, 'plugin', 'workflows'), { recursive: true })
  fs.copyFileSync(CHECKER, path.join(tree, 'tools', 'check_workflows.mjs'))
  fs.writeFileSync(path.join(tree, 'plugin', 'workflows', 'script.js'), source)
  const run = spawnSync(process.execPath, [path.join(tree, 'tools', 'check_workflows.mjs')], { encoding: 'utf8' })
  return { status: run.status, out: `${run.stdout}${run.stderr}`.trim() }
}

function refused(source, why) {
  const { status, out } = checkScript(source)
  assert.equal(status, 1, out)
  assert.match(out, /^FAIL plugin\/workflows\/script\.js: /, out)
  assert.match(out, why, out)
}

function accepted(source) {
  const { status, out } = checkScript(source)
  assert.equal(status, 0, out)
  assert.match(out, /^ok {3}plugin\/workflows\/script\.js$/, out)
}

test('the shipped workflow scripts pass the checker', () => {
  const run = spawnSync(process.execPath, [CHECKER], { encoding: 'utf8' })
  assert.equal(run.status, 0, run.stdout + run.stderr)
  const lines = run.stdout.trim().split('\n')
  const shipped = fs.readdirSync(WORKFLOWS).filter((n) => n.endsWith('.js'))
  assert.ok(shipped.length >= 3, `only ${shipped.length} shipped script(s)`)
  assert.equal(lines.length, shipped.length)
  for (const line of lines) assert.match(line, /^ok {3}plugin\/workflows\/.+\.js$/)
})

test('a plain script passes', () => {
  accepted(`${META}// a comment\nconst x = new Date(args.now)\nconst y = Math.floor(1.5)\nlog(String(x) + y)\nreturn { x }\n`)
  accepted(`${META.trim()};\nreturn 1\n`)
  accepted(`${META}/* a block\n   comment */\nreturn 1\n`)
  accepted(`${META.trim()} // a trailing comment\nreturn 1\n`)
})

// Finding 7: the runtime decodes escapes before it looks for Date.now, Math.random and an argless
// new Date; the checker reads the text as written, so it refuses every escape that could spell one.
test('a unicode escape is refused wherever it is', () => {
  refused(`${META}const r = Math.r\\u0061ndom()\n`, /line 2: a \\u escape/)
  refused(`${META}const t = D\\u0061te.now()\n`, /line 2: a \\u escape/)
  refused(`${META}// \\u0041 in a comment\nreturn 1\n`, /line 2: a \\u escape/)
  refused(`${META}const s = '\\u{41}'\n`, /a \\u escape/)
})

test('an escaped backslash before a u is not an escape', () => {
  accepted(`${META}const s = 'C:\\\\users'\nreturn s\n`)
})

test('new Date with nothing, a comment or a spread inside the parenthesis is refused', () => {
  refused(`${META}const d = new Date()\n`, /line 2: Date other than new Date\(<arguments>\)/)
  refused(`${META}const d = new Date( )\n`, /line 2: Date other than/)
  refused(`${META}const d = new Date(/* now */)\n`, /line 2: Date other than/)
  refused(`${META}const d = new Date(\n  // now\n)\n`, /line 2: Date other than/)
  refused(`${META}const d = new Date(...[])\n`, /line 2: Date other than/)
  refused(`${META}const d = new Date\n`, /line 2: Date other than/)
  refused(`${META}const t = Date.now()\n`, /line 2: Date other than/)
})

test('new Date with an argument passes', () => {
  accepted(`${META}const d = new Date(args.now)\nreturn d\n`)
  accepted(`${META}const d = new Date(0)\nreturn d\n`)
})

test('Math.random and Math in any form but Math.<name> are refused', () => {
  refused(`${META}const r = Math.random()\n`, /line 2: Math other than Math\.<name>, or Math\.random/)
  refused(`${META}const r = Math['floor'](1)\n`, /line 2: Math other than/)
  refused(`${META}const r = Math?.floor(1)\n`, /line 2: Math other than/)
})

// Finding 8: the runtime reads meta as one `const` declaration whose value is the object literal
// itself, with no reserved key; the checker must not pass a meta it would drop.
test('meta followed by more than the literal is refused', () => {
  refused(`export const meta = { name: 'x', description: 'y' }.name\nreturn 1\n`, /meta is followed by "\.name"/)
  refused(`export const meta = { name: 'x', description: 'y' }, other = 1\nreturn 1\n`, /meta is followed by ", other = 1"/)
  refused(`export const meta = { name: 'x', description: 'y' }\n.name\nreturn 1\n`, /followed on line 2 by "\.name"/)
  refused(`export const meta = { name: 'x', description: 'y' }\n(1)\n`, /followed on line 2 by "\(1\)"/)
  refused(`export const meta = { name: 'x', description: 'y' }\n+ 1\n`, /followed on line 2 by "\+ 1"/)
  refused(`export const meta = { name: 'x', description: 'y' }\n[0]\n`, /followed on line 2 by "\[0\]"/)
  refused(`export const meta = { name: 'x', description: 'y' }\nin args\n`, /followed on line 2 by "in args"/)
})

test('a next-line token that cannot continue an expression starts a statement of its own', () => {
  // A semicolon is inserted before each of these, so meta stays the literal.
  accepted(`${META}!args\nreturn 1\n`)
  accepted(`${META}~args\nreturn 1\n`)
  accepted(`${META}'use strict'\nreturn 1\n`)
  accepted(`${META}{ log('x') }\nreturn 1\n`)
  accepted(`${META}1\nreturn 1\n`)
  accepted(`${META}++args.n\nreturn 1\n`)
})

test('a reserved key in meta is refused', () => {
  refused(`export const meta = { name: 'x', description: 'y', constructor: 1 }\n`, /a reserved key \(constructor\) in meta/)
  refused(`export const meta = { name: 'x', description: 'y', prototype: 1 }\n`, /a reserved key \(prototype\) in meta/)
  refused(`export const meta = { __proto__: { name: 'x', description: 'y' } }\n`, /a reserved key \(__proto__\) in meta/)
  refused(`export const meta = { '__proto__': { name: 'x', description: 'y' } }\n`, /a reserved key \(__proto__\) in meta/)
})

test('a name read through the prototype is not a name', () => {
  // With a null-prototype object the reader never finds Object.prototype's own properties.
  refused(`export const meta = { description: 'y', toString: 'x' }\n`, /meta\.name is not a non-empty string/)
})

// Finding 9: the runtime parses the script as a module and then compiles the body as a script, so
// a form either grammar refuses is refused; and nothing loads a module.
test('import.meta, require and dynamic import are refused', () => {
  refused(`${META}const u = import.meta.url\n`, /loads a module/)
  refused(`${META}const u = import .meta.url\n`, /loads a module/)
  refused(`${META}const fs = require('node:fs')\n`, /loads a module/)
  refused(`${META}const fs = await import('node:fs')\n`, /loads a module/)
})

test('a decorator or member spelling of require is not module loading', () => {
  // A prompt may spell the contract decorator with its parenthesis; only a bare require( is Node's.
  accepted(`${META}const p = 'put @require(lambda x: x > 0) on it'\nreturn p\n`)
  accepted(`${META}const p = 'contracts.require(lambda x: x > 0)'\nreturn p\n`)
  refused(`${META}const fs = require('node:fs')\n`, /loads a module/)
  refused(`${META}const fs = require ('node:fs')\n`, /loads a module/)
})

test('new.target is refused', () => {
  refused(`${META}const t = new.target\n`, /uses new\.target/)
  refused(`${META}const t = new . target\n`, /uses new\.target/)
})

test('a body the module grammar refuses is refused', () => {
  // An HTML-like comment is a comment in a script and an error in a module; the runtime parses as
  // a module first. So is `await` as a name in a nested function, which a script allows.
  refused(`${META}const x = 1\n<!-- a comment\nreturn x\n`, /does not parse as a module/)
  refused(`${META}function f(await) {}\nreturn 1\n`, /does not parse as a module/)
})

test('a sloppy-mode form is refused: the copy is parsed strictly', () => {
  // An octal literal is an error under either grammar only because the wrapper opens with
  // 'use strict'; the message names the grammar parsed first.
  refused(`${META}const n = 010\n`, /does not parse as a script/)
})

test('a syntax error names the script and its own line', () => {
  refused(`${META}const x = )\n`, /plugin\/workflows\/script\.js:2\n/)
})

// --- the workflow scripts, under stand-in hooks ------------------------------------------------

const RUNNER = `import fs from 'node:fs'
import body from './body.mjs'
const responses = JSON.parse(fs.readFileSync('responses.json', 'utf8'))
const args = JSON.parse(fs.readFileSync('args.json', 'utf8'))
const calls = []
const logs = []
const phases = []
let next = 0
async function agent(prompt, opts = {}) {
  calls.push({ label: opts.label || null, phase: opts.phase || null, prompt })
  if (next >= responses.length) throw new Error('no scripted answer for agent ' + JSON.stringify(opts.label) + ' (call ' + calls.length + ')')
  return responses[next++]
}
async function parallel(thunks) {
  const out = []
  for (const thunk of thunks) {
    try {
      out.push(await thunk())
    } catch {
      out.push(null)
    }
  }
  return out
}
async function pipeline(items, ...stages) {
  const out = []
  for (const [i, item] of items.entries()) {
    let value = item
    for (const stage of stages) value = await stage(value, item, i)
    out.push(value)
  }
  return out
}
const budget = { total: null, spent: () => 0, remaining: () => Infinity }
const workflow = () => {
  throw new Error('workflow() has no stand-in')
}
let result = null
let error = null
try {
  result = await body(agent, parallel, pipeline, (t) => phases.push(t), (m) => logs.push(m), args, budget, workflow)
} catch (e) {
  error = e.message
}
process.stdout.write(JSON.stringify({ result, error, calls, logs, phases }))
`

function runWorkflow(name, args, responses) {
  const dir = scratch()
  const text = fs.readFileSync(path.join(WORKFLOWS, `${name}.js`), 'utf8')
  const start = text.indexOf('export const meta = {')
  assert.notEqual(start, -1, `${name}.js has no meta`)
  const body = text.slice(0, start) + text.slice(start + 'export '.length)
  fs.writeFileSync(
    path.join(dir, 'body.mjs'),
    `export default async function __workflow(agent, parallel, pipeline, phase, log, args, budget, workflow) {\n${body}\n}\n`,
  )
  fs.writeFileSync(path.join(dir, 'run.mjs'), RUNNER)
  fs.writeFileSync(path.join(dir, 'responses.json'), JSON.stringify(responses))
  fs.writeFileSync(path.join(dir, 'args.json'), JSON.stringify(args))
  const run = spawnSync(process.execPath, [path.join(dir, 'run.mjs')], { encoding: 'utf8', cwd: dir })
  assert.equal(run.status, 0, run.stderr)
  return JSON.parse(run.stdout)
}

const labelled = (r, prefix) => r.calls.filter((c) => c.label && c.label.startsWith(prefix))

// Finding 6: pytest reads pytest.ini before pyproject.toml, and a conftest.py can deselect tests
// from a hook, so a fix that touched one is a person's to review, not a green gate.
for (const file of ['pytest.ini', '.pytest.ini', 'tests/conftest.py', 'setup.cfg', 'tox.ini']) {
  test(`gate-until-green hands a fix that touched ${file} to a person`, () => {
    const red = { passed: false, failures: [{ lane: 'test', ids: ['tests/test_x.py::test_y'], summary: 'red', excerpt: '' }] }
    const fix = { changed: true, files: [file], note: 'deselected it', needs_person: '' }
    const r = runWorkflow('gate-until-green', {}, [red, fix, { passed: true, failures: [] }])
    assert.equal(r.error, null)
    assert.equal(r.result.green, false)
    assert.equal(r.result.gate_passed, true)
    assert.ok(r.result.stopped && r.result.stopped.includes(file), JSON.stringify(r.result))
  })
}

test('gate-until-green reports green after a fix to ordinary code', () => {
  const red = { passed: false, failures: [{ lane: 'test', ids: ['tests/test_x.py::test_y'], summary: 'red', excerpt: '' }] }
  const fix = { changed: true, files: ['src/pkg/mod.py', 'tests/test_x.py'], note: 'fixed', needs_person: '' }
  const r = runWorkflow('gate-until-green', {}, [red, fix, { passed: true, failures: [] }])
  assert.equal(r.error, null)
  assert.deepEqual(r.result, { green: true, rounds: r.result.rounds })
})

// improve-module's agents, with the answers a well-behaved run gives.
const MODULE = 'src/pkg/mod.py'
const PIN_FILE = 'tests/test_mod_pinned.py'
const SHA_A = 'a'.repeat(64)
const SHA_B = 'b'.repeat(64)
const SHA_C = 'c'.repeat(64)
const map = (extra = {}) => ({
  found: true,
  package: 'pkg',
  dotted: 'pkg.mod',
  exempt: false,
  module_sha256: SHA_A,
  module_dirty: false,
  functions: [{ name: 'double', kind: 'function', public: true, has_contracts: false, io: false }],
  test_files: [],
  notes: '',
  ...extra,
})
const pin = (extra = {}) => ({
  test_file: PIN_FILE,
  tests: [{ name: 'test_double', function: 'double', pins: 'double(2) is 4' }],
  passed: true,
  output: '1 passed',
  module_sha256_after: SHA_A,
  pinned_sha256: SHA_B,
  ...extra,
})
const contract = (extra = {}) => ({
  changed: true,
  files: [MODULE],
  functions: [{ name: 'double', requires: 1, ensures: 1 }],
  decisions: [],
  pinned_passed: true,
  output: '1 passed',
  note: 'contracts added',
  ...extra,
})
const proof = (extra = {}) => ({
  exit_code: 0,
  refused: false,
  targets: 1,
  counterexamples: [],
  uncontracted: [],
  problems: [],
  set_aside: [],
  verdicts: [{ condition: 'src/pkg/mod.py:3', verdict: 'Confirmed over all paths.' }],
  gate_output: 'gate: no counterexample within 20 s per condition',
  pinned_passed: true,
  pinned_sha256: SHA_B,
  ...extra,
})
const counterexample = { function: 'pkg.mod.double', input: 'x = -1', violated: 'result >= 0' }
const repair = (extra = {}) => ({ changed: true, files: [MODULE], note: 'fixed', findings: [], preconditions: [], ...extra })
const skeptic = { claims: [] }
const report = { functions: [], summary: 'ok' }
const improve = (responses, args = {}) => runWorkflow('improve-module', { module: MODULE, ...args }, responses)

// Finding 10: gate --module refuses an exempt module, so nothing could be proven.
test('improve-module stops after the Read phase when the module is exempt', () => {
  const r = improve([map({ exempt: true })])
  assert.equal(r.error, null)
  assert.equal(r.calls.length, 1, JSON.stringify(r.calls.map((c) => c.label)))
  assert.equal(r.result.stopped, 'the module is exempt')
  assert.match(r.result.error, /exempt/)
})

test('improve-module ends without the skeptics and the reporter when the gate refuses the module', () => {
  const refusal = proof({ refused: true, exit_code: 2, targets: 0, gate_output: 'gate: pkg.mod is not a checked module of pkg' })
  const r = improve([map(), pin(), contract(), refusal])
  assert.equal(r.error, null)
  assert.match(r.result.error, /the gate did not check src\/pkg\/mod\.py/)
  assert.equal(r.result.proven, false)
  assert.deepEqual(labelled(r, 'skeptic'), [])
  assert.deepEqual(labelled(r, 'report'), [])
  assert.ok(!r.phases.includes('Vacuity') && !r.phases.includes('Report'), JSON.stringify(r.phases))
})

// Finding 11: a digest is never handed to the agent it checks, and the pinned file is held to its
// own digest in every proving round.
test('improve-module never shows the start sha256 to the agents it checks', () => {
  const r = improve([map(), pin(), contract(), proof(), skeptic, skeptic, report])
  assert.equal(r.error, null)
  assert.equal(r.result.proven, true)
  for (const c of r.calls.slice(1)) assert.ok(!c.prompt.includes(SHA_A), `${c.label} was told the start digest`)
})

// What the pinned tests pass on a pinned file that moved without a named correction is not the
// pinned behaviour, so the result carries no pinned verdict and the reporter is told what happened.
const MOVED = /pinned tests passed on a pinned file that changed without a named correction/

test('improve-module stops when the pinned file changed after the Contract phase', () => {
  const r = improve([map(), pin(), contract(), proof({ pinned_sha256: SHA_C }), skeptic, skeptic, report])
  assert.equal(r.error, null)
  assert.ok(r.result.stopped && r.result.stopped.includes(PIN_FILE), JSON.stringify(r.result))
  assert.match(r.result.stopped, /Contract phase/)
  assert.equal(r.result.pinned_passed, null)
  const [reporter] = labelled(r, 'report')
  assert.match(reporter.prompt, MOVED)
  assert.ok(!reporter.prompt.includes('pinned behaviour holds'), reporter.prompt)
})

test('improve-module stops when the pinned file changed after a repair that named no pinned assertion', () => {
  const r = improve([
    map(),
    pin(),
    contract(),
    proof({ exit_code: 1, counterexamples: [counterexample] }),
    repair(),
    proof({ pinned_sha256: SHA_C }),
    skeptic,
    skeptic,
    report,
  ])
  assert.equal(r.error, null)
  assert.ok(r.result.stopped && r.result.stopped.includes(PIN_FILE), JSON.stringify(r.result))
  assert.match(r.result.stopped, /repair in round 1/)
  assert.equal(r.result.pinned_passed, null)
  assert.match(labelled(r, 'report')[0].prompt, MOVED)
})

test('improve-module keeps the pinned verdict when the pinned file is as it passed', () => {
  const r = improve([map(), pin(), contract(), proof(), skeptic, skeptic, report])
  assert.equal(r.error, null)
  assert.equal(r.result.pinned_passed, true)
  assert.ok(labelled(r, 'report')[0].prompt.includes('pinned behaviour holds: true'))
})

test('improve-module accepts a pinned file changed by a repair that named the pinned assertion', () => {
  const finding = { function: 'double', input: '0', actual: '1', expected: '0', test: `${PIN_FILE}::test_double` }
  const r = improve([
    map(),
    pin(),
    contract(),
    proof({ exit_code: 1, counterexamples: [counterexample] }),
    repair({ findings: [finding] }),
    proof({ pinned_sha256: SHA_C }),
    skeptic,
    skeptic,
    report,
  ])
  assert.equal(r.error, null)
  assert.equal(r.result.stopped, null)
  assert.equal(r.result.proven, true)
  assert.deepEqual(r.result.findings, [finding])
})

test('improve-module stops before the module changes when the Pin phase reports no digest of its file', () => {
  const r = improve([map(), pin({ pinned_sha256: '' })])
  assert.equal(r.error, null)
  assert.equal(r.calls.length, 2)
  assert.equal(r.result.stopped, 'no sha256 of the pinned tests after the Pin phase')
})

// Finding 12: a gate line with no field of its own (a coroutine, a directive) is a repair's, not a
// person's decision.
test('improve-module repairs a gate problem beside the decisions instead of stopping on them', () => {
  const decisions = [{ function: 'read_text', kind: 'public_io', why: 'reads a file' }]
  const problem = 'pkg.mod.fetch is a coroutine: CrossHair would check the un-awaited coroutine object'
  const red = proof({ exit_code: 1, uncontracted: ['pkg.mod.read_text'], problems: [problem] })
  const r = improve([map(), pin(), contract({ decisions }), red, repair(), red, skeptic, skeptic, report], { maxRounds: 2 })
  assert.equal(r.error, null)
  const repairs = labelled(r, 'repair')
  assert.equal(repairs.length, 1, JSON.stringify(r.logs))
  assert.ok(repairs[0].prompt.includes(problem))
  assert.ok(!r.logs.some((l) => l.includes("only the public I/O functions left for a person's decision")), JSON.stringify(r.logs))
  // The second round repeats the first; the stop names what repeated, and there was no counterexample.
  assert.equal(r.result.stopped, 'the same functions without contracts and gate problems two rounds running')
})

test('improve-module names the counterexamples when those are what repeated', () => {
  const red = proof({ exit_code: 1, counterexamples: [counterexample] })
  const r = improve([map(), pin(), contract(), red, repair(), red, skeptic, skeptic, report], { maxRounds: 2 })
  assert.equal(r.error, null)
  assert.equal(r.result.stopped, 'the same counterexamples two rounds running')
})

test('improve-module still stops on the decisions alone when the gate reports nothing else', () => {
  const decisions = [{ function: 'read_text', kind: 'public_io', why: 'reads a file' }]
  const red = proof({ exit_code: 1, uncontracted: ['pkg.mod.read_text'] })
  const r = improve([map(), pin(), contract({ decisions }), red, skeptic, skeptic, report])
  assert.equal(r.error, null)
  assert.deepEqual(labelled(r, 'repair'), [])
  assert.match(r.result.stopped, /only the public I\/O functions left for a person's decision/)
})

// Finding 13: a function set aside under [tool.beethoven-qa.unproven] passes the gate unproven, and
// the reviewer must see it as that, never as "no counterexample".
const SET_ASIDE = [{ name: 'pkg.mod.read_text', reason: 'reads a file' }]
const SET_ASIDE_RULE = /not proven: contracts checked at run time only \(<reason>\)/

test('improve-module carries the set-aside functions into the result, the log and the report', () => {
  const r = improve([map(), pin(), contract(), proof({ set_aside: SET_ASIDE }), skeptic, skeptic, report])
  assert.equal(r.error, null)
  assert.equal(r.result.proven, true)
  assert.deepEqual(r.result.set_aside, SET_ASIDE)
  assert.ok(r.logs.some((l) => l.includes('pkg.mod.read_text (reads a file)')), JSON.stringify(r.logs))
  const [reporter] = labelled(r, 'report')
  assert.ok(reporter.prompt.includes('pkg.mod.read_text'))
  assert.match(reporter.prompt, SET_ASIDE_RULE)
})

test('verify-module carries the set-aside functions into the result, the log and the review', () => {
  const proven = {
    dotted: 'pkg.mod',
    exit_code: 0,
    refused: false,
    targets: 1,
    counterexamples: [],
    set_aside: SET_ASIDE,
    verdicts: [{ condition: 'src/pkg/mod.py:3', verdict: 'Confirmed over all paths.' }],
    gate_output: 'gate: no counterexample within 20 s per condition',
  }
  const r = runWorkflow('verify-module', { module: MODULE }, [proven, { functions: [] }])
  assert.equal(r.error, null)
  assert.equal(r.result.proven, true)
  assert.deepEqual(r.result.set_aside, SET_ASIDE)
  assert.ok(r.logs.some((l) => l.includes('pkg.mod.read_text (reads a file)')), JSON.stringify(r.logs))
  const [reviewer] = labelled(r, 'contracts of')
  assert.ok(reviewer.prompt.includes('pkg.mod.read_text'))
  assert.match(reviewer.prompt, SET_ASIDE_RULE)
})
