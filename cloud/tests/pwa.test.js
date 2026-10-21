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

test('資料過期或流水線失敗時清空可執行名單與數量', async () => {
  const { executableListsForHealth } = await import('../public/app.js');
  const data = { long: [{ code: '1111' }], short: [{ code: '2222' }] };
  assert.deepEqual(executableListsForHealth(data, { state: 'stale' }),
    { long: [], short: [], longCount: 0, shortCount: 0 });
  assert.deepEqual(executableListsForHealth(data, { state: 'error' }),
    { long: [], short: [], longCount: 0, shortCount: 0 });
  assert.deepEqual(executableListsForHealth(data, { state: 'success' }),
    { long: data.long, short: data.short, longCount: 1, shortCount: 1 });
  const app = await read('app.js');
  assert.match(app, /const executable = executableListsForHealth\(data, health\)/);
  assert.match(app, /renderList\('long-list', executable\.long/);
});

test('API載入失敗時頂部與推薦區都改為不可執行狀態', async () => {
  const app = await read('app.js');
  const catchBlock = app.match(/catch \(error\) \{[\s\S]*?\}\s*finally/)?.[0] ?? '';
  assert.match(catchBlock, /data-health/);
  assert.match(catchBlock, /載入失敗/);
  assert.match(catchBlock, /status-error/);
  assert.match(catchBlock, /summary-long-note/);
  assert.match(catchBlock, /summary-short-note/);
  assert.match(catchBlock, /execution-eligibility-note/);
  assert.match(catchBlock, /resolveLoadFailure/);
  assert.match(catchBlock, /renderListError\('long-list'/);
  assert.match(catchBlock, /renderListError\('short-list'/);
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

test('手機首屏只顯示資料狀態與兩張方向摘要卡', async () => {
  const html = await read('index.html');
  const header = html.match(/<header[\s\S]*?<\/header>/)?.[0] ?? '';
  assert.match(header, /id="data-date"/);
  assert.match(header, /id="data-health"/);
  assert.match(header, /id="refresh"/);
  assert.doesNotMatch(header, /更新時間|資料來源|策略閘門/);
  assert.match(html, /<section[^>]*class="today-summary"/);
  assert.match(html, /id="summary-long"/);
  assert.match(html, /id="summary-short"/);
  assert.doesNotMatch(html, /id="summary-date"/);
  assert.match(html, /研究觀察[^<]*非投資建議[^<]*不會自動下單/);
  assert.doesNotMatch(html, /id="summary-night"/);
  assert.ok(html.indexOf('today-summary') < html.indexOf('signals'));
  assert.ok(html.indexOf('signals') < html.indexOf('market-context'));
});

test('夜盤、結算、策略研究與系統資訊集中在單一進階區', async () => {
  const html = await read('index.html');
  const advancedStart = html.indexOf('<details class="advanced-hub"');
  const advancedEnd = html.lastIndexOf('</details>');
  const advanced = advancedStart >= 0 ? html.slice(advancedStart, advancedEnd + 10) : '';
  assert.match(advanced, /進階研究與系統/);
  for (const section of ['market-context', 'settlement-summary', 'strategy-campaign',
    'v2-analysis', 'backtest-performance', 'system-details']) {
    assert.match(advanced, new RegExp(`class="${section}`));
  }
  assert.match(advanced, /下載 latest\.csv/);
  assert.doesNotMatch(html, /<details class="advanced-hub" open/);
});

test('研究與技術資訊預設收合且手機操作符合無障礙需求', async () => {
  const [html, styles] = await Promise.all([read('index.html'), read('styles.css')]);
  assert.match(html, /<details class="v2-analysis"/);
  assert.match(html, /<summary[\s\S]*V2 成本後績效檢驗/);
  assert.match(html, /<details class="system-details"/);
  assert.match(html, /<details class="settlement-summary"/);
  assert.ok(html.indexOf('signals') < html.indexOf('settlement-summary'));
  assert.match(styles, /:focus-visible/);
  assert.match(styles, /min-height:\s*44px/);
  assert.match(styles, /@media\s*\(max-width:\s*420px\)/);
  assert.match(styles, /@media\s*\(max-width:\s*560px\)[\s\S]*?\.summary-grid\s*\{\s*grid-template-columns:\s*1fr/);
  assert.match(styles, /overflow-wrap:\s*anywhere/);
});

test('載入失敗顯示一般使用者可理解的訊息而非原始解析錯誤', async () => {
  const { friendlyLoadError, resolveLoadFailure } = await import('../public/app.js');
  assert.equal(friendlyLoadError(new SyntaxError("Unexpected token '<'")), '資料暫時無法載入，請稍後重新整理。');
  assert.deepEqual(resolveLoadFailure(), {
    count: '—', note: '資料載入失敗｜不可執行',
    eligibility: '官方當沖資格無法驗證；目前清單不可執行。',
  });
});

test('首屏說明通過閘門數與基礎候選數不同', async () => {
  const [html, app] = await Promise.all([read('index.html'), read('app.js')]);
  assert.match(html, /通過策略閘門/);
  assert.match(app, /pre_gate_counts/);
  assert.match(app, /基礎候選/);
});

test('PWA顯示一個月研究週期且明列不自動啟用與不保證改善', async () => {
  const [html, app] = await Promise.all([read('index.html'), read('app.js')]);
  for (const id of ['campaign-status', 'campaign-period', 'campaign-baseline',
    'campaign-current', 'campaign-candidate']) {
    assert.match(html, new RegExp(`id="${id}"`));
    assert.match(app, new RegExp(id));
  }
  assert.match(html, /候選不會自動啟用/);
  assert.match(html, /不保證未來勝率或PF提高/);

  const { resolveStrategyCampaign } = await import('../public/app.js');
  const view = resolveStrategyCampaign({ daily_status: { strategy_campaign: {
    status: 'complete', start_date: '2026-10-20', end_date: '2026-11-20',
    baseline: { win_rate: 0.35, profit_factor: 0.49 },
    current: { win_rate: 0.42, profit_factor: 1.1 }, candidate_found: true,
    ready_for_manual_review: false, unique_signal_days: 20,
    candidate_forward_signal_days: 1, minimum_unique_signal_days: 20,
  } } });
  assert.match(view.candidate, /候選觀察日 1（非績效核准）/);
  assert.doesNotMatch(view.candidate, /符合月期比較/);
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
  assert.match(sw, /taistock-monitor-v15/);
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
  assert.match(sw, /taistock-monitor-v15/);
});

test('個股卡顯示券商短沖倒貨風險且明列不計分', async () => {
  const app = await read('app.js');
  assert.match(app, /broker_risk_research/);
  assert.match(app, /分點短線風險研究/);
  assert.match(app, /不計分/);
  assert.match(app, /非官方認定/);
});

test('PWA顯示官方當沖資格排除與K線方向衝突警示', async () => {
  const html = await read('index.html');
  const app = await read('app.js');
  assert.match(html, /execution-eligibility-note/);
  assert.match(app, /execution_eligibility/);
  assert.match(app, /官方當沖資格/);
  assert.match(app, /candlestick_research/);
  assert.match(app, /紅K/);
  assert.match(app, /不直接改分或排除/);
});

test('個股卡以觸發價與風險警示為主，研究細節預設收合', async () => {
  const [html, app, styles] = await Promise.all([read('index.html'), read('app.js'), read('styles.css')]);
  assert.match(app, /card-primary-price/);
  assert.match(app, /官方當沖資格已通過/);
  assert.match(app, /signal-warning/);
  assert.match(app, /查看原因與研究細節/);
  assert.match(styles, /\.signal-warning/);
  assert.match(styles, /\.card-details/);
  assert.doesNotMatch(html, /<details class="strategy-campaign" open>/);
});

test('推薦股票下方顯示最近5日K線圖與最後3日型態解釋', async () => {
  const { normalizeCandleGeometry } = await import('../public/app.js');
  const candles = [
    { date: '2026-10-09', open: 10, high: 11, low: 9, close: 10.8 },
    { date: '2026-10-10', open: 11, high: 12, low: 10, close: 11.8 },
    { date: '2026-10-13', open: 12, high: 13, low: 11, close: 12.8 },
    { date: '2026-10-14', open: 13, high: 14, low: 12, close: 13.8 },
    { date: '2026-10-15', open: 14, high: 15, low: 13, close: 14.8 },
  ];
  const points = normalizeCandleGeometry(candles, 300, 140);
  assert.equal(points.length, 5);
  assert.ok(points.every(point => point.x >= 0 && point.x <= 300));
  assert.ok(points.every(point => ['highY', 'lowY', 'openY', 'closeY']
    .every(key => point[key] >= 0 && point[key] <= 140)));
  const [app, styles, html] = await Promise.all([read('app.js'), read('styles.css'), read('index.html')]);
  assert.match(app, /createElementNS/);
  assert.match(app, /最近5日K線/);
  assert.match(app, /最後3日/);
  assert.match(app, /不計分/);
  assert.match(styles, /\.candle-study/);
  assert.match(html, /48種K棒型態戰法/);
  assert.match(html, /PLlAWMYbuVkC-sX0QxC_FgepGeL-k-YY1L/);
  assert.deepEqual(normalizeCandleGeometry(candles.slice(0, 4), 300, 140), []);
});
