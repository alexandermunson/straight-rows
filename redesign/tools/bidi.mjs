// Minimal WebDriver BiDi driver for headless Firefox, using Node's built-in WebSocket (no dependencies).
// Used only for verifying the viewer redesign: screenshots, state reads, simulated clicks and keys.
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const sleep = ms => new Promise(r => setTimeout(r, ms));

export async function launch({ port = 9333, width = 1600, height = 1000 } = {}) {
  const profile = mkdtempSync(join(tmpdir(), 'ffbidi-'));
  writeFileSync(join(profile, 'user.js'), [
    'user_pref("browser.shell.checkDefaultBrowser", false);',
    'user_pref("datareporting.policy.dataSubmissionEnabled", false);',
    'user_pref("toolkit.telemetry.reportingpolicy.firstRun", false);',
    'user_pref("ui.prefersReducedMotion", 0);',
    ...(process.env.DARK ? ['user_pref("layout.css.prefers-color-scheme.content-override", 0);', 'user_pref("ui.systemUsesDarkTheme", 1);'] : []),
  ].join('\n'));
  const proc = spawn('firefox', ['--headless', '--no-remote', '--profile', profile, `--remote-debugging-port=${port}`,
    `--width=${width}`, `--height=${height}`], { stdio: ['ignore', 'ignore', 'pipe'] });
  let ws;
  for (let i = 0; i < 100; i++) {
    await sleep(200);
    try {
      ws = new WebSocket(`ws://127.0.0.1:${port}/session`);
      await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
      break;
    } catch { ws = null; }
  }
  if (!ws) throw new Error('could not connect to Firefox BiDi');
  let id = 0; const pending = new Map();
  ws.onmessage = m => { const d = JSON.parse(m.data); if (d.id && pending.has(d.id)) { const p = pending.get(d.id); pending.delete(d.id); d.type === 'error' ? p.rej(new Error(p.m + ' -> ' + d.error + ': ' + d.message)) : p.res(d.result); } };
  const send = (method, params = {}) => new Promise((res, rej) => { const i = ++id; pending.set(i, { res, rej, m: method }); ws.send(JSON.stringify({ id: i, method, params })); });
  await send('session.new', { capabilities: {} });
  const ctx = (await send('browsingContext.create', { type: 'tab' })).context;
  // collect page errors from the first script on
  await send('script.addPreloadScript', { functionDeclaration: "() => { window.__errs = []; addEventListener('error', e => window.__errs.push(String(e.message))); addEventListener('unhandledrejection', e => window.__errs.push('rejection: ' + String(e.reason))); }" });
  const B = {
    send, ctx,
    async viewport(w, h) { await send('browsingContext.setViewport', { context: ctx, viewport: { width: w, height: h } }); },
    async go(url) { await send('browsingContext.navigate', { context: ctx, url, wait: 'complete' }); },
    async eval(expr) {
      const r = await send('script.evaluate', { target: { context: ctx }, expression: expr, awaitPromise: true, serializationOptions: { maxDomDepth: 0 } });
      if (r.type === 'exception') throw new Error('page exception: ' + JSON.stringify(r.exceptionDetails.text));
      return unwrap(r.result);
    },
    async json(expr) { return JSON.parse(await B.eval(`JSON.stringify((() => { ${expr} })())`)); },
    async shot(path, origin = 'viewport') {
      const r = await send('browsingContext.captureScreenshot', { context: ctx, origin });
      writeFileSync(path, Buffer.from(r.data, 'base64'));
    },
    async key(k) { await send('input.performActions', { context: ctx, actions: [{ type: 'key', id: 'kb', actions: [{ type: 'keyDown', value: k }, { type: 'keyUp', value: k }] }] }); },
    async close() { try { await send('browser.close', {}); } catch {} try { ws.close(); } catch {} proc.kill(); await sleep(300); rmSync(profile, { recursive: true, force: true }); },
    sleep,
  };
  return B;
}
function unwrap(v) {
  if (!v) return v;
  switch (v.type) {
    case 'string': case 'number': case 'boolean': return v.value;
    case 'undefined': case 'null': return null;
    case 'array': return v.value.map(unwrap);
    case 'object': return Object.fromEntries(v.value.map(([k, x]) => [typeof k === 'string' ? k : unwrap(k), unwrap(x)]));
    default: return v.value ?? null;
  }
}
