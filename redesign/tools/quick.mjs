// node quick.mjs <url> <out.png> [js] [w] [h]: load with ?test, optionally run js, report page errors, screenshot.
import { launch } from './bidi.mjs';
const [url, out, js, w = '1600', h = '1000'] = process.argv.slice(2);
const B = await launch({ port: 9300 + Math.floor(Math.random() * 600) });
try {
  await B.viewport(+w, +h); await B.go(url); await B.sleep(300);
  if (js) await B.eval(`(() => { ${js} })()`);
  await B.sleep(1200);
  console.log('errors:', JSON.stringify(await B.eval('window.__errs')));
  await B.shot(out);
} finally { await B.close(); }
