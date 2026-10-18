const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const script = fs.readFileSync(path.join(__dirname, "../app/static/board.js"), "utf8");

function setup({ withEvents = true } = {}) {
  const listeners = {};
  const board = {
    html: '<div data-board-revision="1">Initial board</div>',
    replacements: 0,
    editorOpen: false,
    get innerHTML() { return this.html; },
    set innerHTML(value) { this.html = value; this.replacements += 1; },
    addEventListener(name, handler) { listeners[name] = handler; },
    dispatch(name, event = {}) { listeners[name]?.(event); },
    contains() { return false; },
    querySelector(selector) {
      if (selector === "details[open]") return this.editorOpen ? {} : null;
      if (selector === "[data-board-revision]") {
        return { dataset: { boardRevision: this.html.match(/data-board-revision="(\d+)"/)?.[1] } };
      }
      return null;
    },
    querySelectorAll() { return []; },
  };
  const notice = { hidden: true };
  const apply = {
    disabled: false,
    addEventListener(name, handler) { if (name === "click") this.handler = handler; },
    click() { this.handler?.(); },
  };
  const connection = {
    textContent: "",
    parentElement: { classList: { toggle() {} } },
  };
  const document = {
    activeElement: null,
    querySelector(selector) {
      return {
        "[data-board-content]": board,
        "#connection-status": connection,
        "#board-updates": notice,
        "#apply-board-updates": apply,
      }[selector] ?? null;
    },
  };
  let poll;
  let stream;
  let confirm = true;
  let nextRevision = 1;
  let fetches = 0;
  const EventSource = class {
    constructor() { this.listeners = {}; stream = this; }
    addEventListener(name, handler) { this.listeners[name] = handler; }
    emit(revision) { this.listeners.board({ data: String(revision) }); }
  };
  const window = {
    EventSource: withEvents ? EventSource : null,
    setInterval(handler) { poll = handler; },
    setTimeout(handler) { Promise.resolve().then(handler); },
    confirm() { return confirm; },
    location: { assign() {} },
  };
  class DOMParser {
    parseFromString(html) {
      return {
        querySelector() {
          const revision = html.match(/data-board-revision="(\d+)"/)?.[1];
          return revision ? { getAttribute() { return revision; } } : null;
        },
      };
    }
  }
  vm.runInNewContext(script, {
    document, window, EventSource, DOMParser,
    fetch: async () => {
      fetches += 1;
      return {
        ok: true, redirected: false,
        text: async () => `<div data-board-revision="${nextRevision}">Version ${nextRevision}</div>`,
      };
    },
  });
  return {
    board, notice, apply,
    poll: () => poll(),
    emit: (revision) => stream.emit(revision),
    setRevision: (revision) => { nextRevision = revision; },
    setConfirm: (value) => { confirm = value; },
    setFocused: (value) => {
      document.activeElement = value ? { closest: () => ({}) } : null;
      board.contains = () => value;
    },
    get fetches() { return fetches; },
  };
}

function settle() {
  return new Promise((resolve) => setImmediate(resolve));
}

test("polling does not replace an unchanged board", async () => {
  const ui = setup({ withEvents: false });
  ui.poll();
  await settle();
  assert.equal(ui.fetches, 1);
  assert.equal(ui.board.replacements, 0);
  assert.equal(ui.notice.hidden, true);
});

test("incoming changes preserve a draft and remain explicitly applicable", async () => {
  const ui = setup();
  ui.board.dispatch("input", { target: { closest: () => ({}) } });
  ui.setRevision(2);
  ui.emit(2);
  await settle();
  assert.equal(ui.board.replacements, 0);
  assert.equal(ui.notice.hidden, false);

  ui.poll();
  await settle();
  assert.equal(ui.board.replacements, 0);
  ui.setConfirm(false);
  ui.apply.click();
  await settle();
  assert.equal(ui.board.replacements, 0);

  ui.setConfirm(true);
  ui.apply.click();
  await settle();
  assert.equal(ui.board.replacements, 1);
  assert.equal(ui.notice.hidden, true);
});

test("closing an untouched editor applies queued updates", async () => {
  const ui = setup();
  ui.board.editorOpen = true;
  ui.setRevision(2);
  ui.emit(2);
  assert.equal(ui.notice.hidden, false);
  ui.board.editorOpen = false;
  ui.board.dispatch("toggle");
  await settle();
  assert.equal(ui.board.replacements, 1);
  assert.equal(ui.notice.hidden, true);
});

test("polling reveals changes without EventSource while preserving a draft", async () => {
  const ui = setup({ withEvents: false });
  ui.board.dispatch("input", { target: { closest: () => ({}) } });
  ui.setRevision(2);
  ui.poll();
  await settle();
  assert.equal(ui.board.replacements, 0);
  assert.equal(ui.notice.hidden, false);
});

test("typing during an in-flight refresh does not replace the form", async () => {
  const ui = setup();
  ui.setRevision(2);
  ui.poll();
  ui.board.dispatch("input", { target: { closest: () => ({}) } });
  await settle();
  assert.equal(ui.board.replacements, 0);
  assert.equal(ui.notice.hidden, false);
});

test("leaving an untouched form applies queued updates", async () => {
  const ui = setup();
  ui.setFocused(true);
  ui.setRevision(2);
  ui.emit(2);
  assert.equal(ui.notice.hidden, false);
  ui.setFocused(false);
  ui.board.dispatch("focusout");
  await settle();
  assert.equal(ui.board.replacements, 1);
  assert.equal(ui.notice.hidden, true);
});
