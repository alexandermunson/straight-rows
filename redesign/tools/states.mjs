// Viewer states captured before and after the redesign. Each state is reached only through the public surface:
// a URL hash, or the same clicks/keys a person would use (expressed as DOM calls or the page's own functions).
const sel = id => `select(M.events.findIndex(e => e.id === '${id}'), true);`;
const selFirst = cls => `select(M.events.findIndex(e => e.kind === 'DET' && e.cls === '${cls}'), true);`;
const JAM = '#t0=239000&t1=242600';
export const STATES = [
  { name: 'inv-all-72h-60s', hash: '' },
  { name: 'inv-12h-10s', hash: '#t0=172800&t1=216000' },
  { name: 'inv-1h-1s', hash: JAM },
  { name: 'inv-10min-1s', hash: '#t0=240000&t1=240600' },
  { name: 'inv-sel-jam-source', act: sel('D1264'), wait: 1500 },
  { name: 'inv-sel-overlap-jam-in-storm', act: sel('D837'), wait: 1500 },
  { name: 'inv-sel-spoof', act: sel('D1203'), wait: 1500 },
  { name: 'inv-sel-multipath-matched', act: sel('D804'), wait: 1500 },
  { name: 'inv-sel-labelled-episode', act: sel('TRN-12'), wait: 1500 },
  { name: 'inv-missed-row-synthetic', act: `const t = M.events.find(e => e.id === 'TRN-03'); t.matched = false; buildList(); document.querySelector('#events .ev:last-child').scrollIntoView();`, wait: 400 },
  { name: 'inv-hover-tooltip', hash: JAM, act: `S.hoverX = 640; S.hoverY = 320; dirtyTop = true;`, wait: 400 },
  { name: 'inv-layers-open', act: `document.querySelector('aside details').open = true;`, wait: 300 },
  { name: 'inv-layers-all-toggled', hash: JAM, act: `document.querySelector('aside details').open = true; document.querySelectorAll('#opts input').forEach(n => n.click());`, wait: 600 },
  { name: 'inv-tractor-T2-hidden', hash: JAM, act: `document.querySelectorAll('#trs input')[1].click();`, wait: 600 },
  { name: 'inv-filter-jamming', act: `const f = document.getElementById('fCls'); f.value = 'JAMMING'; f.dispatchEvent(new Event('change'));`, wait: 300 },
  { name: 'inv-info-folds-open', act: sel('D1264') + ` document.querySelectorAll('#info details').forEach(d => d.open = true);`, wait: 1500 },
  { name: 'inv-map-whole-area', act: sel('D1017') + ` document.getElementById('mapAll').click();`, wait: 1500 },
  { name: 'inv-map-zoomed-in', act: sel('D1264') + ` document.getElementById('mapIn').click(); document.getElementById('mapIn').click();`, wait: 1500 },
  { name: 'inv-key-n-next', act: `window.dispatchEvent(new KeyboardEvent('keydown', {key: 'n'}));`, wait: 1500 },
  { name: 'ops-day3', hash: '#tab=ops&day=2' },
  { name: 'ops-day1', hash: '#tab=ops&day=0' },
  { name: 'ops-day2', hash: '#tab=ops&day=1' },
  { name: 'ops-card-open', hash: '#tab=ops&day=2&open=1' },
  { name: 'ops-trust-open', hash: '#tab=ops&day=2', act: `document.querySelector('#ops details').open = true;`, wait: 300 },
  { name: 'ops-card-more-open', hash: '#tab=ops&day=2&open=1', act: `document.querySelectorAll('#ops details.card[open] details').forEach(d => d.open = true);`, wait: 300 },
  { name: 'spec-default', hash: '#tab=spec', wait: 800 },
  { name: 'spec-howto-open', hash: '#tab=spec', act: `document.querySelector('#spec details').open = true;`, wait: 500 },
  { name: 'spec-meancn0-day3-T2', hash: '#tab=spec&ch=cn0&scope=3&tr=1', wait: 800 },
  { name: 'spec-xte-all-T3', hash: '#tab=spec&ch=xte&scope=0&tr=2', wait: 800 },
  { name: 'spec-overtones-off-T2-hidden', hash: '#tab=spec', act: `document.getElementById('spHarm').click(); document.querySelectorAll('#spec input[data-sp]')[1].click();`, wait: 500 },
  { name: 'spec-hover', hash: '#tab=spec', act: `SP.hp = [420, 120]; SP.hs = [700, 160]; drawPsd(); drawSpec();`, wait: 300 },
  { name: 'size-1280-inv-sel-jam', w: 1280, h: 720, act: sel('D1264'), wait: 1500 },
  { name: 'size-1280-ops', w: 1280, h: 720, hash: '#tab=ops&day=2&open=1' },
  { name: 'size-1280-spec', w: 1280, h: 720, hash: '#tab=spec', wait: 800 },
  { name: 'size-1920-inv-all', w: 1920, h: 1080 },
];
export const SUB_STATES = [
  { name: 'sub-inv-all', hash: '' },
  { name: 'sub-inv-sel-jam', act: selFirst('JAMMING') + ` document.querySelectorAll('#info details').forEach(d => d.open = true);`, wait: 1500 },
  { name: 'sub-ops-day3', hash: '#tab=ops&day=2' },
  { name: 'sub-spec', hash: '#tab=spec', wait: 800 },
];
