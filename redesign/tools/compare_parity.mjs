// node compare_parity.mjs <old.json> <new.json> <out.txt>: field-by-field comparison of two parity dumps.
import { readFileSync, writeFileSync } from 'node:fs';
const [a, b, out] = process.argv.slice(2).map((x, i) => i < 2 ? JSON.parse(readFileSync(x, 'utf8')) : x);
const lines = [], diffs = [];
const cmp = (path, x, y) => {
  if (x && typeof x === 'object' && y && typeof y === 'object') { for (const k of new Set([...Object.keys(x), ...Object.keys(y)])) cmp(`${path}.${k}`, x[k], y[k]); return; }
  if (JSON.stringify(x) !== JSON.stringify(y)) diffs.push(`${path}: old ${JSON.stringify(x)} | new ${JSON.stringify(y)}`);
};
for (const sec of ['values', 'envelopes', 'savedHash', 'hashes', 'interactions']) {
  const before = diffs.length; cmp(sec, a[sec], b[sec]);
  const n = sec === 'interactions' ? a[sec].length : typeof a[sec] === 'object' ? Object.keys(a[sec]).length : 1;
  lines.push(`${sec.padEnd(13)} ${n} items compared, ${diffs.length - before} differences`);
}
lines.push('', ...diffs);
writeFileSync(out, lines.join('\n') + '\n'); console.log(lines.slice(0, 8).join('\n'));
