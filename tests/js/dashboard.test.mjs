// Frontend-tests: laden het dashboard in jsdom met een nagebootste fetch.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

const HTML = readFileSync(new URL('../../app/static/index.html', import.meta.url), 'utf8');
const now = Date.now();
const ep = (minAgo) => Math.floor((now - minAgo * 60e3) / 1000);
// lokale datum als dagnaam, zoals de server die gebruikt
const dayName = (ms) => { const d = new Date(ms); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };
const today = dayName(now);
// days.json + dagbestanden zoals de server ze levert: served({'2026-10-09': [regels]})
const served = (days) => Object.fromEntries([
  ['days.json', JSON.stringify({ days: Object.keys(days) })],
  ...Object.entries(days).map(([d, lines]) => [`days/${d}.csv`, lines.join('\n') + '\n']),
]);

function load(files, url = 'http://dash/?lang=nl', languages = ['en-US'], stored = null, setup = null) {
  const dom = new JSDOM(HTML, {
    runScripts: 'dangerously',
    url,
    beforeParse(w) {
      Object.defineProperty(w.navigator, 'languages', { value: languages });
      if (stored) w.localStorage.setItem('spindash.lang', stored);
      if (setup) setup(w);
      w.__fetched = [];
      w.__posts = [];
      w.fetch = (u, opts = {}) => {
        w.__fetched.push(u);
        if (opts.method === 'POST') {     // opslaan: vastleggen; files.POST = false laat het mislukken
          w.__posts.push({ url: u, type: opts.headers['Content-Type'], body: JSON.parse(opts.body) });
          if (files.POST === false) return Promise.reject(new Error('offline'));
          const reply = Object.entries(files.POSTREPLY || {}).find(([k]) => u.includes(k));
          return Promise.resolve({ ok: true, json: () => Promise.resolve(reply ? reply[1] : {}) });
        }
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
// naam van een kaartje in de instellingen, zonder de positie die alleen voor schermlezers is
const cardName = (label) => [...label.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join('').trim();
const rows = (doc) => [...doc.querySelectorAll('#events tr')].map(text);

const POOL = served({ [today]: [
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
  const dom = await load({}, 'http://dash/?demo&lang=nl', ['en-US'], null, (w) => w.localStorage.setItem('spindash.group', JSON.stringify({ merge: false })));
  const d = dom.window.document;
  assert.equal(d.querySelectorAll('.lane').length, 6);
  assert.match(d.getElementById('sourceLine').textContent, /Voorbeelddata/);
  dom.window.close();
});

test('without pool grouping, pool members are grouped into one row with names from disks.csv', async () => {
  const dom = await load(POOL, 'http://dash/?lang=nl', ['en-US'], null, (w) => w.localStorage.setItem('spindash.group', JSON.stringify({ merge: false })));
  const r = rows(dom.window.document);
  assert.ok(r.some((x) => /trunk, trunk2 Opgespind/.test(x)), r.join('\n'));
  assert.ok(r.some((x) => /trunk, trunk2 Naar standby/.test(x)));
  assert.equal(dom.window.document.querySelectorAll('.lane').length, 2);
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
    'R gelezen bazarr /Media/series/a.mkv',
    'via share SMB-share /Media/',
    'R gelezen plex /Media/series/c.mkv',
  ]);
  dom.window.close();
});

test('"Per bron" ranks sources by the spin-ups they caused, with reads, writes and drives', async () => {
  const dom = await load(POOL);
  const d = dom.window.document;
  const rows = [...d.querySelectorAll('#sources .src')].map(text);
  // soort als tekst voor schermlezers; spin-ups eerst, daarna de activiteit
  assert.equal(rows[0], 'container: bazarr 1 veroorzaakte spin-ups 1 gelezen · 0 geschreven · trunk');
  assert.ok(rows.slice(1).every((r) => / 0 veroorzaakte spin-ups /.test(r)), rows.join('\n'));
  assert.doesNotMatch(text(d.getElementById('causes')), /Door wie|bazarr/);   // staat niet meer bij de oorzaken
  dom.window.close();
});

test('filtering on one disk shows its events separately', async () => {
  const dom = await load(POOL, 'http://dash/?lang=nl', ['en-US'], null, (w) => w.localStorage.setItem('spindash.group', JSON.stringify({ merge: false })));
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
  assert.ok(rows(d).some((x) => /trunk Angelaufen/.test(x)));
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
const SPREAD = served({ [today]: [`${ep(40 * day)},disk,sdd,disk1`, ...[40, 35, 9, 8.9, 1.5, 1.4, 0.2, 0.1].map((d, i) =>
  `${ep(d * day)},spin,sdd,${i % 2 ? 'standby' : 'active'}`)] });
// een wissel toont eerst het skelet; wacht tot het dashboard terug is
const settle = async (d) => {
  for (let i = 0; i < 150 && (d.getElementById('dash').hidden || !d.getElementById('loader').hidden); i++)
    await new Promise((r) => setTimeout(r, 20));
};
const click = async (d, sel) => { d.querySelector(sel).click(); await settle(d); };
const upTimes = (d) => rows(d).filter((x) => /Opgespind/.test(x)).length;

test('day view steps one day back and forward', async () => {
  const dom = await load(SPREAD);
  const d = dom.window.document;
  await click(d, '[data-range="1"]');
  assert.equal(d.getElementById('nav').hidden, false);
  assert.equal(d.getElementById('next').disabled, true);
  assert.equal(d.getElementById('now').hidden, false);
  assert.equal(d.getElementById('now').getAttribute('aria-pressed'), 'true');
  assert.equal(upTimes(d), 1);                          // 0,2 dag geleden
  await click(d, '#prev');
  assert.equal(upTimes(d), 1);                          // 1,5 dag geleden
  assert.equal(d.getElementById('next').disabled, false);
  assert.equal(d.getElementById('now').getAttribute('aria-pressed'), 'false');
  assert.equal(d.getElementById('prev').getAttribute('aria-label'), 'Vorige dag');
  await click(d, '#prev');
  assert.equal(upTimes(d), 0);                          // 2 tot 3 dagen geleden: niets
  await click(d, '#next'); await click(d, '#next');
  assert.equal(d.getElementById('next').disabled, true);
  assert.equal(upTimes(d), 1);
  dom.window.close();
});

test('week and month views step a whole period, and "now" jumps back', async () => {
  const dom = await load(SPREAD);
  const d = dom.window.document;
  await click(d, '[data-range="7"]');
  assert.equal(upTimes(d), 2);                          // 1,5 en 0,2 dag
  await click(d, '#prev');
  assert.equal(upTimes(d), 1);                          // 9 dagen
  assert.equal(d.getElementById('prev').getAttribute('aria-label'), 'Vorige week');
  await click(d, '#now');
  assert.equal(upTimes(d), 2);
  assert.equal(d.getElementById('now').getAttribute('aria-pressed'), 'true');
  await click(d, '[data-range="30"]');
  assert.equal(upTimes(d), 3);
  await click(d, '#prev');
  assert.equal(upTimes(d), 1);                          // 35 dagen; 40 dagen is het begin van de data
  assert.equal(d.getElementById('prev').disabled, true);
  assert.equal(d.getElementById('prev').getAttribute('aria-label'), 'Vorige maand');
  await click(d, '[data-range="all"]');
  assert.equal(d.getElementById('nav').hidden, true);
  assert.equal(upTimes(d), 4);
  dom.window.close();
});

// ---------- info-icoontjes ----------
const ROOT = served({ [today]: [
  `${ep(300)},disk,sdd,trunk`, `${ep(300)},state,sdd,standby`,
  `${ep(121)},who,unraid,shfs (via /mnt/user),1,geopend,/mnt/trunk/`,
  `${ep(120)},spin,sdd,active`, `${ep(60)},spin,sdd,standby`,
  `${ep(51)},who,onbekend,find (al gestopt),9,geopend,/mnt/trunk/Media/x.nfo`,
  `${ep(50)},spin,sdd,active`, `${ep(40)},spin,sdd,standby`,
] });

test('ranking rows that need explaining get an info icon with a tooltip', async () => {
  const dom = await load(ROOT);
  const d = dom.window.document, w = dom.window;
  const infos = [...d.querySelectorAll('#causes .info, #sources .info')].map((b) => [text(b.closest('.cause-name').querySelector('.cn')), b.dataset.info]);
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
  const btn = d.querySelector('#sources .info[data-info="stoppedNote"]');
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
  await click(d, '[data-range="all"]');                  // de spin-up van gisteren valt buiten de 24 uur
  const r = rows(d);
  assert.equal(r.filter((x) => /Opgespind/.test(x)).length, 1, r.join('\n'));   // alleen die van gisteren
  assert.equal(r.filter((x) => /Naar standby/.test(x)).length, 2);
  assert.match(text(d.querySelector('.lane-stats')), /^1 spin-ups/);
  dom.window.close();
});

// ---------- lezen/schrijven en actieve schijven ----------
const RW = served({ [today]: [
  `${ep(300)},disk,sdd,trunk`,
  `${ep(300)},state,sdd,active`,                                       // draait al, geen spin-up
  `${ep(200)},who,container,plex,10,geschreven,/mnt/trunk/appdata/plex/Logs/server.log`,
  `${ep(190)},who,container,plex,10,geschreven,/mnt/trunk/appdata/plex/Logs/server.log`,
  `${ep(180)},who,container,sonarr,11,geschreven,/mnt/trunk/appdata/sonarr/sonarr.db`,
  `${ep(120)},who,container,bazarr,12,geopend,/mnt/trunk/Media/series/a.mkv`,
] });

test('a write event is grouped under the drive in the writing card, a read in the reading card', async () => {
  const dom = await load(RW);
  const d = dom.window.document;
  const ws = d.getElementById('actWrites'), rs = d.getElementById('actReads');
  assert.ok(ws.querySelector('.actdisk'), text(ws));
  assert.match(text(ws), /plex/);
  assert.match(text(ws), /sonarr/);
  assert.doesNotMatch(text(ws), /bazarr/);
  assert.match(text(rs), /bazarr/);
  assert.doesNotMatch(text(rs), /plex|sonarr/);
  dom.window.close();
});

test('a write event in a session list carries a W badge, a read an R badge', async () => {
  const SES = served({ [today]: [
    `${ep(300)},disk,sdd,trunk`, `${ep(300)},state,sdd,standby`,
    `${ep(121)},who,container,plex,10,geschreven,/mnt/trunk/appdata/plex/Logs/server.log`,
    `${ep(120.5)},who,container,bazarr,2,geopend,/mnt/trunk/Media/series/a.mkv`,
    `${ep(120)},spin,sdd,active`, `${ep(60)},spin,sdd,standby`,
  ] });
  const dom = await load(SES);
  const d = dom.window.document;
  d.querySelector('tr.has-sess .sess').click();
  const badges = [...d.querySelectorAll('.files .ev .rw')].map((b) => b.textContent);
  assert.ok(badges.includes('W'), badges.join(','));   // geschreven -> W
  assert.ok(badges.includes('R'), badges.join(','));   // geopend op bestand -> R
  dom.window.close();
});

test('active drives ranks the top writer and shows write and read counts', async () => {
  const dom = await load(RW);
  const d = dom.window.document;
  // plex schreef 2x, sonarr 1x, bazarr las 1x: 4 gebeurtenissen op trunk
  const ws = text(d.getElementById('actWrites')), rs = text(d.getElementById('actReads'));
  assert.match(ws, /trunk 3 keer geschreven \(75% van 4 gebeurtenissen\)/);
  assert.match(rs, /trunk 1 keer gelezen \(25% van 4 gebeurtenissen\)/);
  // volgorde: plex (2) voor sonarr (1)
  const names = [...d.querySelectorAll('#actWrites .cn')].map(text);
  assert.ok(names.indexOf('plex') < names.indexOf('sonarr'), names.join(','));
  dom.window.close();
});

test('a direction without activity says so in its own card', async () => {
  const RO = served({ [today]: [
    `${ep(300)},disk,sdd,trunk`, `${ep(300)},state,sdd,active`,
    `${ep(120)},who,container,bazarr,12,geopend,/mnt/trunk/Media/series/a.mkv`,
  ] });
  const dom = await load(RO);
  const d = dom.window.document;
  assert.equal(text(d.getElementById('actWrites')), 'Niets geschreven in deze periode.');
  assert.match(text(d.getElementById('actReads')), /bazarr/);
  dom.window.close();
});

test('legacy who line without direction still renders without a badge', async () => {
  // oud 'geopend'-record van een bestand: geen R/W-badge (richting onbekend), geen crash
  const LEG = served({ [today]: [
    `${ep(300)},disk,sdd,trunk`, `${ep(300)},state,sdd,standby`,
    `${ep(121)},who,container,bazarr,2,geopend,/mnt/trunk/Media/series/a.mkv`,
    `${ep(120)},spin,sdd,active`, `${ep(60)},spin,sdd,standby`,
  ] });
  const dom = await load(LEG);
  const d = dom.window.document;
  const up = rows(d).find((x) => /Opgespind/.test(x));
  assert.match(up, /gelezen: a\.mkv/);            // oude regel toont als 'gelezen', geen richting-badge
  dom.window.close();
});

// ---------- live feed, laatste uur en pad-overlay ----------
test('live activity feed shows recent files newest first with R/W dot', async () => {
  const dom = await load(RW);
  const d = dom.window.document;
  const items = [...d.querySelectorAll('#feed li')];
  assert.ok(items.length >= 3, items.map(text).join(' | '));
  // nieuwste (bazarr, 120 min geleden) bovenaan; oudste (plex, 200 min) onderaan
  assert.match(text(items[0]), /a\.mkv/);
  assert.match(text(items[items.length - 1]), /server\.log/);
  // R/W-punten aanwezig
  assert.ok(d.querySelector('#feed .fb.w'), 'write badge');
  assert.ok(d.querySelector('#feed .fb.r'), 'read badge');
  // relatieve tijd aanwezig (min of uur geleden)
  assert.match(text(items[0]), /\d+ (min|u) geleden/);
  dom.window.close();
});

test('feed shows the folder structure and the full path in the overlay', async () => {
  const dom = await load(RW);
  const d = dom.window.document, w = dom.window;
  const fp = [...d.querySelectorAll('#feed .fp')].find((el) => /a\.mkv/.test(el.textContent));
  // zichtbaar: share-relatief pad met mappen, niet alleen de bestandsnaam
  assert.equal(fp.textContent, '/Media/series/a.mkv');
  // volledige pad in data-path voor de hover-overlay
  assert.equal(fp.dataset.path, '/mnt/trunk/Media/series/a.mkv');
  fp.dispatchEvent(new w.Event('focusin', { bubbles: true }));
  const tip = d.getElementById('tip');
  assert.equal(tip.hidden, false);
  assert.equal(tip.textContent, '/mnt/trunk/Media/series/a.mkv');
  dom.window.close();
});

test('last hour toggle narrows the window and shows minute ticks', async () => {
  const dom = await load(RW);
  const d = dom.window.document, w = dom.window;
  d.querySelector('[data-range="h"]').click();
  await new Promise((r) => setTimeout(r, 50));
  assert.equal(d.querySelector('[data-range="h"]').getAttribute('aria-pressed'), 'true');
  // gebeurtenissen ouder dan een uur vallen buiten de tabel
  const r = rows(d);
  assert.equal(r.filter((x) => /Opgespind|Naar standby/.test(x)).length, 0, r.join('\n'));
  dom.window.close();
});

test('hovering a file path in Active drives shows the full path overlay', async () => {
  const dom = await load(RW);
  const d = dom.window.document, w = dom.window;
  const el = d.querySelector('#actWrites .haspath');
  assert.ok(el, 'file row with path');
  el.dispatchEvent(new w.Event('focusin', { bubbles: true }));
  const tip = d.getElementById('tip');
  assert.equal(tip.hidden, false);
  assert.match(tip.textContent, /\/appdata\//);
  el.dispatchEvent(new w.Event('focusout', { bubbles: true }));
  assert.equal(tip.hidden, true);
  dom.window.close();
});

// ---------- live stream (Server-Sent Events) ----------
function loadWithStream(files) {
  let handler = null;
  const dom = new JSDOM(HTML, {
    runScripts: 'dangerously',
    url: 'http://dash/?lang=nl',
    beforeParse(w) {
      Object.defineProperty(w.navigator, 'languages', { value: ['en-US'] });
      w.fetch = (u) => {
        const key = Object.keys(files).find((k) => u.includes(k));
        if (!key || files[key] === null) return Promise.resolve({ ok: false });
        return Promise.resolve({ ok: true, text: () => Promise.resolve(files[key]),
                                 json: () => Promise.resolve(JSON.parse(files[key])) });
      };
      w.EventSource = class {
        constructor() { }
        set onmessage(fn) { handler = fn; }
        set onerror(fn) { }
        close() { }
      };
      w.__push = (line) => handler && handler({ data: line });
    },
  });
  return new Promise((resolve) => setTimeout(() => resolve(dom), 300));
}

test('a streamed who line appears in the feed immediately', async () => {
  const dom = await loadWithStream(RW);
  const d = dom.window.document, w = dom.window;
  const before = d.querySelectorAll('#feed li').length;
  // simuleer een event dat de server net heeft gelogd
  w.__push(`${Math.floor(Date.now() / 1000)},who,container,radarr,99,geschreven,/mnt/trunk/appdata/radarr/radarr.db`);
  await new Promise((r) => setTimeout(r, 50));
  const items = [...d.querySelectorAll('#feed li')];
  assert.ok(items.length > before, 'feed groeit zonder te wachten op de minuten-fetch');
  assert.match(text(items[0]), /radarr\.db/);          // nieuwste bovenaan
  assert.ok(items[0].querySelector('.fb.w'), 'write badge op het gestreamde event');
  assert.ok(items[0].classList.contains('fresh'), 'nieuw item krijgt een highlight');
  dom.window.close();
});

// ---------- laadpaneel ----------
test('first load shows a loading panel with progress, then the dashboard', async () => {
  let release;
  const slow = new Promise((r) => { release = r; });
  const files = served({
    '2026-10-08': [`${ep(30 * 60)},disk,sdd,trunk`, `${ep(30 * 60)},state,sdd,standby`],
    '2026-10-09': [],
  });
  files['days.json'] = JSON.stringify({ days: ['2026-10-08', '2026-10-09'], bytes: 2 * 1048576 });
  const day2 = [`${ep(120)},spin,sdd,active`, `${ep(60)},spin,sdd,standby`].join('\n') + '\n';
  files['days/2026-10-09.csv'] = slow.then(() => day2);              // tweede dag komt later binnen
  const dom = await load(files);
  const d = dom.window.document;
  const bar = d.getElementById('loaderBar');
  assert.equal(d.getElementById('loader').hidden, false);
  assert.equal(d.getElementById('dash').hidden, true);
  assert.match(d.getElementById('loaderText').textContent, /^1 van 2 dagen geladen · 0,0 van 2,0 MB$/);
  assert.equal(bar.classList.contains('indeterminate'), false);
  release();
  await new Promise((r) => setTimeout(r, 200));
  assert.equal(d.getElementById('loader').hidden, true);
  assert.equal(d.getElementById('dash').hidden, false);
  assert.equal(bar.getAttribute('aria-valuenow'), '96');                 // laatste stap: opbouwen
  assert.ok(rows(d).some((x) => /Opgespind/.test(x)));
  dom.window.close();
});

test('no data hides the loading panel and shows the status message', async () => {
  const dom = await load({ 'days.json': null });
  const d = dom.window.document;
  assert.equal(d.getElementById('loader').hidden, true);
  assert.equal(d.getElementById('status').hidden, false);
  dom.window.close();
});

// ---------- alleen de dagen van de getoonde periode ----------
test('only the days of the shown period are fetched; browsing back loads the rest', async () => {
  const days = {};
  for (let k = 11; k >= 0; k--){
    const t0 = now - k * day * 60e3;
    days[dayName(t0)] = [`${Math.floor(t0 / 1000) - 7200},disk,sdd,disk1`,
      `${Math.floor(t0 / 1000) - 3600},spin,sdd,active`, `${Math.floor(t0 / 1000) - 1800},spin,sdd,standby`];
  }
  const files = served(days);
  files['days.json'] = JSON.stringify({ days: Object.keys(days), sizes: Object.keys(days).map(() => 100) });
  const dom = await load(files);
  const d = dom.window.document;
  const fetchedDays = () => dom.window.__fetched.filter((u) => u.includes('/days/')).map((u) => u.match(/(\d{4}-\d{2}-\d{2})/)[1]);
  const names = Object.keys(days);
  // standaard 24 uur: vandaag, gisteren en een dag marge
  assert.equal(d.querySelector('[data-range="1"]').getAttribute('aria-pressed'), 'true');
  assert.deepEqual(fetchedDays().sort(), names.slice(9));
  assert.equal(upTimes(d), 1);
  d.querySelector('[data-range="7"]').click();          // wissel: skelet en laadbalk, de knop is meteen actief
  assert.equal(d.getElementById('dash').hidden, true);
  assert.equal(d.getElementById('loader').hidden, false);
  assert.equal(d.getElementById('loaderBar').hidden, false);
  assert.equal(d.querySelector('[data-range="7"]').getAttribute('aria-pressed'), 'true');
  await settle(d);
  assert.deepEqual(fetchedDays().sort(), names.slice(3));
  assert.equal(upTimes(d), 7);
  await click(d, '#prev');                               // week terug: de oudere dagen worden bijgeladen
  assert.deepEqual(fetchedDays().sort(), names);
  assert.equal(upTimes(d), 5);                           // 7 t/m 11 dagen geleden
  const before = fetchedDays().length;
  d.querySelector('[data-range="all"]').click();         // alles staat al in het geheugen: toch even het skelet
  assert.equal(d.getElementById('loader').hidden, false);
  await settle(d);
  assert.equal(fetchedDays().length, before);
  assert.equal(upTimes(d), 12);
  dom.window.close();
});

test('loading finishes in a hidden tab, where requestAnimationFrame never fires', async () => {
  const dom = await load(POOL, 'http://dash/?lang=nl', ['en-US'], null, (w) => { w.requestAnimationFrame = () => 0; });
  const d = dom.window.document;
  assert.equal(d.getElementById('dash').hidden, false);
  assert.equal(d.getElementById('loader').hidden, true);
  dom.window.close();
});

// ---------- toegankelijkheid ----------
// WCAG-contrast tussen twee hexkleuren
const lum = (hex) => {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
};
const contrast = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
const tokens = (block) => Object.fromEntries([...block.matchAll(/--([\w-]+):(#[0-9A-Fa-f]{6})/g)].map((m) => [m[1], m[2]]));

test('text and badge colours reach 4.5:1 in the light and dark theme', () => {
  const light = tokens(HTML.match(/:root\{([^}]*)\}/)[1]);
  const dark = tokens(HTML.match(/:root\[data-theme="dark"\]\{([^}]*)\}/)[1]);
  const pairs = [['ink', 'panel'], ['ink', 'bg'], ['muted', 'panel'], ['muted', 'bg'], ['up-text', 'panel'], ['up-text', 'bg'],
    ['on-accent', 'up-text'], ['on-accent', 'muted'], ['on-accent', 'spin']];
  for (const [name, th] of [['light', light], ['dark', dark]]) {
    for (const [fg, bg] of pairs) {
      const r = contrast(th[fg], th[bg]);
      assert.ok(r >= 4.5, `${name}: --${fg} on --${bg} is ${r.toFixed(2)}:1`);
    }
  }
});

test('font sizes are set in rem so they follow the browser text size', () => {
  assert.doesNotMatch(HTML, /font-size:\s*\d+(\.\d+)?px/);
  assert.doesNotMatch(HTML.match(/<style>[\s\S]*<\/style>/)[0], /font:[^;}]*\d+px/);
});

test('a lane can be read with the keyboard: arrow keys step through its periods', async () => {
  const dom = await load(POOL);
  const d = dom.window.document, w = dom.window;
  const tr = d.querySelector('.trace');
  assert.equal(tr.getAttribute('tabindex'), '0');
  assert.match(tr.getAttribute('aria-label'), /^trunk: 1 spin-ups, \d+% actief$/);
  assert.equal(d.getElementById(tr.getAttribute('aria-describedby')).textContent, 'Gebruik de pijltjestoetsen links en rechts om door de periodes te stappen.');
  assert.equal(tr.querySelector('svg').getAttribute('aria-hidden'), 'true');
  const key = (k) => tr.dispatchEvent(new w.KeyboardEvent('keydown', { key: k, bubbles: true }));
  tr.focus();
  const tip = d.getElementById('tip'), live = d.getElementById('tipLive');
  key('ArrowRight');
  assert.equal(tip.hidden, false);
  assert.match(live.textContent, /^trunk, in standby/);
  for (let n = 0; n < 5 && !/draait/.test(live.textContent); n++) key('ArrowRight');   // naar de spin-up
  assert.match(live.textContent, /^trunk, draait \(2 van 2\), .*Gewekt door Media\/series/);
  key('End'); const last = live.textContent;
  key('ArrowRight');                                     // voorbij het einde blijft hij staan
  assert.equal(live.textContent, last);
  key('Home');
  assert.match(live.textContent, /^trunk, in standby/);
  tr.blur();
  assert.equal(tip.hidden, true);
  dom.window.close();
});

test('histogram bars and info icons carry text for screen readers', async () => {
  const dom = await load(ROOT);
  const d = dom.window.document;
  const bars = [...d.querySelectorAll('#hist div')];
  assert.equal(bars.length, 24);
  assert.ok(bars.every((b) => b.getAttribute('role') === 'img' && /spin-ups$/.test(b.getAttribute('aria-label'))));
  assert.equal(d.getElementById('histLabels').getAttribute('aria-hidden'), 'true');
  assert.equal(d.getElementById('axis').getAttribute('aria-hidden'), 'true');
  const info = d.querySelector('#causes .info');
  assert.match(d.getElementById(info.getAttribute('aria-describedby')).textContent, /^Root van een pool betekent/);
  dom.window.close();
});

test('the kind of source is named in text, not only by the colour of its dot', async () => {
  const dom = await load(POOL);
  const d = dom.window.document;
  const dot = d.querySelector('#sources .kind-dot.container');
  assert.equal(dot.title, 'container');
  assert.equal(dot.nextElementSibling.className, 'sr');
  assert.equal(dot.nextElementSibling.textContent, 'container: ');
  dom.window.close();
});

// ---------- instellingen: taal en kaartjes ----------
const cardOrder = (d) => [...d.querySelectorAll('#dash > [data-card]')].filter((el) => !el.hidden).map((el) => el.dataset.card);

test('the settings button opens a dialog with the language choice and the card list', async () => {
  const dom = await load(POOL);
  const d = dom.window.document;
  const gear = d.getElementById('openSettings');
  assert.equal(gear.getAttribute('aria-label'), 'Instellingen');
  assert.ok(d.getElementById('settings').contains(d.getElementById('lang')));   // taalkeuze zit in de instellingen
  gear.click();
  assert.ok(d.getElementById('settings').hasAttribute('open'));
  assert.equal(gear.getAttribute('aria-expanded'), 'true');
  const names = [...d.querySelectorAll('#cardList li label')].map(cardName);
  assert.deepEqual(names, ['Kerncijfers', 'Tijdlijn per schijf', 'Tijdlijn per bron', 'Spin-ups per dag', 'Spin-ups per uur van de dag',
    'Oorzaken van spin-ups per map', 'Wakker gehouden per bron', 'Activiteit per bron', 'Ritme per bron',
    'Lezen per schijf', 'Schrijven per schijf', 'Draaitijd en rust per schijf', 'Live activiteit', 'Gebeurtenissen']);
  d.getElementById('settingsDone').click();
  assert.ok(!d.getElementById('settings').hasAttribute('open'));
  assert.equal(d.activeElement, gear);                    // focus terug op de knop
  dom.window.close();
});

test('cards can be hidden and moved, and the layout is remembered', async () => {
  const dom = await load(POOL);
  const d = dom.window.document, w = dom.window;
  assert.deepEqual(cardOrder(d), ['kpi', 'lanes', 'srcLanes', 'days', 'hist', 'causes', 'awake', 'src', 'rhythm', 'actR', 'actW', 'rest', 'feed', 'events']);
  d.getElementById('openSettings').click();
  const box = d.querySelector('#cardList li[data-card="actR"] input');
  box.checked = false; box.dispatchEvent(new w.Event('change', { bubbles: true }));
  assert.equal(d.querySelector('#dash > [data-card="actR"]').hidden, true);
  d.querySelector('#cardList li[data-card="events"] [data-move="-1"]').click();   // gebeurtenissen één omhoog
  assert.deepEqual(cardOrder(d), ['kpi', 'lanes', 'srcLanes', 'days', 'hist', 'causes', 'awake', 'src', 'rhythm', 'actW', 'rest', 'events', 'feed']);
  assert.equal(d.activeElement, d.querySelector('#cardList li[data-card="events"] [data-move="-1"]'));
  assert.equal(d.getElementById('cardLive').textContent, 'Gebeurtenissen: plaats 13 van 14');
  for (let i = 0; i < 4; i++) d.querySelector('#cardList li[data-card="hist"] [data-move="-1"]').click();   // de histogram naar boven
  assert.equal(d.querySelector('#cardList li[data-card="hist"] [data-move="-1"]').disabled, true);
  assert.equal(d.activeElement, d.querySelector('#cardList li[data-card="hist"] [data-move="1"]'));   // bovenaan: focus naar omlaag
  const saved = w.localStorage.getItem('spindash.cards');
  dom.window.close();

  const again = await load(POOL, 'http://dash/?lang=nl', ['en-US'], null, (w2) => w2.localStorage.setItem('spindash.cards', saved));
  assert.deepEqual(cardOrder(again.window.document), ['hist', 'kpi', 'lanes', 'srcLanes', 'days', 'causes', 'awake', 'src', 'rhythm', 'actW', 'rest', 'events', 'feed']);
  again.window.close();
});

test('a saved layout with unknown or missing cards still shows every known card', async () => {
  const stored = JSON.stringify([{ id: 'feed' }, { id: 'nope' }, { id: 'feed', hidden: true }, { id: 'hist', hidden: true }]);
  const dom = await load(POOL, 'http://dash/?lang=nl', ['en-US'], null, (w) => w.localStorage.setItem('spindash.cards', stored));
  const d = dom.window.document;
  assert.deepEqual(cardOrder(d), ['feed', 'kpi', 'lanes', 'srcLanes', 'days', 'causes', 'awake', 'src', 'rhythm', 'actR', 'actW', 'rest', 'events']);
  d.getElementById('openSettings').click();
  d.getElementById('resetCards').click();
  assert.deepEqual(cardOrder(d), ['kpi', 'lanes', 'srcLanes', 'days', 'hist', 'causes', 'awake', 'src', 'rhythm', 'actR', 'actW', 'rest', 'feed', 'events']);
  assert.equal(dom.window.localStorage.getItem('spindash.cards'), null);
  dom.window.close();
});

test('a saved layout with the old Active drives card gets the reading and writing cards in its place', async () => {
  const stored = JSON.stringify([{ id: 'act', hidden: true }, { id: 'lanes' }, { id: 'events' }, { id: 'hist' }, { id: 'causes' }, { id: 'feed' }]);
  const dom = await load(POOL, 'http://dash/?lang=nl', ['en-US'], null, (w) => w.localStorage.setItem('spindash.cards', stored));
  const d = dom.window.document;
  assert.deepEqual([...d.querySelectorAll('#dash > [data-card]')].map((el) => el.dataset.card),
    ['actR', 'actW', 'lanes', 'events', 'hist', 'causes', 'feed', 'kpi', 'srcLanes', 'days', 'awake', 'src', 'rhythm', 'rest']);
  assert.equal(d.querySelector('#dash > [data-card="actR"]').hidden, true);   // verborgen blijft verborgen
  assert.equal(d.querySelector('#dash > [data-card="actW"]').hidden, true);
  dom.window.close();
});

test('every card except the timeline and the events is half width on a wide screen', async () => {
  const dom = await load(POOL);
  const d = dom.window.document;
  const half = [...d.querySelectorAll('#dash > [data-card]')].filter((el) => el.hasAttribute('data-half')).map((el) => el.dataset.card);
  assert.deepEqual(half, ['days', 'hist', 'causes', 'awake', 'src', 'rhythm', 'actR', 'actW', 'rest', 'feed']);
  assert.match(HTML, /#dash>\.card\[data-half\]\{grid-column:auto\}/);
  assert.match(HTML, /@media \(max-width:820px\)\{[^@]*#dash>\.card\[data-half\]\{grid-column:1\/-1\}/);   // telefoon: volle breedte
  dom.window.close();
});

test('changing the language in settings also translates the card list', async () => {
  const dom = await load(POOL);
  const d = dom.window.document, w = dom.window;
  d.getElementById('openSettings').click();
  const sel = d.getElementById('lang');
  sel.value = 'de'; sel.dispatchEvent(new w.Event('change'));
  assert.equal(cardName(d.querySelector('#cardList li label')), 'Kennzahlen');
  assert.equal(d.getElementById('openSettings').getAttribute('aria-label'), 'Einstellungen');
  dom.window.close();
});

// ---------- pools en array als één baan ----------
const ARRAYDATA = served({ [today]: [
  `${ep(300)},disk,sdb,parity`, `${ep(300)},disk,sdc,disk1`, `${ep(300)},disk,sdf,disk2`,
  `${ep(300)},disk,sdd,trunk`, `${ep(300)},disk,sde,trunk2`,
  ...['sdb', 'sdc', 'sdf', 'sdd', 'sde'].map((dev) => `${ep(300)},state,${dev},standby`),
  `${ep(101)},who,container,plex,7,geopend,/mnt/disk1/Films/x.mkv`,
  `${ep(100)},spin,sdc,active`, `${ep(80)},spin,sdc,standby`,
  `${ep(50)},spin,sdf,active`, `${ep(49)},spin,sdc,active`, `${ep(30)},spin,sdf,standby`, `${ep(30)},spin,sdc,standby`,
] });
const laneNames = (d) => [...d.querySelectorAll('.lane-name')].map((el) => [el.firstChild.textContent, el.querySelector('small')?.textContent].filter(Boolean).join(' '));

test('a pool shows as one lane and one event row by default', async () => {
  const dom = await load(POOL);
  const d = dom.window.document;
  assert.deepEqual(laneNames(d), ['trunk sdd, sde']);
  assert.match(d.querySelector('.trace').getAttribute('aria-label'), /^trunk: 1 spin-ups/);   // één spin-up, niet twee
  const r = rows(d);
  assert.ok(r.some((x) => /^\S+ \S+ \S+, \S+ trunk Opgespind/.test(x)), r.join('\n'));
  assert.ok(r.every((x) => !/trunk2/.test(x)));
  assert.deepEqual([...d.querySelectorAll('#devFilter option')].map(text), ['Alle pools', 'trunk']);
  assert.equal(d.getElementById('groupDrives').checked, true);
  dom.window.close();
});

test('the array is one lane by default, and one switch shows every drive again', async () => {
  const dom = await load(ARRAYDATA);
  const d = dom.window.document, w = dom.window;
  assert.deepEqual(laneNames(d), ['Array 3 schijven', 'trunk sdd, sde']);
  assert.match(d.getElementById('sourceLine').textContent, /^5 schijven,/);   // de samenvatting telt schijven, geen banen
  assert.equal(text(d.querySelector('#devFilter option')), 'Alle schijven en pools');
  // disk1 alleen, later disk2 en disk1 bijna tegelijk: twee keer wakker
  assert.match(d.querySelector('.trace').getAttribute('aria-label'), /^Array: 2 spin-ups/);
  assert.match(text(d.getElementById('causes')), /^Films 1 Onbekend/);   // activiteit van disk1 telt voor de array
  const box = d.getElementById('groupDrives');
  box.checked = false; box.dispatchEvent(new w.Event('change'));
  assert.deepEqual(laneNames(d), ['parity sdb', 'disk1 sdc', 'disk2 sdf', 'trunk sdd', 'trunk2 sde']);
  assert.equal(text(d.querySelector('#devFilter option')), 'Alle schijven');
  assert.deepEqual(JSON.parse(w.localStorage.getItem('spindash.group')), { merge: false, compose: false });
  dom.window.close();
});

test('the array lane tooltip says how many drives were spinning', async () => {
  const dom = await load(ARRAYDATA);
  const d = dom.window.document, w = dom.window;
  const tr = d.querySelector('.trace');
  tr.focus();
  const seen = [];
  for (let n = 0; n < 6; n++){ tr.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true })); seen.push(d.getElementById('tipLive').textContent); }
  assert.ok(seen.some((x) => /^Array, draait \(1 van 3\)/.test(x)), seen.join('\n'));
  assert.ok(seen.some((x) => /^Array, draait \(2 van 3\)/.test(x)), seen.join('\n'));
  dom.window.close();
});

test('cards can be dragged into a new order by their handle', async () => {
  const dom = await load(POOL);
  const d = dom.window.document, w = dom.window;
  d.getElementById('openSettings').click();
  // jsdom kent geen layout: elke rij is 44px hoog, op de plek waar hij nu in de lijst staat
  for (const li of d.querySelectorAll('#cardList li')) {
    li.getBoundingClientRect = () => { const i = [...li.parentNode.children].indexOf(li); return { top: i * 44, height: 44, bottom: i * 44 + 44 }; };
  }
  const ev = (type, el, y) => el.dispatchEvent(new w.MouseEvent(type, { bubbles: true, cancelable: true, clientY: y }));
  const grip = d.querySelector('#cardList li[data-card="events"] .grip');
  ev('pointerdown', grip, 594);
  assert.ok(grip.closest('li').classList.contains('dragging'));
  ev('pointermove', grip, 150);   // nog geen opslag tijdens het slepen
  ev('pointermove', grip, 50);    // tussen de eerste en tweede rij
  assert.equal(w.localStorage.getItem('spindash.cards'), null);
  ev('pointerup', d, 50);
  assert.deepEqual(cardOrder(d), ['kpi', 'events', 'lanes', 'srcLanes', 'days', 'hist', 'causes', 'awake', 'src', 'rhythm', 'actR', 'actW', 'rest', 'feed']);
  assert.deepEqual([...d.querySelectorAll('#cardList li')].map((li) => li.dataset.card), ['kpi', 'events', 'lanes', 'srcLanes', 'days', 'hist', 'causes', 'awake', 'src', 'rhythm', 'actR', 'actW', 'rest', 'feed']);
  assert.ok(!d.querySelector('#cardList .dragging'));
  assert.equal(d.getElementById('cardLive').textContent, 'Gebeurtenissen: plaats 2 van 14');
  assert.deepEqual(JSON.parse(w.localStorage.getItem('spindash.cards')).map((c) => c.id), ['kpi', 'events', 'lanes', 'srcLanes', 'days', 'hist', 'causes', 'awake', 'src', 'rhythm', 'actR', 'actW', 'rest', 'feed']);
  // de handgreep zit buiten het label: slepen zet het kaartje niet aan of uit
  assert.ok(!d.querySelector('#cardList li label .grip'));
  dom.window.close();
});

test('the timeline card has a heading like the other cards', async () => {
  const dom = await load(POOL);
  const d = dom.window.document;
  const card = d.querySelector('#dash > [data-card="lanes"]');
  assert.equal(text(card.querySelector('h2')), 'Tijdlijn per schijf');
  assert.equal(d.getElementById(card.getAttribute('aria-labelledby')), card.querySelector('h2'));
  dom.window.close();
});

// ---------- verborgen paden ----------
const setHide = (d, w, value) => { const ta = d.getElementById('hidePaths'); ta.value = value; ta.dispatchEvent(new w.Event('change')); };
const feedText = (d) => [...d.querySelectorAll('#feed li')].map(text).join('\n');

test('hidden paths with a wildcard disappear from the feed, rankings and causes', async () => {
  const dom = await load(POOL);
  const d = dom.window.document, w = dom.window;
  assert.match(feedText(d), /a\.mkv/);
  assert.match(text(d.getElementById('causes')), /Media\/series/);
  setHide(d, w, '/mnt/user/Media/series/*\n');            // share-vorm, ook al staat het als /mnt/trunk/... in de data
  assert.doesNotMatch(feedText(d), /series/);
  assert.match(feedText(d), /SMB-share/);                  // de rest blijft staan
  assert.doesNotMatch(text(d.getElementById('causes')), /Media\/series/);
  assert.doesNotMatch(rows(d).join('\n'), /a\.mkv/);
  assert.match(d.getElementById('hideCount').textContent, /^2 van \d+ bestandsgebeurtenissen verborgen$/);
  assert.deepEqual(JSON.parse(w.localStorage.getItem('spindash.hide')), ['/mnt/user/Media/series/*']);
  setHide(d, w, '');                                       // leeg: alles terug en niets meer bewaard
  assert.match(feedText(d), /a\.mkv/);
  assert.equal(w.localStorage.getItem('spindash.hide'), null);
  assert.equal(d.getElementById('hideCount').textContent, '');
  dom.window.close();
});

test('a pattern without a leading slash matches inside the share, and saved patterns apply on load', async () => {
  const dom = await load(POOL, 'http://dash/?lang=nl', ['en-US'], null, (w) => w.localStorage.setItem('spindash.hide', JSON.stringify(['*.mkv'])));
  const d = dom.window.document;
  assert.equal(d.getElementById('hidePaths').value, '*.mkv');
  assert.doesNotMatch(feedText(d), /\.mkv/);
  assert.match(feedText(d), /SMB-share/);
  dom.window.close();
});

test('a hidden path does not show up when it streams in live', async () => {
  const dom = await loadWithStream(RW);
  const d = dom.window.document, w = dom.window;
  setHide(d, w, '/mnt/user/appdata/radarr/*');
  const now = Math.floor(Date.now() / 1000);
  w.__push(`${now},who,container,radarr,99,geschreven,/mnt/trunk/appdata/radarr/radarr.db`);
  w.__push(`${now},who,container,sonarr,98,geschreven,/mnt/trunk/appdata/sonarr/sonarr.db`);
  await new Promise((r) => setTimeout(r, 400));
  assert.doesNotMatch(feedText(d), /radarr\.db/);
  assert.match(feedText(d), /sonarr\.db/);
  dom.window.close();
});

// ---------- verborgen paden op de server ----------
const withHide = (list) => (w) => w.localStorage.setItem('spindash.hide', JSON.stringify(list));
const hideSettled = () => new Promise((r) => setTimeout(r, 50));

test('hidden paths come from the server and win over this browser', async () => {
  const files = { ...POOL, 'settings.json': JSON.stringify({ hide: ['*.mkv'] }) };
  const dom = await load(files, 'http://dash/?lang=nl', ['en-US'], null, withHide(['/mnt/user/other/*']));
  const d = dom.window.document, w = dom.window;
  assert.equal(d.getElementById('hidePaths').value, '*.mkv');
  assert.doesNotMatch(feedText(d), /\.mkv/);
  assert.deepEqual(JSON.parse(w.localStorage.getItem('spindash.hide')), ['*.mkv']);   // kopie voor als de server weg is
  assert.deepEqual(w.__posts, []);                                                      // niets overgezet
  dom.window.close();
});

test('changing hidden paths saves them on the server as JSON', async () => {
  const dom = await load({ ...POOL, 'settings.json': JSON.stringify({ hide: [] }) });
  const d = dom.window.document, w = dom.window;
  setHide(d, w, ' /mnt/user/Media/series/*\n\n');
  await hideSettled();
  assert.deepEqual(w.__posts, [{ url: 'data/settings.json', type: 'application/json', body: { hide: ['/mnt/user/Media/series/*'] } }]);
  setHide(d, w, '/mnt/user/Media/series/*');      // zelfde lijst: geen nieuw verzoek
  setHide(d, w, '');
  await hideSettled();
  assert.deepEqual(w.__posts.map((p) => p.body), [{ hide: ['/mnt/user/Media/series/*'] }, { hide: [] }]);
  assert.equal(d.getElementById('hideSaved').textContent, '');
  dom.window.close();
});

test('a browser list is moved to the server once when the server has none', async () => {
  const dom = await load({ ...POOL, 'settings.json': '{}' }, 'http://dash/?lang=nl', ['en-US'], null, withHide(['*.mkv']));
  const d = dom.window.document, w = dom.window;
  assert.deepEqual(w.__posts.map((p) => p.body), [{ hide: ['*.mkv'] }]);
  assert.equal(d.getElementById('hidePaths').value, '*.mkv');
  assert.doesNotMatch(feedText(d), /\.mkv/);
  dom.window.close();
  // zonder eigen lijst valt er niets over te zetten
  const empty = await load({ ...POOL, 'settings.json': '{}' });
  assert.deepEqual(empty.window.__posts, []);
  empty.window.close();
});

test('without the server, hidden paths stay in this browser', async () => {
  const dom = await load(POOL, 'http://dash/?lang=nl', ['en-US'], null, withHide(['*.mkv']));   // settings.json: 404
  const d = dom.window.document, w = dom.window;
  assert.equal(d.getElementById('hidePaths').value, '*.mkv');
  assert.doesNotMatch(feedText(d), /\.mkv/);
  setHide(d, w, '*.srt');
  await hideSettled();
  assert.deepEqual(w.__posts, []);
  assert.deepEqual(JSON.parse(w.localStorage.getItem('spindash.hide')), ['*.srt']);
  dom.window.close();
});

test('a failed save says the list is only kept in this browser', async () => {
  const dom = await load({ ...POOL, 'settings.json': JSON.stringify({ hide: [] }), POST: false });
  const d = dom.window.document, w = dom.window;
  setHide(d, w, '*.mkv');
  await hideSettled();
  assert.equal(w.__posts.length, 1);
  assert.match(d.getElementById('hideSaved').textContent, /alleen in deze browser/);
  assert.deepEqual(JSON.parse(w.localStorage.getItem('spindash.hide')), ['*.mkv']);
  dom.window.close();
});

test('demo mode never asks the server for settings', async () => {
  const dom = await load({ 'settings.json': JSON.stringify({ hide: ['*'] }) }, 'http://dash/?demo&lang=nl', ['en-US'], null, withHide(['*.mkv']));
  const w = dom.window;
  assert.ok(!w.__fetched.some((u) => u.includes('settings')));
  assert.equal(w.document.getElementById('hidePaths').value, '*.mkv');
  setHide(w.document, w, '*.srt');
  await hideSettled();
  assert.deepEqual(w.__posts, []);
  dom.window.close();
});

// ---------- kerncijfers en spin-ups per dag ----------
// Vorige 24 uur: 3 spin-ups (3,5 uur draaien); laatste 24 uur: 1 spin-up (1 uur).
const HISTORY = [
  `${ep(3000)},disk,sdd,trunk`, `${ep(3000)},state,sdd,standby`,
  `${ep(2000)},spin,sdd,active`, `${ep(1990)},spin,sdd,standby`,
  `${ep(1800)},spin,sdd,active`, `${ep(1700)},spin,sdd,standby`,
  `${ep(1600)},spin,sdd,active`, `${ep(1500)},spin,sdd,standby`,
  `${ep(1000)},spin,sdd,active`, `${ep(940)},spin,sdd,standby`,
  `${ep(900)},who,container,plex,4,geopend,/mnt/trunk/Media/series/c.mkv`,
];
const byDay = (lines) => {
  const out = {};
  for (const l of lines) (out[dayName(+l.split(',')[0] * 1000)] ||= []).push(l);
  return served(Object.fromEntries(Object.entries(out).sort()));   // days.json is oplopend, zoals de server het levert
};
const kpi = (d, key) => d.querySelector(`[data-kpi="${key}"]`);

test('key figures show the period and compare with the previous one', async () => {
  const spinsOnly = HISTORY.filter((l) => !/,who,/.test(l)).join('\n') + '\n';
  const dom = await load({ ...byDay(HISTORY), 'spins.csv': spinsOnly });
  const d = dom.window.document;
  assert.ok(dom.window.__fetched.includes('data/spins.csv'));
  assert.equal(text(kpi(d, 'kpiUps').querySelector('.k-val')), '1');
  assert.match(text(kpi(d, 'kpiUps').querySelector('.k-cmp')), /^▼ −67% \(beter\) · Vorige dag: 3$/);
  assert.ok(kpi(d, 'kpiUps').querySelector('.k-cmp').classList.contains('better'));
  assert.equal(text(kpi(d, 'kpiHours').querySelector('.k-val')), '1 u');
  assert.match(text(kpi(d, 'kpiHours').querySelector('.k-cmp')), /Vorige dag: 3,5 u$/);
  assert.equal(text(kpi(d, 'kpiRest').querySelector('.k-val')), '96%');
  assert.match(text(kpi(d, 'kpiRest').querySelector('.k-cmp')), /^▲ \+10 procentpunt \(beter\) · Vorige dag: 85%$/);
  assert.equal(text(kpi(d, 'kpiLongest').querySelector('.k-val')), '15 u 40 min');
  assert.equal(d.getElementById('kpiNote').textContent, '');
  dom.window.close();
});

test('without spins.csv (older server) the key figures use the loaded days', async () => {
  const dom = await load(byDay(HISTORY));
  const d = dom.window.document;
  assert.equal(text(kpi(d, 'kpiUps').querySelector('.k-val')), '1');
  assert.match(text(kpi(d, 'kpiUps').querySelector('.k-cmp')), /Vorige dag: 3$/);
  dom.window.close();
});

test('key figures say when there is no previous period to compare with', async () => {
  const dom = await load(POOL);
  const d = dom.window.document;
  assert.equal(d.querySelectorAll('.k-cmp').length, 0);
  assert.equal(d.getElementById('kpiNote').textContent, 'Nog te weinig geschiedenis om met de vorige periode te vergelijken.');
  d.querySelector('[data-range="all"]').click();          // "alles" heeft geen vorige periode, en dat is geen gebrek
  await new Promise((r) => setTimeout(r, 100));
  assert.equal(d.getElementById('kpiNote').textContent, '');
  dom.window.close();
});

test('spin-ups per day shows every day with the selected period highlighted', async () => {
  const spinsOnly = HISTORY.filter((l) => !/,who,/.test(l)).join('\n') + '\n';
  const dom = await load({ ...byDay(HISTORY), 'spins.csv': spinsOnly });
  const d = dom.window.document;
  const bars = [...d.querySelectorAll('#daysHist > div')];
  const first = new Date((now / 1000 - 3000 * 60) * 1000); first.setHours(0, 0, 0, 0);
  const expect = Math.round((new Date(today + 'T00:00').getTime() - first.getTime()) / 864e5) + 1;
  assert.equal(bars.length, expect);
  const counts = bars.map((b) => +b.getAttribute('aria-label').match(/: (\d+) spin-ups$/)[1]);
  assert.equal(counts.reduce((x, y) => x + y, 0), 4);
  assert.ok(bars[bars.length - 1].classList.contains('in'));   // vandaag valt in de laatste 24 uur
  assert.ok(!bars[0].classList.contains('in'));
  assert.match(d.getElementById('daysNote').textContent, new RegExp(`^Laatste ${expect} dagen, gemiddeld `));
  dom.window.close();
});

// ---------- review: kleine fixes, uurkaartje, per bron, draaiduur en rust ----------
test('the previous-hour button reads "Vorig uur" in Dutch', async () => {
  const dom = await load(POOL);
  const d = dom.window.document;
  d.querySelector('[data-range="h"]').click();
  await new Promise((r) => setTimeout(r, 100));
  assert.equal(d.getElementById('prev').getAttribute('aria-label'), 'Vorig uur');
  dom.window.close();
});

test('the reading and writing cards label processes and files', async () => {
  const dom = await load(RW);
  const d = dom.window.document;
  assert.deepEqual([...d.querySelectorAll('#actWrites h4')].map(text), ['Processen', 'Bestanden']);
  dom.window.close();
});

test('the feed merges repeats, names an unknown source and says it ignores the period', async () => {
  const files = served({ [today]: [...RW[`days/${today}.csv`].trim().split('\n'),
    `${ep(50)},activity,trunk,MODIFY,/mnt/trunk/appdata/x.log`,
    `${ep(40)},who,container,plex,10,geschreven,/mnt/trunk2/appdata/plex/Logs/server.log`] });   // zelfde share-pad, andere schijf
  const dom = await load(files);
  const d = dom.window.document;
  const items = [...d.querySelectorAll('#feed li')].map(text);
  const plex = items.filter((x) => /server\.log/.test(x));
  assert.equal(plex.length, 1, items.join('\n'));
  assert.match(plex[0], /plex ×3 /);
  assert.match(items.find((x) => /x\.log/.test(x)), /Onbekend/);
  assert.equal(d.getElementById('feedNote').textContent, '');
  d.querySelector('[data-range="h"]').click();          // een uur terug kan: daar is data
  await new Promise((r) => setTimeout(r, 100));
  d.getElementById('prev').click();
  await new Promise((r) => setTimeout(r, 100));
  assert.equal(d.getElementById('feedNote').textContent, 'Altijd de nieuwste gebeurtenissen, welke periode je ook kiest.');
  dom.window.close();
});

test('spin-ups by hour cover at least the last 7 days', async () => {
  const spinsOnly = HISTORY.filter((l) => !/,who,/.test(l)).join('\n') + '\n';
  const dom = await load({ ...byDay(HISTORY), 'spins.csv': spinsOnly });
  const d = dom.window.document;
  const n = [...d.querySelectorAll('#hist div')].reduce((x, b) => x + +b.getAttribute('aria-label').match(/: (\d+) spin-ups$/)[1], 0);
  assert.equal(n, 4);                                  // ook de 3 van gisteren, niet alleen die van de laatste 24 uur
  assert.match(d.getElementById('histNote').textContent, /^Spin-ups .+ – .+\. Drukste uur: /);
  dom.window.close();
});

test('key figures count a pool spin-up once but spinning hours per drive', async () => {
  const dom = await load(POOL);
  const d = dom.window.document;
  assert.equal(text(kpi(d, 'kpiUps').querySelector('.k-val')), '1');
  assert.equal(text(kpi(d, 'kpiHours').querySelector('.k-val')), '2 u');   // trunk en trunk2 elk ongeveer een uur
  dom.window.close();
});

test('spin duration and rest shows the median run, longest rest and quick wake-ups per lane', async () => {
  const QUICK = served({ [today]: [
    `${ep(300)},disk,sdd,trunk`, `${ep(300)},state,sdd,standby`,
    `${ep(200)},spin,sdd,active`, `${ep(190)},spin,sdd,standby`,
    `${ep(175)},spin,sdd,active`, `${ep(100)},spin,sdd,standby`,      // 15 min na standby weer wakker
  ] });
  const dom = await load(QUICK);
  const d = dom.window.document;
  const cells = [...d.querySelectorAll('#rest tbody tr[data-dev="sdd"] td')].map(text);
  assert.equal(cells[0], 'trunk');
  assert.equal(cells[1], '2');
  assert.equal(cells[2], '43 min');                    // mediaan van 10 en 75 minuten
  assert.equal(cells[4], '1');
  assert.ok(d.querySelector('#rest tbody td.quick'));
  dom.window.close();
});

test('demo data is long enough to compare a week with the week before', async () => {
  const dom = await load({}, 'http://dash/?demo&lang=nl');
  const d = dom.window.document;
  d.querySelector('[data-range="7"]').click();
  await new Promise((r) => setTimeout(r, 100));
  assert.equal(d.getElementById('kpiNote').textContent, '');
  assert.match(text(kpi(d, 'kpiUps').querySelector('.k-cmp')), /Vorige week: \d+$/);
  dom.window.close();
});

// ---------- containers: houdt wakker, ritme, tijdlijn per bron, profiel ----------
const AWAKE = served({ [today]: [
  `${ep(300)},disk,sdd,trunk`, `${ep(300)},state,sdd,standby`,
  `${ep(121)},who,container,bazarr,2,geopend,/mnt/trunk/Media/series/a.mkv`,
  `${ep(120)},spin,sdd,active`,
  `${ep(110)},who,container,plex,10,geschreven,/mnt/trunk/appdata/plex/Logs/server.log`,
  `${ep(100)},who,container,sonarr,11,geschreven,/mnt/trunk/appdata/sonarr/sonarr.db`,
  `${ep(80)},who,container,plex,10,geschreven,/mnt/trunk/appdata/plex/Logs/server.log`,
  `${ep(60)},spin,sdd,standby`,
] });

test('"what keeps them awake" adds up the time each access postponed the spin-down', async () => {
  const dom = await load(AWAKE);
  const rows = [...dom.window.document.querySelectorAll('#awake .src')].map(text);
  // plex: 10 min na de spin-up + 20 min na sonarr; sonarr: 10 min na plex; plex was de laatste vóór standby
  assert.deepEqual(rows, [
    'container: plex 30 min 1× de laatste toegang vóór standby',
    'container: sonarr 10 min',
  ]);
  dom.window.close();
});

test('the rhythm card recognises a fixed interval and a daily time', async () => {
  const lines = [];
  for (let dd = 5; dd >= 0; dd--) {                      // elke dag begint met disk en state, zoals op de server
    const t0 = new Date(now - dd * 864e5); t0.setHours(0, 0, 30, 0);
    lines.push(`${Math.floor(t0 / 1000)},disk,sdd,trunk`, `${Math.floor(t0 / 1000)},state,sdd,active`);
  }
  for (let i = 0; i < 9; i++) lines.push(`${ep(200 - i * 15)},who,container,sonarr,11,geschreven,/mnt/trunk/appdata/sonarr/f${i}.db`);
  for (let dd = 1; dd <= 4; dd++) {                       // vier dagen rond 03:00
    const at = new Date(now - dd * 864e5); at.setHours(3, 5, dd, 0);
    lines.push(`${Math.floor(at / 1000)},who,unraid,mover,7,verplaatst,/mnt/trunk/Media/m${dd}.mkv`);
  }
  const dom = await load(byDay(lines));
  const d = dom.window.document;
  d.querySelector('[data-range="7"]').click();
  for (let i = 0; i < 40 && !d.querySelector('#rhythm .rhy[data-k="unraid|mover"]'); i++) await new Promise((r) => setTimeout(r, 50));
  const label = (k) => text(d.querySelector(`#rhythm .rhy[data-k="${k}"] .rhy-label`));
  assert.equal(label('container|sonarr'), 'elke 15 min');
  assert.equal(label('unraid|mover'), 'dagelijks rond 03:05');
  assert.equal(d.querySelectorAll('#rhythm .rhy[data-k="container|sonarr"] .strip i').length, 24);
  dom.window.close();
});

test('the timeline per source draws every access as a tick under the spinning band', async () => {
  const dom = await load(AWAKE);
  const d = dom.window.document;
  const rowsEl = [...d.querySelectorAll('#srcLaneList .srow')];
  assert.deepEqual(rowsEl.map((r) => r.dataset.k), ['container|plex', 'container|bazarr', 'container|sonarr']);
  const plex = rowsEl[0];
  assert.equal((plex.querySelector('.tw').getAttribute('d').match(/M/g) || []).length, 2);
  assert.equal(plex.querySelector('.tr').getAttribute('d'), '');
  assert.ok(plex.querySelector('.band').getAttribute('d').length > 0);
  assert.equal(d.getElementById('srcAxis').innerHTML, d.getElementById('axis').innerHTML);
  dom.window.close();
});

test('clicking a source opens its profile with figures, heatmap, folders and files', async () => {
  const dom = await load(AWAKE);
  const d = dom.window.document;
  const btn = d.querySelector('#sources .srcbtn[data-src="container|plex"]');
  btn.click();
  assert.ok(d.getElementById('profile').hasAttribute('open'));
  assert.equal(text(d.getElementById('profileTitle')), 'plex');
  const kpis = [...d.querySelectorAll('#profileBody .kpi')].map((k) => `${text(k.querySelector('.k-label'))} ${text(k.querySelector('.k-val'))}`);
  assert.deepEqual(kpis, ['Veroorzaakte spin-ups 0', 'Wakker gehouden 30 min', 'Gelezen 0', 'Geschreven 2']);
  const cells = d.querySelectorAll('#profileBody .heat i');
  assert.equal(cells.length % 24, 0);
  assert.equal(d.querySelectorAll('#profileBody .heat i.on').length >= 1, true);
  assert.match(text(d.getElementById('profileBody')), /appdata\/plex 2/);
  assert.match(text(d.getElementById('profileBody')), /\/appdata\/plex\/Logs\/server\.log 2/);
  d.getElementById('profileDone').click();
  assert.ok(!d.getElementById('profile').hasAttribute('open'));
  assert.equal(d.activeElement, btn);                      // focus terug op de naam
  dom.window.close();
});

test('every card title follows one pattern: a subject, optionally "per" a dimension', async () => {
  for (const [lang, per] of [['en', ' per '], ['nl', ' per '], ['fr', ' par '], ['de', ' pro '], ['es', ' por ']]) {
    const dom = await load(POOL, `http://dash/?lang=${lang}`);
    const titles = [...dom.window.document.querySelectorAll('#dash > [data-card] h2')].map(text);
    assert.equal(titles.length, 14);
    // geen vragen, geen dubbele punten, geen titel die met het voorzetsel begint
    assert.ok(titles.every((x) => !/[?:]/.test(x) && !x.toLowerCase().startsWith(per.trim())), `${lang}: ${titles.join(' | ')}`);
    assert.ok(titles.filter((x) => x.includes(per)).length >= 10, `${lang}: ${titles.join(' | ')}`);
    dom.window.close();
  }
});

// ---------- docker: proces in de container, start/stop, opslag ----------
const DOCK = served({ [today]: [
  `${ep(300)},disk,sdd,trunk`, `${ep(300)},state,sdd,standby`,
  `${ep(130)},ctr,radarr,die,0`, `${ep(129.7)},ctr,radarr,start`,          // herstart, 9 min vóór de spin-up
  `${ep(121)},proc,7,Plex Media Scan`,
  `${ep(121)},who,container,plex,7,geopend,/mnt/trunk/Media/films/b.mkv`,
  `${ep(120)},spin,sdd,active`,
  `${ep(119)},ctr,plex,start`,                                           // vlak vóór de tweede spin-up
  `${ep(110)},who,container,sonarr,8,geschreven,/mnt/trunk/appdata/sonarr/sonarr.db`,
  `${ep(100)},spin,sdd,standby`, `${ep(96)},spin,sdd,active`,
  `${ep(95)},who,container,plex,9,geopend,/mnt/trunk/Media/films/c.mkv`,  // geen proc-regel: alleen de naam
  `${ep(60)},spin,sdd,standby`,
] });

test('the process inside a container shows in the feed and the profile', async () => {
  const dom = await load(DOCK);
  const d = dom.window.document;
  const items = [...d.querySelectorAll('#feed li')].map(text);
  assert.match(items.find((x) => /b\.mkv/.test(x)), /plex › Plex Media Scan/);
  assert.doesNotMatch(items.find((x) => /c\.mkv/.test(x)), /›/);           // oude regels zonder proc: gewoon de container
  d.querySelector('#sources .srcbtn[data-src="container|plex"]').click();
  assert.match(text(d.getElementById('profileBody')), /Processen\s*Plex Media Scan 1/);
  dom.window.close();
});

test('a streamed proc line names the process of the who line after it', async () => {
  const dom = await loadWithStream(DOCK);
  const w = dom.window, n = Math.floor(Date.now() / 1000);
  w.__push(`${n},proc,77,Plex Transcoder`);
  w.__push(`${n},who,container,plex,77,geopend,/mnt/trunk/Media/films/z.mkv`);
  await new Promise((r) => setTimeout(r, 400));
  assert.match([...w.document.querySelectorAll('#feed li')].map(text).find((x) => /z\.mkv/.test(x)), /plex › Plex Transcoder/);
  dom.window.close();
});

test('container start and stop show on the source timeline, in the profile and before a spin-up', async () => {
  const dom = await load(DOCK);
  const d = dom.window.document;
  const plex = d.querySelector('#srcLaneList .srow[data-k="container|plex"]');
  assert.equal((plex.querySelector('.cs').getAttribute('d').match(/M/g) || []).length, 1);
  assert.equal(plex.querySelector('.cd').getAttribute('d'), '');
  // radarr herstartte 9 min vóór de spin-up: buiten de 5 minuten, dus niet genoemd
  const ups = rows(d).filter((x) => /Opgespind/.test(x));
  assert.ok(ups.every((x) => !/radarr/.test(x)), ups.join('\n'));
  d.querySelector('#sources .srcbtn[data-src="container|plex"]').click();
  assert.match(text(d.getElementById('profileBody')), /Start en stop\s*Start \S+ \d+ /);
  dom.window.close();
});

test('a container restart just before a spin-up is named in the events table', async () => {
  const NEAR = served({ [today]: [
    `${ep(300)},disk,sdd,trunk`, `${ep(300)},state,sdd,standby`,
    `${ep(250)},who,container,plex,7,geopend,/mnt/trunk/Media/x.mkv`,     // wel activiteit, maar niet bij de spin-up
    `${ep(123)},ctr,radarr,die,0`, `${ep(122.8)},ctr,radarr,start`,
    `${ep(120)},spin,sdd,active`, `${ep(60)},spin,sdd,standby`,
  ] });
  const dom = await load(NEAR);
  const up = rows(dom.window.document).find((x) => /Opgespind/.test(x));
  assert.match(up, /Geen bestandsactiviteit gezien Herstart van radarr 3 min eerder/);
  dom.window.close();
});

test('settings list the paths each container maps and where they live', async () => {
  const containers = { docker: true, containers: [
    { name: 'plex', state: 'running', mounts: [
      { source: '/mnt/user/appdata/plex', target: '/config', rw: true, where: { kind: 'share', share: 'appdata', storage: 'pool>array', pool: 'cache', hdd: true } },
      { source: '/mnt/user/Media', target: '/media', rw: false, where: { kind: 'share', share: 'Media', storage: 'unknown', pool: 'cache', hdd: null } }] },
    { name: 'radarr', state: 'running', mounts: [
      { source: '/mnt/disk1/appdata/radarr', target: '/config', rw: true, where: { kind: 'disk', name: 'disk1', hdd: true } }] },
    { name: 'nginx', state: 'exited', mounts: [
      { source: '/mnt/cache/appdata/nginx', target: '/config', rw: true, where: { kind: 'pool', name: 'cache', hdd: false } },
      { source: '/mnt/cache/appdata/nginx', target: '/data', rw: true, where: { kind: 'pool', name: 'cache', hdd: false } }] },   // zelfde map twee keer
    { name: 'empty', state: 'running', mounts: [] },
  ] };
  const dom = await load({ ...DOCK, 'containers.json': JSON.stringify(containers) });
  const d = dom.window.document;
  assert.equal(d.querySelector('[data-card="storage"]'), null);            // geen kaartje meer
  d.getElementById('openSettings').click();
  const li = (n) => text(d.querySelector(`#mountList li[data-ctr="${n}"]`));
  assert.deepEqual([...d.querySelectorAll('#mountList li')].map((x) => x.dataset.ctr), ['plex', 'radarr', 'nginx']);
  // het pad met de pool of array waar het staat; met de mover beide plekken; onbekend: '?'
  assert.deepEqual([...d.querySelectorAll('#mountList li[data-ctr="plex"] .map')].map(text), ['/mnt/user/appdata/plex cache → Array', '/mnt/user/Media ?']);
  assert.equal(li('radarr'), 'radarr /mnt/disk1/appdata/radarr Array');
  assert.equal(li('nginx'), 'nginx (gestopt) /mnt/cache/appdata/nginx cache');   // dubbele map maar één keer
  // elke plek een eigen, vaste kleur: dezelfde naam overal dezelfde kleur
  const colors = {};
  for (const tg of d.querySelectorAll('#mountList .loc:not(.unknown)')) {
    const c = tg.style.getPropertyValue('--c');
    assert.ok(!colors[tg.textContent] || colors[tg.textContent] === c, tg.textContent);
    colors[tg.textContent] = c;
  }
  assert.deepEqual(Object.keys(colors).sort(), ['Array', 'cache']);
  assert.notEqual(colors.Array, colors.cache);
  assert.match(d.querySelector('#mountList li[data-ctr="plex"] .loc').title, /cache · kan op een harde schijf staan/);
  // onder de kaartjes
  const order = [...d.querySelectorAll('#settings h3')].map((h) => h.dataset.i18n);
  assert.deepEqual(order.slice(-2), ['cardsTitle', 'mountsTitle']);
  dom.window.close();
});

test('settings explain a missing Docker socket or a failed request', async () => {
  let dom = await load({ ...DOCK, 'containers.json': JSON.stringify({ docker: false, containers: [] }) });
  dom.window.document.getElementById('openSettings').click();
  assert.match(text(dom.window.document.getElementById('mountList')), /Geen Docker-socket/);
  dom.window.close();
  dom = await load(DOCK);                                                  // containers.json: 404
  dom.window.document.getElementById('openSettings').click();
  assert.equal(text(dom.window.document.getElementById('mountList')), 'De containers konden niet gelezen worden.');
  dom.window.close();
});

// ---------- kaartjes: maat en positie in de instellingen ----------
const posOf = (d) => Object.fromEntries([...d.querySelectorAll('#cardList li')].map((li) => [li.dataset.card, li.dataset.pos]));

test('the card list shows which cards are small or large and which sit side by side', async () => {
  const dom = await load(POOL);
  const d = dom.window.document;
  d.getElementById('openSettings').click();
  assert.deepEqual(posOf(d), { kpi: 'full', lanes: 'full', srcLanes: 'full', days: 'left', hist: 'right', causes: 'left', awake: 'right',
    src: 'left', rhythm: 'right', actR: 'left', actW: 'right', rest: 'left', feed: 'right', events: 'full' });
  const li = (id) => d.querySelector(`#cardList li[data-card="${id}"]`);
  assert.ok(li('days').classList.contains('pair-l') && li('hist').classList.contains('pair-r'));
  assert.equal(text(li('days').querySelector('.size')), 'Klein');
  assert.equal(text(li('kpi').querySelector('.size')), 'Groot');
  assert.equal(li('kpi').querySelector('.lay').disabled, true);               // groot: niets om mee te wisselen
  assert.equal(li('days').querySelector('.lay').getAttribute('aria-label'), 'Spin-ups per dag: naar rechts');
  assert.equal(li('hist').querySelector('label .sr').textContent, ', rechterhelft');
  dom.window.close();
});

test('a card can be made small or large, and the choice is remembered and reset', async () => {
  const dom = await load(POOL);
  const d = dom.window.document, w = dom.window;
  d.getElementById('openSettings').click();
  d.querySelector('#cardList li[data-card="kpi"] .size').click();
  assert.ok(d.querySelector('#dash > [data-card="kpi"]').hasAttribute('data-half'));
  assert.equal(d.getElementById('cardLive').textContent, 'Kerncijfers: Klein');
  assert.equal(posOf(d).kpi, 'alone');                                       // klein, maar de tijdlijn erna is groot
  assert.equal(d.activeElement, d.querySelector('#cardList li[data-card="kpi"] .size'));
  d.querySelector('#cardList li[data-card="feed"] .size').click();            // live activiteit groot
  assert.ok(!d.querySelector('#dash > [data-card="feed"]').hasAttribute('data-half'));
  assert.equal(posOf(d).rest, 'alone');
  const saved = JSON.parse(w.localStorage.getItem('spindash.cards'));
  assert.equal(saved.find((c) => c.id === 'kpi').size, 'half');
  dom.window.close();

  const again = await load(POOL, 'http://dash/?lang=nl', ['en-US'], null, (w2) => w2.localStorage.setItem('spindash.cards', JSON.stringify(saved)));
  const d2 = again.window.document;
  assert.ok(d2.querySelector('#dash > [data-card="kpi"]').hasAttribute('data-half'));
  assert.ok(!d2.querySelector('#dash > [data-card="feed"]').hasAttribute('data-half'));
  d2.getElementById('openSettings').click();
  d2.getElementById('resetCards').click();
  assert.ok(!d2.querySelector('#dash > [data-card="kpi"]').hasAttribute('data-half'));
  assert.ok(d2.querySelector('#dash > [data-card="feed"]').hasAttribute('data-half'));
  again.window.close();
});

test('two cards side by side swap places with the layout button, also across a hidden card', async () => {
  const dom = await load(POOL);
  const d = dom.window.document, w = dom.window;
  d.getElementById('openSettings').click();
  d.querySelector('#cardList li[data-card="days"] .lay').click();
  assert.deepEqual(cardOrder(d).slice(3, 5), ['hist', 'days']);
  assert.equal(posOf(d).days, 'right');
  assert.equal(d.getElementById('cardLive').textContent, 'Spin-ups per dag: nu rechts');
  assert.equal(d.activeElement, d.querySelector('#cardList li[data-card="days"] .lay'));
  // verberg "wakker gehouden": oorzaken staat dan naast per bron, en die twee wisselen
  const box = d.querySelector('#cardList li[data-card="awake"] input');
  box.checked = false; box.dispatchEvent(new w.Event('change', { bubbles: true }));
  d.querySelector('#cardList li[data-card="src"] .lay').click();
  const ids = [...d.querySelectorAll('#cardList li')].map((li) => li.dataset.card);
  assert.deepEqual(ids.slice(5, 8), ['src', 'awake', 'causes']);
  assert.equal(posOf(d).src, 'left');
  dom.window.close();
});

test('an old saved layout without sizes keeps the default sizes', async () => {
  const stored = JSON.stringify([{ id: 'events' }, { id: 'lanes', hidden: false }, { id: 'feed', size: 'reus' }]);
  const dom = await load(POOL, 'http://dash/?lang=nl', ['en-US'], null, (w) => w.localStorage.setItem('spindash.cards', stored));
  const d = dom.window.document;
  assert.ok(!d.querySelector('#dash > [data-card="events"]').hasAttribute('data-half'));
  assert.ok(d.querySelector('#dash > [data-card="feed"]').hasAttribute('data-half'));    // onbekende maat: standaard
  assert.ok(d.querySelector('#dash > [data-card="days"]').hasAttribute('data-half'));
  dom.window.close();
});

test('on a phone the period buttons fill the width instead of scrolling with a hidden scrollbar', () => {
  assert.doesNotMatch(HTML, /#ranges\{[^}]*overflow-x:auto/);
  assert.doesNotMatch(HTML, /#ranges::-webkit-scrollbar/);
  assert.match(HTML, /@media \(max-width:560px\)\{\s*#ranges\{display:flex;width:100%\}\s*#ranges button\{flex:1 1 auto;white-space:normal/);
});

test('the English UI calls the hardware "drive" everywhere, never "disk"', () => {
  const en = HTML.slice(HTML.indexOf('\n  en: {'), HTML.indexOf('\n  nl: {'));
  const texts = [...en.matchAll(/(\w+):'((?:[^'\\]|\\.)*)'/g)].filter(([, k]) => k !== 'viaDisk').map(([, k, v]) => `${k}: ${v}`);
  assert.deepEqual(texts.filter((x) => /\bdisks?\b/i.test(x.split(': ')[1])), []);
});

test('full file paths can be reached with the keyboard and keep focus when the feed redraws', async () => {
  const dom = await loadWithStream(RW);
  const d = dom.window.document, w = dom.window;
  const fp = d.querySelector('#feed .haspath');
  assert.equal(fp.getAttribute('tabindex'), '0');
  assert.match(fp.getAttribute('aria-label'), /^\/mnt\//);                 // schermlezer hoort het hele pad
  assert.equal(d.querySelector('#actWrites .haspath').getAttribute('tabindex'), '0');
  fp.focus();
  assert.equal(d.getElementById('tip').hidden, false);
  fp.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  assert.equal(d.getElementById('tip').hidden, true);
  const path = fp.dataset.path;
  w.__push(`${Math.floor(Date.now() / 1000)},who,container,sonarr,98,geschreven,/mnt/trunk/appdata/sonarr/new.db`);
  await new Promise((r) => setTimeout(r, 400));
  assert.equal(d.activeElement.dataset.path, path);                        // focus overleeft het opnieuw tekenen
  dom.window.close();
});

// jsdom kent Blob.text() niet; FileReader wel
const blobText = (w, blob) => new Promise((resolve) => { const r = new w.FileReader(); r.onload = () => resolve(r.result); r.readAsText(blob); });

test('the CSV export holds the spin-ups, file access and container events of the period', async () => {
  const dom = await load(DOCK, 'http://dash/?lang=nl', ['en-US'], null, (w) => {
    w.URL.createObjectURL = (blob) => { w.__blob = blob; return 'blob:csv'; };
    w.URL.revokeObjectURL = () => {};
    w.HTMLAnchorElement.prototype.click = function () { w.__download = this.download; };
  });
  const d = dom.window.document, w = dom.window;
  const btn = d.getElementById('exportCsv');
  assert.equal(btn.getAttribute('aria-label'), 'Deze periode exporteren als CSV');
  btn.click();
  assert.match(w.__download, /^spin-dashboard_\d{8}-\d{4}_\d{8}-\d{4}\.csv$/);
  const csv = (await blobText(w, w.__blob)).replace(/^\ufeff/, '');
  const lines = csv.trim().split('\r\n');
  assert.equal(lines[0], 'time,type,drive,device,source_kind,source,process,action,detail');
  assert.ok(lines.some((l) => /,spin-up,trunk,sdd,/.test(l)), csv);
  assert.ok(lines.some((l) => /,standby,trunk,sdd,/.test(l)));
  assert.ok(lines.some((l) => /,access,trunk,,container,plex,Plex Media Scan,opened,\/mnt\/trunk\/Media\/films\/b\.mkv$/.test(l)), csv);
  assert.ok(lines.some((l) => /,access,trunk,,container,sonarr,,written,\/mnt\/trunk\/appdata\/sonarr\/sonarr\.db$/.test(l)));
  assert.ok(lines.some((l) => /,container die,,,container,radarr,,,exit code 0$/.test(l)), csv);
  assert.ok(lines.some((l) => /,container start,,,container,radarr,,,$/.test(l)));
  assert.ok(lines.some((l) => /,container start,,,container,plex,,,$/.test(l)));
  const times = lines.slice(1).map((l) => l.slice(0, 19));
  assert.deepEqual(times, times.slice().sort());                          // op tijd
  dom.window.close();
});

test('CSV cells with commas or quotes are quoted', async () => {
  const COMMA = served({ [today]: [
    `${ep(300)},disk,sdd,trunk`, `${ep(300)},state,sdd,active`,
    `${ep(100)},who,container,plex,7,geopend,/mnt/trunk/Media/a, "b".mkv`,
  ] });
  const dom = await load(COMMA, 'http://dash/?lang=nl', ['en-US'], null, (w) => {
    w.URL.createObjectURL = (blob) => { w.__blob = blob; return 'blob:csv'; };
    w.URL.revokeObjectURL = () => {};
    w.HTMLAnchorElement.prototype.click = function () {};
  });
  dom.window.document.getElementById('exportCsv').click();
  const csv = await blobText(dom.window, dom.window.__blob);
  assert.match(csv, /,opened,"\/mnt\/trunk\/Media\/a, ""b"".mkv"\r\n/);
  dom.window.close();
});

test('containers of one Compose project can count as one source', async () => {
  const containers = { docker: true, containers: [
    { name: 'plex', state: 'running', project: 'media', mounts: [] },
    { name: 'sonarr', state: 'running', project: 'media', mounts: [] },
    { name: 'radarr', state: 'running', project: '', mounts: [] },
  ] };
  const files = { ...DOCK, 'containers.json': JSON.stringify(containers) };
  const dom = await load(files);
  const d = dom.window.document, w = dom.window;
  const keys = () => [...d.querySelectorAll('#sources .srcbtn')].map((b) => b.dataset.src);
  assert.ok(keys().includes('container|plex') && keys().includes('container|sonarr'));   // standaard uit
  d.getElementById('openSettings').click();
  const box = d.getElementById('groupCompose');
  box.checked = true; box.dispatchEvent(new w.Event('change'));
  assert.ok(keys().includes('container|media') && !keys().includes('container|plex') && !keys().includes('container|sonarr'));
  const media = [...d.querySelectorAll('#sources .src')].find((x) => x.querySelector('[data-src="container|media"]'));
  assert.match(text(media), /media stack 1 veroorzaakte spin-ups 2 gelezen · 1 geschreven/);   // plex wekte 1×, las 2×; sonarr schreef 1×
  assert.equal(media.querySelector('.srcbtn').title, 'Compose-project media: plex, sonarr');
  // de start van plex hoort nu bij de stack
  const lane = d.querySelector('#srcLaneList .srow[data-k="container|media"]');
  assert.equal((lane.querySelector('.cs').getAttribute('d').match(/M/g) || []).length, 1);
  d.querySelector('[data-src="container|media"]').click();
  assert.match(text(d.getElementById('profileBody')), /Start en stop\s*Start plex/);
  assert.deepEqual(JSON.parse(w.localStorage.getItem('spindash.group')), { merge: true, compose: true });
  assert.match([...d.querySelectorAll('#feed li')].map(text).join('\n'), /plex › Plex Media Scan/);   // de feed blijft per container
  dom.window.close();
});

// ---------- stille uren ----------
const hhmmOf = (ms) => { const d = new Date(ms); return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`; };

test('quiet hours from the server shade the timeline and mark spin-ups inside them', async () => {
  // venster om de eerste spin-up van DOCK heen (120 min geleden), niet om de tweede (96 min geleden)
  const quiet = { from: hhmmOf(now - 125 * 60e3), to: hhmmOf(now - 110 * 60e3), drives: [] };
  const dom = await load({ ...DOCK, 'settings.json': JSON.stringify({ hide: [], quiet, webhook: 'https://ntfy.example/x' }) });
  const d = dom.window.document;
  assert.ok(d.querySelector('#laneList .trace .quiet').getAttribute('d').length > 0);
  assert.equal(d.getElementById('legQuiet').hidden, false);
  const ups = [...d.querySelectorAll('#events tr')].filter((tr) => /Opgespind/.test(text(tr)));
  assert.equal(ups.length, 2);
  assert.equal(ups.filter((tr) => tr.querySelector('.quiet-tag')).length, 1);
  assert.equal(text(ups.find((tr) => tr.querySelector('.quiet-tag')).querySelector('.quiet-tag')), 'stille uren');
  d.getElementById('openSettings').click();
  assert.equal(d.getElementById('quietOn').checked, true);
  assert.equal(d.getElementById('quietFrom').value, quiet.from);
  assert.equal(d.getElementById('webhookUrl').value, 'https://ntfy.example/x');
  assert.deepEqual([...d.querySelectorAll('#quietLanes input')].map((i) => i.value), ['trunk']);
  dom.window.close();
});

test('quiet hours apply only to the chosen drives and are saved on the server', async () => {
  const quiet = { from: hhmmOf(now - 125 * 60e3), to: hhmmOf(now - 110 * 60e3), drives: ['array'] };   // trunk doet niet mee
  const dom = await load({ ...DOCK, 'settings.json': JSON.stringify({ hide: [], quiet }), POSTREPLY: { 'notify-test': { ok: true, status: 200 } } });
  const d = dom.window.document, w = dom.window;
  assert.equal(d.querySelector('#laneList .trace .quiet').getAttribute('d'), '');
  assert.equal(d.querySelectorAll('#events .quiet-tag').length, 0);
  d.getElementById('openSettings').click();
  const box = d.querySelector('#quietLanes input[value="trunk"]');
  box.checked = true; box.dispatchEvent(new w.Event('change', { bubbles: true }));
  await new Promise((r) => setTimeout(r, 30));
  assert.deepEqual(w.__posts.at(-1).body, { quiet: { ...quiet, drives: ['trunk'] }, webhook: '' });
  assert.equal(d.querySelectorAll('#events .quiet-tag').length, 1);
  const on = d.getElementById('quietOn');
  on.checked = false; on.dispatchEvent(new w.Event('change'));
  await new Promise((r) => setTimeout(r, 30));
  assert.deepEqual(w.__posts.at(-1).body, { quiet: null, webhook: '' });
  assert.equal(d.getElementById('quietBox').hidden, true);
  d.getElementById('webhookTest').click();
  await new Promise((r) => setTimeout(r, 30));
  assert.ok(w.__posts.some((p) => p.url === 'data/notify-test'));
  assert.equal(d.getElementById('quietStatus').textContent, 'Test verstuurd.');
  dom.window.close();
});

test('a source name in the live feed and in Events opens its profile too', async () => {
  const dom = await load(DOCK);
  const d = dom.window.document;
  // live-feed: de naam is een knop, de procesnaam staat erachter
  const li = [...d.querySelectorAll('#feed li')].find((x) => /b\.mkv/.test(text(x)));
  const btn = li.querySelector('.fs .srcbtn');
  assert.equal(btn.dataset.src, 'container|plex');
  assert.equal(text(li.querySelector('.fs')), 'plex › Plex Media Scan');
  btn.click();
  assert.ok(d.getElementById('profile').hasAttribute('open'));
  assert.equal(text(d.getElementById('profileTitle')), 'plex');
  d.getElementById('profileDone').click();
  // gebeurtenissen: het bronlabel bij de oorzaak, met zijn uiterlijk als label
  const badge = d.querySelector('#events td.cause .srcbtn.who.container');
  assert.equal(badge.dataset.src, 'container|plex');
  badge.click();
  assert.ok(d.getElementById('profile').hasAttribute('open'));
  dom.window.close();
});

test('a feed source outside the selected period stays plain text', async () => {
  const dom = await load(DOCK);
  const d = dom.window.document;
  d.querySelector('[data-range="h"]').click();             // laatste uur: plex las 95 min geleden, valt erbuiten
  await new Promise((r) => setTimeout(r, 100));
  const li = [...d.querySelectorAll('#feed li')].find((x) => /c\.mkv/.test(text(x)));
  assert.equal(li.querySelector('.fs .srcbtn'), null);
  assert.equal(text(li.querySelector('.fs')), 'plex');
  dom.window.close();
});

test('a process in Reads and Writes per drive opens its profile', async () => {
  const dom = await load(RW);
  const d = dom.window.document;
  const names = [...d.querySelectorAll('#actWrites .srcbtn')].map((b) => b.dataset.src);
  assert.deepEqual(names, ['container|plex', 'container|sonarr']);
  assert.equal(d.querySelectorAll('#actWrites .haspath .srcbtn, #actWrites .srcbtn.haspath').length, 0);   // bestanden blijven paden
  d.querySelector('#actReads .srcbtn[data-src="container|bazarr"]').click();
  assert.ok(d.getElementById('profile').hasAttribute('open'));
  assert.equal(text(d.getElementById('profileTitle')), 'bazarr');
  dom.window.close();
});

test('pool and array tag colours keep white text readable (4.5:1)', () => {
  const list = HTML.match(/const LOC_COLORS = \[([^\]]+)\]/)[1].match(/#[0-9A-Fa-f]{6}/g);
  const lum = (hex) => {
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  assert.ok(list.length >= 6);
  for (const c of list) assert.ok(1.05 / (lum(c) + 0.05) >= 4.5, `${c}: ${(1.05 / (lum(c) + 0.05)).toFixed(2)}`);
});

test('the container profile lists the paths of that container with their location labels', async () => {
  const containers = { docker: true, containers: [
    { name: 'plex', state: 'running', project: 'media', mounts: [
      { source: '/mnt/frunk/appdata', target: '/config', rw: true, where: { kind: 'pool', name: 'frunk', hdd: false } },
      { source: '/mnt/frunk/appdata', target: '/transcode', rw: true, where: { kind: 'pool', name: 'frunk', hdd: false } },
      { source: '/mnt/trunk/Media', target: '/media', rw: true, where: { kind: 'pool', name: 'trunk', hdd: true } }] },
    { name: 'sonarr', state: 'running', project: 'media', mounts: [
      { source: '/mnt/trunk/Media', target: '/tv', rw: true, where: { kind: 'pool', name: 'trunk', hdd: true } }] },
  ] };
  const dom = await load({ ...DOCK, 'containers.json': JSON.stringify(containers) });
  const d = dom.window.document, w = dom.window;
  d.querySelector('#sources .srcbtn[data-src="container|plex"]').click();
  const paths = d.querySelector('#profileBody .prof-paths');
  assert.equal(text(paths.querySelector('h4')), 'Paden');
  assert.deepEqual([...paths.querySelectorAll('.map')].map(text), ['/mnt/frunk/appdata frunk', '/mnt/trunk/Media trunk']);
  // dezelfde kleur als in de instellingen
  const inProfile = paths.querySelector('.loc').style.getPropertyValue('--c');
  d.getElementById('profileDone').click();
  d.getElementById('openSettings').click();
  const inSettings = [...d.querySelectorAll('#mountList .loc')].find((x) => x.textContent === 'frunk').style.getPropertyValue('--c');
  assert.equal(inProfile, inSettings);
  d.getElementById('settingsDone').click();
  // als Compose-project: de paden van elk lid onder zijn naam
  const box = d.getElementById('groupCompose');
  box.checked = true; box.dispatchEvent(new w.Event('change'));
  d.querySelector('#sources .srcbtn[data-src="container|media"]').click();
  assert.deepEqual([...d.querySelectorAll('#profileBody .prof-paths .prof-ctr')].map(text), ['plex', 'sonarr']);
  dom.window.close();
});

test('a container without known paths has no paths section in its profile', async () => {
  const dom = await load({ ...DOCK, 'containers.json': JSON.stringify({ docker: true, containers: [] }) });
  const d = dom.window.document;
  d.querySelector('#sources .srcbtn[data-src="container|sonarr"]').click();   // geen mounts bekend
  assert.equal(d.querySelector('#profileBody .prof-paths'), null);
  dom.window.close();
});
