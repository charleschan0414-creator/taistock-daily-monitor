import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const read = name => readFile(new URL(`../public/${name}`, import.meta.url), 'utf8');

test('資料健康狀態以明確文字區分成功與過期', async () => {
  const { resolveDataHealth } = await import('../public/app.js');
  const fresh = resolveDataHealth({ data_date: '2026-10-16', daily_status: { status: 'success' } }, new Date('2026-10-17T08:00:00+08:00'));
  const stale = resolveDataHealth({ data_date: '2026-10-15', daily_status: { status: 'success' } }, new Date('2026-10-19T08:00:00+08:00'));
  const failed = resolveDataHealth({ data_date: '2026-10-16', daily_status: { status: 'failed' } }, new Date('2026-10-17T08:00:00+08:00'));
  assert.deepEqual(fresh, { state: 'success', label: '資料成功' });
  assert.deepEqual(stale, { state: 'stale', label: '資料過期' });
  assert.deepEqual(failed, { state: 'error', label: '更新失敗' });
});

test('API載入失敗時頂部狀態不會停在載入中', async () => {
  const app = await read('app.js');
  const catchBlock = app.match(/catch \(error\) \{[\s\S]*?\}\s*finally/)?.[0] ?? '';
  assert.match(catchBlock, /data-health/);
  assert.match(catchBlock, /載入失敗/);
  assert.match(catchBlock, /status-error/);
});

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

test('手機首屏依序顯示精簡狀態列與四張今日重點卡', async () => {
  const html = await read('index.html');
  const header = html.match(/<header[\s\S]*?<\/header>/)?.[0] ?? '';
  assert.match(header, /id="data-date"/);
  assert.match(header, /id="data-health"/);
  assert.match(header, /id="refresh"/);
  assert.doesNotMatch(header, /更新時間|資料來源|策略閘門/);
  assert.match(html, /<section[^>]*class="today-summary"/);
  assert.match(html, /id="summary-date"/);
  assert.match(html, /id="summary-long"/);
  assert.match(html, /id="summary-short"/);
  assert.match(html, /id="summary-night"/);
  assert.ok(html.indexOf('today-summary') < html.indexOf('market-context'));
});

test('研究與技術資訊預設收合且手機操作符合無障礙需求', async () => {
  const [html, styles] = await Promise.all([read('index.html'), read('styles.css')]);
  assert.match(html, /<details class="v2-analysis"/);
  assert.match(html, /<summary[\s\S]*V2 成本後績效檢驗/);
  assert.match(html, /<details class="system-details"/);
  assert.match(styles, /:focus-visible/);
  assert.match(styles, /min-height:\s*44px/);
  assert.match(styles, /@media\s*\(max-width:\s*420px\)/);
});

test('訊號卡明列偏多偏空方向且動態內容不用 innerHTML', async () => {
  const app = await read('app.js');
  assert.match(app, /function card\(row, direction\)/);
  assert.match(app, /directionLabel\.textContent = direction/);
  assert.match(app, /renderList\('long-list',[^\n]*'偏多'/);
  assert.match(app, /renderList\('short-list',[^\n]*'偏空'/);
  assert.doesNotMatch(app, /\.innerHTML\s*=/);
});

test('策略保留期指標相容成本後巢狀與舊版扁平格式', async () => {
  const { resolveStrategyTestMetrics } = await import('../public/app.js');
  assert.deepEqual(resolveStrategyTestMetrics({ daily_status: { strategy: { candidate: { test: {
    standard: { win_rate: 0.4573, profit_factor: 1.1122 },
  } } } } }), { win_rate: 0.4573, profit_factor: 1.1122 });
  assert.deepEqual(resolveStrategyTestMetrics({ daily_status: { strategy: { candidate: { test: {
    win_rate: 0.42, profit_factor: 1.17,
  } } } } }), { win_rate: 0.42, profit_factor: 1.17 });
});

test('PWA顯示核心100日成本後三情境且保留研究限制', async () => {
  const [html, app, styles, sw] = await Promise.all([read('index.html'), read('app.js'), read('styles.css'), read('sw.js')]);
  assert.match(html, /100日成本後績效/);
  assert.match(html, /訊號批次，不是帳戶報酬/);
  for (const id of ['backtest-period', 'backtest-standard-overall', 'backtest-standard-long',
    'backtest-standard-short', 'backtest-gross-overall', 'backtest-stress-overall']) {
    assert.match(html, new RegExp(`id="${id}"`));
    assert.match(app, new RegExp(id));
  }
  assert.match(html, /standard 0\.435%/);
  assert.match(html, /stress 0\.535%/);
  assert.match(html, /借券費與融券限制/);
  assert.match(app, /renderBacktestPerformance\(data\)/);
  assert.doesNotMatch(app, /\.innerHTML\s*=/);
  assert.match(styles, /\.backtest-performance/);
  assert.match(sw, /taistock-monitor-v7/);
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
  assert.match(sw, /taistock-monitor-v7/);
});
