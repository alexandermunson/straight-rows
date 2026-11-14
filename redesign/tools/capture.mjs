// node capture.mjs <base url> <out dir> [sub]  -> one PNG per state, plus state.json with what each state read back.
import { mkdirSync, writeFileSync } from 'node:fs';
import { launch } from './bidi.mjs';
import { STATES, SUB_STATES } from './states.mjs';

const [base, out, mode] = process.argv.slice(2);
mkdirSync(out, { recursive: true });
const list = mode === 'sub' ? SUB_STATES : STATES;
const B = await launch({ port: 9300 + Math.floor(Math.random() * 600) });
const read = `return { tab: document.body.className, t0: Math.round(view.t0), t1: Math.round(view.t1), res: curRes, sel: S.sel,
  selId: S.sel >= 0 ? M.events[S.sel].id : null, vis: S.vis, layers: {truth: S.truth, dets: S.dets, mp: S.mp, base: S.base, xlog: S.xlog},
  cls: S.cls, rows: document.querySelectorAll('#events .ev').length, mapAll: MV.all, mapZ: MV.z, opsDay: OPS.day,
  spec: {ch: SP.ch, scope: SP.scope, tr: SP.tr, harm: SP.harm, vis: SP.vis}, status: document.getElementById('status').textContent,
  open: [...document.querySelectorAll('details')].filter(d => d.offsetParent !== null || d.open).map(d => d.open ? 1 : 0).join('') };`;
const result = {};
try {
  for (const st of list) {
    await B.viewport(st.w || 1600, st.h || 1000);
    await B.go('about:blank');
    await B.go(`${base}/?test${st.hash || ''}`);
    await B.sleep(250);
    if (st.act) { await B.eval(`(() => { ${st.act} })()`); }
    await B.sleep(st.wait || 300);
    result[st.name] = await B.json(read);
    await B.shot(`${out}/${st.name}.png`);
    process.stdout.write(st.name + ' ');
  }
} finally { await B.close(); }
writeFileSync(`${out}/state${mode === 'sub' ? '-sub' : ''}.json`, JSON.stringify(result, null, 1));
console.log('\ndone', Object.keys(result).length);
