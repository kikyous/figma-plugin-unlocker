import fs from 'node:fs/promises';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { patchBundle } = require('./plugin-access-hook.cjs');
const bundleUrl = 'https://www.figma.com/webpack-artifacts/assets/figma_app-6982ed034b50d254.min.js.br';
const destination = new URL('../.local/frontend/figma_app-current.js', import.meta.url);

const response = await fetch(bundleUrl, { signal: AbortSignal.timeout(30_000) });
if (!response.ok) throw new Error(`Frontend download failed: HTTP ${response.status}`);
const source = await response.text();
patchBundle(source);
await fs.mkdir(new URL('.', destination), { recursive: true });
await fs.writeFile(destination, source, 'utf8');
console.log('Downloaded and verified the supported frontend bundle:', destination.pathname);
