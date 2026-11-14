// node kb.mjs <base url> <out.txt>: keyboard-only pass. Tabs through the page, records what receives focus, whether a
// visible focus outline is drawn, and checks Enter on an event row and on an operator lane block.
import { writeFileSync } from 'node:fs';
import { launch } from './bidi.mjs';
const [base, out] = process.argv.slice(2);
const B = await launch({ port: 9300 + Math.floor(Math.random() * 600) });
const lines = [];
const desc = `const a = document.activeElement; const cs = getComputedStyle(a);
  return {tag: a.tagName, id: a.id, cls: a.className, text: (a.textContent || '').trim().slice(0, 40), outline: cs.outlineStyle + ' ' + cs.outlineWidth, sel: S.sel};`;
try {
  await B.viewport(1600, 1000); await B.go(`${base}/?test`); await B.sleep(300);
  const seen = new Set();
  for (let i = 0; i < 40; i++) { await B.key('\uE004'); const d = await B.json(desc); seen.add(d.tag + '#' + d.id + '.' + String(d.cls).split(' ')[0]); lines.push(`tab ${String(i + 1).padStart(2)}: ${d.tag} ${d.id || ''} [${d.cls}] "${d.text}" outline=${d.outline}`); }
  // first event row: focus it and press Enter
  await B.eval(`document.querySelector('#events .ev').focus()`); await B.key('\uE007'); await B.sleep(900);
  const r = await B.json(`return {sel: S.sel, id: S.sel >= 0 ? M.events[S.sel].id : null, focusedRow: document.activeElement.classList.contains('ev')}`);
  lines.push(`Enter on the first event row -> selected ${r.id} (row keeps focus: ${r.focusedRow})`);
  await B.eval(`document.querySelector('#events .ev:nth-child(3)').focus()`); await B.key(' '); await B.sleep(900);
  lines.push(`Space on the third event row -> selected ${await B.eval(`S.sel >= 0 ? M.events[S.sel].id : 'nothing'`)}`);
  await B.go('about:blank'); await B.go(`${base}/?test#tab=ops&day=2`); await B.sleep(500);
  const nlanes = await B.eval(`document.querySelectorAll('#ops .track a[tabindex="0"]').length`);
  await B.eval(`document.querySelector('#ops .track a') && document.querySelector('#ops .track a').focus()`); await B.key('\uE007'); await B.sleep(600);
  lines.push(`Operator lanes focusable: ${nlanes}; Enter on the first -> card open: ${await B.eval(`[...document.querySelectorAll('#ops details.card')].some(d => d.open)`)}, focus on: ${await B.eval('document.activeElement.tagName')}`);
} finally { await B.close(); }
writeFileSync(out, lines.join('\n') + '\n'); console.log(lines.slice(-4).join('\n'));
