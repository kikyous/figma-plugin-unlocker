import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { patchBundle } = require('./plugin-access-hook.cjs');
const sourcePath = process.argv[2] ?? new URL('../.local/frontend/figma_app-current.js', import.meta.url);
const source = fs.readFileSync(sourcePath, 'utf8');
const patched = patchBundle(source);
new vm.Script(patched);
assert.throws(() => patchBundle(source + ' '), /Unsupported Figma frontend bundle/);

function permissions(text) {
  const start = text.indexOf('150206(e,t,n){');
  const end = text.indexOf('},966684(e,t,n){', start);
  assert(start >= 0 && end > start);
  const factory = vm.runInNewContext('"use strict";(function' + text.slice(start + '150206'.length, end + 1) + ')');
  const dependencies = {
    374339: { t: key => key },
    720143: { nl: () => false },
    935724: { $A: view => view.editorType === 2 },
    476951: { tn: () => true },
    758763: { Vj: () => false },
    159022: { Jh: () => false },
    386186: { dk: () => false },
    467914: { kc: () => ({}) },
  };
  const load = id => dependencies[id] ?? {};
  load.d = (exports, getters) => {
    for (const [name, getter] of Object.entries(getters)) Object.defineProperty(exports, name, { get: getter });
  };
  const exports = {};
  factory({ exports }, exports, load);
  return exports;
}

const original = permissions(source);
const changed = permissions(patched);
function state(editorType = 1, view = 'fullscreen') {
  return {
    user: { id: 'local-test-user' },
    selectedView: { editorType, view },
    openFile: { teamId: 'local-test-team', canRunExtensions: false, canAccessFullDevMode: false, canExport: true },
    mirror: { appModel: { isReadOnly: true } },
  };
}
const current = state();
const before = structuredClone(current);
assert.equal(original.Eh(current), false);
assert.equal(original.cb(current), false);
assert.equal(changed.Eh(current), true);
assert.equal(changed.cb(current), true);
assert.deepEqual(current, before, 'Plugin selectors must not mutate document permissions');

for (const editorType of [2, 3, 4, 5, 6, 7]) {
  const other = state(editorType);
  assert.equal(changed.Eh(other), original.Eh(other));
  assert.equal(changed.cb(other), original.cb(other));
}
const browser = state(1, 'fileBrowser');
assert.equal(changed.Eh(browser), original.Eh(browser));
assert.equal(changed.cb(browser), original.cb(browser));
const anonymous = state();
anonymous.user = null;
assert.equal(changed.cb(anonymous), false, 'Do not enable anonymous plugin execution');
const restrictedDevMode = state(2);
restrictedDevMode.openFile.canExport = false;
assert.equal(changed.Eh(restrictedDevMode), original.Eh(restrictedDevMode));
assert.equal(changed.Eh(restrictedDevMode), false);
console.log('Frontend verification passed: compilation, exact bundle match, normal Design plugin access, unchanged document state, other editors, anonymous access, and Dev Mode export restrictions.');
