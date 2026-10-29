import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const directory = new URL("../.local/", import.meta.url);
const source = fs.readFileSync(new URL("original-main.js", directory), "utf8");
const patched = fs.readFileSync(new URL("patched-main.js", directory), "utf8");

function extract(text, start, end) {
  const first = text.indexOf(start);
  const last = text.indexOf(end, first);
  assert(first >= 0 && last > first, "Expected function boundaries");
  return text.slice(first, last);
}

function load(text) {
  const messages = [];
  const context = vm.createContext({
    Nm: { stringOfActionMenuItemName: name => name.string },
    ME: name => name,
    Tz: items => items,
    wr: () => { throw new Error("Unknown menu type"); },
    ae: { fromBrowserWindow: () => ({
      postMessageToActiveWebBinding: (...args) => messages.push(args),
    }) },
  });
  vm.runInContext(
    extract(text, "function Det(", "function Aet(") +
    extract(text, "function BE(", "function ME(") +
    ";globalThis.api={menu:BE,click:Det};", context,
  );
  return { ...context.api, messages };
}

const before = load(source);
const after = load(patched);
let checks = 0;
for (const type of ["run-local-plugin", "run-installed-plugin", "run-last"]) {
  const action = { type, localFileId: 42, command: "read", showError: "view-only" };
  const item = { type: "run-menu-action", name: { string: "Plumb" }, disabled: true, menuAction: action };
  assert.equal(before.menu(item).enabled, false);
  const menu = after.menu(item);
  assert.equal(menu.enabled, true);
  after.click(menu, {});
  const [channel, forwarded] = after.messages.at(-1);
  assert.equal(channel, "handlePluginMenuAction");
  assert.equal(forwarded.type, type);
  assert.equal(forwarded.localFileId, 42);
  assert.equal(forwarded.command, "read");
  assert.equal(Object.hasOwn(forwarded, "showError"), false);
  assert.equal(action.showError, "view-only", "Do not mutate original menu data");
  checks++;
}
for (const type of ["insert-installed-widget", "publish-widget", "manage-widgets", "unknown-action"]) {
  const action = { type, showError: "original-error" };
  const menu = after.menu({ type: "run-menu-action", name: { string: "Other" }, disabled: true, menuAction: action });
  assert.equal(menu.enabled, false);
  after.click(menu, {});
  assert.equal(after.messages.at(-1)[1], action, "Unrelated actions pass through unchanged");
  checks++;
}
assert.equal(after.menu({ type: "run-menu-action", name: { string: "No action" }, disabled: true }).enabled, false);
assert.equal(after.menu({ type: "header", name: "Heading" }).enabled, false);
console.log(`Verified ${checks} action types, absent actions, headers, and unmodified source behavior.`);
