#!/usr/bin/env node
// Checks the plugin's workflow scripts the way Claude Code will read them, before any of them runs
// (CI runs it; so can anyone: `node tools/check_workflows.mjs`). The rules come from the workflows
// page (code.claude.com/docs/en/workflows, read 2026-10-04) and the guard the runtime installs
// (read from Claude Code 2.1.289 on 2026-10-04):
//
// - `export const meta = {` is the first statement (comments may precede it), the object is a pure
//   literal (strings without interpolation, numbers, booleans, null, arrays, objects with plain
//   keys; no spread, no computed key, no method, no name standing for a value, no call) with a
//   non-empty string `name` and `description`, none of its keys is `__proto__`, `constructor` or
//   `prototype`, and the statement ends with the literal: a `.`, `,`, `(`, `[`, a backtick or an
//   operator after the closing brace, on the same line or the next (no semicolon is inserted before
//   a token that can continue an expression; one is before `!`, `~`, `{`, a literal or `++`, which
//   start a statement), would make meta an expression over the literal or a second declaration.
//   The runtime reads meta without running the script and drops a script whose meta it cannot read
//   from the slash-command list;
// - the clock and randomness, which throw inside a script so that a relaunched run repeats the same
//   agent() calls, are not named at all, not even in a comment (a text search cannot tell a comment
//   from code, and a script that names them has no reason to). Matched on the text as written, so
//   spacing, brackets and optional chaining do not slip past, and every `\u` escape is refused, so
//   an escaped spelling cannot hide a name (the runtime decodes escapes before it looks): `Date`
//   may appear only as `new Date(<arguments>)`, the one form the runtime allows, where a
//   parenthesis holding nothing, a comment or a spread counts as no argument; and `Math` only as
//   `Math.<name>` with a name other than `random`;
// - no module loading: `import(`, `import.meta` and `require(`. The runtime refuses the first two
//   before the run starts; `require` is not in the context it runs a script in, so a script that
//   calls it fails in the middle of a run instead. A `@require(` or `.require(` is not Node's
//   require (a prompt may spell the contract decorator that way) and passes;
// - no `new.target`, which the runtime's parse refuses at the top of a script;
// - the body parses under both grammars the runtime applies: it parses the whole script as a
//   module, then compiles the body as a strict-mode script. A workflow body is not a module (it has
//   top-level await and a top-level return), so node cannot check the file as it is. Instead the
//   meta line loses its `export` and the whole text is wrapped in an async function taking the
//   runtime's globals, which keeps every line number but one (the wrapper's first line), and the
//   copy is parsed by `node --check` twice: as .cjs, with a 'use strict' directive on the wrapper's
//   first line, the script grammar, and as .mjs, the module grammar (where an HTML-like comment is
//   an error and `await` is a reserved word in every nested function). Inside the wrapper the
//   script grammar refuses nothing the module grammar accepts except `import.meta`, which the
//   module-loading rule above refuses already; the script parse runs because the runtime runs it,
//   not because it catches more. Strict mode only adds errors, so this check can be stricter than
//   the runtime but never looser.
//
// The copies go into a fresh temporary directory, and only that directory is removed afterwards.
// No eval, no new Function, no vm: node itself parses the copy in a child process, and the meta is
// read by the small literal reader below, which never executes anything.

import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const DIR = path.join(ROOT, 'plugin', 'workflows')
const META = 'export const meta = {'
// A `require(` after `@` or `.` is a decorator or a member, not Node's require.
const MODULE_LOADING = /\bimport\s*\(|\bimport\s*\.\s*meta\b|(?<![@\w$.])require\s*\(/
const NEW_TARGET = /\bnew\s*\.\s*target\b/
// An odd run of backslashes before `u` is an escape; `\\u` is an escaped backslash and a `u`.
const UNICODE_ESCAPE = /(?<!\\)(?:\\\\)*\\u/g
const RESERVED_KEYS = ['__proto__', 'constructor', 'prototype']
const HEAD = "async function __workflow(agent, parallel, pipeline, phase, log, args, budget, workflow) {'use strict';\n"
const TAIL = '\n}\n'
// The two grammars the runtime applies; a script must parse under both.
const GRAMMARS = [
  { ext: '.cjs', name: 'script' },
  { ext: '.mjs', name: 'module' },
]

// The offset of the first statement at or after `from`: past whitespace, line comments and block
// comments.
function firstStatement(text, from = 0) {
  let i = from
  for (;;) {
    while (i < text.length && /\s/.test(text[i])) i++
    if (text.startsWith('//', i)) {
      const end = text.indexOf('\n', i)
      if (end === -1) return text.length
      i = end + 1
    } else if (text.startsWith('/*', i)) {
      const end = text.indexOf('*/', i + 2)
      if (end === -1) return text.length
      i = end + 2
    } else {
      return i
    }
  }
}

function lineOf(text, offset) {
  return text.slice(0, offset).split('\n').length
}

// Every naming of the clock or of randomness the runtime would refuse, or that a text search
// cannot prove harmless (an alias such as `const D = Date`), with its line; and every `\u` escape,
// which the runtime decodes before it looks for a name and this search does not.
function clockAndRandomness(text) {
  const found = []
  for (const m of text.matchAll(UNICODE_ESCAPE)) {
    found.push(`line ${lineOf(text, m.index)}: a \\u escape, which could spell a name this check reads as written`)
  }
  for (const m of text.matchAll(/\bDate\b/g)) {
    const before = text.slice(0, m.index)
    const after = text.slice(m.index + m[0].length)
    // To the runtime, a parenthesis holding nothing, a comment or a spread holds no argument.
    const constructed =
      /\bnew\s+$/.test(before) && /^\s*\(/.test(after) && !/^\s*\(\s*(?:\)|\/\*|\/\/|\.\.\.)/.test(after)
    if (!constructed) found.push(`line ${lineOf(text, m.index)}: Date other than new Date(<arguments>)`)
  }
  for (const m of text.matchAll(/\bMath\b/g)) {
    const member = /^\s*\.\s*([A-Za-z_$][\w$]*)/.exec(text.slice(m.index + m[0].length))
    if (!member || member[1] === 'random') {
      found.push(`line ${lineOf(text, m.index)}: Math other than Math.<name>, or Math.random`)
    }
  }
  return found
}

// A reader for the literal subset the runtime accepts in meta. It returns the value it read and the
// offset just past it, or throws an Error naming what is not a literal; it evaluates nothing.
function readLiteral(text, start) {
  let i = start

  function fail(what) {
    throw new Error(`${what} at line ${lineOf(text, i)}`)
  }
  function space() {
    for (;;) {
      while (i < text.length && /\s/.test(text[i])) i++
      if (text.startsWith('//', i)) {
        const end = text.indexOf('\n', i)
        i = end === -1 ? text.length : end + 1
      } else if (text.startsWith('/*', i)) {
        const end = text.indexOf('*/', i + 2)
        if (end === -1) fail('an unclosed comment')
        i = end + 2
      } else {
        return
      }
    }
  }
  function string() {
    const quote = text[i]
    let out = ''
    i++
    while (i < text.length && text[i] !== quote) {
      if (quote === '`' && text.startsWith('${', i)) fail('a template with interpolation in meta')
      if (text[i] === '\\') {
        const next = text[i + 1]
        if (next === undefined) break
        const simple = { n: '\n', t: '\t', r: '\r', b: '\b', f: '\f', v: '\v', 0: '\0' }
        if (next in simple) {
          out += simple[next]
          i += 2
        } else if (next === 'u' || next === 'x') {
          const hex = next === 'x' ? text.slice(i + 2, i + 4) : text[i + 2] === '{' ? text.slice(i + 3, text.indexOf('}', i)) : text.slice(i + 2, i + 6)
          if (!/^[0-9a-fA-F]+$/.test(hex)) fail('a malformed escape in a meta string')
          out += String.fromCodePoint(parseInt(hex, 16))
          i += next === 'x' ? 4 : text[i + 2] === '{' ? hex.length + 4 : 6
        } else if (next === '\n') {
          i += 2
        } else {
          out += next
          i += 2
        }
      } else {
        if (text[i] === '\n' && quote !== '`') fail('a line break inside a meta string')
        out += text[i]
        i++
      }
    }
    if (text[i] !== quote) fail('an unclosed string in meta')
    i++
    return out
  }
  function value() {
    space()
    const c = text[i]
    if (c === '{') return object()
    if (c === '[') return array()
    if (c === "'" || c === '"' || c === '`') return string()
    if (text.startsWith('...', i)) fail('spread in meta')
    const num = /^-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?(?![\w$])/.exec(text.slice(i))
    if (num) {
      i += num[0].length
      return Number(num[0])
    }
    const word = /^[A-Za-z_$][\w$]*/.exec(text.slice(i))
    if (word && ['true', 'false', 'null'].includes(word[0])) {
      i += word[0].length
      return word[0] === 'null' ? null : word[0] === 'true'
    }
    if (word) fail(`a name (${word[0]}) standing for a value in meta`)
    fail(`a value that is not a literal (${JSON.stringify(text.slice(i, i + 12))}) in meta`)
  }
  function array() {
    const out = []
    i++
    for (;;) {
      space()
      if (text[i] === ']') {
        i++
        return out
      }
      if (text[i] === ',') fail('a hole in an array in meta')
      out.push(value())
      space()
      if (text[i] === ',') i++
      else if (text[i] !== ']') fail('a malformed array in meta')
    }
  }
  function object() {
    // No prototype: a key the runtime refuses must not reach one here either, and a `__proto__`
    // key written into a plain object would set the prototype instead of a property.
    const out = Object.create(null)
    i++
    for (;;) {
      space()
      if (text[i] === '}') {
        i++
        return out
      }
      if (text.startsWith('...', i)) fail('spread in meta')
      if (text[i] === '[') fail('a computed key in meta')
      let key
      if (text[i] === "'" || text[i] === '"') key = string()
      else {
        const word = /^[A-Za-z_$][\w$]*/.exec(text.slice(i))
        if (!word) fail('a key that is neither a name nor a string in meta')
        key = word[0]
        i += key.length
      }
      if (RESERVED_KEYS.includes(key)) fail(`a reserved key (${key}) in meta`)
      space()
      if (text[i] === '(') fail(`a method (${key}) in meta`)
      if (text[i] !== ':') fail(`a key without a value (${key}) in meta`)
      i++
      out[key] = value()
      space()
      if (text[i] === ',') i++
      else if (text[i] !== '}') fail('a malformed object in meta')
    }
  }

  space()
  if (text[i] !== '{') fail('meta that is not an object')
  const read = object()
  return { value: read, end: i }
}

// The start of what follows an offset, for a message: the rest of its line, cut short.
function shownFrom(text, offset) {
  return JSON.stringify(text.slice(offset).split('\n')[0].slice(0, 12))
}

// What follows the literal must end the statement: a `;`, the end of the text, or a line break
// after which the next token starts a statement of its own, as a token that cannot continue an
// expression does (a name other than `in` or `instanceof`, `!`, `~`, `{`, a string or a number,
// `++`, `--`: a semicolon is inserted before it). A `.`, `,`, `(`, `[`, a backtick or an operator
// would make meta an expression over the literal or a second declaration, on the same line or at
// the start of the next, where no semicolon is inserted before one.
function afterMeta(text, end) {
  let i = end
  let broke = false
  for (;;) {
    while (i < text.length && (text[i] === ' ' || text[i] === '\t')) i++
    if (!text.startsWith('/*', i)) break
    const close = text.indexOf('*/', i + 2)
    if (close === -1) return 'an unclosed comment follows meta'
    if (text.slice(i, close).includes('\n')) broke = true
    i = close + 2
  }
  if (i >= text.length || text[i] === ';') return null
  if (broke || text[i] === '\n' || text[i] === '\r' || text.startsWith('//', i)) {
    const next = firstStatement(text, i)
    if (next >= text.length || text[next] === ';') return null
    const word = /^[A-Za-z_$][\w$]*/.exec(text.slice(next))
    if (word ? !['in', 'instanceof'].includes(word[0]) : /^(?:[!~{'"]|\d|\+\+|--)/.test(text.slice(next))) return null
    return `meta is followed on line ${lineOf(text, next)} by ${shownFrom(text, next)}, which continues the statement instead of starting a new one (no semicolon is inserted before it)`
  }
  return `meta is followed by ${shownFrom(text, i)}, which makes it more than the literal`
}

function metaProblem(text, start) {
  let read
  try {
    read = readLiteral(text, start + META.length - 1)
  } catch (err) {
    return `meta is not a pure literal: ${err.message}`
  }
  const meta = read.value
  for (const key of ['name', 'description']) {
    if (typeof meta[key] !== 'string' || meta[key].trim() === '') return `meta.${key} is not a non-empty string`
  }
  return afterMeta(text, read.end)
}

function escapeRegExp(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

// Node's message names the temporary copy and its line; name the workflow file and its own line.
// The stack lines, the version line and the module-loading warning node adds when a .cjs copy
// fails say nothing about the script.
function nodeMessage(stderr, copy, shown) {
  const at = new RegExp(`${escapeRegExp(copy)}:(\\d+)`, 'g')
  return stderr
    .replace(at, (_, line) => `${shown}:${Number(line) - 1}`)
    .split('\n')
    .filter((l) => !/^\s+at /.test(l) && !/^Node\.js v/.test(l) && !/^\(node:\d+\) Warning/.test(l) && !/^\(Use `node /.test(l))
    .join('\n')
    .trim()
}

function check(file, tmp) {
  const shown = path.relative(ROOT, file)
  const text = fs.readFileSync(file, 'utf8')
  const start = firstStatement(text)
  if (!text.startsWith(META, start)) {
    return `the first statement is not "${META}"`
  }
  const meta = metaProblem(text, start)
  if (meta) return meta
  const found = clockAndRandomness(text)
  if (found.length) {
    return `names the clock or randomness, which throw inside a workflow script, or spells a name with an escape:\n  ${found.join('\n  ')}`
  }
  if (MODULE_LOADING.test(text)) {
    return 'loads a module (import(), import.meta or require()), which a workflow script may not do'
  }
  if (NEW_TARGET.test(text)) {
    return 'uses new.target, which the runtime refuses at the top of a workflow script'
  }
  const body = text.slice(0, start) + text.slice(start + 'export '.length)
  for (const grammar of GRAMMARS) {
    const copy = path.join(tmp, path.basename(file, '.js') + grammar.ext)
    fs.writeFileSync(copy, HEAD + body + TAIL)
    const run = spawnSync(process.execPath, ['--check', copy], { encoding: 'utf8' })
    if (run.error) return `node could not be started: ${run.error.message}`
    if (run.status !== 0) return `does not parse as a ${grammar.name}:\n${nodeMessage(run.stderr || '', copy, shown)}`
  }
  return null
}

function main() {
  let names
  try {
    names = fs.readdirSync(DIR).filter((n) => n.endsWith('.js')).sort()
  } catch (err) {
    console.error(`check_workflows: cannot read ${path.relative(ROOT, DIR)}: ${err.message}`)
    return 1
  }
  if (names.length === 0) {
    // Nothing checked is not a pass.
    console.error(`check_workflows: no workflow scripts in ${path.relative(ROOT, DIR)}`)
    return 1
  }
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'beethoven-qa-workflows-'))
  let failed = 0
  try {
    for (const name of names) {
      const file = path.join(DIR, name)
      const problem = check(file, tmp)
      const shown = path.relative(ROOT, file)
      if (problem) {
        failed++
        console.log(`FAIL ${shown}: ${problem}`)
      } else {
        console.log(`ok   ${shown}`)
      }
    }
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true })
  }
  return failed ? 1 : 0
}

process.exitCode = main()
