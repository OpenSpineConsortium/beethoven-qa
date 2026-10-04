#!/usr/bin/env node
// Checks the plugin's workflow scripts the way Claude Code will read them, before any of them runs
// (CI runs it; so can anyone: `node tools/check_workflows.mjs`). The rules come from the workflows
// page (code.claude.com/docs/en/workflows, read 2026-10-04) and the guard the runtime installs:
//
// - `export const meta = {` is the first statement (comments may precede it), and the object is a
//   pure literal (strings without interpolation, numbers, booleans, null, arrays, objects with plain
//   keys; no spread, no computed key, no method, no name standing for a value, no call) with a
//   non-empty string `name` and `description`: the runtime reads it without running the script and
//   drops a script whose meta it cannot read from the slash-command list;
// - the clock and randomness, which throw inside a script so that a relaunched run repeats the same
//   agent() calls, are not named at all, not even in a comment (a text search cannot tell a comment
//   from code, and a script that names them has no reason to). Matched as tokens, not spellings, so
//   spacing, brackets and optional chaining do not slip past: `Date` may appear only as
//   `new Date(<arguments>)` (the one form the runtime allows), and `Math` only as `Math.<name>`
//   with a name other than `random`;
// - no module loading, which fails a script before the run starts;
// - the body parses. A workflow body is not a module (it has top-level await and a top-level
//   return), so node cannot check the file as it is. Instead the meta line loses its `export` and
//   the whole text is wrapped in an async function taking the runtime's globals, which keeps every
//   line number but one (the wrapper's first line) and is parsed by `node --check`. The copy is
//   written as .mjs so it parses in strict mode: strict mode only adds errors, so this check can be
//   stricter than the runtime but never looser.
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
const MODULE_LOADING = /\bimport\s*\(/
const HEAD = 'async function __workflow(agent, parallel, pipeline, phase, log, args, budget, workflow) {\n'
const TAIL = '\n}\n'

// The offset of the first statement: past whitespace, line comments and block comments.
function firstStatement(text) {
  let i = 0
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
// cannot prove harmless (an alias such as `const D = Date`), with its line.
function clockAndRandomness(text) {
  const found = []
  for (const m of text.matchAll(/\bDate\b/g)) {
    const before = text.slice(0, m.index)
    const after = text.slice(m.index + m[0].length)
    const constructed = /\bnew\s+$/.test(before) && /^\s*\(\s*[^)\s]/.test(after)
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

// A reader for the literal subset the runtime accepts in meta. It returns the value it read, or
// throws an Error naming what is not a literal; it evaluates nothing.
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
    const out = {}
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
  return object()
}

function metaProblem(text, start) {
  let meta
  try {
    meta = readLiteral(text, start + META.length - 1)
  } catch (err) {
    return `meta is not a pure literal: ${err.message}`
  }
  for (const key of ['name', 'description']) {
    if (typeof meta[key] !== 'string' || meta[key].trim() === '') return `meta.${key} is not a non-empty string`
  }
  return null
}

function escapeRegExp(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

// Node's message names the temporary copy and its line; name the workflow file and its own line.
function nodeMessage(stderr, copy, shown) {
  const at = new RegExp(`${escapeRegExp(copy)}:(\\d+)`, 'g')
  return stderr
    .replace(at, (_, line) => `${shown}:${Number(line) - 1}`)
    .split('\n')
    .filter((l) => !/^\s+at /.test(l) && !/^Node\.js v/.test(l))
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
    return `names the clock or randomness, which throw inside a workflow script:\n  ${found.join('\n  ')}`
  }
  if (MODULE_LOADING.test(text)) {
    return 'loads a module, which a workflow script may not do'
  }
  const body = text.slice(0, start) + text.slice(start + 'export '.length)
  const copy = path.join(tmp, path.basename(file, '.js') + '.mjs')
  fs.writeFileSync(copy, HEAD + body + TAIL)
  const run = spawnSync(process.execPath, ['--check', copy], { encoding: 'utf8' })
  if (run.error) return `node could not be started: ${run.error.message}`
  if (run.status !== 0) return `does not parse:\n${nodeMessage(run.stderr || '', copy, shown)}`
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
