// node bench.mjs <base url> [runs]: loads ?test&bench, which POSTs redraw timings to the server log (read them there).
import { launch } from './bidi.mjs';
const [base, runs = '3'] = process.argv.slice(2);
const B = await launch({ port: 9300 + Math.floor(Math.random() * 600) });
try { await B.viewport(1600, 1000); for (let i = 0; i < +runs; i++) { await B.go('about:blank'); await B.go(`${base}/?test&bench`); await B.sleep(500); } }
finally { await B.close(); }
console.log('bench done');
