import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const read = name => readFile(new URL(`../public/${name}`, import.meta.url), 'utf8');

test('PWA提供五項TAIFEX市場情境卡與非選股分數聲明', async () => {
  const [html, app, sw] = await Promise.all([read('index.html'), read('app.js'), read('sw.js')]);
  assert.match(html, /id="market-risk"/);
  assert.match(html, /id="market-list"/);
  assert.match(html, /期交所行情，可能延遲/);
  assert.match(html, /僅為市場脈動，不改變選股分數/);
  assert.match(app, /global_market/);
  assert.match(app, /change_pct/);
  assert.match(app, /volume/);
  assert.match(app, /createRefreshController/);
  assert.match(sw, /\/refresh\.js/);
});
