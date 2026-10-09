// Frontend-tests: laden het dashboard in jsdom met een nagebootste fetch.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

const HTML = readFileSync(new URL('../../app/static/index.html', import.meta.url), 'utf8');
const now = Date.now();
const ep = (minAgo) => Math.floor((now - minAgo * 60e3) / 1000);
// days.json + dagbestanden zoals de server ze levert: served({'2026-10-09': [regels]})
const served = (days) => Object.fromEntries([
  ['days.json', JSON.stringify({ days: Object.keys(days) })],
  ...Object.entries(days).map(([d, lines]) => [`days/${d}.csv`, lines.join('\n') + '\n']),
]);

function load(files, url = 'http://dash/?lang=nl', languages = ['en-US'], stored = null) {
  const dom = new JSDOM(HTML, {
    runScripts: 'dangerously',
    url,
    beforeParse(w) {
      Object.defineProperty(w.navigator, 'languages', { value: languages });
      if (stored) w.localStorage.setItem('spindash.lang', stored);
      w.fetch = (u) => {
        const key = Object.keys(files).find((k) => u.includes(k));
        if (!key || files[key] === null) return Promise.resolve({ ok: false });
        return Promise.resolve({ ok: true, text: () => Promise.resolve(files[key]),
                                 json: () => Promise.resolve(JSON.parse(files[key])) });
      };
    },
  });
  return new Promise((resolve) => setTimeout(() => resolve(dom), 300));
}
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const rows = (doc) => [...doc.querySelectorAll('#events tr')].map(text);

const POOL = served({ d1: [
  `${ep(300)},disk,sdd,trunk`, `${ep(300)},disk,sde,trunk2`,
  `${ep(300)},state,sdd,standby`, `${ep(300)},state,sde,standby`,
  `${ep(121)},who,unraid,shfs (via /mnt/user),1,geopend,/mnt/trunk/`,
  `${ep(121)},who,container,bazarr,2,geopend,/mnt/trunk/Media/series/a.mkv`,
  `${ep(120)},spin,sdd,active`, `${ep(119.5)},spin,sde,active`,
  `${ep(110)},who,gebruiker,SMB-share,3,geopend,/mnt/trunk/Media/films/b.mkv`,
  `${ep(100)},who,container,plex,4,geopend,/mnt/trunk/Media/series/c.mkv`,
  `${ep(60)},spin,sdd,standby`, `${ep(60)},spin,sde,standby`,
] });

test('missing data shows a status message instead of the dashboard', async () => {
  const dom = await load({ 'days.json': null });
  const d = dom.window.document;
  assert.equal(d.getElementById('status').hidden, false);
  assert.equal(d.getElementById('dash').hidden, true);
  dom.window.close();
});

test('demo mode renders lanes for every disk', async () => {
  const dom = await load({}, 'http://dash/?demo&lang=nl');
  const d = dom.window.document;
  assert.equal(d.querySelectorAll('.lane').length, 6);
  assert.match(d.getElementById('sourceLine').textContent, /Voorbeelddata/);
  dom.window.close();
});

test('pool members are grouped into one row with names from disks.csv', async () => {
  const dom = await load(POOL);
  const r = rows(dom.window.document);
  assert.ok(r.some((x) => /trunk, trunk2 Opgespind/.test(x)), r.join('\n'));
  assert.ok(r.some((x) => /trunk, trunk2 Naar standby/.test(x)));
  dom.window.close();
});

test('the cause is the earliest file and shows the container name', async () => {
  const dom = await load(POOL);
  const up = rows(dom.window.document).find((x) => /Opgespind/.test(x));
  assert.match(up, /Media\/series gelezen: a\.mkv bazarr/);
  dom.window.close();
});

test('session list collapses SMB access to one entry per share', async () => {
  const dom = await load(POOL);
  const d = dom.window.document;
  d.querySelector('tr.has-sess .sess').click();
  const files = [...d.querySelectorAll('.files li')].map(text);
  assert.deepEqual(files.map((f) => f.replace(/^\S+ /, '')), [
    'gelezen bazarr /Media/series/a.mkv',
    'via share SMB-share /Media/',
    'gelezen plex /Media/series/c.mkv',
  ]);
  dom.window.close();
});

test('"Door wie" ranks sources of spin-ups', async () => {
  const dom = await load(POOL);
  const causes = text(dom.window.document.getElementById('causes'));
  assert.match(causes, /Media\/series 1 Door wie bazarr 1/);
  dom.window.close();
});

test('filtering on one disk shows its events separately', async () => {
  const dom = await load(POOL);
  const d = dom.window.document;
  const sel = d.getElementById('devFilter');
  sel.value = 'sde';
  sel.dispatchEvent(new dom.window.Event('change'));
  const r = rows(d);
  assert.ok(r.every((x) => !/trunk, trunk2/.test(x)));
  assert.ok(r.some((x) => /trunk2 Opgespind/.test(x)));
  dom.window.close();
});

// ---------- talen ----------
test('language follows the browser and falls back to English', async () => {
  let dom = await load(POOL, 'http://dash/', ['de-DE', 'en']);
  let d = dom.window.document;
  assert.equal(text(d.querySelector('h1')), 'Wann laufen meine Festplatten');
  assert.equal(d.documentElement.lang, 'de');
  assert.ok(rows(d).some((x) => /trunk, trunk2 Angelaufen/.test(x)));
  dom.window.close();
  dom = await load(POOL, 'http://dash/', ['ja-JP']);
  d = dom.window.document;
  assert.equal(text(d.querySelector('h1')), 'When do my drives spin');
  assert.ok(rows(d).some((x) => /Spun up/.test(x)));
  dom.window.close();
});

test('switching language translates the page and is remembered', async () => {
  const dom = await load(POOL, 'http://dash/', ['en-US']);
  const d = dom.window.document;
  const sel = d.getElementById('lang');
  for (const [code, heading, up] of [
    ['fr', 'Quand mes disques tournent-ils', 'Démarré'],
    ['es', 'Cuándo giran mis discos', 'Arrancado'],
    ['nl', 'Wanneer draaien mijn schijven', 'Opgespind'],
  ]) {
    sel.value = code;
    sel.dispatchEvent(new dom.window.Event('change'));
    assert.equal(text(d.querySelector('h1')), heading);
    assert.ok(rows(d).some((x) => x.includes(up)), code);
    assert.equal(d.querySelector('[data-range="all"]').textContent, { fr: 'Tout', es: 'Todo', nl: 'Alles' }[code]);
  }
  assert.equal(dom.window.localStorage.getItem('spindash.lang'), 'nl');
  dom.window.close();
});

test('a remembered language wins over the browser language', async () => {
  let dom = await load(POOL, 'http://dash/', ['de-DE'], 'es');
  assert.equal(text(dom.window.document.querySelector('h1')), 'Cuándo giran mis discos');
  assert.equal(dom.window.document.getElementById('lang').value, 'es');
  dom.window.close();
  dom = await load(POOL, 'http://dash/', ['de-DE'], 'xx');       // onbekende waarde: browsertaal
  assert.equal(text(dom.window.document.querySelector('h1')), 'Wann laufen meine Festplatten');
  dom.window.close();
});

test('every language has the same keys as English', async () => {
  const dom = await load(POOL, 'http://dash/', ['en-US']);
  const d = dom.window.document;
  const codes = [...d.querySelectorAll('#lang option')].map((o) => o.value);
  assert.deepEqual(codes, ['en', 'nl', 'fr', 'de', 'es']);
  const m = HTML.match(/const I18N = (\{[\s\S]*?\n\});/);
  const dict = new Function(`return ${m[1]}`)();
  const keys = Object.keys(dict.en).sort();
  for (const c of codes) assert.deepEqual(Object.keys(dict[c]).sort(), keys, c);
  dom.window.close();
});

// ---------- bladeren ----------
const day = 24 * 60;
const SPREAD = served({ d1: [`${ep(40 * day)},disk,sdd,disk1`, ...[40, 35, 9, 8.9, 1.5, 1.4, 0.2, 0.1].map((d, i) =>
  `${ep(d * day)},spin,sdd,${i % 2 ? 'standby' : 'active'}`)] });
const click = (d, sel) => d.querySelector(sel).click();
const upTimes = (d) => rows(d).filter((x) => /Opgespind/.test(x)).length;

test('day view steps one day back and forward', async () => {
  const dom = await load(SPREAD);
  const d = dom.window.document;
  click(d, '[data-range="1"]');
  assert.equal(d.getElementById('nav').hidden, false);
  assert.equal(d.getElementById('next').disabled, true);
  assert.equal(d.getElementById('now').hidden, false);
  assert.equal(d.getElementById('now').getAttribute('aria-pressed'), 'true');
  assert.equal(upTimes(d), 1);                          // 0,2 dag geleden
  click(d, '#prev');
  assert.equal(upTimes(d), 1);                          // 1,5 dag geleden
  assert.equal(d.getElementById('next').disabled, false);
  assert.equal(d.getElementById('now').getAttribute('aria-pressed'), 'false');
  assert.equal(d.getElementById('prev').getAttribute('aria-label'), 'Vorige dag');
  click(d, '#prev');
  assert.equal(upTimes(d), 0);                          // 2 tot 3 dagen geleden: niets
  click(d, '#next'); click(d, '#next');
  assert.equal(d.getElementById('next').disabled, true);
  assert.equal(upTimes(d), 1);
  dom.window.close();
});

test('week and month views step a whole period, and "now" jumps back', async () => {
  const dom = await load(SPREAD);
  const d = dom.window.document;
  click(d, '[data-range="7"]');
  assert.equal(upTimes(d), 2);                          // 1,5 en 0,2 dag
  click(d, '#prev');
  assert.equal(upTimes(d), 1);                          // 9 dagen
  assert.equal(d.getElementById('prev').getAttribute('aria-label'), 'Vorige week');
  click(d, '#now');
  assert.equal(upTimes(d), 2);
  assert.equal(d.getElementById('now').getAttribute('aria-pressed'), 'true');
  click(d, '[data-range="30"]');
  assert.equal(upTimes(d), 3);
  click(d, '#prev');
  assert.equal(upTimes(d), 1);                          // 35 dagen; 40 dagen is het begin van de data
  assert.equal(d.getElementById('prev').disabled, true);
  assert.equal(d.getElementById('prev').getAttribute('aria-label'), 'Vorige maand');
  click(d, '[data-range="all"]');
  assert.equal(d.getElementById('nav').hidden, true);
  assert.equal(upTimes(d), 4);
  dom.window.close();
});

// ---------- info-icoontjes ----------
const ROOT = served({ d1: [
  `${ep(300)},disk,sdd,trunk`, `${ep(300)},state,sdd,standby`,
  `${ep(121)},who,unraid,shfs (via /mnt/user),1,geopend,/mnt/trunk/`,
  `${ep(120)},spin,sdd,active`, `${ep(60)},spin,sdd,standby`,
  `${ep(51)},who,onbekend,find (al gestopt),9,geopend,/mnt/trunk/Media/x.nfo`,
  `${ep(50)},spin,sdd,active`, `${ep(40)},spin,sdd,standby`,
] });

test('ranking rows that need explaining get an info icon with a tooltip', async () => {
  const dom = await load(ROOT);
  const d = dom.window.document, w = dom.window;
  const infos = [...d.querySelectorAll('#causes .info')].map((b) => [text(b.closest('.cause-name').querySelector('.cn')), b.dataset.info]);
  assert.deepEqual(infos, [
    ['Root van trunk', 'rootNote'],
    ['shfs (via /mnt/user)', 'shfsNote'],
    ['find (al gestopt)', 'stoppedNote'],
  ]);
  assert.equal(d.querySelectorAll('#causes p').length, 0);         // geen losse uitlegalinea's meer
  const btn = d.querySelector('#causes .info');
  const tip = d.getElementById('tip');
  btn.dispatchEvent(new w.Event('focusin', { bubbles: true }));
  assert.equal(tip.hidden, false);
  assert.match(tip.textContent, /^Root van een pool betekent/);
  assert.equal(btn.getAttribute('aria-expanded'), 'true');
  d.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Escape' }));
  assert.equal(tip.hidden, true);
  btn.click();                                                       // tikken op telefoon
  assert.equal(tip.hidden, false);
  btn.click();
  assert.equal(tip.hidden, true);
  dom.window.close();
});

test('tooltip text follows the chosen language', async () => {
  const dom = await load(ROOT, 'http://dash/?lang=de');
  const d = dom.window.document;
  const btn = d.querySelector('#causes .info[data-info="stoppedNote"]');
  assert.equal(text(btn.closest('.cause-name').querySelector('.cn')), 'find (bereits beendet)');
  btn.click();
  assert.match(d.getElementById('tip').textContent, /^Der Prozess war bereits beendet/);
  dom.window.close();
});

// ---------- dagbestanden ----------
test('day files are combined, and a state line is not a spin-up', async () => {
  const dom = await load(served({
    '2026-10-08': [`${ep(30 * 60)},disk,sdd,trunk`, `${ep(30 * 60)},state,sdd,standby`,
                   `${ep(26 * 60)},spin,sdd,active`, `${ep(25 * 60)},spin,sdd,standby`],
    '2026-10-09': [`${ep(20 * 60)},disk,sdd,trunk`, `${ep(20 * 60)},state,sdd,standby`,
                   `${ep(3 * 60)},state,sdd,active`,                 // na een herstart: draait al
                   `${ep(60)},spin,sdd,standby`],
  }));
  const d = dom.window.document;
  const r = rows(d);
  assert.equal(r.filter((x) => /Opgespind/.test(x)).length, 1, r.join('\n'));   // alleen die van gisteren
  assert.equal(r.filter((x) => /Naar standby/.test(x)).length, 2);
  assert.match(text(d.querySelector('.lane-stats')), /^1 spin-ups/);
  dom.window.close();
});
