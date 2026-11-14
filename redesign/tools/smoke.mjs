import { launch } from './bidi.mjs';
const B = await launch({ port: 9341 });
await B.viewport(1600, 1000);
await B.go('http://127.0.0.1:8781/?test');
console.log(await B.json(`return {res: curRes, t0: view.t0, t1: view.t1, n: M.events.length, sel: S.sel, status: document.getElementById('status').textContent}`));
await B.shot('/tmp/claude-1000/-home-alex-BlueStaqHackathon/31129923-b870-4b59-842e-548fc2a84db9/scratchpad/smoke.png');
await B.close();
