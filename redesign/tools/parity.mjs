// node parity.mjs <base url> <out.json>
// Reads back, through the page's own functions and DOM, everything that must not change in a restyle:
//  A. rendered series values (the samples the hover readout shows, and the min/max envelope each panel draws) at fixed
//     timestamps and windows, plus each panel's y range;
//  B. the state every URL-hash form restores;
//  C. the state after keyboard shortcuts, window buttons, layer and tractor toggles, the cause filter and event clicks.
import { writeFileSync } from 'node:fs';
import { launch } from './bidi.mjs';
const [base, out] = process.argv.slice(2);
const B = await launch({ port: 9300 + Math.floor(Math.random() * 600) });
const R = { values: {}, envelopes: {}, hashes: {}, interactions: [] };
const STATE = `return { tab: ['ops', 'spec'].find(c => document.body.classList.contains(c)) || 'inv', t0: Math.round(target.t0), t1: Math.round(target.t1),
  sel: S.sel >= 0 ? M.events[S.sel].id : null, vis: S.vis.join(), layers: [S.truth, S.dets, S.mp, S.base, S.xlog].join(), cls: S.cls,
  rows: [...document.querySelectorAll('#events .ev')].map(n => M.events[+n.dataset.i].id).join(), opsDay: OPS.day, mapAll: MV.all,
  spec: [SP.ch, SP.scope, SP.tr].join(), cardsOpen: [...document.querySelectorAll('#ops details.card')].filter(d => d.open).map(d => d.id).join(),
  infoHead: (document.querySelector('#info h3') || {}).textContent || '' };`;
const fresh = async (hash = '') => { await B.go('about:blank'); await B.go(`${base}/?test${hash}`); await B.sleep(300); };
try {
  await B.viewport(1600, 1000);
  await fresh();
  // ---- A. values
  R.values = await B.json(`const out = {}, chs = ['cn0', 'mincn0', 'xte', 'sats', 'fix', 'link', 'lat', 'lon', 'spd'];
    for (const t of [46800, 50000, 100000, 150000, 181000, 200000, 240100, 240555, 250000]) for (const res of [1, 10, 60]) for (let tr = 0; tr < 3; tr++)
      out[t + '/' + res + '/' + tr] = chs.map(ch => { const v = sampleAt(res, tr, ch, t); return v === v ? +v.toFixed(5) : null; }).join();
    return out;`);
  for (const [a, b] of [[0, 259200], [172800, 216000], [239000, 242600], [240000, 240600]]) {
    R.envelopes[a + '-' + b] = await B.json(`setTarget(${a}, ${b}, true); cssv = getComputedStyle(document.documentElement); drawBase();
      const L = LV[curRes], chs = ['cn0', 'mincn0', 'xte', 'sats', 'fix', 'link'], out = {res: curRes, panels: []};
      P.forEach((p, pi) => { const row = {y0: +p.y0.toFixed(4), y1: +p.y1.toFixed(4), env: []};
        if (pi < 6) for (let tr = 0; tr < 3; tr++) { const d = dec(L.a[tr][CH[chs[pi]]], curRes, p.r.x, 800, ${a}, ${b}, pi === 2 && S.xlog ? (v => Math.log10(1 + v / 0.1)) : null);
          let s = 0, n = 0; if (d.mode === 1) { for (let c = 0; c < d.W; c++) if (d.mn[c] !== Infinity) { s += d.mn[c] * 1.7 + d.mx[c] * 0.3; n++; } } else { for (let i = d.i0; i < d.i1; i++) { const v = L.a[tr][CH[chs[pi]]][i]; if (v === v) { s += v; n++; } } }
          row.env.push([d.mode, n, +s.toFixed(3), +d.lo.toFixed(4), +d.hi.toFixed(4)].join()); }
        out.panels.push(row); });
      return out;`);
  }
  // ---- B. URL hash forms (an old-version link: the hash it writes itself after selecting an event)
  await B.eval(`select(M.events.findIndex(e => e.id === 'D1264'), true)`); await B.sleep(1200);
  const saved = await B.eval('location.hash');
  R.savedHash = saved;
  const jam = saved.match(/ev=(\d+)/)[1];
  for (const h of [saved, `#t0=239000&t1=242600&ev=${jam}`, `#t0=239000&t1=242600&ev=${jam}&map=all`, '#t0=100000&t1=110000', '#tab=ops&day=0', '#tab=ops&day=1',
                   '#tab=ops&day=2&open=1', '#tab=spec', '#tab=spec&ch=xte&scope=2&tr=2', '#tab=spec&ch=cn0&scope=3&tr=1']) {
    await fresh(h); await B.sleep(500);
    R.hashes[h] = await B.json(STATE);
  }
  // ---- C. interactions, from a fresh page; real key presses go to the focused document
  await fresh();
  const step = async (name, js, wait = 250, keys) => {
    if (js) await B.eval(`(() => { ${js} })()`);
    if (keys) for (const k of keys) await B.key(k);
    await B.sleep(wait);
    R.interactions.push([name, await B.json(STATE)]);
  };
  await step('key n', null, 300, ['n']);
  await step('key n again', null, 300, ['n']);
  await step('key p', null, 300, ['p']);
  await step('key ArrowRight', null, 300, ['']);
  await step('key ArrowLeft', null, 300, ['']);
  await step('key +', null, 300, ['+']);
  await step('key -', null, 300, ['-']);
  await step('key 0', null, 300, ['0']);
  for (let i = 0; i < 8; i++) await step(`window button ${i}`, `document.querySelectorAll('#spans button')[${i}].click();`);
  for (let i = 0; i < 5; i++) await step(`layer toggle ${i}`, `document.querySelectorAll('#opts input')[${i}].click();`);
  for (let i = 0; i < 3; i++) await step(`tractor toggle ${i}`, `document.querySelectorAll('#trs input')[${i}].click();`);
  for (const c of ['JAMMING', 'SPOOFING', 'MULTIPATH', 'ALL']) await step(`cause filter ${c}`, `const f = document.getElementById('fCls'); f.value = '${c}'; f.dispatchEvent(new Event('change'));`);
  for (const k of [0, 5, 12]) await step(`click event row ${k}`, `document.querySelectorAll('#events .ev')[${k}].click();`, 600);
  await step('map whole area', `document.getElementById('mapAll').click();`);
  await step('map field', `document.getElementById('mapField').click();`);
  await step('see in operator actions', `select(M.events.findIndex(e => e.id === 'D1264'), true); document.getElementById('toOps').click();`, 600);
  await step('ops: show event in plots', `document.querySelector('#ops details.card[open] button[data-ev]').click();`, 600);
  await step('ops day button 0', `showTab('ops'); document.querySelectorAll('#ops button[data-day]')[0].click();`);
  await step('spectral tab', `document.getElementById('tabSpec').click();`, 800);
  await step('spectral channel xte', `const s = document.getElementById('spCh'); s.value = 'xte'; s.dispatchEvent(new Event('change'));`);
  await step('spectral tractor T3', `document.querySelectorAll('#spec button[data-sptr]')[2].click();`);
  await step('investigate tab', `document.getElementById('tabInv').click();`);
} finally { await B.close(); }
writeFileSync(out, JSON.stringify(R, null, 1));
console.log('parity written', Object.keys(R.values).length, 'value keys,', Object.keys(R.hashes).length, 'hashes,', R.interactions.length, 'interaction steps');
