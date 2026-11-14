import { launch } from './bidi.mjs';
const B = await launch({ port: 9341 });
await B.viewport(1600, 1000);
await B.go('http://127.0.0.1:8781/?test');
console.log(await B.json(`return {res: curRes, t0: view.t0, t1: view.t1, n: M.events.length, sel: S.sel, status: document.getElementById('status').textContent}`));
await B.shot(new URL('../smoke.png', import.meta.url).pathname);
await B.close();
