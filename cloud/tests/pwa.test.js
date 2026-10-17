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

test('PWA顯示V2成本後績效檢驗、方法限制與新版快取', async () => {
  const [html, app, styles, sw] = await Promise.all([
    read('index.html'), read('app.js'), read('styles.css'), read('sw.js'),
  ]);
  assert.match(html, /V2 成本後績效檢驗/);
  assert.match(html, /標準成本[^<]*0\.435%/);
  assert.match(html, /壓力成本[^<]*0\.535%/);
  assert.match(html, /id="v2-quality"/);
  assert.match(html, /id="v2-status"/);
  assert.match(html, /id="v2-long-metrics"/);
  assert.match(html, /id="v2-short-metrics"/);
  assert.match(html, /日K OHLC 模擬/);
  assert.match(html, /不是 13:20 真實當沖/);
  assert.match(html, /不保證 60% 勝率或獲利/);
  assert.match(app, /resolveV2Analysis\(data\)/);
  assert.match(app, /v2-quality/);
  assert.doesNotMatch(app, /\.innerHTML\s*=/);
  assert.match(styles, /\.v2-analysis/);
  assert.match(sw, /taistock-monitor-v3/);
});
