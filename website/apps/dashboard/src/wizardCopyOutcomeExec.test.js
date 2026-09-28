// wizardCopyOutcomeExec.test.js — #2935.
//
// WHY THIS FILE EXISTS. Every Copy control in the wizard's CONNECT step used to
// report success UNCONDITIONALLY:
//   * `WizardPromptCard.doCopy` called `navigator.clipboard.writeText(text)`
//     without awaiting or catching, then immediately `setCopied(true)` — which
//     flipped the label to "Copied ✓" AND announced "Copied to clipboard"
//     through the live region;
//   * the three inline buttons called `navigator.clipboard?.writeText(...)`,
//     which gave no feedback at all.
// `?.` covers only a MISSING clipboard object. The real failure mode is a write
// that REJECTS — permission denied, non-secure context, no user activation —
// and on any rejection the user got a green tick over a clipboard still holding
// the previous payload, then pasted the wrong thing into their agent config.
//
// WHY EXECUTION, NOT A TEXT SCAN. The exit claim is a BEHAVIOUR: "the Copy label
// may only say 'Copied ✓' when the write actually RESOLVED". A promise's outcome
// is invisible to source text, and `await` / `.then` / `.catch` / a helper are
// interchangeable spellings — each would satisfy a regex while the defect lived.
// So this file takes the REAL `runCopyAttempt`, `copyLabel` and
// `selectCopyTarget` out of `main.jsx`, builds them with `new Function(...)` over
// stubbed deps, and drives the rejecting and resolving paths. The text pins at
// the bottom are only WIRING backstops: execution cannot see JSX, and the round-1
// review proved that without them a control could be reverted to the unobserved
// fire-and-forget shape with every executed assertion still green.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { stripComments } from './testSupport.js'

const here = dirname(fileURLToPath(import.meta.url))
const mainJsx = readFileSync(join(here, 'main.jsx'), 'utf8')
const mainJsxCode = stripComments(mainJsx)

// ── token-aware extraction of a real declaration from main.jsx ──────────────
// Strings, template literals and comments are skipped so a brace inside copy
// cannot truncate the slice. A missing declaration THROWS — the test must fail
// loudly, never silently run nothing. (Same helper as keyMintFailureExec.)
function skipString(src, i) {
  const q = src[i]
  i += 1
  while (i < src.length) {
    const c = src[i]
    if (c === '\\') { i += 2; continue }
    if (q === '`' && c === '$' && src[i + 1] === '{') {
      let depth = 1
      i += 2
      while (i < src.length && depth > 0) {
        const cc = src[i]
        if (cc === '\\') { i += 2; continue }
        if (cc === "'" || cc === '"' || cc === '`') { i = skipString(src, i); continue }
        if (cc === '{') depth++
        else if (cc === '}') depth--
        i++
      }
      continue
    }
    if (c === q) return i + 1
    i += 1
  }
  return i
}

function matchBrace(src, open) {
  let depth = 0
  for (let i = open; i < src.length; i++) {
    const ch = src[i]
    if (ch === '/' && src[i + 1] === '/') {
      i = src.indexOf('\n', i)
      if (i < 0) return -1
      continue
    }
    if (ch === '/' && src[i + 1] === '*') {
      i = src.indexOf('*/', i + 2)
      if (i < 0) return -1
      i += 1
      continue
    }
    if (ch === "'" || ch === '"' || ch === '`') { i = skipString(src, i) - 1; continue }
    if (ch === '{') depth++
    else if (ch === '}') {
      depth--
      if (depth === 0) return i
    }
  }
  return -1
}

function extractDeclaration(src, decl) {
  const start = src.indexOf(`${decl}(`)
  assert.ok(start > -1, `main.jsx must declare ${decl}()`)
  const open = src.indexOf('{', start)
  assert.ok(open > -1, `${decl}: the body must open with {`)
  const end = matchBrace(src, open)
  assert.ok(end > -1, `${decl}: the body braces must balance`)
  return src.slice(start, end + 1)
}

// The attempt-state constants are single-line declarations and must be the REAL
// ones (not re-typed literals): a test that supplies its own `COPY_IDLE` shape
// would verify the test's copy instead of the shipped truth.
function extractConst(src, name) {
  const start = src.indexOf(`const ${name} =`)
  assert.ok(start > -1, `main.jsx must declare const ${name}`)
  const end = src.indexOf('\n', start)
  assert.ok(end > start, `const ${name} must be a single-line declaration`)
  return src.slice(start, end)
}

const COPY_CONSTS = ['COPY_IDLE', 'COPY_FLASH_MS']
// Round-22 review: the per-node hiding bans missed a hidden ANCESTOR wrapper.
// Round-23 review: the wrapper is now held to the SAME ban as the NODE (one regex,
// below), so a wrapper collapsed, faded, moved off-screen or clipped cannot silence
// the whole control either. Measured: the only style properties either component uses
// at all are `margin`/`marginTop`, at values that do not trip the ban.
const Q = '[\'"`]' // the three quote characters a plain JS/JSX literal may use
// Round-28 review: the leading digit was REQUIRED, so `.0px` walked past; and with no
// look-behind, `100%` matched the `00`. Both fixed: an optional `.`, and a guard on the
// character BEFORE the run.
const ZERO = `${Q}?(?<![\\d])\\.?0+(?:\\.0+)?(?:px|rem|em|%)?${Q}?(?![\\d.])`
const HIDDEN_BAN = new RegExp(
  [
    // Round-28 review: a spelling list is the wrong shape here — `collapse` hides a
    // non-table element just as completely. Ban every value EXCEPT the visible keywords.
    // The lookahead consumes its OWN whitespace: with `\s*` outside it the engine
    // backtracks to zero spaces and evaluates the lookahead at the space, which then
    // bans `visibility: 'visible'` itself (a round-28 false red).
    `(?<![-\\w])visibility\\s*:(?!\\s*${Q}?(?:visible|inherit|initial|unset|revert)${Q}?)`,
    `(?<![-\\w])color\\s*:\\s*${Q}?transparent`,
    // Round-37 review: only the literal `transparent` was banned, so a zero-alpha colour
    // (`rgba(0,0,0,0)`, `rgb(0 0 0 / 0)`, `#0000`, `#00000000`) rendered the alert
    // invisible; and the look-behind keeps a legitimate `border-color` out of scope.
    // Round-44 review: `(?:rgba|hsla|rgb|hsl)` + a trailing zero-run also matched a 3-argument
    // `rgb(255,255,0)` — a fully OPAQUE colour reported as "hidden". An alpha must be explicit:
    // the `a` spelling (whose LAST argument is the alpha), or the `/ alpha` form.
    `(?<![-\\w])color\\s*:\\s*${Q}?(?:rgba|hsla)\\([^)]*[,/]\\s*${Q}?0*\\.?0+${Q}?%?\\s*\\)`,
    `(?<![-\\w])color\\s*:\\s*${Q}?(?:rgb|hsl)\\([^)]*\\/\\s*${Q}?0*\\.?0+${Q}?%?\\s*\\)`,
    `(?<![-\\w])color\\s*:\\s*${Q}?#(?:[0-9a-fA-F]{3}0|[0-9a-fA-F]{6}00)(?![0-9a-fA-F])${Q}?`,
    // Round-46 review: the spelling arm required `opacity(0` to abut the quote, so
    // `brightness(1) opacity(0)` walked past — and it also FALSE-RED `opacity(0.5)` (a visible
    // value). The numeric read in `styleHiding(...)`, asserted below, replaces it.
    `(?<![-\\w])display\\s*:\\s*${Q}none${Q}`,
    `(?<![-\\w])opacity\\s*:\\s*${ZERO}`,
    `(?<![-\\w])zoom\\s*:\\s*${ZERO}`,
    // Round-42 review: no property boundary, so `lineHeight`/`minHeight` matched the `Height`
    // substring and were rejected as HIDING the control, with a message that misdirects the
    // fixer. The `color` and `hidden` arms already carry exactly this guard.
    `(?<![-\\w])(?:max-|max)?[Hh]eight\\s*:\\s*${ZERO}`,
    `(?<![-\\w])(?:max-|max)?[Ww]idth\\s*:\\s*${ZERO}`,
    // Round-24 review: ANY `aria-hidden` (not just `=true`) — it is never legitimate
    // on these nodes, and `aria-hidden={true}` evaded the `['"`]?true` spelling.
    `aria-hidden`,
    // Round-39 review: `text-indent: -9999px` is the classic off-screen recipe and was missing
    // from the family this arm enumerates.
    `(?<![-\\w])(?:left|right|top|bottom|textIndent|text-indent|margin[A-Z]?\\w*)\\s*${Q}?\\s*:\\s*${Q}?-?\\d{3,}`,
    // Round-39 review: ZERO was banned, but a 1px-tall alert with the EXEMPT `overflow: hidden`
    // silenced the failure just as completely, and `opacity`/`zoom` already got the numeric
    // treatment. Read the number: any height/width below 8, with `100%`/`12px`/`auto` green.
    // Round-46 review: the unit was optional AND unconstrained, so `maxWidth: '7.5rem'`
    // (120px, fully visible) matched "below 8". Only a unitless or `px` value is a px value.
    `(?<![-\\w])(?:max-|max)?[Hh]eight\\s*:\\s*${Q}?(?:[0-7](?:\\.\\d+)?|\\.\\d+)(?:px)?${Q}?(?![\\d.\\w%])`,
    `(?<![-\\w])(?:max-|max)?[Ww]idth\\s*:\\s*${Q}?(?:[0-7](?:\\.\\d+)?|\\.\\d+)(?:px)?${Q}?(?![\\d.\\w%])`,
    // Round-28 review: `scale(0` was literal, so `scaleX(0)`/`scaleY(0)`/`scale3d(0,…)`
    // all evaded it.
    // Round-37 review: the arm required `matrix(0` and `translate[XY]?` with a SIGNED
    // 3-digit FIRST argument, so `translate(0, -9999px)`, `translate3d(…)`, `translateZ(…)`
    // and `matrix(1, 0, 0, 1, 0, -9999)` all walked past. Any translate/matrix whose ARGUMENT
    // LIST carries a 3+ digit number is off-screen or scaled — ban the value, not the spelling.
    // Round-49 review: the `scale(` value was matched literally, so one space (`scale( 0 )`,
    // `scale(-0)`, `scaleX( 0 )`) rendered the alert at zero scale with the suite green — read
    // the NUMBER, not the spelling.
    // Round-50 review: the numeric read was SINGLE-argument, so the multi-argument zero scale the
    // round-28 comment already claimed to cover (`scale3d(0,0,0)`, `scale(0, 0)`) collapsed the
    // alert with the suite green. Read the WHOLE argument list: every argument zero.
    `transform\\s*:\\s*${Q}?[^\\s]*?(?:scale[A-Za-z0-9]*\\(\\s*-?(?:0*(?:\\.0+)?\\s*,?\\s*)+\\)|matrix\\([^)]*\\d{3,}|translate[A-Za-z0-9]*\\([^)]*\\d{3,})`,
    // Round-28 review: only `inset` was banned, so `circle(0)`/`polygon(0 0)` evaded it.
    // Ban every value except the OFF keywords (the `clip-path: none` exemption, above).
    `(?<![-\\w])clip[Pp]ath\\s*:(?!\\s*${Q}?(?:none|inherit|initial|unset|revert)${Q}?)`,
    // Round-27 review: `clip: rect(0 0 0 0)` is the classic visually-hidden recipe.
    `(?<![-\\w])clip\\s*:\\s*${Q}?[^\\s]*rect\\(`,
    // Round-31 review: only the `overflow` shorthand was exempt, so the identically
    // clipping longhands `overflowX`/`overflowY` were false reds.
    // Round-42 review: `backfaceVisibility: 'hidden'` does not hide anything, so it is exempt
    // alongside the `overflow*` carve-out (the `visibility` arm still bans `visibility: hidden`).
    `(?<!overflow[XxYy]?\\s*:\\s*${Q})(?<!backfaceVisibility\\s*:\\s*${Q}?)(?<![\\-\\w])hidden(?![\\w-])`,
  ].join("|"),
  // Round-49 review: CSS keywords are CASE-INSENSITIVE, so `display: 'NONE'` removed the alert
  // while the case-sensitive arm missed it. Every arm is keyword/value based, so the flag is
  // safe (and makes `VISIBLE`/`NONE`/`HIDDEN`/`SCALE(0)` all behave like their lower-case forms).
  'i',
)
const COPY_DECLS = ['async function runCopyAttempt', 'function copyLabel', 'function selectCopyTarget']

// Round-54 review: the alert PICK, the guard prefix pin and the composed-falsy scan all required
// the DOT spelling `copy.failed`, so the behaviour-identical bracket form `copy['failed']` died at
// the PICK — before `assertGuardOperands` (which resolves bracket access) ever ran. One shape.
const FAILED_REF = 'copy\\s*(?:(?:\\?\\s*)?\\.\\s*failed|\\[\\s*[\'"`]failed[\'"`]\\s*\\])'

// ── A tiny CONSTANT-EXPRESSION evaluator (rounds 37-38 review) ─────────────────
// A spelling list cannot decide the `{… && (` gates: `!0` is TRUTHY while `!1` is not,
// `Boolean(false)` / `((!1))` / `void 0` are the same value as the literal they hide behind,
// and `1 > 2` / `1-1` / `0n` / `1e-999` are constants JS evaluates to a falsy without any
// token this scan could have listed. Evaluate the expression instead. `ok:false` means "NOT
// provably constant" — the common case, and the only safe answer for anything unrecognised.
//
// KNOWN LIMIT (round 45): the handler pin requires the 4th argument to be read from the
// callback's own parameter, so threading it through a local (`const el = event && event.currentTarget`)
// is a false red; the declared-green block body covers a DIRECT pass-through only.
//
// KNOWN LIMIT (round 43): the truthy-`copied:` ban is a LITERAL-key ban, so a state object
// laundered through an identifier (`const S = { copied: true }; setCopy({ ...COPY_IDLE, ...S })`)
// or a computed key (`{ ['copied']: true }`) is not caught — the same producer/laundering
// boundary this file already declares, with no realistic edit path in either component.
//
// ACCEPTED over-broad backstop (round 40): both components ban `${…}` template interpolation
// outright, so a behaviour-preserving interpolated non-success gate (`{`${label}` && (…)}`)
// reddens. It stays: interpolation is the one remaining way to stitch a success word
// invisibly to the token denylist, and neither component needs a template.
//
// Round-39 review: parens are PARSED, never stripped. A global strip deleted the call parens
// of `Boolean(0)` (reducing it to the token `Boolean0`, so that branch became unreachable) and
// inverted grouping under a bang — `!(1 < 2)` became `!1 < 2`, which JS reads as `false < 2`,
// TRUTHY — so a genuinely dead gate evaluated as live.
// The opening tag that starts at `start` (`<`), ending at the matching `>` at brace depth 0 —
// a `>` inside `{…}` (`onClick={(e) => …}`) or a quoted attribute is not the tag's end.
function openingTag(src, start) {
  let depth = 0
  let quote = null
  for (let i = start; i < src.length; i += 1) {
    const c = src[i]
    if (quote) {
      if (c === '\\') i += 1
      else if (c === quote) quote = null
      continue
    }
    if (c === '"' || c === "'" || c === '`') { quote = c; continue }
    if (c === '{' || c === '(') depth += 1
    else if (c === '}' || c === ')') depth -= 1
    else if (c === '>' && depth === 0) return src.slice(start, i + 1)
  }
  return null
}

function evalConst(src) {
  let i = 0
  const ws = () => { while (i < src.length && /\s/.test(src[i])) i += 1 }
  const combine = (op, l, r) => {
    // `&&`/`||` decide by VALUE, which is the whole point: `X && false` is always falsy and
    // `X || true` always truthy, with `X` unknowable — and that is enough to catch every gate.
    if (op === '&&') {
      if (l.ok && !l.value) return { ok: true, value: l.value }
      if (l.ok && l.value) return r
      if (r.ok && !r.value) return { ok: true, value: r.value }
      return { ok: false }
    }
    if (op === '||') {
      if (l.ok && l.value) return { ok: true, value: l.value }
      if (l.ok && !l.value) return r
      if (r.ok && r.value) return { ok: true, value: r.value }
      return { ok: false }
    }
    if (!l.ok || !r.ok) return { ok: false }
    const a = l.value
    const b = r.value
    switch (op) {
      case '===': return { ok: true, value: a === b }
      case '!==': return { ok: true, value: a !== b }
      case '==': return { ok: true, value: a == b } // eslint-disable-line eqeqeq
      case '!=': return { ok: true, value: a != b } // eslint-disable-line eqeqeq
      case '<': return { ok: true, value: a < b }
      case '>': return { ok: true, value: a > b }
      case '<=': return { ok: true, value: a <= b }
      case '>=': return { ok: true, value: a >= b }
      case '+': return { ok: true, value: a + b }
      case '-': return { ok: true, value: a - b }
      case '*': return { ok: true, value: a * b }
      case '/': return { ok: true, value: a / b }
      case '%': return { ok: true, value: a % b }
      default: return { ok: false }
    }
  }
  const EXACT = [['||'], ['&&'], ['===', '!==', '==', '!='], ['<=', '>=', '<', '>'], ['+', '-'], ['*', '/', '%']]
  function primary() {
    ws()
    if (src[i] === '(') {
      i += 1
      const r = expr(0)
      ws()
      if (src[i] === ')') i += 1
      // The window is CUT at the JSX the gate renders, so a group that has not closed by the
      // end of the window is a truncation, not a mismatch — accept the inner value.
      else if (i < src.length) return { ok: false }
      return r
    }
    if (src[i] === '!') { i += 1; const r = primary(); return r.ok ? { ok: true, value: !r.value } : r }
    if (src[i] === '-' || src[i] === '+' || src[i] === '~') {
      const op = src[i]
      i += 1
      const r = primary()
      if (!r.ok) return r
      return { ok: true, value: op === '-' ? -r.value : op === '+' ? +r.value : ~r.value }
    }
    if (src.startsWith('Boolean', i) && !/\w/.test(src[i + 7] || '')) {
      let k = i + 7
      while (k < src.length && /\s/.test(src[k])) k += 1
      if (src[k] !== '(') return { ok: false }
      i = k + 1
      ws()
      // Round-40 review: `Boolean()` is `false` — bailing on the empty argument list made it
      // "not constant", so `{Boolean() && (…)}` dead-gated a control with every pin green.
      if (src[i] === ')') { i += 1; return { ok: true, value: false } }
      const r = expr(0)
      ws()
      if (src[i] !== ')') return { ok: false }
      i += 1
      return r.ok ? { ok: true, value: !!r.value } : { ok: false }
    }
    // Round-40 review: `Number(0)`/`Number('')` are constants too — and `Number('abc')` is NaN,
    // also falsy — so they belong to the same dead-gate class.
    if (src.startsWith('Number', i) && !/\w/.test(src[i + 6] || '')) {
      let k = i + 6
      while (k < src.length && /\s/.test(src[k])) k += 1
      if (src[k] !== '(') return { ok: false }
      i = k + 1
      ws()
      if (src[i] === ')') { i += 1; return { ok: true, value: 0 } }
      const r = expr(0)
      ws()
      if (src[i] !== ')') return { ok: false }
      i += 1
      return r.ok ? { ok: true, value: Number(r.value) } : { ok: false }
    }
    // Round-48 review: a literal CONTAINER's `.length` is a constant, and the evaluator called
    // it "not provably constant" — so `{copy.failed && [].length && (…)}` rendered a stray `0`
    // where the failure remedy should be, with every pin green.
    const litLen = /^\[\s*([^\][]*?)\s*\]\s*\.\s*length\b/.exec(src.slice(i))
    if (litLen) {
      i += litLen[0].length
      return { ok: true, value: litLen[1].trim() === '' ? 0 : litLen[1].split(',').length }
    }
    // …and the same read through the constructors: `String().length` / `Array().length` are 0.
    const ctorLen = /^(?:new\s+)?(?:String|Array)\s*\(\s*([^()]*?)\s*\)\s*\.\s*length\b/.exec(src.slice(i))
    if (ctorLen) {
      i += ctorLen[0].length
      const lit = /^(['"])([\s\S]*)\1$/.exec(ctorLen[1].trim())
      return { ok: true, value: lit ? lit[2].length : 0 }
    }
    if (src.startsWith('void', i) && !/\w/.test(src[i + 4] || '')) {
      // Round-40 review: the operand was required to be a NUMBER preceded by whitespace, so
      // `void(0)` and `void 'x'` were "not constant". `void <anything>` is `undefined`, so try
      // to consume the operand for positioning and return the value either way.
      i += 4
      ws()
      const mark = i
      if (!expr(0).ok) i = mark
      return { ok: true, value: undefined }
    }
    const num = /^(?:0[xX][0-9a-fA-F]+|0[bB][01]+|0[oO][0-7]+|\d+(?:\.\d+)?(?:[eE][-+]?\d+)?|\.\d+(?:[eE][-+]?\d+)?)(n?)/.exec(src.slice(i))
    if (num) { i += num[0].length; return { ok: true, value: num[1] === 'n' ? 0 : Number(num[0]) } }
    const kw = /^(true|false|null|undefined|NaN)\b/.exec(src.slice(i))
    if (kw) {
      i += kw[0].length
      const t = kw[0]
      return { ok: true, value: t === 'true' ? true : t === 'false' ? false : t === 'null' ? null : t === 'undefined' ? undefined : NaN }
    }
    const str = /^(['"])((?:\\.|(?!\1)[^\\])*)\1/.exec(src.slice(i))
    if (str) {
      i += str[0].length
      const ln = /^\s*\.\s*length\b/.exec(src.slice(i))
      if (ln) { i += ln[0].length; return { ok: true, value: str[2].length } }
      return { ok: true, value: str[2] }
    }
    // Round-40 review: a plain backtick template with no `${…}` is a constant string
    // (`{`` && (…)}` is a dead gate); an interpolated one is not, and stays unknown.
    const tpl = /^`(?:\\.|[^`\\$]|\$(?!\{))*`/.exec(src.slice(i))
    if (tpl) { i += tpl[0].length; return { ok: true, value: tpl[0].slice(1, -1) } }
    return { ok: false }
  }
  function expr(level) {
    if (level >= EXACT.length) return primary()
    let left = expr(level + 1)
    for (;;) {
      ws()
      const op = EXACT[level].find((o) => src.startsWith(o, i))
      if (!op) break
      i += op.length
      left = combine(op, left, expr(level + 1))
    }
    return left
  }
  const r = expr(0)
  return r.ok ? { ok: true, value: r.value, end: i } : { ok: false }
}

// Every `{… && (` gate whose condition is a CONSTANT FALSY is dead code — it removes the
// control (or the live region) while every presence pin still reads the source text.
// Round-43 review: the wrapper check asserted on the LAST opening tag in a fixed 240-char
// window, which is the nearest SIBLING whenever the wrapper holds anything before the control
// (`<div style={{display:'none'}}><p>caption</p><WizardPromptCard/></div>` shipped green), and
// which missed a wrapper whose own tag exceeded the window. Resolve the element that actually
// ENCLOSES the position, by replaying the prefix through a tag stack.
// Round-50 review: same replay, but returning the INNERMOST ENCLOSING tag's INDEX, so a pin can
// read that element's own style object instead of trusting source order.
function enclosingTagAt(src, pos) {
  const prefix = src.slice(0, pos)
  const re = /<(\/?)([A-Za-z][\w.]*)\b/g
  const stack = []
  let m
  while ((m = re.exec(prefix))) {
    const tag = openingTag(prefix, m.index)
    if (!tag) continue
    if (m[1] === '/') stack.pop()
    else if (!/\/>\s*$/.test(tag)) stack.push(m.index)
  }
  return stack.length ? stack[stack.length - 1] : -1
}

function enclosingTag(src, pos) {
  const prefix = src.slice(0, pos)
  const re = /<(\/?)([A-Za-z][\w.]*)\b/g
  const stack = []
  let m
  while ((m = re.exec(prefix))) {
    const tag = openingTag(prefix, m.index)
    if (!tag) continue
    if (m[1] === '/') stack.pop()
    else if (!/\/>\s*$/.test(tag)) stack.push(tag)
  }
  return stack.length ? stack[stack.length - 1] : null
}

// Round-46 review: a JS object literal is LAST-WINS, so a duplicated `display`/`flexWrap`/
// `flex`/`minWidth`/`margin`/`flexBasis` key silently reverted the very behaviour a pin
// asserts while `assert.match` read the FIRST key. Round 34 fixed `fontSize`/`opacity`/`zoom`
// this way; every pinned style key now gets the same treatment.
// Round-47 review: the round-46 scan matched only IDENTIFIER keys, so a COMPUTED key
// (`['flexWrap']: 'nowrap'`, `['flexBasis']: 0`, `['opacity']: 0.01`) was invisible to both the
// duplicate ban and the numeric hiding read — the round-3 wrap fix and the round-7 alert layout
// were revertible with the suite green. Normalise the SPELLING first, then read it.
// Round-48 review: a BARE quoted key (`"flexWrap": 'nowrap'`, `"fontSize": 0`) is a full
// member name, last-wins at runtime — so the duplicate ban and the numeric read were blind to it
// and `flexWrap` could be reverted (or the remedy set to `fontSize: 0`) with every pin green.
// Anchored to an object-literal key position (`{`/`,`) so a ternary's `'a' : 'b'` is untouched.
// Round-49 review: an ESCAPED key (`"fontSiz\u0065": 0`, `"flexWr\x61p": 'nowrap'`) dedupes at
// RUNTIME (the last key wins, so the alert/remedy can be sized to 0) while `[\w-]+` cannot match
// the backslash — the escape is decoded first, exactly as `deEsc` does for the success text.
const deUni = (src) => src
  .replace(/\\u\{([0-9a-fA-F]+)\}/g, (_, h) => String.fromCodePoint(parseInt(h, 16)))
  .replace(/\\u([0-9a-fA-F]{4})/g, (_, h) => String.fromCharCode(parseInt(h, 16)))
  .replace(/\\x([0-9a-fA-F]{2})/g, (_, h) => String.fromCharCode(parseInt(h, 16)))

// Round-54 review: a CONSTANT-folded key (`['flex'+'Wrap']`, `copy['att'+'empt']`) evaluates to the
// real name at runtime — last-wins for a duplicate object key, and a real member for the guard —
// while a single-literal pattern cannot see it. Fold adjacent string literals first.
const foldStrings = (src) => {
  let out = src
  for (let i = 0; i < 8; i += 1) {
    const next = out.replace(/(['"`])((?:\\.|(?!\1)[^\\])*)\1\s*\+\s*(['"`])((?:\\.|(?!\3)[^\\])*)\3/g,
      (_, q1, a, q2, b) => `${q1}${a}${b}${q2}`)
    if (next === out) break
    out = next
  }
  return out
}

const styleDeKey = (src) => deUni(foldStrings(src))
  // Round-53 review: redundant parens are declared behaviour-preserving everywhere else, so
  // `[('flexWrap')]: 'nowrap'` was a last-wins duplicate the normaliser could not see.
  .replace(/\[\s*\(*\s*(['"`])([\w-]+)\1\s*\)*\s*\]\s*:/g, '$2:')
  .replace(/([{,]\s*)(['"`])([\w-]+)\2\s*:/g, '$1$3:')

function styleEntries(tag) {
  // Round-51 review: the padded-brace respelling (`style={ { … } }`) is declared allowed, so the
  // reader must tolerate the whitespace.
  const m = tag.match(/style\s*=\s*\{\s*\{([\s\S]*?)\}\s*\}/)
  if (!m) return []
  // The captured body starts AFTER `{{`, so a first key has no `{`/`,` in front of it —
  // give `styleDeKey` its object-literal boundary.
  return [...styleDeKey(`{${m[1]}`).matchAll(/(?<![-\\w])([A-Za-z][\w-]*)\s*:/g)].map((x) => x[1])
}

// Round-49 review: the four build-fork layout pins below were `assert.match` on the whole
// OPENING TAG, so a decoy attribute (`<div data-flexwrap="flexWrap: 'wrap'" style={{display:
// 'flex'}}>`) satisfied them while the real style entry was gone — the round-3/round-7 P1
// layout fix reverted with the suite green. Read the STYLE object's own entries (last-wins).
function styleEntry(tag, key) {
  const m = tag.match(/style\s*=\s*\{\s*\{([\s\S]*?)\}\s*\}/)
  if (!m) return null
  const hits = [...styleDeKey(`{${m[1]}`).matchAll(new RegExp(`(?<![-\\w])${key}\\s*:\\s*([^,}]*)`, 'g'))]
  return hits.length ? hits[hits.length - 1][1].trim().replace(/^['"`]|['"`]$/g, '') : null
}

// Round-54 review: a JSX spread is last-wins exactly like a duplicate prop, but the duplicate-prop
// pins count `<prop>=` and the readers take the FIRST binding — so `{...{ text: undefined }}` made
// a control copy the string "undefined", `{...{ onClick: undefined }}` removed the handler,
// `{...{ className: 'error small' }}` widened the alert's class list, and `{...{ key: 0 }}` stopped
// a repeat failure from re-announcing, all with the suite green. Reject a spread that re-binds a
// pinned prop. DECLARED COST: the spread's contents are read textually, so a spread whose keys come
// from another object is not inspected.
function assertNoSpreadOverride(tag, prop, what) {
  assert.doesNotMatch(tag, new RegExp(`\\{\\s*\\.\\.\\.[^}]*\\b${prop}\\s*:`),
    `${what}: a JSX spread must not re-bind \`${prop}\` (a spread is last-wins, so it silently overrides the pinned attribute)`)
}

function assertStyleKeysUnique(tag, what) {
  const keys = styleEntries(tag)
  for (const key of new Set(keys)) {
    assert.equal(keys.filter((k) => k === key).length, 1,
      `a duplicate \`${key}\` style key is last-wins at runtime (the pin reads the first): ${what}`)
  }
}

// Round-46 review: several hiding arms were spelling-based (`filter: '…opacity(0'`, an
// all-zero alpha) or scoped to the alert only, so `filter: 'brightness(1) opacity(0)'`,
// `color: 'rgba(0,0,0,0.001)'` and a wrapper with `opacity: 0.01` all walked past. Read the
// NUMBERS, on every node in scope.
function styleHiding(src) {
  const hits = []
  const tiny = (raw, pct) => {
    const v = Number(raw) / (pct ? 100 : 1)
    return Number.isFinite(v) && v < 0.05
  }
  // Round-47 review: the `%` was matched OUTSIDE the capture and thrown away, so a valid CSS
  // percentage read as its own numerator — `filter: 'opacity(4%)'` (= 0.04) and
  // `color: 'rgba(0,0,0,4%)'` were both GREEN. Capture it and scale.
  for (const m of styleDeKey(src).matchAll(/(?:^|[^\w-])(opacity|zoom)\s*:\s*['"`]?([-\d.eE+]+)(%)?/g)) {
    if (tiny(m[2], m[3])) hits.push(m[0].trim())
  }
  for (const m of styleDeKey(src).matchAll(/filter\s*:\s*[^\n]*?opacity\(\s*([-\d.eE+]+)\s*(%)?\s*\)/g)) {
    if (tiny(m[1], m[2])) hits.push(m[0].trim())
  }
  for (const m of styleDeKey(src).matchAll(/color\s*:\s*['"`]?(?:rgba|hsla)\([^)]*[,/]\s*([-\d.eE+]+)\s*(%)?\s*\)/g)) {
    if (tiny(m[1], m[2])) hits.push(m[0].trim())
  }
  // Round-50 review: the single-argument zero-scale regex could not see a MULTI-argument zero
  // (`scale3d(0,0,0)`, `scale(0, 0)`) nor a zero in one axis (`scale(0, 0.5)` — zero WIDTH, so the
  // alert is just as invisible). Read the whole argument list.
  for (const m of styleDeKey(src).matchAll(/scale[A-Za-z0-9]*\s*\(([^)]*)\)/g)) {
    // Round-54 review: `scale()` also accepts a SPACE-separated argument list (`scale(0 1)` is
    // zero width), so splitting on the comma alone left that spelling unread.
    const args = m[1].split(/[,\s]+/).map((x) => Number(x.replace(/[^\d.eE+-]/g, '')))
    if (args.some((v) => Number.isFinite(v) && v === 0)) hits.push(m[0].trim())
  }
  return hits
}

// Round-47 review: the guard pin was a PREFIX match, so a guard that is well-formed but
// PERMANENTLY FALSE silenced the failure affordance with the suite green — the cheapest
// spellings being `{copy.failed && copy.copied && (…)}` (`copied` is false exactly when
// `failed` is true), the self-contradicting `{copy.failed && !copy.failed && (…)}`, and the
// branch-swapped ternary `{copy.failed ? null : (<p role="alert">…)}`. Decide the OPERAND LIST
// and the BRANCH, not just the spelling.
function assertGuardOperands(node, what) {
  const tagAt = node.search(/<[a-z]/)
  assert.ok(tagAt > 0, `${what}: no JSX element found after the guard`)
  const region = node.slice(0, tagAt)
  assert.equal((region.match(/failed/g) || []).length, 1,
    `${what}: the guard must test \`failed\` EXACTLY once — a second \`failed\` operand makes it permanently false — got \`${region.trim()}\``)
  // Round-50 review: the ban was the single word `copied`, so the SAME permanently-false guard
  // was reachable through any other state predicate — `{copy.failed && copy.attempt === 0 && (…)}`
  // (`attempt` is >= 1 whenever `failed` is true, so the alert never renders on a rejected write)
  // was GREEN because the evaluator only decides CONSTANTS. The guard may reference `copy.failed`
  // and nothing else.
  // Round-51 review: the ban was the literal-dot spelling, so the same permanently-false guard was
  // reachable as `copy . attempt === 0` or `copy['attempt'] === 0`. Resolve the MEMBER ACCESS.
  for (const acc of foldStrings(region).matchAll(/copy\s*(?:\?\.|\.|\[\s*(?:['"`]([^'"`]*)['"`]|([\w$]+))\s*\])\s*([\w$]*)/g)) {
    const key = acc[1] ?? acc[2] ?? acc[3]
    assert.equal(key, 'failed',
      `${what}: the guard must reference ONLY \`copy.failed\` — any other \`copy.*\` operand is a state predicate the seam can make permanently false (a hidden failure report) — found \`copy.${key}\` in \`${region.trim()}\``)
  }
  assert.doesNotMatch(region, /\?[\s\S]*:/,
    `${what}: a ternary whose \`:\` precedes the node puts the alert in the FALSY branch (it renders at idle and never on failure) — got \`${region.trim()}\``)
  // Round-48 review: the scan above only saw a ternary whose COLON precedes the node. Wrapping
  // the node in a parenthesised constant-false ternary — `{copy.failed && (false ? (<p
  // role="alert">…)}` — puts the alert in a dead branch with the colon AFTER the node, and the
  // failure has no visible affordance. Decide each ternary CONDITION by VALUE, wherever it sits.
  for (let k = region.indexOf('?'); k >= 0; k = region.indexOf('?', k + 1)) {
    if (region[k + 1] === '.') continue
    let b = k
    while (b > 0 && !/[({,?:&|]/.test(region[b - 1])) b -= 1
    const cond = evalConst(region.slice(b, k))
    assert.ok(!(cond.ok && !cond.value),
      `${what}: a ternary in the guard whose condition is a CONSTANT FALSY puts the alert in a dead branch (it never renders) — got \`${region.trim()}\``)
  }
}

function deadGateValues(src) {
  const hits = []
  for (let i = 0; i < src.length; i += 1) {
    if (src[i] !== '{') continue
    // The condition ends at the JSX it gates (or a bounded window, for a `{a && b}` without
    // markup). `<` is only a cut when it starts a tag — `{1 < 2 && x}` is a comparison.
    const rest = src.slice(i + 1, i + 400)
    const tag = rest.search(/<[A-Za-z/]/)
    const window = tag > -1 ? rest.slice(0, tag) : rest
    if (!window.includes('&&')) continue
    const r = evalConst(window)
    if (r.ok && !r.value) hits.push(`${src.slice(i, i + 1)}${window.trim()}`)
  }
  return hits
}

// A constant TERNARY is the same dead-gate class, and an `&&`-only detector cannot see it:
// `{false ? (<button …/>) : null}` renders `null` and removes the control entirely. The gate
// body must be read to its MATCHING brace (not cut at `<`), because the alternative sits AFTER
// the JSX consequent — the one place where a truncated window cannot decide the value.
function gateBody(src, open) {
  let depth = 0
  let quote = null
  for (let i = open; i < src.length; i += 1) {
    const c = src[i]
    if (quote) {
      if (c === '\\') i += 1
      else if (c === quote) quote = null
      continue
    }
    if (c === '"' || c === "'" || c === '`') { quote = c; continue }
    if (c === '{') depth += 1
    else if (c === '}') { depth -= 1; if (depth === 0) return src.slice(open + 1, i) }
  }
  return ''
}

function topLevel(text, ch) {
  let depth = 0
  let quote = null
  for (let i = 0; i < text.length; i += 1) {
    const c = text[i]
    if (quote) {
      if (c === '\\') i += 1
      else if (c === quote) quote = null
      continue
    }
    if (c === '"' || c === "'" || c === '`') { quote = c; continue }
    if (c === '(' || c === '[' || c === '{') depth += 1
    else if (c === ')' || c === ']' || c === '}') depth -= 1
    else if (c === ch && depth === 0) return i
  }
  return -1
}

function deadTernaries(src) {
  const hits = []
  for (let i = 0; i < src.length; i += 1) {
    if (src[i] !== '{') continue
    const body = gateBody(src, i)
    if (!body.includes('?')) continue
    const q = topLevel(body, '?')
    if (q < 0) continue
    const cond = evalConst(body.slice(0, q))
    if (!cond.ok) continue
    const rest = body.slice(q + 1)
    const colon = topLevel(rest, ':')
    if (colon < 0) continue
    // Only the branch that actually RENDERS decides the gate; the JSX one is unknown, so a
    // live `{true ? <X/> : null}` is not a false red.
    let branch = cond.value ? rest.slice(0, colon) : rest.slice(colon + 1)
    const tag = branch.search(/<[A-Za-z/]/)
    if (tag > -1) branch = branch.slice(0, tag)
    const r = evalConst(branch)
    if (r.ok && !r.value) hits.push(body.trim().slice(0, 60))
  }
  return hits
}

// …and a `copy.failed && <CONSTANT FALSY>` conjunction never renders the alert, however many
// redundant parens / `!` / `Boolean(…)` wrappers sit between the two, and however deep in the
// chain the falsy sits (`copy.failed && true && false && (`) — `&&` is associative (rounds 36-38).
function composedFalsyAfterGuard(src) {
  const hits = []
  const re = new RegExp(`${FAILED_REF}\\s*\\)*\\s*&&`, 'g')
  let m
  while ((m = re.exec(src))) {
    const rest = src.slice(m.index + m[0].length, m.index + m[0].length + 300)
    const tag = rest.search(/<[A-Za-z/]/)
    const window = tag > -1 ? rest.slice(0, tag) : rest
    const r = evalConst(window)
    if (r.ok && !r.value) hits.push(`${m[0]}${window.trim()}`)
  }
  return hits
}

function buildCopyHelpers(deps) {
  const src = [
    ...COPY_CONSTS.map((n) => extractConst(mainJsx, n)),
    ...COPY_DECLS.map((d) => extractDeclaration(mainJsx, d)),
  ].join('\n')
  const params = Object.keys(deps)
  const body = `${src}\nreturn { runCopyAttempt, copyLabel, selectCopyTarget }`
  return new Function(...params, body)(...params.map((n) => deps[n]))
}

// ── the REAL observed-write seam and the REAL label ─────────────────────────
function copyEnv(clipboard, extraDeps = {}) {
  // `undefined` means "no clipboard object at all" — the case `?.` used to
  // swallow silently. The attempt must still report a failure.
  const navigator = clipboard === undefined ? {} : { clipboard }
  const scheduled = []
  const helpers = buildCopyHelpers({
    navigator,
    setTimeout: (fn, ms) => { scheduled.push({ fn, ms }); return scheduled.length },
    ...extraDeps,
  })
  return { ...helpers, scheduled }
}

// A stateful setter: `runCopyAttempt` publishes values AND a functional updater
// (the flash reset), so the log must resolve the updater against the last state.
function stateLog() {
  const seen = []
  let state
  const setCopy = (next) => {
    state = typeof next === 'function' ? next(state) : next
    seen.push(state)
  }
  return { seen, setCopy, current: () => state }
}

test('#2935: a REJECTED clipboard write is never reported as a copy', async () => {
  // The reported production mechanism: Playwright without
  // `grant_permissions(["clipboard-write"])` → `writeText` rejects with "Write
  // permission denied", while the old button label read "Copied ✓".
  const { runCopyAttempt, copyLabel } = copyEnv({
    writeText: async () => { throw new Error("Failed to execute 'writeText' on 'Clipboard': Write permission denied") },
  })
  const log = stateLog()
  await runCopyAttempt('tt_live_key', log.setCopy, { current: 0 })
  assert.equal(log.seen.length, 1, 'a rejected write publishes exactly one outcome')
  assert.deepEqual(log.seen[0], { copied: false, failed: true, attempt: 1 },
    'the only state a rejected write may publish is failure')
  assert.notEqual(copyLabel(log.seen[0], 'Copy'), 'Copied ✓',
    'the Copy label must not claim a copy the clipboard refused')
  assert.notEqual(copyLabel(log.seen[0], 'Copy URL'), 'Copied ✓',
    'nor may the Copy URL control claim it')
})

test('#2935: a RESOLVED clipboard write is reported as a copy — and only then', async () => {
  const written = []
  const { runCopyAttempt, copyLabel, scheduled } = copyEnv({ writeText: async (v) => { written.push(v) } })
  const log = stateLog()
  await runCopyAttempt('tt_live_key', log.setCopy, { current: 0 })
  assert.deepEqual(written, ['tt_live_key'], 'the exact text is handed to the clipboard')
  assert.deepEqual(log.seen, [{ copied: true, failed: false, attempt: 1 }])
  assert.equal(copyLabel(log.seen[0], 'Copy'), 'Copied ✓',
    'the label flips to the success state only for an observed write')
  assert.equal(scheduled.length, 1, 'a resolved write schedules its flash reset')
  assert.equal(scheduled[0].ms, 1600, 'the success flash lasts 1.6s')
  scheduled[0].fn()
  assert.deepEqual(log.current(), { copied: false, failed: false, attempt: 1 },
    'the flash reset returns the control to idle')
})

test('#2935: no outcome is published while the write is in flight', async () => {
  // The old defect set success BEFORE the promise resolved. A write still in
  // flight must publish NOTHING.
  let settle
  const pending = new Promise((r) => { settle = r })
  const { runCopyAttempt } = copyEnv({ writeText: () => pending })
  const log = stateLog()
  const inFlight = runCopyAttempt('tt_live_key', log.setCopy, { current: 0 })
  assert.deepEqual(log.seen, [], 'nothing may be announced before the write settles')
  settle()
  await inFlight
  assert.deepEqual(log.seen.at(-1), { copied: true, failed: false, attempt: 1 })
})

test('#2935: a missing or broken clipboard is a failure, never a silent success', async () => {
  // Round-52 review: the exec shapes below prove the BEHAVIOUR; this is the wiring backstop that
  // the seam reads the write's SHAPE (a thenable) instead of awaiting it blindly.
  // Round-53 review: this read the RAW source (comments included), so a COMMENT spelling the
  // check satisfied it with no executing guard at all. Read comment-stripped code.
  assert.match(extractDeclaration(mainJsxCode, 'async function runCopyAttempt'),
    // either polarity of the shape check is the same guard (the shipped form fails closed).
    /typeof\s+[A-Za-z_$][\w$]*\??\.then\s*(?:===|!==)\s*['"`]function['"`]/,
    'the seam must require the clipboard write to return a THENABLE before it may report success')
  assert.doesNotMatch(extractDeclaration(mainJsx, 'async function runCopyAttempt'),
    /if\s*\(\s*!\s*\w+\s*\|\|\s*typeof\s+\w+\s*===?\s*['"`](?:string|number|boolean)['"`]/,
    'the shape check must test the THENABLE requirement, not enumerate non-thenable TYPES (an enumeration always misses one)')
  // `navigator.clipboard?.writeText(...)` returned `undefined` for a missing
  // clipboard and then reported success anyway. The observed seam reports the
  // failure for every shape where no write could have happened — and because the
  // shapes throw SYNCHRONOUSLY, this is also the case where the failure state
  // must carry a NEW attempt token for the keyed alert to re-announce it.
  const shapes = [
    ['no clipboard object', undefined],
    ['clipboard without writeText', {}],
    ['writeText not callable', { writeText: 'not a function' }],
    // Round-52 review: those three all THROW SYNCHRONOUSLY, so the one member of the family that
    // RESOLVES was untested — a callable `writeText` returning NOTHING (a page-level shim, an
    // extension overriding `navigator.clipboard`, a non-conforming polyfill). `await undefined`
    // resolves, so the seam manufactured a "Copied ✓" over an untouched clipboard: the #2935 harm
    // exactly. The seam now requires a THENABLE and fails closed otherwise.
    ['writeText returns undefined', { writeText: () => {} }],
    ['writeText returns null', { writeText: () => null }],
    ['writeText returns a non-thenable object', { writeText: () => ({ ok: true }) }],
    ['writeText returns a non-thenable string', { writeText: () => 'done' }],
    // Round-53 review: the four shapes above are EXACTLY the ones a naive "reject nullish / an
    // object without `then` / a string" check also rejects, so the THENABLE requirement itself was
    // unpinned — `writeText: () => 7` (a primitive that is neither nullish nor an object nor a
    // string) still published `{copied:true}`. Pin the requirement, not a spelling of the check.
    ['writeText returns a truthy number', { writeText: () => 7 }],
    ['writeText returns a truthy boolean', { writeText: () => true }],
    ['writeText returns 0', { writeText: () => 0 }],
    ['writeText returns a non-thenable function', { writeText: () => (() => {}) }],
  ]
  for (const [name, clipboard] of shapes) {
    const { runCopyAttempt, copyLabel } = copyEnv(clipboard)
    const log = stateLog()
    const ref = { current: 0 }
    await runCopyAttempt('tt_live_key', log.setCopy, ref)
    await runCopyAttempt('tt_live_key', log.setCopy, ref)
    assert.deepEqual(log.seen.map((s) => s.failed), [true, true], `${name}: must publish failure`)
    assert.notEqual(copyLabel(log.seen.at(-1), 'Copy'), 'Copied ✓', `${name}: must not claim a copy`)
    assert.deepEqual(log.seen.map((s) => s.attempt), [1, 2],
      `${name}: a repeated failure must carry a new token so the keyed alert remounts`)
  }
})

test('#2935: an earlier write settling late cannot publish over a newer attempt', async () => {
  // A second click during an in-flight write starts a newer attempt; the first
  // write must not publish when it eventually settles (last-wins).
  let settleFirst
  let settleSecond
  const first = new Promise((r) => { settleFirst = r })
  const second = new Promise((r) => { settleSecond = r })
  const queue = [first, second]
  let n = 0
  const { runCopyAttempt } = copyEnv({ writeText: () => queue[n++] })
  const log = stateLog()
  const ref = { current: 0 }
  const stale = runCopyAttempt('tt_live_key', log.setCopy, ref)
  const fresh = runCopyAttempt('tt_live_key', log.setCopy, ref)
  settleSecond()
  await fresh
  const afterFresh = log.seen.map((s) => ({ ...s }))
  settleFirst()
  await stale
  assert.deepEqual(log.seen, afterFresh, 'the stale attempt must publish nothing')
  assert.equal(log.seen.at(-1).attempt, 2, 'the newer attempt owns the outcome')
})

test('#2935: the success reset is bound to its own attempt token', async () => {
  // (a) TWO SUCCESSES: `cur.copied` alone would still be true, so only the
  // `cur.attempt === attempt` conjunct stops the first timer wiping the second
  // attempt's flash. This is the shape that discriminates the token guard.
  const a = copyEnv({ writeText: async () => {} })
  const logA = stateLog()
  const refA = { current: 0 }
  await a.runCopyAttempt('tt_live_key', logA.setCopy, refA) // attempt 1
  await a.runCopyAttempt('tt_live_key', logA.setCopy, refA) // attempt 2
  assert.equal(a.scheduled.length, 2)
  a.scheduled[0].fn() // attempt 1's reset fires late
  assert.deepEqual(logA.current(), { copied: true, failed: false, attempt: 2 },
    'a stale reset must not clear a newer attempt’s success')
  a.scheduled[1].fn()
  assert.deepEqual(logA.current(), { copied: false, failed: false, attempt: 2 },
    'the matching reset clears its own success')

  // (b) a success replaced by a FAILURE: the `cur.copied` conjunct covers this
  // (a reset bound to a failure never schedules one), and the stale token still
  // must not clear the newer failure.
  let n = 0
  const b = copyEnv({ writeText: async () => { n += 1; if (n === 2) throw new Error('denied') } })
  const logB = stateLog()
  const refB = { current: 0 }
  await b.runCopyAttempt('tt_live_key', logB.setCopy, refB) // success
  await b.runCopyAttempt('tt_live_key', logB.setCopy, refB) // failure
  assert.equal(b.scheduled.length, 1, 'only the success scheduled a reset')
  b.scheduled[0].fn()
  assert.deepEqual(logB.current(), { copied: false, failed: true, attempt: 2 },
    'an earlier attempt’s reset must not clear a newer attempt’s failure')
})

test('#2935: a target change supersedes an in-flight attempt', async () => {
  // The component's `[text]` effect BUMPS the counter (never resets it): a write
  // started for a prompt/key that has since been replaced must not publish its
  // outcome onto the new target, and the monotonic counter is what makes the
  // superseded attempt's token stale.
  let settle
  const pending = new Promise((r) => { settle = r })
  const { runCopyAttempt } = copyEnv({ writeText: () => pending })
  const log = stateLog()
  const ref = { current: 0 }
  const stale = runCopyAttempt('prompt A', log.setCopy, ref)
  ref.current += 1 // the target changed mid-write
  settle()
  await stale
  assert.deepEqual(log.seen, [], 'a write for a superseded target must not publish')
})

test('#2935: a rejected copy selects the control’s text — and only a rejecting, CURRENT one does', async () => {
  // Round-3 review (ux): the message asks the user to select and copy by hand,
  // but the inline controls' `<code>` is not keyboard-focusable. Selecting the
  // text (as the #4330/#4342 reveal fallback does) gives a keyboard-only user a
  // working path. The message does NOT claim a selection, so a miss stays honest.
  //
  // Round-4 review: a correct HELPER proves nothing if its one CALLER does not
  // invoke it (deleting `selectCopyTarget(button)` from `runCopyAttempt` used to
  // leave every test green), so this drives the real seam. It also pins the two
  // ways the previous placement lied: selecting for a SUPERSEDED attempt (moves
  // the user's selection with no alert), and a throw inside the fallback
  // suppressing the failure report entirely (silence = the #2935 defect class).
  const events = []
  const target = { __target: true }
  const doc = { createRange: () => ({ selectNodeContents: (el) => events.push(['select', el]) }) }
  const win = {
    getSelection: () => ({
      removeAllRanges: () => events.push(['remove']),
      addRange: () => events.push(['add']),
    }),
  }
  const button = {
    parentElement: { querySelector: (sel) => (sel === 'code, pre' ? target : null) },
    closest: () => null,
  }
  const selection = () => [['select', target], ['remove'], ['add']]

  // (a) the caller drives the fallback on a rejecting write.
  const rejected = copyEnv({ writeText: async () => { throw new Error('denied') } }, { document: doc, window: win })
  const logA = stateLog()
  await rejected.runCopyAttempt('tt_live_key', logA.setCopy, { current: 0 }, button)
  assert.deepEqual(events, selection(), 'a rejected write must select the control’s text')
  assert.deepEqual(logA.seen, [{ copied: false, failed: true, attempt: 1 }])

  // (b) a RESOLVED write must not touch the selection.
  events.length = 0
  const resolved = copyEnv({ writeText: async () => {} }, { document: doc, window: win })
  await resolved.runCopyAttempt('tt_live_key', stateLog().setCopy, { current: 0 }, button)
  assert.deepEqual(events, [], 'a successful copy must not move the user’s selection')

  // (c) a SUPERSEDED attempt must not select the NEW target’s text with no alert.
  events.length = 0
  const ref = { current: 0 }
  const stale = copyEnv({ writeText: async () => { throw new Error('denied') } }, { document: doc, window: win })
  const pending = stale.runCopyAttempt('old prompt', stateLog().setCopy, ref, button)
  ref.current += 1 // the target changed while the write was in flight
  await pending
  assert.deepEqual(events, [], 'a superseded attempt must not select text it never tried to copy')

  // (d) a throw INSIDE the fallback must not erase the failure report.
  const boom = copyEnv(
    { writeText: async () => { throw new Error('denied') } },
    { document: { createRange: () => { throw new Error('Range unavailable') } }, window: win },
  )
  const logD = stateLog()
  await boom.runCopyAttempt('tt_live_key', logD.setCopy, { current: 0 }, button)
  assert.deepEqual(logD.seen, [{ copied: false, failed: true, attempt: 1 }],
    'a fallback throw must still publish the failure (silence is the defect)')

  // (e) the card shape and a miss, through the helper directly: the card's own
  // actions row holds no text, so the prompt `<pre>` is reached via the card.
  const miss = []
  const cardEnv = buildCopyHelpers({
    document: { createRange: () => ({ selectNodeContents: (el) => miss.push(['select', el]) }) },
    window: { getSelection: () => ({ removeAllRanges: () => miss.push('remove'), addRange: () => miss.push('add') }) },
  })
  let closestSel = null
  cardEnv.selectCopyTarget({
    parentElement: { querySelector: () => null },
    closest: (sel) => {
      closestSel = sel
      return sel === '.wizard-prompt-card' ? { querySelector: (q) => (q === 'pre' ? target : null) } : null
    },
  })
  assert.equal(closestSel, '.wizard-prompt-card')
  // Round-22 review: the stub DISCARDED its argument, so this asserted that
  // *something* was selected, not that the prompt was — `|| card` (select the whole
  // card, so ⌘C on a failure also copies the button label and the error sentence)
  // stayed green. Record the element and require it to be the `<pre>` itself.
  assert.deepEqual(miss, [['select', target], 'remove', 'add'],
    'the card prompt <pre> itself must be selected')
  miss.length = 0
  cardEnv.selectCopyTarget({ parentElement: { querySelector: () => null }, closest: () => null })
  cardEnv.selectCopyTarget(undefined)
  assert.deepEqual(miss, [], 'a miss (and no button) is a silent no-op')
})

test('#2935: a failure falls back to the control’s OWN idle label', async () => {
  // Positive control for the negation above: `idleLabel` must be the real label,
  // not a hardcoded 'Copy' (which would relabel the "Copy URL" control).
  const { copyLabel } = copyEnv({ writeText: async () => {} })
  assert.equal(copyLabel({ copied: false, failed: true }, 'Copy URL'), 'Copy URL')
  assert.equal(copyLabel({ copied: false, failed: false }, 'Copy the connect prompt'), 'Copy the connect prompt')
  assert.equal(copyLabel({ copied: true, failed: false }, 'Copy URL'), 'Copied ✓')
})

// ── static backstops for the parts execution cannot reach (the JSX) ─────────
// Round-1 review (P1): execution only proves the HELPERS are correct. A control
// handler reverted to the unobserved `navigator.clipboard.writeText(...);
// setCopy(success)` shape passed every executed test. These WIRING pins close
// that hole; the semantics remain the executed tests above.
//
// Round-54 review — the last five GREEN shapes, and one false red, of the harness:
// (a) a CONSTANT-FOLDED key (`['flex'+'Wrap']`, `copy['att'+'empt']`) evaluates to the real
// name at runtime, so `foldStrings` folds adjacent string LITERALS before the style-key
// normaliser and the guard's member resolver run. A key folded some other way (`String(x)`,
// a variable, a template interpolation) is an accepted cost.
// (b) `scale()` takes a SPACE-separated argument list too (`scale(0 1)` is zero width), so the
// argument list is split on commas AND whitespace.
// (c) a JSX SPREAD is last-wins like a duplicate prop, so `assertNoSpreadOverride` rejects a
// literal spread object that re-binds a pinned prop (`{...{ text: undefined }}`,
// `{...{ className: 'error small' }}`, `{...{ key: 0 }}`, `{...{ onClick: undefined }}`).
// ACCEPTED COST: the spread's keys are read textually, so `{...base}` whose contents are
// defined elsewhere is not inspected.
// (d) the monotonic bump must be a POSITIVE NUMERIC LITERAL, so `+= 1 - 2` (a runtime
// DECREMENT that lets a superseded token be reused) is caught. ACCEPTED COST: a named
// constant (`+= ONE`) is a false red — restate it as `+= 1` if the suite reddens.
// (e) FALSE RED fixed: every `copy.failed` reference is accepted in its DOT, OPTIONAL-CHAIN
// and BRACKET (`copy['failed']`) member forms — `FAILED_REF` — since all three are the same
// predicate. Previously the dot spelling was required in the PICK, the guard-prefix pin, the
// composed-falsy scan and the build-fork structural scan, so `copy['failed']` died before the
// resolver that already understood it ever ran.
//
// LIMITS (round-4 review, extended rounds 17-18 — stated rather than over-promised):
// each pin protects one SHAPE, so a refactor that MOVES that shape out of the
// pinned region reddens the suite even though behaviour is unchanged — e.g. hoisting
// the alert JSX into a named const, extracting the `[text]` effect body into a named
// function, or an equivalent spelling of `+= 1` / `key={copy.attempt}`. That is the
// deliberate cost of pinning JSX from Node with no renderer: update the pin alongside
// such a refactor rather than reading the failure as a regression.
//
// Where a spelling is BEHAVIOUR-PRESERVING and cheap to allow, it is allowed rather
// than accepted as a cost: padded braces, whitespace after commas, a `{` on its own
// line, redundant parens on either alert guard, `copy?.failed` / `copy?.copied` /
// `copy?.attempt`, `null`/`undefined`/`false` as the live region's empty else, `'12px'` for
// `12`, a trailing comma (before OR after the `]`) or extra whitespace (even a line break)
// in the `[text]` dep array, whitespace around an attribute `=` — including on the
// live-region PICK and the `className` pins (round 27), a quoted zero
// (`margin: '0'`), a quoted `fontSize: '12px'` / `flexBasis: '100%'` (any of the three
// quotes, round 27), and an attribute/style reorder on the alert or live region all
// stay GREEN (rounds
// 18-19 review — each of those was previously a false red carrying a misleading "no
// hardcoded success text" message). Round-24 review adds the GUARD'S boolean shape:
// `!!copy.failed` / `Boolean(copy.failed)` / `!(!copy.failed)` / a ternary
// (`... ? (<p...>) : null`) all render the alert exactly as `copy.failed && ...` does,
// including in combination with any number of parens, so all are GREEN.
//
// Round-26 review — a REGRESSION this file caused and now guards. Widening a guard pin
// to tolerate `!!`/`Boolean(`/parens also admitted a SINGLE leading `!`, so
// `{!copy.failed && (` satisfied the pin AND the alert pick, which then validated the
// inverted node: the alert rendered while IDLE and vanished on failure. Both suites were
// green. A regex cannot count bangs, so the guard is analysed by PREFIX (round 26): the
// text between `{` and `copy.failed` must name ONLY `copy.failed` and carry an EVEN `!`
// count. Two other round-26 regressions of the same shape are fixed the same way: the
// live region's sentence is now removed from the PICKED NODE, never by a global replace
// (a global strip erased a SECOND copy of the sentence, so an unconditional
// `<p>{'Copied to clipboard'}</p>` — or a second polite `role="log"` — shipped green),
// and `\uXXXX` escapes are DECODED before the scan, so `'Cop\u0069ed'` is `'Copied'`.
//
// Round-27 review (a P1 this file shipped green) — the label route to a false success.
// `WizardPromptCard` call sites had NO wording pin, so a module-scope constant map
// (`const LABELS = { connect: 'Copied ✓' }`) plus `label={LABELS.connect}` rendered
// "Copied ✓" at IDLE on the primary control. The declaration ban is now
// initialiser-AGNOSTIC (a token anywhere up to a blank line), and each card's `label`
// prop is pinned to the two shipped caption maps — a shape pin, so a label expression
// moved out of that region is an accepted cost (declared above), not a false red.
// A producer FUNCTION that returns the token (`function f() { return 'Copied ✓' }`) is
// the same unbounded class as the `${…}` and synonym KNOWN LIMITs below.
//
// Hiding is additionally checked by VALUE, not only by spelling: any `opacity`/`zoom`
// on an alert must be >= 0.05, so a tiny non-zero value cannot slip past the
// digit-shape-bounded `ZERO` (round 27, exponent-aware since round 28 — `1e-9` used to
// parse as `1`).
//
// Round-28 review — hiding is now partly STRUCTURAL rather than a spelling list, which is
// the right shape for the enumerable cases: `visibility` bans every value except the
// visible keywords (`collapse` was a spelling the list missed), `clip-path` bans every
// value except the off keywords (`circle(0)`/`polygon(0 0)`), and `transform` matches
// `scale*` rather than the literal `scale(` (`scaleX(0)`/`scaleY(0)`/`scale3d(0,…)`).
// `ZERO` also accepts a leading `.` (`.0px` was zero but looked like a non-zero) and
// guards the character BEFORE the run (`100%` used to match the `00`). The ban is ALSO
// applied at the CONNECT-STEP call site, on each control's own tag and on its nearest
// enclosing `<div>`.
//
// Round-30 review: the card payloads are pinned as a MULTISET of builder CALLS (the builder
// name alone let the wrong step, the wrong surface, or `undefined` in the key slot through).
// Round-31 review made that comparison respelling-tolerant (shape with literals elided, plus
// the literal contents separately), so quote style, redundant parens and comma/brace
// whitespace stay green while a changed argument does not.
//
// Round-31 review also closed three holes this file had claimed shut: the success token now
// covers the HEX heavy check (`&#x2714;`) and `deEsc` decodes `\u{…}` AND `\xXX` (the UTF-8
// byte spelling of the glyph); a literal `{false && …}` gate at the CONNECT-STEP call site is
// banned (it removed the control and its failure report with every pin green); and the
// call-site wrapper scan matches ANY element, not just `<div>`. The `overflow: hidden`
// exemption now covers the `overflowX`/`overflowY` longhands too.
//
// One accepted cost this file does NOT control: `wizardConnectTripwire.test.js` (pre-existing,
// not part of this change) is stricter about whitespace around `=` on the card's `label`
// attribute, so `label = {WIZARD_CAPTIONS.connectLabel}` is GREEN here and RED there. That is
// the sibling file's pin, not a gap in this one.
//
// Round-29 review closed two gaps of this file's own making: the button caption argument
// must again START WITH the `label` identifier (a literal caption discards each call site's
// own wording and shipped green once round 28's paren-tolerance edit dropped the
// requirement), and every card must CARRY a `label` prop (previously validated only when
// found, so dropping it was green). Whitespace around `=` is tolerated on the payload and
// `label` pins too.
//
// Round-34 review: three more closures. A JS object literal is LAST-WINS, so every
// `fontSize` on an alert must be 12 and every `opacity`/`zoom` must be >= 0.05 — reading only
// the FIRST occurrence let a later duplicate key render the failure invisible. A declaration
// initialiser may not COMPUTE the token via `.concat`/`.reduce`, nor INTERPOLATE it
// (`` `Cop${'ied'}` ``) when the same initialiser also carries a success word fragment.
// Whitespace BEFORE a style colon (`display : 'block'`) is rendering-identical and now
// allowed, and `false` is accepted as the live region's empty else (it renders as nothing).
//
// Round-35 review: three more closures — `.concat(`/`.reduce(` joined `SYNTHESIS` at RENDER
// sites (not just in declarations); `deJoin` also joins JSX JUXTAPOSITION (`{'Cop'}{'ied'}`,
// `Cop{'ied'}`), which is concatenation without a `+`; a control must START from `COPY_IDLE`
// and may never carry another truthy `copied:` (a truthy initialiser announces success at
// mount, and the `copied: true` count only read that one spelling); and a DUPLICATE JSX prop
// (compiled last-wins) is banned, since every pin reads the first match.
//
// Round-37 review: a dead JSX gate and a guard composed with a constant falsy are decided by
// VALUE through a small constant-expression evaluator (`constValue`/`deadGateValues`/
// `composedFalsyAfterGuard`), because a spelling list cannot tell `!0` (truthy) from `!1`
// (dead) and cannot see through `(…)`/`Boolean(…)`/`void 0`. The handler parameter's parens and
// whitespace inside a JSX attribute VALUE's braces are both behaviour-preserving and GREEN.
//
// Round-36 review: `deJoin` joins all THREE juxtaposition directions (expr+expr, text+expr,
// expr+text); the failure guard may not be composed with a constant FALSY after it
// (`copy.failed && false && (` renders nothing); and `useState({ ...COPY_IDLE })` — the
// declared spread respelling — is accepted, since the truthy-`copied:` ban already covers
// `{ ...COPY_IDLE, copied: !0 }`.
//
// The two lookahead bans consume their own whitespace on purpose: with `\s*` outside the
// lookahead, the engine backtracks to zero spaces and evaluates it AT the space, banning
// `visibility: 'visible'` itself (a round-28 false red caught by the battery).
//
// Round-46 review: a JS object literal is LAST-WINS, so a duplicated style key reverted a pin
// while `assert.match` read the first occurrence (`styleEntries`/`assertStyleKeysUnique`), and
// the hiding checks now read the NUMBERS (opacity/zoom/filter/alpha) instead of a spelling list.
// Round-47 review extends both: a COMPUTED key (`['flexWrap']: 'nowrap'`, `['opacity']: 0.01`) is
// normalised to its identifier first (`styleDeKey`), a `%` alpha is scaled (`opacity(4%)` is
// 0.04), the alert's class list is pinned POSITIVELY to exactly `error` (a joined-pair ban was
// defeated by `{'error' + ' '.repeat(1) + 'small'}`), and the failure guard is decided by its
// OPERAND LIST and BRANCH, not a prefix: `{copy.failed && copy.copied && (…)}`, the
// self-contradicting `{copy.failed && !copy.failed && (…)}` and the branch-swapped
// `{copy.failed ? null : (<p role="alert">…)}` all render no failure while a prefix pin saw a
// well-formed guard. DECLARED LIMIT of that read (round 53 CORRECTED it: a PLAIN backtick
// template key IS normalised, because `Q` includes the backtick — the earlier note claimed
// otherwise): `styleDeKey` handles a QUOTED computed key — `'x'`, `"x"` and `` `x` `` — and, since
// round 53, redundant parens around it (`[('x')]`); an INTERPOLATED template (`` [`flex${''}Wrap`] ``)
// or a VARIABLE key is not normalised. The `%` alpha applies to `filter`/`rgba`/`hsla` only, and the
// guard check counts textual `failed` operands (a guard reaching the same boolean through an alias
// is not seen).
//
// Round-48 review: two further closures — a BARE quoted object key (`"flexWrap": 'nowrap'`,
// `"fontSize": 0`) is a full member name and is last-wins too, so `styleDeKey` normalises it
// (anchored to a `{`/`,` key position, so a ternary's `'a' : 'b'` is untouched); and a literal
// container's `.length` (`[].length`, `String().length`) is a CONSTANT, which the evaluator
// previously called "not provably constant" while `{copy.failed && [].length && (…)}` rendered a
// stray `0` in place of the failure remedy. The guard's ternaries are also decided by VALUE
// wherever they sit, so a parenthesised constant-false consequent (`(false ? (<p role="alert">…`)
// is caught with the colon AFTER the node.
//
// DECLARED LIMIT (constant evaluator): it recognises literals, `!`/`-`/`+`/`~`, `Boolean()`,
// `Number()`, `void`, comparisons/arithmetic/`&&`/`||`, a backtick template without `${…}`, and
// `.length` on an array/`String()`/`Array()` literal. An ARBITRARY constant expression is not
// evaluated — `Math.max()`, `Object.keys({}).length`, `[].slice().length` — so a dead gate
// spelled that way is not caught; the fix for that is a real JS evaluator (or a renderer), not
// another regex arm.
// Round-49 review CORRECTED two over-claims here (a declared limit that is actually enforced
// sends the next reader to "fix" a detector that already works): the evaluator returns the value
// of the FIRST PARSABLE PREFIX, so a falsy prefix is caught whatever follows it — `''.trim().length`
// and `''.anything` ARE caught — and `[].concat([]).length` is caught by the separate SYNTHESIS
// denylist (`.concat(`), not by the evaluator.
//
// KNOWN LIMIT (hiding): a denylist over CSS cannot be complete — a `filter`, a
// `mix-blend-mode`, a class from the stylesheet, or a two-level ancestor wrapper are all
// reachable spellings. Closing it properly needs a RENDERED node (this dashboard has no
// renderer for `main.jsx`) or a browser-side assertion; the spellings above are the ones
// a real edit has been observed to use.
//
// Success text is pinned as an ABSENCE, not a count: once the live region's own
// observed sentence is removed, no success indicator may remain anywhere in the
// control's component OR in the connect step that renders it — so a hardcoded
// `'Copied'`/`'Copy ✓'`/a bare `✓` or `✔`/an entity (`&#10003;`, `&#x2713;`,
// `&#x10004;`)/an escape (`'Copy \u2713'`) reddens at the button, beside it, in the
// alert, as an extra live-region child, or at a CALL SITE. Adjacent literals are
// JOINED before scanning (`'Cop' + 'ied!'` → `'Copied!'`), and the constructs that
// SYNTHESISE text are rejected outright — `String.fromCharCode`, `Array#join`, and a
// `${…}` template interpolation inside either component (all measured absent there).
// Case-SENSITIVE and identifier-bounded on purpose: `copy.copied` and the legacy
// `setWizardCopied(...)` setters are state, not success text. One form is banned
// separately: a DECLARATION bound to a success literal (adjacent literals are joined
// first) or whose initialiser is COMPUTED (`fromCharCode`/`fromCodePoint`/`Array#join`)
// — either launders
// the string past a scope-local scan into an identifier the connect step could render by
// name (measured: neither declaration exists).
//
// KNOWN LIMIT: the connect step legitimately uses `${…}` in its own formatting, so a
// success string stitched by INTERPOLATION at a call site (rather than by `+`, a
// backtick literal, or a bare glyph) is beyond a text scan. Closing it needs a
// renderer (this dashboard has none for `main.jsx`), not a better regex.
//
// Hiding is banned per node AND per component: the alert and the live region each
// reject `display:none`/`visibility:hidden`/`opacity:0`/zero `height`/`maxHeight`/
// `width`/off-screen (`left`/`right`/`top`/`bottom`/`margin*`)/`transform:
// scale(0)|translate*(±3+ digits)`/`clip-path: inset(…)`/ANY `aria-hidden`/the `hidden`
// attribute — quote- (single, double AND backtick), unit- and decimal-tolerant (`'0'`,
// `0.0`, `'0px'`, `` `0` ``) — and each component is held to that SAME ban, so a
// hidden/collapsed/off-screen/clipped ANCESTOR wrapper cannot silence the control either.
// `overflow: hidden` and `clip-path: none` are EXEMPT: they clip/normalise, they do not remove.
//
// Quote STYLE and JSX-EXPRESSION form are both rendering-identical and are allowed
// rather than accepted as costs (this repo's eslint enables exactly two rules and no
// stylistic one): `'x'`/`"x"`, and `className="x"`/`className={'x'}` (likewise
// `role`, `aria-live`, `label`). So are `{ ...COPY_IDLE, attempt }` in place of
// `COPY_IDLE`, `??` for `||`, and any number of redundant parens around either alert
// guard.
//
// SECOND KNOWN LIMIT: the success ban is a TOKEN denylist (`Copied`/`✓`/`✔`/entity/
// escape), so an unconditional SYNONYM success word (`'Done!'`) rendered at any of those
// sites is not caught. A denylist cannot be completed by adding words; closing it needs a
// structural allow-list of the nodes the control may render, i.e. a renderer.
//
// The remedy's VALUE is extracted with any quote style (round 26: a single-quote-only
// extraction THREW a TypeError on a behaviour-preserving quote change instead of failing
// the assertion, which is worse than a false red — it hides the message).
//
// The HANDLER is pinned as well as the effect: `React.useCallback(...)` must depend on
// `text`, because the component is deliberately not remounted when the target changes and
// an empty dep array would copy the PREVIOUS payload while reporting success.
//
// The payloads are pinned by PROPERTY at every call site: each connect-step
// `<WizardPromptCard>` must hand a BUILT prompt (the three text builders) rather than a
// bare value, and each `<InlineCopyButton>` must bind its own `text` (the key or the
// canonical URL) and may not take a success-wording `label`; the component's own
// default label is pinned to the exact idle string `'Copy'`, so an idle-but-WRONG
// wording (`'Copy URL'` on the two API-key controls) cannot slip through.
//
// NOTE the pre-existing `wizardKeyCodeStyle` helper (~line 7217, used by `.key-row`)
// is OUTSIDE this change and therefore unpinned — mutating it neither reddens nor is
// it expected to. Only the two render sites this issue touched carry layout pins.
//
// One case is deliberately NOT caught here and is covered by
// `wizardConnectTripwire.test.js` instead: an EXTRA UNBOUND `<button>` planted inside
// `WizardPromptCard` (that file's copy-control COUNT pin is scoped to the card; a decoy
// inside `InlineCopyButton` is unpinned by both files — it announces nothing and is
// keyboard-inert, so it is a shape cost, not a false report). This file selects the
// button BY its captured handler name (so a decoy can never stand in for it — a decoy
// carrying the handler IS caught, since the selector then binds the decoy).
function cardSlice() {
  const start = mainJsxCode.indexOf('function WizardPromptCard')
  const end = mainJsxCode.indexOf('function WizardBlock')
  assert.ok(start > -1 && end > start, 'the WizardPromptCard slice markers must exist')
  return normaliseState(mainJsxCode.slice(start, end))
}

function inlineSlice() {
  const start = mainJsxCode.indexOf('function InlineCopyButton')
  const end = mainJsxCode.indexOf('function UpgradeCta')
  assert.ok(start > -1 && end > start, 'the InlineCopyButton slice markers must exist')
  return normaliseState(mainJsxCode.slice(start, end))
}

// Round-44 review: the rename is applied to IDENTIFIERS only — a plain `\bWORD\b` replace
// also rewrote the same word inside a JSX string, so renaming the state to `status` turned
// `role="status"` into `role="copy"` and gave a bogus "must render the live region" throw.
// Round-39 review: renaming the state local (or its setter) is behaviour-preserving — the
// file already captures the `useRef` name for exactly that reason — yet a dozen-odd pins spell
// `copy`/`setCopy`. Rename them back to the canonical pair at extraction, so the pins keep
// their teeth without pinning the SPELLING of a local.
function renameIdentifier(src, from, to) {
  let out = ''
  let quote = null
  for (let i = 0; i < src.length; i += 1) {
    const c = src[i]
    if (quote) {
      out += c
      if (c === '\\') { out += src[i + 1] || ''; i += 1 } else if (c === quote) quote = null
      continue
    }
    if (c === '"' || c === "'" || c === '`') { quote = c; out += c; continue }
    // …and never a PROPERTY (`copied.copied` must keep its own key — otherwise a state named
    // `copied` rewrote the payload's property too and the rename looked like a defect).
    // …nor a JSX ATTRIBUTE NAME: a state named `role`/`key` must not rewrite `role="status"`
    // or `key={…}` into `copy=…` (round 46 — an attribute is followed by `=`).
    if (src.startsWith(from, i)
      && src[i - 1] !== '.'
      && !/[\w$]/.test(src[i - 1] || '')
      && !/[\w$]/.test(src[i + from.length] || '')
      && !/^\s*=[^=]/.test(src.slice(i + from.length))) {
      out += to
      i += from.length - 1
      continue
    }
    out += c
  }
  return out
}

function normaliseState(comp) {
  const m = comp.match(/const\s*\[\s*([A-Za-z_$][\w$]*)\s*,\s*([A-Za-z_$][\w$]*)\s*\]\s*=\s*React\.useState/)
  if (!m) return comp
  const [state, setter] = [m[1], m[2]]
  let out = comp
  if (setter !== 'setCopy') out = renameIdentifier(out, setter, 'setCopy')
  if (state !== 'copy') out = renameIdentifier(out, state, 'copy')
  return out
}

function controlSlice() {
  const controls = mainJsxCode.slice(
    mainJsxCode.indexOf('async function runCopyAttempt'),
    mainJsxCode.indexOf('function UpgradeCta'))
  assert.ok(controls.includes('function WizardPromptCard'), 'the slice must cover the card')
  assert.ok(controls.includes('function InlineCopyButton'), 'the slice must cover the inline control')
  return controls
}

function connectStepSlice() {
  // The whole connect step, anchored the same way wizardConnectTripwire does
  // (wizardPasteRow → the step-3 block). Scoping the call-site count here rather
  // than to the whole file means an unrelated screen adding its own
  // `InlineCopyButton` cannot fail this #2935 guard.
  const start = mainJsxCode.indexOf('const wizardPasteRow = (')
  const end = mainJsxCode.indexOf('{wizardStep === 3 && (')
  assert.ok(start > -1 && end > start, 'the connect-step markers must exist')
  return mainJsxCode.slice(start, end)
}

test('#2935: every connect-step control runs the observed attempt — none writes the clipboard directly', () => {
  const controls = controlSlice()
  // The only clipboard write in the control block is the one inside
  // `runCopyAttempt`; a control-local write is the unobserved defect. Matching
  // the optional-chain form too catches the `?.` spelling that silently no-ops a
  // missing clipboard.
  assert.equal((controls.match(/\.clipboard\??\.writeText/g) || []).length, 1,
    'the connect-step controls may write the clipboard only through runCopyAttempt')
  // The ONLY success state in the block is constructed inside `runCopyAttempt`; a
  // control-local `copied: true` is the optimistic false "Copied ✓".
  assert.equal((controls.match(/copied: true/g) || []).length, 1,
    'only runCopyAttempt may publish a success state')
  // BOTH control handlers must call the observed attempt (the card's and the
  // inline's); renaming the handler is fine, bypassing the seam is not.
  // Round-7 review: this scan ran only over the COMPONENT DEFINITIONS, so a
  // fresh fire-and-forget control added at a connect-step CALL SITE was green.
  // The connect step must route every copy through the shared control.
  // Round-8 review: `/navigator\.clipboard/` requires a literal dot, so the
  // `navigator?.clipboard?.writeText(...)` spelling — the shape this file's own
  // header calls out — evaded it. Round 9: a literal-dot pattern also misses
  // `navigator['clipboard']` and `const { clipboard } = navigator`, so ban the
  // NAME, not one spelling. (Comments are stripped and the step legitimately
  // mentions no clipboard/writeText today, so this cannot false-positive.)
  assert.doesNotMatch(connectStepSlice(), /\bclipboard\b/,
    'the connect step must route every copy through the shared observed control')
  assert.doesNotMatch(connectStepSlice(), /\bwriteText\b/,
    'no connect-step call site may write the clipboard itself')
  // Round-13 review: capture the attempt-counter ref PER COMPONENT (the card and
  // the inline control are two independent functions, so renaming one local is a
  // valid refactor), and tolerate whitespace/line breaks at every joint — these
  // call sites are long lines with no formatter configured, so manual wrapping is
  // the realistic edit. A behaviour change still reddens every pin.
  for (const [label, comp] of [['card', cardSlice()], ['inline', inlineSlice()]]) {
    const ref = comp.match(/const ([A-Za-z_$][\w$]*)\s*=\s*React\.useRef\(\s*0\s*\)/)
    assert.ok(ref, `${label}: must declare the attempt-counter ref`)
    const refName = ref[1]
    assert.equal((comp.match(new RegExp(`=>\\s*\\{?\\s*(?:return\\s+)?runCopyAttempt\\(\\s*text\\s*,\\s*setCopy\\s*,\\s*${refName}`, 'g')) || []).length, 1,
      `${label}: the handler must run the observed attempt (never write the clipboard directly)`)
    // The fallback needs the control's element, so the call site must thread a
    // fourth argument (matched only up to the comma, so either spelling and any
    // parameter name is fine). The executed test supplies its own button, so only
    // this pin catches a call site that stopped threading one.
    assert.equal((comp.match(new RegExp(`runCopyAttempt\\(\\s*text\\s*,\\s*setCopy\\s*,\\s*${refName}\\s*,`, 'g')) || []).length, 1,
      `${label}: the call site must thread its button through to the manual-copy fallback`)
    // …and the fourth VALUE must be the element the handler is bound to, not the
    // SyntheticEvent: a SyntheticEvent (and `document.body`) has no `closest`, so
    // `selectCopyTarget` would silently return and the keyboard-only fallback would
    // vanish with nothing failing — and "pass `event` instead of `event.currentTarget`"
    // is the textbook fix for the null-`currentTarget`-after-await footgun. Capture
    // the callback's PARAMETER and require it to be the receiver of `currentTarget`.
    assert.ok(comp.match(new RegExp(
      // Round-37 review: the parameter's PARENS were required, so the behaviour-identical
      // `event => …` was a false red. These pins are about WHICH identifier is threaded.
      `React\\.useCallback\\(\\s*(?:\\(\\s*)?([A-Za-z_$][\\w$]*)\\s*\\)?\\s*=>\\s*\\{?\\s*(?:return\\s+)?runCopyAttempt\\(\\s*text\\s*,\\s*setCopy\\s*,\\s*${refName}\\s*,\\s*\\(?\\s*\\1(?:\\s*&&\\s*\\1)?\\??\\.currentTarget\\s*,?\\s*\\)`)),
      `${label}: the fallback needs the bound element — pass event.currentTarget, not the event`)
    // …and the declared handler must be the thing the button invokes (a no-op
    // binding is a dead Copy control). The name is CAPTURED, so renaming passes.
    const decl = comp.match(new RegExp(
      // Round-37 review: parens optional here too (`event => …` is the same handler).
      `const ([A-Za-z_$][\\w$]*)\\s*=\\s*React\\.useCallback\\(\\s*(?:\\([^)]*\\)|[A-Za-z_$][\\w$]*)\\s*=>\\s*\\{?\\s*(?:return\\s+)?runCopyAttempt\\(\\s*text\\s*,\\s*setCopy\\s*,\\s*${refName}`))
    assert.ok(decl, `${label}: must declare a handler that runs the observed attempt`)
    // Round-38 review: `onClick={(e) => doCopy(e)}` — a wrapper that runs the SAME handler
    // with the same event — is behaviour-preserving, so requiring the bare identifier was a
    // false red. What matters is that the captured handler is INVOKED **with the event**.
    // Round-40 review: the ARGUMENT was unchecked, so `onClick={() => doCopy()}` and
    // `onClick={(e) => doCopy(e.currentTarget)}` shipped green while handing the handler
    // `undefined`/an Element — `event && event.currentTarget` is then `undefined`, and
    // `selectCopyTarget` early-returns, silently deleting #2935's own keyboard fallback.
    // Round-41 review: a BLOCK body (`(e) => { doCopy(e) }`, `{ return doCopy(e) }`) and
    // redundant parens around the body or the argument are the same handler with the same
    // event — the normal shape once a wrapper needs to add a statement — so they are allowed;
    // what stays mandatory is that the wrapper PASSES ITS OWN PARAMETER THROUGH.
    const BIND = (name) => {
      const CALL = `\\(*\\s*${name}\\s*\\(\\s*\\(*\\s*\\1\\s*\\)*\\s*\\)\\s*\\)*\\s*;?`
      return new RegExp(
        `onClick\\s*=\\s*\\{\\s*(?:${name}` +
        `|\\(?\\s*([A-Za-z_$][\\w$]*)\\s*\\)?\\s*=>\\s*` +
        `(?:${CALL}|\\{[^{}]*?${CALL}\\s*\\}))\\s*\\}`)
    }
    assert.match(comp, BIND(decl[1]),
      `${label}: the button must invoke that handler (a no-op binding is a dead Copy control)`)
    // Round-17 review: pin the button's ENTIRE content, selecting it BY the captured
    // handler name. Appending a hardcoded `{'Copied ✓'}` after the label call restored
    // the shipped symptom with every other pin green, and `pick()`-ing the FIRST
    // `<button>` let a decoy button stand in for the real control.
    // Round-38 review: `[^>]*` cannot cross the `>` in `=>`, so an arrow-wrapped binding
    // defeated the pick entirely. Scan the opening tag with a depth/quote-aware reader — a
    // `>` inside braces (or a string) is not the end of the tag.
    let button = null
    for (const m of comp.matchAll(/<button\b/g)) {
      const tag = openingTag(comp, m.index)
      if (!tag || !BIND(decl[1]).test(tag)) continue
      const close = comp.indexOf('</button>', m.index + tag.length)
      if (close > -1) { button = comp.slice(m.index, close + 9); break }
    }
    assert.ok(button, `${label}: the handler must be bound to a rendered button`)
    // Round-29 review: the round-28 paren-tolerance edit replaced `\(?\s*label\b[^)]*\)+`
    // with a bare `[^)]*\)+`, silently DROPPING the property round 19 established — that
    // the caption argument must be the `label` identifier and never a literal. A literal
    // caption (`copyLabel(copy, 'Copy URL')`) then discards each call site's own wording
    // and shipped green. Restored, with the paren tolerance kept.
    assert.match(button, /<button\b[\s\S]*?>\s*\{\s*\(*\s*copyLabel\(\s*\(*\s*copy\s*\)*\s*,\s*\(*\s*label\b[^)]*\)+\s*\)*\s*\}\s*<\/button>/,
      `${label}: the button's ONLY content must be the observed label (no hardcoded success text)`)
    // Round-18 review: the pin above guarded only the INSIDE of the button, and
    // rejected only a SINGLE-quoted literal. Round-19: the `(?!['"`])` guard was
    // DEFEATED by one space (`\s*` sits before the lookahead, so it matched the
    // space and `[^)]*` swallowed the literal) — so the argument is now pinned
    // PROPERTY-wise: it must begin with the `label` identifier, never a literal.
  }
})

test('#2935: the controls render their label and announcements from the observed outcome', () => {
  // Round-10 review: every pin below is scoped to the NODE that must carry it,
  // because component-wide `assert.match` PRESENCE checks are decoy-satisfiable —
  // a hardcoded `{'Copied ✓'}` button beside a dead `{false && copyLabel(...)}`
  // kept them green, i.e. the shipped #2935 symptom passed the suite.
  const card = cardSlice()
  const inline = inlineSlice()
  const pick = (src, re, what) => {
    const m = src.match(re)
    assert.ok(m, `main.jsx must render ${what}`)
    return m[0]
  }
  // Round-27 review: the PICK still spelled `role=` literally, so `role = "status"` threw
  // BEFORE any assertion ran — a behaviour-preserving respelling reported as a MISSING node.
  const LIVE_PICK = /<span[^>]*role\s*=\s*\{?\s*['"`]status['"`]\s*\}?[^>]*>[\s\S]*?<\/span>/
  const cardLive = pick(card, LIVE_PICK, 'the card live region')
  const inlineLive = pick(inline, LIVE_PICK, 'the inline live region')
  // Round-17 review: the alert pins need the WHOLE element (opening tag AND content),
  // or an appended `{' — Copied ✓'}` after the remedy is invisible to them.
  const cardAlert = pick(card, new RegExp(`[(\\s!]*(?:Boolean\\(\\s*)?[(\\s!]*${FAILED_REF}\\s*\\)*\\s*(?:&&|\\?)[\\s\\S]{0,40}?<[a-z]+[^>]*>[\\s\\S]*?<\\/[a-z]+>`), 'the card failure alert')
  const inlineAlert = pick(inline, new RegExp(`[(\\s!]*(?:Boolean\\(\\s*)?[(\\s!]*${FAILED_REF}\\s*\\)*\\s*(?:&&|\\?)[\\s\\S]{0,40}?<[a-z]+[^>]*>[\\s\\S]*?<\\/[a-z]+>`), 'the inline failure alert')
  // Round-15 review: a PRESENCE pin is not liveness. Inverting the guard
  // (`{!copy.failed && …}`), wrapping the node in a literal `{false && …}`, or
  // hiding it (`visibility:'hidden'`, a duplicate `display:'none'`, `opacity: 0`,
  // `height: 0`, or `aria-hidden`) all silenced the failure with every other pin
  // green — which is the #2935 defect class itself. Pin the alert's OWN guard and
  // reject a dead, hidden or aria-hidden node.
  // Round-16 review: keep the leading `\{` and do NOT restate the node's attributes here — the order-independent `alert` pick below
  // already pins `role`/`key`/`className`/`fontSize` on the same node, so spelling
  // `key={copy.attempt}` as the first attribute made a pure reorder a false red.
  assert.match(card, new RegExp(`\\{\\s*[(\\s!]*(?:Boolean\\(\\s*)?[(\\s!]*${FAILED_REF}\\s*\\)*\\s*(?:&&|\\?)`),
    'the card alert must render only on failure (its own `copy.failed` guard)')
  assert.match(inline, new RegExp(`\\{\\s*[(\\s!]*(?:Boolean\\(\\s*)?[(\\s!]*${FAILED_REF}\\s*\\)*\\s*(?:&&|\\?)`),
    'the inline alert must render only on failure (its own `copy.failed` guard)')
  // Round-26 review: P1 REGRESSION, introduced by the round-25 widening above —
  // `[(\s!]*` admits a leading `!`, so `{!copy.failed && (` satisfied the guard pin AND
  // the alert pick, which then validated the INVERTED node: the alert rendered while IDLE
  // and vanished on failure. That is the #2935 silence with the premise reversed on screen,
  // and both suites were green. A regex cannot count bangs, so analyse the PREFIX between
  // '{' and 'copy.failed' directly: the guard must test ONLY `copy.failed`, and its '!'
  // count must be EVEN (`!!` / `!(!…)` are the same predicate; a single '!' is not).
  const guardPrefix = (src) => {
    const m = src.match(new RegExp(`\\{\\s*([^{}]{0,60}?)${FAILED_REF}`))
    return m ? m[1] : null
  }
  for (const [name, comp] of [['card', card], ['inline', inline]]) {
    const pre = guardPrefix(comp)
    assert.ok(pre !== null, `${name}: the alert must be guarded by \`copy.failed\``)
    assert.equal(
      (pre.match(/!/g) || []).length % 2,
      0,
      `${name}: the failure guard must not be INVERTED (an odd '!' count silences the failure)`,
    )
    assert.ok(
      !/[A-Za-z_$]/.test(pre.split('Boolean').join('')),
      `${name}: the guard must test \`copy.failed\` and nothing else`,
    )
  }
  // Round-21 review: anchoring on quotes made the detector blind to a BACKTICK
  // literal or a BARE glyph beside the control, and scanning only the two component
  // definitions missed success text planted at a CALL SITE. The rule is the plain
  // one: once the live region's own observed sentence is removed, no success
  // indicator may appear anywhere in the control's component OR in the connect step
  // that renders it. `copyLabel` (the ONLY legitimate `'Copied ✓'`, module scope) and
  // the idle default `'Copy'` sit outside both, by construction.
  // Case-SENSITIVE on purpose, and guarded by identifier boundaries: `copy.copied`
  // and the legacy `setWizardCopied(...)` setters are state, not success text.
  // Round-22 review: the scan is TEXTUAL, so adjacent literals are JOINED first
  // (`'Cop' + 'ied!'` → `'Copied!'`) and the constructs that SYNTHESIZE a glyph from
  // code points are rejected outright. Neither construct appears anywhere in the
  // shipped slices (measured), so banning them costs no false red.
  const SUCCESS_TOKEN = /(?<![A-Za-z_$])Copied(?![A-Za-z_$])|✓|✔|&#0*10003;|&#0*[xX]0*2713;|&#0*10004;|\\u2713|\\u2714|&#0*[xX]0*2714;/g
  // Round-35 review: the DECLARATION ban learned `.concat`/`.reduce` in round 34, but the
  // RENDER-site scan did not — an unconditional success built by either shipped green.
  const SYNTHESIS = /fromCharCode|fromCodePoint|\.join\(|\.concat\(|\.reduce\(/g
  // Round-22 review: a template INTERPOLATION can also stitch a success string
  // (`Cop${'ied!'}`). `${` is measured absent from both components (it is used in
  // the connect step's own formatting, so this ban is component-scoped only).
  const INTERPOLATION = /\$\{/g
  // Round-35 review: juxtaposition is concatenation without a `+`. Join ADJACENT JSX
  // expressions (`{'Cop'}{'ied'}`) and a JSX TEXT run followed by an expression
  // (`Cop{'ied'}`) as well, so the token scan sees the string those render.
  const deJoin = (src) => src
    .replace(/['"`]\s*\+\s*['"`]/g, '')
    .replace(/['"`]\s*\}\s*\{\s*['"`]/g, '')
    .replace(/(?<![A-Za-z_$])Cop\s*\{\s*['"`]/g, "'Cop")
    // Round-36 review: the third juxtaposition direction — expression THEN text
    // (`{'Cop'}ied`) — renders identically but was missed (the closure was asymmetric).
    .replace(/['"`]\s*\}\s*(?=[A-Za-z])/g, '')
  // Round-26 review: decode `\uXXXX` before scanning — `'Cop\u0069ed'` IS `'Copied'`.
  // Round-31 review: `\uXXXX` was the ONLY escape decoded, so two further spellings of the
  // ENUMERATED token shipped green — `'\u{2713}'` (a real check mark) and `'\xE2\x9C\x93'`
  // (its UTF-8 bytes, which Babel decodes to the same glyph). Both are decoded here.
  const deEsc = (src) => {
    const uni = src
      .replace(/\\u\{([0-9a-fA-F]{1,6})\}/g, (_, h) => String.fromCodePoint(parseInt(h, 16)))
      .replace(/\\u([0-9a-fA-F]{4})/g, (_, h) => String.fromCharCode(parseInt(h, 16)))
    return uni.replace(/(?:\\x[0-9a-fA-F]{2})+/g, (m) => {
      const bytes = m.match(/\\x([0-9a-fA-F]{2})/g).map((x) => parseInt(x.slice(2), 16))
      return Buffer.from(bytes).toString('utf8')
    })
  }
  for (const [name, comp, liveNode] of [['card', card, cardLive], ['inline', inline, inlineLive]]) {
    assert.deepEqual(deEsc(deJoin(comp)).match(SYNTHESIS) || [], [],
      `${name}: success text must not be computed (no fromCharCode / Array#join / fromCodePoint)`)
    assert.deepEqual(comp.match(INTERPOLATION) || [], [],
      `${name}: success text must not be interpolated into a template literal`)
    // Round-26 review: remove only the PICKED live node, never every occurrence. A
    // global strip also erased a SECOND copy of the sentence, so an unconditional
    // `<p>{'Copied to clipboard'}</p>` (or a second polite `role="log"` region) in the
    // same component shipped green. A visible duplicate is a WORSE defect than the
    // hidden extra child this pin was written for.
    assert.deepEqual(deEsc(deJoin(comp.split(liveNode).join(''))).match(SUCCESS_TOKEN) || [], [],
      `${name}: nothing but the live region's own observed sentence may state success`)
    // Round-22 review: the per-node hiding bans miss a hidden ANCESTOR wrapper
    // (`<div aria-hidden="true">` around the whole control), which silences the
    // failure just as completely. These three forms are unambiguous hiding and
    // appear nowhere in either component.
    assert.doesNotMatch(comp, HIDDEN_BAN,
      `${name}: no ancestor wrapper may hide the control (its own nodes are pinned separately)`)
  }
  // Round-32 review: `deEsc` was wired to the COMPONENT scans only, so an escaped success
  // spelling at a CONNECT-STEP call site (`label={'Cop\\u0069ed'}`, `{'Copy \\u{2713}'}`,
  // `\xE2\x9C\x93`) shipped green — the very spellings the round-31 decoder was added for.
  assert.deepEqual(deEsc(deJoin(connectStepSlice())).match(SYNTHESIS) || [], [],
    'the connect step must not compute success text')
  assert.deepEqual(deEsc(deJoin(connectStepSlice())).match(SUCCESS_TOKEN) || [], [],
    'the connect step must render no success text of its own')
  // Round-24 review: a success string hoisted to MODULE scope and rendered by NAME in
  // the connect step is an IDENTIFIER, not a literal, so every scan above is blind to
  // it. The token cannot be banned module-wide — the legacy wizard, the key modal and
  // the `.snippet-copy` control all legitimately render `'Copied ✓'` — so ban the forms
  // that can launder it. Round-27 review: "bound to a success literal" was too narrow — it
  // required the WHOLE initialiser to be one string, so `{ connect: 'Copied ✓' }`,
  // `['Copied ✓']` and `new Map([['connect', 'Copied ✓']])` all laundered it past every
  // scan and into a `label` prop (a P1 this file shipped green). The ban now rejects a
  // success token ANYWHERE in a declaration's initialiser (up to a blank line), plus a
  // COMPUTED initialiser (`fromCharCode` / `fromCodePoint` / `Array#join`).
  // (Measured: neither exists today.)
  // Round-32 review: a character WINDOW bound the scan (`{0,160}?`), so a caption map with
  // enough entries before the token laundered it past every scan — contradicting this pin's
  // own rule. Walk each declaration's BALANCED INITIALISER instead (depth counted while
  // SKIPPING string literals, so a brace inside a string cannot truncate or extend it), and
  // test the raw initialiser text — which still contains its literals — for the token.
  const declInitialisers = []
  // Round-33 review: three further evasions of this guard, all fixed here.
  // (a) `export const X` was not matched at all (the regex anchored on `const`).
  // (b) The value starting on the NEXT line (`const X =\n  { connect: 'Copied ✓' }`)
  //     produced an EMPTY slice, because the walk broke on the first newline at depth 0
  //     before the value had begun. Leading whitespace is now skipped first, so the break
  //     at depth 0 can only happen AFTER the initialiser started.
  // (c) The token test did not JOIN adjacent literals, so `'Cop' + 'ied!'` never contained
  //     the contiguous token (the component scan already joined; the declaration scan did
  //     not). The computed-initialiser assert is now the SAME balanced slice, instead of a
  //     `[^\n]*` window that missed a constructor split across lines.
  for (const m of mainJsxCode.matchAll(/(?:^|\n)\s*(?:export\s+(?:default\s+)?)?(?:const|let|var)\s+[A-Za-z_$][\w$]*\s*=/g)) {
    let i = m.index + m[0].length
    while (i < mainJsxCode.length && /\s/.test(mainJsxCode[i])) i += 1
    let depth = 0
    for (; i < mainJsxCode.length; i += 1) {
      const ch = mainJsxCode[i]
      if (ch === "'" || ch === '"' || ch === '`') { i = skipString(mainJsxCode, i) - 1; continue }
      if (ch === '(' || ch === '[' || ch === '{') { depth += 1; continue }
      if (ch === ')' || ch === ']' || ch === '}') { if (depth === 0) break; depth -= 1; continue }
      if (ch === ';' && depth === 0) break
      if (ch === '\n' && depth === 0) {
        // Round-33 review: a line-CONTINUED initialiser (`const X = String\n  .fromCharCode(…)`)
        // was truncated at the newline. Only end the initialiser when the next line begins a
        // new statement — i.e. NOT with an operator/closer that can only continue this one.
        let k = i + 1
        while (k < mainJsxCode.length && /\s/.test(mainJsxCode[k])) k += 1
        if (!/[.?+*\/&|,<>!=)\]}]/.test(mainJsxCode[k] || '')) break
      }
    }
    declInitialisers.push(mainJsxCode.slice(m.index + m[0].length, i))
  }
  assert.deepEqual(
    declInitialisers.filter((init) =>
      deEsc(deJoin(init)).match(/(?<![A-Za-z_$])Copied(?![A-Za-z_$])|✓|✔|&#0*10003;|&#0*[xX]0*2713;|&#0*10004;|&#0*[xX]0*2714;/),
    ),
    [],
    'no declaration initialiser may contain a success string (adjacent literals joined, escapes decoded) — it could be rendered by name',
  )
  assert.deepEqual(
    declInitialisers.filter((init) => /fromCharCode|fromCodePoint|\.join\(|\.concat\(|\.reduce\(/.test(init)),
    [],
    'no declaration may COMPUTE a success string either (a constructor split across lines must not evade this)',
  )
  // Round-34 review: a template INTERPOLATION in a declaration initialiser — const L =
  // `Cop${'ied'}` — stitches the token without ever containing it. `${…}` alone is far too
  // common to ban module-wide, so this rejects it only when the SAME initialiser also carries
  // a success WORD FRAGMENT (`Cop`/`✓`/`✔`), which is what makes the interpolation a success
  // string rather than ordinary formatting.
  assert.deepEqual(
    declInitialisers.filter((init) => /\$\{/.test(init) && /(?<![A-Za-z_$])Cop|✓|✔/.test(init)),
    [],
    'no declaration may interpolate a success string either',
  )

  // The live region's OWN sentence. (The button's own label is pinned in the test
  // above, where the button is selected BY its captured handler name — selecting
  // the first `<button>` here let a decoy button stand in for the real control.)
  for (const [name, live] of [['card', cardLive], ['inline', inlineLive]]) {
    // Round-19 review: anchored to the WHOLE element, not just the first child —
    // appending text after the observed ternary announced success on every render
    // while the ternary stayed honestly empty (and the count comparison below
    // cannot see it, since the extra literal sits inside the live region too).
    assert.match(live,
      new RegExp(
        `^<span[^>]*>\\s*\\{\\s*\\(*\\s*copy\\??\\.copied\\s*\\)*\\s*(?:\\?\\s*${Q}Copied to clipboard${Q}\\s*:\\s*(?:${Q}${Q}|null|undefined|false)|&&\\s*${Q}Copied to clipboard${Q})\\s*\\}\\s*<\\/span>$`,
      ),
      `${name}: the live region's content must be ONLY the observed success sentence`)
    assert.match(live, /className\s*=\s*\{?\s*['"`]sr-only['"`]\s*\}?/, `${name}: the live region itself must stay visually hidden`)
    // Round-18 review: the alert's not-hidden ban was never applied here, so
    // `aria-hidden`/`display:'none'`/`visibility:'hidden'` on the sr-only span
    // silenced the success announcement while every pin stayed green.
    assert.doesNotMatch(live, HIDDEN_BAN,
      `${name}: the live region must not be hidden (a hidden live region announces nothing)`)
  }
  // The alert node ITSELF: assertive, keyed (so a repeated failure re-announces),
  // red, and at the shared size.
  for (const [name, alert] of [['card', cardAlert], ['inline', inlineAlert]]) {
    for (const prop of ['role', 'className', 'key']) {
      assertNoSpreadOverride(alert, prop, `the ${name} alert`)
    }
    assertGuardOperands(alert, `${name} alert`)
    // Round-47 review: banning the JOINED `'error small'` pair was defeated by any computed
    // separator (`{'error' + ' '.repeat(1) + 'small'}`) while `.error` overrides `.small` (the
    // 14px regression this pin exists to stop). Pin the class list POSITIVELY: exactly `error`.
    const cls = alert.match(/className\s*=\s*(\{[^}]*\}|['"`][^'"`]*['"`])/)
    assert.ok(cls, `${name}: the alert itself must carry a className`)
    assert.equal(deJoin(cls[1]).replace(/^\{|\}$/g, '').replace(/['"`]/g, '').trim(), 'error',
      `${name}: the alert's class list must be EXACTLY \`error\` — a second class flips it to .small/14px — got \`${cls[1]}\``)
    assert.match(alert, /role\s*=\s*\{?\s*['"`]alert['"`]\s*\}?/, `${name}: the alert itself must be assertive`)
    // Round-37 review: the braces were unpadded, so `key={ copy.attempt }` (the file's own
    // declared "whitespace inside JSX braces" respelling) was a false red.
    assert.match(alert, /key\s*=\s*\{\s*copy\??\.attempt\s*\}/,
      `${name}: the alert itself must be keyed on the attempt (re-announce a repeat)`)
    assert.match(alert, /className\s*=\s*\{?\s*['"`]error['"`]\s*\}?/, `${name}: the alert itself must carry the error styling`)
    // Round-28 review: that pin matches a PREFIX, so `className={'error' + ' sr-only'}`
    // passed it and hid the failure from sighted users. The hiding CLASS is banned too.
    assert.doesNotMatch(alert, /\bsr-only\b/,
      `${name}: the visible alert must not carry the visually-hidden class`)
    // Round-34 review: a JS object literal is LAST-WINS, so `fontSize: 12, …, fontSize: 0`
    // rendered the alert invisible while this pin — which read the FIRST occurrence — stayed
    // green. Every declared size must be the shared 12px.
    const fontSizes = [...alert.matchAll(new RegExp(`fontSize\\s*:\\s*${Q}?([\\d.]+)`, 'g'))]
      .map((m) => Number(m[1]))
    assert.ok(fontSizes.length > 0, `${name}: the alert itself must render at the shared 12px`)
    assert.deepEqual(
      fontSizes.filter((n) => n !== 12),
      [],
      `${name}: EVERY fontSize on the alert must be 12 (a later duplicate key wins)`,
    )
    // Round-17 review: content, not just presence — an appended success sentence
    // ("… — Copied ✓") left the remedy const honest while the render site lied.
    assert.match(alert, />\s*\{\s*COPY_FAILED_MESSAGE\s*\}\s*<\//,
      `${name}: the alert must render the remedy and NOTHING else (no appended success text)`)
    // Round-18 review: the ban missed the HTML `hidden` attribute (UA `display:none`)
    // and off-screen positioning, so the failure could be silenced silently.
    // Round-19 review: JSX styles are camelCase — `(?:max-)?height: 0` never matched
    // `maxHeight: 0`, off-screen was only `left:`, not `top:`, and `opacity: 0.0`
    // walked past `opacity:\s*0(?![.\d])`.
    // Round-22 review: the value shapes were still bare-integer, so `'0'`/`0.0`/
    // `'0px'` and the DOUBLE-quoted spellings, plus `right`/`bottom`, all evaded it.
    assert.doesNotMatch(alert, HIDDEN_BAN,
      `${name}: the alert must not be hidden (present but invisible or aria-hidden is still silent)`)
    // Round-27 review: ZERO is bounded to all-zero DIGITS, so a tiny NON-zero value
    // (`opacity: 0.00001`, `zoom: 0.001`) rendered the alert invisible and passed every
    // ban. Check the NUMBERS, not the spelling: any opacity/zoom declared here is >= 0.05.
    for (const prop of ['opacity', 'zoom']) {
      // Round-28 review: `[\\d.]+` captured only `1` from `1e-9`, which read as >= 0.05.
      // Round-34 review: the check read only the FIRST occurrence, so a LATER duplicate key
      // (`opacity: 1, …, opacity: 0.001`) won at runtime and rendered the alert invisible.
      const gots = [...alert.matchAll(new RegExp(`${prop}\\s*:\\s*${Q}?(-?\\d*\\.?\\d+(?:[eE][-+]?\\d+)?)`, 'g'))]
        .map((m) => Number(m[1]))
      assert.ok(
        gots.every((v) => v >= 0.05),
        `${name}: every ${prop} on the alert must be >= 0.05 (a later duplicate key wins) — got ${gots.join(', ')}`,
      )
    }
  }
  // The inline alert's placement (rounds 7-8) and the card alert's spacing
  // normalisation (round 10: without `margin: 0` it measured 23.2px above /
  // 11.2px below, the same misgrouping the inline `marginTop` pin prevents).
  assertStyleKeysUnique(cardAlert, 'the card alert')
  assert.match(cardAlert, /margin\s*:\s*['"`]?0/,
    'the card alert must neutralise `.error` 12px so it stays grouped with the prompt')
  assertStyleKeysUnique(inlineAlert, 'the inline alert')
  assert.match(inlineAlert, /display\s*:\s*['"`]block['"`]/,
    'the alert must be a block so it gets its own line in the block-flow <li>')
  assert.match(inlineAlert, new RegExp(`flexBasis\\s*:\\s*${Q}100%${Q}`),
    'and must claim its own flex line in the two flex rows')
  assert.match(inlineAlert, /marginTop\s*:\s*['"`]0\.35rem['"`]/,
    'the alert owns its spacing instead of adding `.error` 12px to the row gap')
  // Round-49 review (UX, measured): the card's alert must render AFTER its actions row. Placed
  // above it, the 12px alert pushed the button down 26.19px (41.19px below 390px) — measured in a
  // real browser — so `elementFromPoint` at the point the user had just clicked returned the
  // alert and a retry at the same coordinates never registered. The three inline controls put
  // their alert BELOW the control and shift 0.00, so this is also the consistent order.
  // Round-50/51 review: a raw `indexOf` on the class string was satisfiable by an earlier
  // occurrence (`data-note="wizard-prompt-actions"`), and matching the className ATTRIBUTE was
  // satisfiable by an earlier element carrying the same class (`<span className="wizard-prompt-actions" />`)
  // — and it also rejected the declared-allowed `className={'…'}` respelling. Resolve the row
  // STRUCTURALLY: the element that ENCLOSES the bound copy button.
  // Round-51 review: keying on the literal `onClick={doCopy}` made every declared-allowed
  // handler respelling a false red (`onClick={(e) => doCopy(e)}`, a block body, a paren body),
  // so find the <button> whose own opening tag REFERENCES the bound handler — spelling-agnostic.
  const btnAt = [...card.matchAll(/<button\b/g)]
    .map((m) => m.index)
    .filter((i) => /\bdoCopy\b/.test(openingTag(card, i) || ''))[0]
  assert.ok(btnAt !== undefined, 'the card must render its bound copy button')
  assertNoSpreadOverride(openingTag(card, btnAt), 'onClick', 'the card copy button')
  const cardActionsAt = enclosingTagAt(card, btnAt)
  assert.ok(cardActionsAt > -1, 'the card copy button must sit inside an actions row')
  const cardAlertAt = card.indexOf(cardAlert)
  assert.ok(cardAlertAt > cardActionsAt,
    'the card failure alert must render AFTER the actions row (above it, the alert reflows the '
    + 'button out from under the pointer and a retry at the same coordinates never registers)')
  assert.notEqual(enclosingTagAt(card, cardAlertAt), cardActionsAt,
    'the alert must be a SIBLING of the actions row, not a child of it (inside it, the alert '
    + 'still reflows the button)')

  // Surfaces, counts and formatting (order-independent, round 8).
  for (const [name, comp] of [['card', card], ['inline', inline]]) {
    // Round-15 review: the control block's own clipboard ban is a literal-dot
    // pattern, so `navigator['clipboard']` and `const { clipboard } = navigator`
    // evaded it inside a component. Neither component needs `navigator` at all
    // (the seam is defined above them), so ban the identifier outright.
    assert.doesNotMatch(comp, /\bnavigator\b/,
      `${name}: the component must not write the clipboard itself — only runCopyAttempt may`)
    // Round-15 review: a dead `{false && …}` gate is invisible to a presence pin.
    // Round-37 review: a spelling list is the wrong shape — `!0` is TRUTHY while `!1` is
    // not, and `{0 && (` / `{null && (` / `{'' && (` / `{void 0 && (` / `{Boolean(false) && (`
    // all dead-gate a control or the live region. Evaluate the operand instead.
    assert.deepEqual(deadGateValues(comp), [],
      `${name}: no control or live region may be gated behind a CONSTANT FALSY (dead code)`)
    assert.deepEqual(deadTernaries(comp), [],
      `${name}: no control or live region may be gated behind a CONSTANT TERNARY (dead code)`)
    assert.deepEqual(styleHiding(comp), [],
      `${name}: nothing in the control may be faded to invisibility (opacity/zoom/filter/alpha)`)
    for (const el of comp.matchAll(/<[a-z][\w-]*\b/g)) {
      const tag = openingTag(comp, el.index)
      if (tag) assertStyleKeysUnique(tag, `${name}: ${tag}`)
    }
    // Round-36 review: the ban above only matched `false` in the FIRST operand, so a dead
    // gate composed AFTER the guard (`copy.failed && false && (`, `&& null &&`, `&& 0 &&`,
    // `&& !1 &&`) silenced the failure with every pin green — the alert node is still present,
    // just never rendered.
    // …and one redundant paren or `!`/`Boolean(` wrapper defeated the round-36 token ban, so
    // the conjunction is decided by VALUE too.
    assert.deepEqual(composedFalsyAfterGuard(comp), [],
      `${name}: the failure guard must not be composed with a constant FALSY (the alert would never render)`)
    assert.match(comp, /className\s*=\s*\{?\s*['"`]sr-only['"`]\s*\}?/, `${name}: the announcement must stay visually hidden`)
    assert.match(comp, /\brole\s*=\s*\{?\s*['"`]status['"`]\s*\}?/, `${name}: the success must use a status live region`)
    assert.match(comp, /\baria-live\s*=\s*\{?\s*['"`]polite['"`]\s*\}?/, `${name}: and it must be polite`)
    assert.equal((comp.match(/role\s*=\s*\{?\s*['"`]status['"`]\s*\}?/g) || []).length, 1,
      `${name}: exactly one polite live region`)
    assert.equal((comp.match(/aria-live\s*=/g) || []).length, 1,
      `${name}: exactly one live region, whatever its spelling`)
    assert.ok((comp.match(/role\s*=\s*\{?\s*['"`]alert['"`]\s*\}?/g) || []).length <= 1,
      `${name}: at most one assertive alert (a second would double-read the failure)`)
    // Round-35 review: `useState({ ...COPY_IDLE, copied: !0 })` renders "Copied ✓" and
    // announces it AT MOUNT, and the `copied: true` count below reads only the literal
    // spelling. Pin the initialiser, and ban ANY truthy `copied:` that is not the one
    // published (inside `runCopyAttempt`, outside both components) — false is the idle value.
    assert.match(
      comp,
      // Round-36 review: pinning the BARE identifier rejected the declared
      // `{ ...COPY_IDLE }` respelling (evaluated once, behaviour-identical). The separate
      // truthy-`copied:` ban below is what rejects `{ ...COPY_IDLE, copied: !0 }`.
      // Round-38 review: the LAZY initialiser (`useState(() => COPY_IDLE)`) is the idiomatic
      // form and stores the same reference, so it is a behaviour-preserving false red.
      // Round-39 review: requiring the spread to be the ONLY entry contradicted the
      // truthy-`copied:` ban below, which owns that decision: `{ ...COPY_IDLE, copied: false }`
      // and `{ ...COPY_IDLE, attempt: 0 }` are the idle state, while `copied: !0` stays RED.
      // Round-40 review: a paren between the arrow and the object (`() => ({ ...COPY_IDLE })`,
      // `(({ ...COPY_IDLE }))`) is the file's own declared "any number of redundant parens".
      // Round-43 review: a redundant paren around the LAZY arrow (`(() => COPY_IDLE)`) is the
      // same declared tolerance the reset pin already applies.
      /React\.useState\(\s*\(*\s*(?:\(\s*\)\s*=>\s*)?\(*\s*(?:\{\s*\.\.\.\s*COPY_IDLE\s*(?:,[^}]*)?\}|COPY_IDLE)\s*\)\s*\)?/,
      `${name}: the control must START idle (an initialiser that is a truthy outcome lies at mount)`,
    )
    // Round-39 review: `copied\s*:\s*(?!false\b)` was defeated by `\s*` BACKTRACKING to empty —
    // the lookahead then evaluated at the space, which is not `false`, so it passed and
    // `{ ...COPY_IDLE, copied: false }` (the idle value) was a false red. The lookahead must
    // consume its own whitespace (the same fix as the round-28 `visibility` trap).
    assert.doesNotMatch(comp, /copied\s*:\s*(?!\s*false\b)/,
      `${name}: the only truthy \`copied\` may be the one runCopyAttempt publishes after a resolved write`)
    // Round-35 review: the call-site ban did not cover the CONTROLS' OWN elements, so a
    // duplicate `onClick={onClick} onClick={undefined}` on the button (last-wins at runtime)
    // dead-gated the control with every pin green. No element in either component may repeat
    // a prop either.
    // Round-39 review: `[^>]*` cannot cross the `>` in `=>`, so once an `onClick={(e) => …}`
    // wrapper (a declared-GREEN respelling) appeared, every prop AFTER it was outside the
    // matched element and a dead duplicate `onClick` shipped green. Read each opening tag.
    for (const el of comp.matchAll(/<[a-z][\w-]*\b/g)) {
      const element = openingTag(comp, el.index)
      if (!element) continue
      for (const prop of ['onClick', 'className', 'style', 'key', 'role', 'aria-live', 'type']) {
        assert.ok(
          (element.match(new RegExp(`\\b${prop}\\s*=`, 'g')) || []).length <= 1,
          `${name}: a duplicate \`${prop}\` prop is last-wins at runtime (the pins read the first): ${element}`,
        )
      }
    }
    assert.equal((comp.match(/COPY_FAILED_MESSAGE/g) || []).length, 1,
      `${name}: the failure is announced once, by the visible alert`)
    // Round-25 review: the EFFECT's dep array was pinned, the HANDLER's was not. The
    // component is deliberately NOT remounted when `text` changes, so an empty dep array
    // leaves the handler closing over the PREVIOUS target: the click then writes the OLD
    // prompt/key/URL while `runCopyAttempt` reports the write resolved — a wrong payload
    // with a false success, i.e. exactly the #2935 harm. (Measured: `[]` on either
    // handler left BOTH suites green.)
    assert.match(
      comp,
      // Round-32 review: `setCopy` is a stable useState setter, so an ADDITIONAL stable dep
      // is behaviour-identical (the pin's job is that `text` is present and the array is not
      // empty, both still enforced).
      /React\.useCallback\([\s\S]*?,\s*\[\s*text\s*(?:,\s*[A-Za-z_$][\w$]*\s*)*,?\s*\]\s*,?\s*\)/,
      `${name}: the handler itself must depend on \`text\` (never a stale target)`,
    )
    assert.doesNotMatch(
      comp,
      /React\.useCallback\([\s\S]*?,\s*\[\s*\]\s*,?\s*\)/,
      `${name}: an empty handler dep array copies a stale target`,
    )
    // Round-46/47 review: the joined-pair ban lived here; it was defeated by a computed
    // separator, so the alert loop now pins the class list POSITIVELY (exactly `error`).
    assert.doesNotMatch(comp, /className\s*=\s*\{?\s*['"`]error small['"`]\s*\}?/,
      `${name}: \`.error\` overrides \`.small\` — that class pair renders at 14px`)
  }

  assert.equal((connectStepSlice().match(/<InlineCopyButton\b/g) || []).length, 3,
    'the three inline Copy controls are the ones wired to the shared control')
  // Round-9 review: the two `harnessKey` controls were pinned only by that count.
  // Dropping `text={harnessKey}` makes the control copy the string "undefined" and
  // report success — the exact harm this issue exists to prevent.
  // Round-35 review: JSX compiles duplicate props into one object literal, LAST-WINS, so
  // `text={harnessKey} text={undefined}` copies "undefined" while every pin (which reads the
  // FIRST match) stays green. No connect-step control may repeat a prop.
  for (const tag of connectStepSlice().match(/<(?:WizardPromptCard|InlineCopyButton)\b[^>]*>/g) || []) {
    for (const prop of ['text', 'label', 'onClick', 'className', 'style']) {
      assert.ok(
        (tag.match(new RegExp(`\\b${prop}\\s*=`, 'g')) || []).length <= 1,
        `a duplicate \`${prop}\` prop is last-wins at runtime (the pins would read the first): ${tag}`,
      )
    }
  }
  for (const tag of connectStepSlice().match(/<InlineCopyButton\b[^>]*>/g) || []) {
    assert.match(tag, /text\s*=\s*\{[^}]+\}/,
      `every inline Copy control must be given its own payload: ${tag}`)
  }
  // Round-18 review: "any expression" is not "the right value" — `text={wizardKeyMode}`
  // (in scope, but not the key) reported success while copying the wrong string.
  assert.equal((connectStepSlice().match(/<InlineCopyButton\b[^>]*text\s*=\s*\{\s*harnessKey\s*\}/g) || []).length, 2,
    'both API-key controls must copy the minted key itself')
  assert.match(connectStepSlice(), /<InlineCopyButton\b[^>]*text\s*=\s*\{\s*CANONICAL_MCP_URL\s*\}/,
    'the connector control must copy the canonical URL itself')
  // Round-20 review: the card's OWN payload was pinned nowhere — dropping `text`
  // (or binding `wizardKeyMode`) handed `undefined`/the wrong string to a control
  // that still reported `Copied ✓`, which is the #2935 harm at the primary control.
  for (const tag of connectStepSlice().match(/<WizardPromptCard\b[^>]*>/g) || []) {
    assert.match(tag, /text\s*=\s*\{\s*\(*\s*(?:wizardPromptText|wizardWorkflowsText|UNIVERSAL_COMMAND)\b/,
      `every connect-step prompt card must be handed a BUILT prompt (never a bare value): ${tag}`)
  }
  // Round-30 review: that pin is BUILDER-NAME only, so the same harm smuggled through the
  // builder's ARGUMENTS was invisible — a card handed step 1's prompt instead of step 2's,
  // or the Claude card handed the wrong SURFACE's prompt, or `undefined` in the key slot
  // (`wizardPrompts.js` renders `Key: ${key}`), all still reported `Copied ✓`. Pin the
  // payloads as a MULTISET (order-independent): each card must carry its own builder call,
  // with the minted key and the live key mode. This is a shape pin — a payload extracted to
  // a named const is an accepted cost, declared in the LIMITS above, and the set is compared
  // as a multiset so reordering the cards is not a false red.
  const CARD_PAYLOADS = [
    'wizardPromptText(wizardHarness, 1, harnessKey, wizardKeyMode)',
    'wizardPromptText(wizardHarness, 2, harnessKey, wizardKeyMode)',
    'UNIVERSAL_COMMAND.codexDesktop(harnessKey)',
    'wizardPromptText(wizardConnectHarness, 1, harnessKey, wizardKeyMode)',
    "wizardWorkflowsText('', 'included')",
  ]
  const cardPayloads = (connectStepSlice().match(/<WizardPromptCard\b[^>]*>/g) || [])
    .map((tag) => (tag.match(/text\s*=\s*\{([^}]*)\}/) || [])[1])
    .filter((v) => v !== undefined)
    .map((v) => v.trim())
  // Round-31 review: the raw-text comparison made this pin redden on the file's OWN declared
  // respellings — backtick/double-quoted string literals, redundant parens around the call,
  // and whitespace after the builder's commas or inside the JSX braces. Compare the SHAPE
  // (identifiers, arguments and order, with literals elided and quote/paren/whitespace noise
  // removed) AND the literal CONTENTS separately, so a respelling is green while a changed
  // argument, step, surface, key or literal text is still red.
  // Round-40 review: the shape strip removed `(`, `)` and whitespace but not a legal TRAILING
  // COMMA, so `wizardPromptText(…, wizardKeyMode,)` was a false red — and the LIMITS already
  // list a trailing comma among the behaviour-preserving respellings.
  const payloadShape = (e) => e.replace(/,\s*\)/g, ')').replace(/['"`][^'"`]*['"`]/g, '·').replace(/[\s(){}]/g, '')
  const payloadLiterals = (e) => (e.match(/['"`][^'"`]*['"`]/g) || []).map((x) => x.slice(1, -1))
  assert.deepEqual(
    cardPayloads.map(payloadShape).sort(),
    CARD_PAYLOADS.map(payloadShape).sort(),
    'every connect-step card must be handed ITS OWN prompt — right builder, right step, right surface, the minted key and the live key mode',
  )
  assert.deepEqual(
    cardPayloads.flatMap(payloadLiterals).sort(),
    CARD_PAYLOADS.flatMap(payloadLiterals).sort(),
    'and every literal inside a card payload must be unchanged (quote style may differ)',
  )
  // Round-20 review: a success-wording `label` PROP (not just the declaration
  // default) renders "Copy ✓" at idle and after a FAILURE, since `copyLabel` falls
  // back to the idle label — so the wording ban must cover the call sites too.
  for (const tag of connectStepSlice().match(/<InlineCopyButton\b[^>]*>/g) || []) {
    const label = tag.match(/\blabel\s*=\s*(?:"([^"]*)"|\{'([^']*)'\})/)
    // Round-32 review: decode escapes BEFORE the wording test — `label={'Cop\u0069ed'}`
    // renders "Copied" at idle on the control that reports the copy.
    assert.ok(!label || !/(?:Copied|✓)/.test(deEsc(label[1] ?? label[2] ?? '')),
      `no inline control may take a success-wording label: ${tag}`)
  }
  // Round-27 review: `WizardPromptCard` call sites had NO wording pin at all, and every
  // shipped one draws its label from a constant map — the SAME shape a laundered
  // `label={LABELS.connect}` has. Combined with the declaration ban above, pinning the
  // prop to those maps closes the LOCAL route: a new map, an array index, a `Map#get` or a
  // producer function is a shape MOVED out of the pinned region, and fails HERE.
  // Round-28 review corrected the claim: the two maps themselves are imported from
  // `./wizardPrompts.js` and `./harnesses.js`, which this file never reads — their
  // CONTENTS are outside every scan here. That is covered, not unguarded:
  // `wizardConnectTripwire.test.js` pins the caption texts and the exact
  // `HARNESS_COPY_LABEL.codexDesktop` binding, and `wizardPrompts.test.js` pins the captions.
  for (const tag of connectStepSlice().match(/<WizardPromptCard\b[^>]*>/g) || []) {
    const label = tag.match(/\blabel\s*=\s*\{([^}]*)\}/)
    // Round-29 review: `!label ||` made a DROPPED prop green, which reverts `regionLabel`
    // to the shared default (the duplicate-landmark shape #2912 fixed) and the button
    // caption to a bare `'Copy'`.
    assert.ok(label, `every connect-step prompt card must carry its own label prop: ${tag}`)
    assert.match(
      label[1].trim(),
      /^(?:WIZARD_CAPTIONS|HARNESS_COPY_LABEL)\.[A-Za-z_$][\w$]*$/,
      `every connect-step prompt card must take its label from the caption maps: ${tag}`,
    )
  }
  // Round-31 review: the `{false && …}` ban above is scoped to the component DEFINITIONS,
  // so dead-gating a control at its call site removed it entirely (no button, no alert) with
  // every pin green — the same "silence the control at its call site" class the wrapper check
  // below exists for. The connect step contains no such gate (measured), so banning it costs
  // no false red.
  assert.deepEqual(deadGateValues(connectStepSlice()), [],
    'no connect-step control may be dead-gated behind a CONSTANT FALSY (it removes the control AND its failure report)')
  assert.deepEqual(deadTernaries(connectStepSlice()), [],
    'no connect-step control may be dead-gated behind a CONSTANT TERNARY (same removal, same silence)')
  // Round-28 review: HIDDEN_BAN was asserted on the components only, so the whole control
  // could be hidden at its CALL SITE — `<div aria-hidden="true">` (or `display: none`)
  // around the card silences the failure just as completely, and that is where a "hide the
  // failing surface" edit lands. Hold each control's own tag AND its nearest enclosing
  // `<div>` opening tag to the same ban.
  for (const m of connectStepSlice().matchAll(/<(?:WizardPromptCard|InlineCopyButton)\b[^>]*>/g)) {
    assert.doesNotMatch(m[0], HIDDEN_BAN,
      `no connect-step control may be hidden at its call site: ${m[0]}`)
    assert.deepEqual(styleHiding(m[0]), [],
      `no connect-step control may be faded at its call site: ${m[0]}`)
    assertNoSpreadOverride(m[0], 'text', 'a connect-step control')
    const before = connectStepSlice().slice(Math.max(0, m.index - 240), m.index)
    // Round-31 review: `<div>`-only, so a `<span>`/`<section>` wrapper hid the control and
    // stayed green. Any element can be the wrapper.
    // Round-39 review: a wrapper carrying `onClick={() => {}}` put its `aria-hidden` behind a
    // `>` that a `[^>]*` scan could not cross — solved by reading real opening tags.
    // Round-43 review: and the ENCLOSING element is now resolved structurally, so a hidden
    // wrapper is caught however much content sits between it and the control.
    const wrapper = enclosingTag(connectStepSlice(), m.index)
    if (wrapper) {
      assert.doesNotMatch(wrapper, HIDDEN_BAN,
        `no connect-step control may be wrapped in a hidden element: ${wrapper}`)
      // Round-46 review: `HIDDEN_BAN` is spelling-based, so a wrapper faded to 1% opacity hid
      // the whole control with the suite green. The numeric read covers it.
      assert.deepEqual(styleHiding(wrapper), [],
        `no connect-step control may be wrapped in a faded element: ${wrapper}`)
      assertStyleKeysUnique(wrapper, `connect-step wrapper: ${wrapper}`)
    }
  }
  // Round-5 review: this change replaced a literal "Copy URL" button with a
  // `label` prop, so a dropped prop would silently relabel the URL control
  // "Copy". Round-10: match order-independently (a pure attribute reorder is
  // behaviour-preserving).
  const urlControl = connectStepSlice().match(/<InlineCopyButton\b[^>]*CANONICAL_MCP_URL[^>]*>/)
  assert.ok(urlControl, 'the Claude connector must render an inline Copy control')
  assert.match(urlControl[0], /label\s*=\s*\{?\s*['"`]Copy URL['"`]\s*\}?/,
    'the Claude connector control must keep its own "Copy URL" label')
  // Round-11 review: two of the three inline controls pass NO `label` and depend
  // entirely on the component's default. Dropping it makes `copyLabel(copy, undefined)`
  // return `undefined`, so both Copy buttons render with no text and no accessible
  // name — with every other pin still green.
  assert.match(inline,
    /function InlineCopyButton\(\{[^}]*\blabel\s*=\s*['"`]Copy['"`]/,
    'InlineCopyButton must declare the IDLE default label for its unlabelled call sites')
  // Round-13 review: a two-literal blocklist is not the property. ANY success
  // wording ('Copied', 'Copied!', 'Copy ✓') as the default makes the two
  // unlabelled API-key controls claim success at idle, before any write.
  // Round-22 review: an IDLE label that names the WRONG payload ('Copy URL') mislabels
  // both API-key controls just as badly, so the default is pinned to the exact string.
  assert.doesNotMatch(inline, /label\s*=\s*['"`][^'"`]*(?:Copied|Copy\s*✓|clipboard)[^'"`]*['"`]/i,
    'the default must be an IDLE label — any success wording would make an unlabelled Copy control lie')
})

test('#2935: an attempt is reset when the control’s target changes', () => {
  // Round-2 review (ux): the card is the SAME instance across a surface / key-mode
  // switch and the inline controls' key/URL can change under them, so a sticky
  // failure — or a success flash — must not carry over to content the user never
  // attempted to copy. The counter is Bumped (not reset) so the token stays
  // monotonic — a superseded write or an old flash timer cannot collide.
  for (const [name, comp] of [['card', cardSlice()], ['inline', inlineSlice()]]) {
    // Round-50 review: this pin used to require the PASSIVE form, so the one fix that removes a
    // painted stale frame (`useLayoutEffect`, whose reset runs before paint) was a red suite. The
    // LAYOUT form is load-bearing here: with `useEffect` the frame that first shows the new
    // prompt still carries the old `Copied ✓` / old alert (~16ms, measured by rAF sampling).
    const start = comp.indexOf('React.useLayoutEffect(')
    assert.ok(start > -1,
      `${name}: the [text] reset must be a LAYOUT effect (a passive effect paints one stale frame)`)
    // Round-10 review: capture the ref name so renaming the local is not a false red.
    const refName = comp.match(/const ([A-Za-z_$][\w$]*)\s*=\s*React\.useRef\(\s*0\s*\)/)
    assert.ok(refName, `${name}: must declare the attempt-counter ref`)
    // Round-6 review: a fixed 200-char window reached into the FOLLOWING
    // `useCallback(…, [text])` for the inline control, so its `[text]` satisfied
    // the pin even with the effect's own dep array dropped (both controls take a
    // changing `text`, so that stale-outcome bug is real). Bound the slice to the
    // effect's OWN statement, then require the `}` immediately before `, [text])`
    // — the following `useCallback`'s dep is preceded by `)` and cannot match.
    const dep = new RegExp(`\\}\\s*,\\s*\\[\\s*text\\s*(?:,\\s*[A-Za-z_$][\\w$]*\\s*)*,?\\s*\\]\\s*,?\\s*\\)`).exec(comp.slice(start))
    assert.ok(dep, `${name}: the reset effect must declare a dep array`)
    const close = start + dep.index + dep[0].length - 1
    const effect = comp.slice(start, close + 1)
    assert.match(effect, /}\s*,\s*\[\s*text\s*(?:,\s*[A-Za-z_$][\w$]*\s*)*,?\s*\]\s*,?\s*\)/, `${name}: the reset must run when the target changes`)
    // Round-50 review: `++` is the same monotonic bump as `+= 1`, so it is allowed rather than a
    // false red (the "all writes" audit below still rejects a reset or a decrement).
    assert.match(effect, new RegExp(`${refName[1]}\\.current\\s*(?:\\+=\\s*1|\\+\\+)`),
      `${name}: the counter must stay monotonic (a stale write/timer must not collide)`)
    // Round-50 review: that pin read only the FIRST effect's own statement, so a SECOND effect in
    // the component (`<ref>.current = 0`) was unguarded — the counter stops being monotonic and an
    // OLD 1.6s flash timer can then clear a NEWER attempt's success. Audit EVERY write to the ref.
    // Round-51 review: the audit read the OPERATOR only and the `\.current` access only, so
    // `attemptRef.current += -1` (the allowed operator, a negative value) and
    // `attemptRef['current'] = 0` were both green — a non-monotonic counter lets a superseded
    // in-flight write publish. Read BOTH access forms and the VALUE.
    const writes = [...comp.matchAll(new RegExp(
      `${refName[1]}(?:\\s*\\.\\s*current|\\s*\\[\\s*['"\`]current['"\`]\\s*\\])\\s*(\\+\\+|\\+=|=(?!=)|--|-=)\\s*([^;\\n,)]*)`, 'g'))]
    assert.ok(writes.length > 0, `${name}: the attempt ref must be bumped`)
    for (const w of writes) {
      assert.ok(w[1] === '+=' || w[1] === '++',
        `${name}: every write to the attempt ref must be a MONOTONIC bump (never a reset/assign/decrement) — found \`${w[0].trim()}\``)
      // Round-54 review: prefix-matching `-` let a constant EXPRESSION through — `+= 1 - 2` is a
      // runtime DECREMENT (the ref goes negative and a superseded token can be reused). `++` has
      // no operand, so only an `+=` needs a literal.
      assert.match(w[1] === '++' ? '1' : (w[2] || '').trim(), /^\d+(?:\.\d+)?$/,
        `${name}: the bump must be a POSITIVE NUMERIC LITERAL (\`+= 1\` or \`++\`) — found \`${w[0].trim()}\``)
    }
    // Round-32 review: `setCopy(() => COPY_IDLE)` is the same reset (the setter ignores the
    // previous value), so the functional-updater form is allowed.
    // Round-40 review: the same redundant-paren tolerance as the initialiser pin.
    assert.match(effect, /setCopy\(\s*\(*\s*(?:\(\s*\)\s*=>\s*)?\(*\s*(?:\{\s*\.\.\.\s*)?COPY_IDLE/, `${name}: the reset must clear the stale outcome`)
  }
})

test('#2935: the failure alert carries the remedy, never a success claim', () => {
  // Round-4 review: the alert's MESSAGE is the only thing that tells the user
  // what happened and how to recover, yet nothing pinned it — `{''}` (or a
  // success sentence) left every test green.
  const msg = extractConst(mainJsx, 'COPY_FAILED_MESSAGE')
  assert.match(msg, /blocked the clipboard/, 'the alert must explain the failure')
  assert.match(msg, /Ctrl-C/, 'and name the manual fallback')
  // Round-16 review: assert the PROPERTY on the string VALUE (not the `const`
  // identifier, which case-insensitively contains `COPY`) — the old two-literal
  // blocklist accepted `'Copied — press ⌘/Ctrl-C'` as a "never a success claim".
  // Round-26 review: this was single-quote-only, so a quote-style change (a declared
  // allowed respelling) did not fail the assertion — it THREW a TypeError.
  const value = (msg.match(/(['"`])([\s\S]*?)\1/) || [])[2]
  assert.ok(value !== undefined, 'the remedy must be a plain string literal')
  assert.doesNotMatch(value, /Copied|Copy\s*✓/i, 'never a success claim')
  // `selectCopyTarget` is BEST-EFFORT and silently no-ops on a miss, so the message
  // may instruct the user but must never CLAIM a selection was made: "select the
  // text and press …" is an instruction, not a claim.
  assert.doesNotMatch(value,
    /\b(?:we|I)(?:'ve| have)? selected\b|\bselected (?:it|the text) for you\b|\bhas been selected\b/i,
    'the message must not claim a selection was made (the fallback can silently miss)')
  assert.match(cardSlice(), /\{\s*COPY_FAILED_MESSAGE\s*\}/, 'the card alert must render the remedy')
  assert.match(inlineSlice(), /\{\s*COPY_FAILED_MESSAGE\s*\}/, 'the inline alert must render the remedy')
})

test('#2935: the build-fork key row wraps the failure alert instead of squeezing the key', () => {
  // Round-3 review (ux, P1): the alert is a THIRD, long flex item in this row, so
  // without wrapping the key <code> collapses to a ~1-character column at phone
  // widths and the alert is pushed off-screen. Round-4 review: the fix was pinned
  // by NOTHING (the #2711 test accepts `wordBreak` as "can shrink"), so it could
  // be reverted with every executed and static assertion still green. A rendering
  // guarantee is stated at its source.
  const step = connectStepSlice()
  const anchor = step.indexOf('Copy your API key now.')
  assert.ok(anchor > -1, 'the build-fork key row must be in the connect step')
  // Round-45 review: a 900-char text WINDOW contains both the row `<div>` and the key
  // `<code>`, so every `assert.match` above was satisfiable by moving a property onto the
  // SIBLING — `flexWrap` relocated to the `<code>` reverted the round-3 P1 layout fix with the
  // whole suite green. Each property is now asserted on the ELEMENT that must carry it.
  // Round-50 review: resolving the row by SOURCE ORDER (`indexOf('<div', anchor)` then
  // `indexOf('<code', rowAt)`) made every property satisfiable by a decoy element placed EARLIER:
  // an off-screen `<div style={{display:'flex',flexWrap:'wrap'}}><code style={{flex:1,minWidth:0}}>`
  // satisfied all four while the real row reverted to `nowrap` and the real key lost `minWidth`.
  // Anchor on the key PINS and resolve each one's ENCLOSING row structurally — a decoy is then
  // just an extra key that must also hold the invariant. The block ends at the next block's own
  // `wizardKeyCodeStyle` usage (a PRE-EXISTING, out-of-scope marker).
  const nextBlockAt = step.indexOf('wizardKeyCodeStyle', anchor)
  const block = nextBlockAt > anchor ? step.slice(anchor, nextBlockAt) : step.slice(anchor)
  const keyPins = [...block.matchAll(/\{\s*harnessKey\s*\}/g)]
  assert.ok(keyPins.length > 0, 'the build-fork key row must render the key')
  for (const pin of keyPins) {
    const codeAt = block.lastIndexOf('<code', pin.index)
    assert.ok(codeAt > -1, 'the build-fork key must render in a <code> element')
    const code = openingTag(block, codeAt)
    const rowAt = enclosingTagAt(block, codeAt)
    assert.ok(rowAt > -1, 'the build-fork key <code> must sit inside a row element')
    const row = openingTag(block, rowAt)
    assertNoSpreadOverride(code, 'style', 'the build-fork key <code>')
    assertNoSpreadOverride(row, 'style', 'the build-fork key row')
    assertStyleKeysUnique(row, 'the build-fork key row')
    assertStyleKeysUnique(code, 'the build-fork key <code>')
    assert.equal(styleEntry(row, 'display'), 'flex',
      'the build-fork key row must be a flex row')
    assert.equal(styleEntry(row, 'flexWrap'), 'wrap',
      'the row must wrap the failure alert rather than squeeze the key')
    assert.equal(styleEntry(code, 'flex'), '1',
      'the key <code> must be allowed to shrink instead of collapsing the row')
    assert.equal(styleEntry(code, 'minWidth'), '0',
      'the key <code> needs minWidth:0 or a flex item cannot shrink below its content')
  }
  // Round-7 review: `flexWrap` alone does NOT wrap here — the key `<code>` has
  // `flex: 1` (basis 0), so it contributes ZERO to the flex line-break sum and
  // the alert's ~413px max-content fits beside it inside the card's 560px, so all
  // three items stay on one line and the key gets the ~63px leftover. Measured in
  // Chromium at 900–1280px: without this the key is 63px wide (12 lines; the
  // round-3 collapse); with `flexBasis: '100%'` it is 485px (2 lines, identical to
  // the no-alert baseline) and the alert takes its own line. `flex-basis` is
  // ignored in the Claude connector's block-flow `<li>`, so it is a no-op there.
  assert.match(inlineSlice().match(new RegExp(`[(\\s!]*(?:Boolean\\(\\s*)?[(\\s!]*${FAILED_REF}\\s*\\)*\\s*(?:&&|\\?)[\\s\\S]{0,40}?<[a-z]+[^>]*>`))[0], new RegExp(`flexBasis\\s*:\\s*${Q}100%${Q}`),
    'the alert must claim its own flex line — `flexWrap` cannot wrap a `flex:1` sibling')
})
