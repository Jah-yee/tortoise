// accountDeleteConfirm.test.js — #4029.
//
// The owner ruled (2026-09-30, decision 1B) that a departing person's
// solely-owned teams are deleted WITH the account, and that the safeguard is a
// confirmation popup carrying a fixed warning and two actions: confirm and
// cancel. This guard is EXECUTED: it imports the real `DeleteAccountSection`
// (a createElement module, importable without a JSX transform) and renders it
// with react-dom/server, then reads the copy and the two actions off the
// element tree React produces. A source-text grep could be satisfied by a
// commented-out or shadowed copy; this cannot.
//
// Class-B mutation evidence (run, observed RED, reverted):
//   * change a word of `DELETE_ACCOUNT_WARNING` (e.g. "personal" → "user") →
//     tests 1 and 2 fail;
//   * swap the Cancel button's onClick to `onConfirm` → the cancel test fails
//     (cancel would request the deletion it exists to prevent);
//   * change main.jsx's `onConfirm={deleteAccount}` to `onConfirm={() => {}}` →
//     the wiring test fails;
//   * drop `method: 'DELETE'` from the `deleteAccount` api() call → the wiring
//     test fails.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { DeleteAccountSection, DELETE_ACCOUNT_WARNING } from './accountDeletion.js'
import { importsFromMain } from './jsxSourceProbe.js'

const HERE = dirname(fileURLToPath(import.meta.url))
const mainJsx = readFileSync(join(HERE, 'main.jsx'), 'utf8')

// The ruling's copy, spelled out here as the independent copy of record — a
// change to the module's constant alone must fail this test.
const REQUIRED_COPY =
  "Deleting your personal account will also delete any teams for which you're the only owner"

const baseProps = {
  open: false,
  busy: false,
  graceDays: 7,
  error: '',
  onOpen() {},
  onCancel() {},
  onConfirm() {},
}

function render(props) {
  return renderToStaticMarkup(React.createElement(DeleteAccountSection, props))
}

/** Walk a React element tree and collect every element of `type`. */
function collect(node, type, acc = []) {
  if (node === null || node === undefined || typeof node === 'boolean') return acc
  if (Array.isArray(node)) {
    for (const child of node) collect(child, type, acc)
    return acc
  }
  if (typeof node !== 'object' || !('props' in node)) return acc
  if (node.type === type) acc.push(node)
  collect(node.props.children, type, acc)
  return acc
}

/** The visible text of an element whose children are strings/nodes. */
function textOf(node) {
  if (node === null || node === undefined || typeof node === 'boolean') return ''
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(textOf).join('')
  if (typeof node === 'object' && 'props' in node) return textOf(node.props.children)
  return ''
}

function buttonByLabel(props, label) {
  const tree = DeleteAccountSection(props)
  const button = collect(tree, 'button').find((b) => textOf(b.props.children) === label)
  assert.ok(button, `no button labelled ${JSON.stringify(label)} was rendered`)
  return button
}

test('#4029: the warning copy is the owner-ruled string, verbatim', () => {
  assert.equal(DELETE_ACCOUNT_WARNING, REQUIRED_COPY)
})

test('#4029: the open popup renders the warning copy verbatim', () => {
  const html = render({ ...baseProps, open: true }).replace(/&#x27;/g, "'")
  assert.ok(html.includes(REQUIRED_COPY),
    'the confirm popup must render the owner-ruled warning copy verbatim')
})

test('#4029: the popup renders only when opened, and offers exactly confirm + cancel', () => {
  const closed = render({ ...baseProps, open: false })
  assert.ok(!closed.includes('role="dialog"'), 'no dialog before the control is used')

  const open = render({ ...baseProps, open: true })
  assert.ok(open.includes('role="dialog"'), 'the confirm popup must be a real dialog')
  const labels = collect(DeleteAccountSection({ ...baseProps, open: true }), 'button')
    .map((b) => textOf(b.props.children))
  assert.deepEqual(labels, ['Delete account', 'Cancel', 'Delete my account'],
    'exactly the opener plus the two ruled actions')
})

test('#4029: confirm requests the deletion; cancel does NOT', () => {
  let confirms = 0
  let cancels = 0
  const props = {
    ...baseProps, open: true,
    onConfirm: () => { confirms += 1 },
    onCancel: () => { cancels += 1 },
  }
  buttonByLabel(props, 'Delete my account').props.onClick()
  assert.equal(confirms, 1)
  assert.equal(cancels, 0, 'the confirm action must never route through cancel')

  buttonByLabel(props, 'Cancel').props.onClick()
  assert.equal(cancels, 1)
  assert.equal(confirms, 1, 'cancel must NOT request the deletion')
})

test('#4029: the opener only opens the popup (no request, no confirm)', () => {
  let opens = 0
  let confirms = 0
  buttonByLabel({ ...baseProps, onOpen: () => { opens += 1 }, onConfirm: () => { confirms += 1 } },
    'Delete account').props.onClick()
  assert.equal(opens, 1)
  assert.equal(confirms, 0, 'the opener must not delete anything by itself')
})

test('#4029: a failed delete surfaces inside the still-open popup', () => {
  const html = render({ ...baseProps, open: true, error: 'Could not delete your account — try again.' })
  assert.ok(html.includes('role="alert"'))
  assert.ok(html.includes('Could not delete your account'))
})

test('#4029: main.jsx renders the section wired to the real account-delete handler', () => {
  const imports = importsFromMain(mainJsx, ['DeleteAccountSection'])
  assert.match(imports.DeleteAccountSection, /^\.\/accountDeletion(\.js)?$/)

  const site = mainJsx.match(/<DeleteAccountSection[\s\S]*?\/>/)
  assert.ok(site, 'main.jsx must render <DeleteAccountSection .../>')
  assert.match(site[0], /onConfirm=\{deleteAccount\}/,
    'the confirm action must be the deleteAccount handler')
  assert.match(site[0], /onCancel=\{/, 'the popup must get a real cancel handler')
  assert.match(site[0], /open=\{deleteAccountOpen\}/, 'the popup must be state-driven')

  const start = mainJsx.indexOf('async function deleteAccount(')
  assert.notEqual(start, -1, 'deleteAccount must exist in main.jsx')
  const body = mainJsx.slice(start, mainJsx.indexOf('\n  async function ', start + 1) === -1
    ? mainJsx.length
    : mainJsx.indexOf('\n  async function ', start + 1))
  assert.match(body, /api\('\/v1\/user\/account',\s*\{\s*method:\s*'DELETE'/,
    'deleteAccount must DELETE /v1/user/account')
  assert.match(body, /await logout\(\)/, 'a scheduled deletion must end the session')
})
