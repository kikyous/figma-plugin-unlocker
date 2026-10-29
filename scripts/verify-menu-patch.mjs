import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

import path from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
const reportPath = process.argv[2] ?? path.join(root, ".local", "patch-report.json");
const report = JSON.parse(fs.readFileSync(reportPath, "utf8"));
const directory = report.artifactDirectory ?? path.dirname(reportPath);
const source = fs.readFileSync(path.join(directory, "original-main.js"), "utf8");
const patched = fs.readFileSync(path.join(directory, "patched-main.js"), "utf8");

const rules = JSON.parse(fs.readFileSync(path.join(root, "patches", "desktop-rules.json"), "utf8"));
const menuPatch = rules.find(p => p.name === "plugin-menu-enabled");
const actionPatch = rules.find(p => p.name === "plugin-action");
assert(menuPatch && actionPatch, "Desktop rules must contain menu and action patches");

function resolveRule(rule) {
  const matches = [...source.matchAll(new RegExp(rule.pattern, "g"))];
  assert.equal(matches.length, 1, `Expected one ${rule.name} target`);
  const match = matches[0];
  return {
    before: match[0],
    after: rule.replacement.replace(/\{(\d+)\}/g, (_, number) => match[Number(number)]),
  };
}
const menu = resolveRule(menuPatch);
const action = resolveRule(actionPatch);

function extractFactory(text, target) {
  const position = text.indexOf(target);
  assert(position >= 0 && text.indexOf(target, position + 1) === -1, "Expected one menu patch target");
  const first = text.lastIndexOf("function ", position);
  const last = text.indexOf("function ", position);
  assert(first >= 0 && last > position, "Expected menu function boundaries");
  return text.slice(first, last);
}

const originalFactory = extractFactory(source, menu.before);
const factoryName = originalFactory.match(/^function ([$\w]+)\(/)?.[1];
const names = originalFactory.match(/([$\w]+)\.stringOfActionMenuItemName\(/)?.[1];
const label = originalFactory.match(/label:([$\w]+)\(/)?.[1];
const submenu = originalFactory.match(/submenu:([$\w]+)\(/)?.[1];
const unknown = originalFactory.match(/default:return ([$\w]+)\(/)?.[1];
const handler = action.before.match(/^function ([$\w]+)\(/)?.[1];
const windows = action.before.match(/([$\w]+)\.fromBrowserWindow\(/)?.[1];
assert(factoryName && names && label && submenu && unknown && handler && windows,
  "Could not identify menu verification dependencies");

function load(text, factoryTarget, handlerText) {
  const first = text.indexOf(handlerText);
  assert(first >= 0 && text.indexOf(handlerText, first + 1) === -1, "Expected one action handler");
  const messages = [];
  const context = vm.createContext({
    [names]: { stringOfActionMenuItemName: name => name.string },
    [label]: name => name,
    [submenu]: items => items,
    [unknown]: () => { throw new Error("Unknown menu type"); },
    [windows]: { fromBrowserWindow: () => ({
      postMessageToActiveWebBinding: (...args) => messages.push(args),
    }) },
  });
  vm.runInContext(
    handlerText + extractFactory(text, factoryTarget) +
    `;globalThis.api={menu:${factoryName},click:${handler}};`, context,
  );
  return { ...context.api, messages };
}

const before = load(source, menu.before, action.before);
// Reports created before the shared rules used a shorter local variable name.
const legacyAction = action.after.replaceAll("__figmaPluginAction", "a");
const after = load(patched, menu.after, patched.includes(action.after) ? action.after : legacyAction);
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
console.log(`Verified Figma ${report.version ?? "unknown version"}: ${checks} action types, absent actions, headers, and unmodified source behavior.`);
