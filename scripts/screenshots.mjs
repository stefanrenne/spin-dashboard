// Recreate the README screenshots from the sample data (see docs/development.md).
// Playwright is deliberately not in package.json: only needed when the screenshots are refreshed.
//   npm install --no-save playwright && npx playwright install chromium
//   node scripts/screenshots.mjs
import { chromium } from 'playwright';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('..', import.meta.url));
const out = root + 'docs/images/';

// the dashboard is one file; the demo needs nothing more
const server = createServer(async (req, res) => {
  res.setHeader('Content-Type', 'text/html; charset=utf-8');
  res.end(await readFile(root + 'app/static/index.html'));
}).listen(0, '127.0.0.1');
await new Promise((r) => server.once('listening', r));
const url = `http://127.0.0.1:${server.address().port}/?demo&lang=en`;

const browser = await chromium.launch();
for (const scheme of ['light', 'dark']) {
  const ctx = await browser.newContext({ viewport: { width: 1200, height: 900 }, deviceScaleFactor: 2, colorScheme: scheme, locale: 'en-US' });
  const page = await ctx.newPage();
  await page.goto(url);
  await page.waitForSelector('#dash:not([hidden])');
  await page.click('[data-range="7"]');
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(1200);           // entrance animation and font
  await page.screenshot({ path: `${out}screenshot-${scheme}.png`, fullPage: true });
  // detail: reads and writes, spin time and live activity
  const clip = await page.evaluate(() => {
    const rs = ['actR', 'actW', 'rest', 'feed'].map((id) => document.querySelector(`[data-card="${id}"]`).getBoundingClientRect());
    const top = Math.min(...rs.map((r) => r.top)) + scrollY, bottom = Math.max(...rs.map((r) => r.bottom)) + scrollY;
    const left = Math.min(...rs.map((r) => r.left)), right = Math.max(...rs.map((r) => r.right));
    return { x: left - 10, y: top - 10, width: right - left + 20, height: bottom - top + 20 };
  });
  await page.screenshot({ path: `${out}active-drives-live-activity${scheme === 'light' ? '-light' : ''}.png`, clip, fullPage: true });
  await ctx.close();
}
await browser.close();
server.close();
console.log('Screenshots written to docs/images/');
