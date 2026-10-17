import { createRefreshController } from './refresh.js';

const $ = id => document.getElementById(id);
const fmt = value => value == null ? '—' : new Intl.NumberFormat('zh-TW', { maximumFractionDigits: 2 }).format(value);
const percent = value => Number.isFinite(value) ? `${(value * 100).toFixed(2)}%` : '—';
const metricSource = direction => direction?.standard?.holdout ?? direction?.standard_holdout ?? direction?.standard ?? direction?.holdout?.standard ?? direction;
const v2Value = value => Number.isFinite(value) ? value : '尚未完成';
const v2Percent = value => Number.isFinite(value) ? percent(value) : '尚未完成';
const v2Metrics = direction => {
  const metrics = metricSource(direction);
  return {
    trades: v2Value(metrics?.trades),
    winRate: v2Percent(metrics?.win_rate),
    averageReturn: v2Percent(metrics?.average_return),
    profitFactor: Number.isFinite(metrics?.profit_factor)
      ? String(metrics.profit_factor)
      : metrics?.profit_factor === null && metrics?.trades > 0 ? '無虧損樣本' : '尚未完成',
  };
};
export function resolveV2Analysis(data) {
  const analysis = data?.daily_status?.v2_analysis ?? data?.v2_analysis;
  const directions = analysis?.selected ?? analysis?.directions;
  return {
    available: Boolean(analysis),
    quality: analysis?.decision?.enabled === true ? '通過' : analysis?.decision?.enabled === false ? '未通過' : '尚未完成',
    status: analysis?.decision?.status ?? '尚未完成',
    long: v2Metrics(directions?.long),
    short: v2Metrics(directions?.short),
  };
}

export function resolveStrategyTestMetrics(data) {
  const metrics = data?.daily_status?.strategy?.candidate?.test;
  return metrics?.standard ?? metrics;
}
function missedTradingDays(dataDate, now = new Date()) {
  if (!dataDate) return Infinity;
  const cursor = new Date(`${dataDate}T12:00:00+08:00`); cursor.setDate(cursor.getDate() + 1);
  const today = new Date(now.toLocaleString('en-US', { timeZone: 'Asia/Taipei' }));
  let count = 0;
  while (cursor.toDateString() !== today.toDateString() && cursor < today) {
    if (cursor.getDay() !== 0 && cursor.getDay() !== 6) count += 1;
    cursor.setDate(cursor.getDate() + 1);
  }
  return count;
}

export function resolveDataHealth(data, now = new Date()) {
  if (data?.daily_status?.status !== 'success') return { state: 'error', label: '更新失敗' };
  if (missedTradingDays(data?.data_date, now) > 0) return { state: 'stale', label: '資料過期' };
  return { state: 'success', label: '資料成功' };
}

function card(row, direction) {
  const node = document.createElement('article'); node.className = 'card';
  const head = document.createElement('div'); head.className = 'card-head';
  const title = document.createElement('div');
  const code = document.createElement('span'); code.className = 'symbol'; code.textContent = row.code;
  const name = document.createElement('span'); name.className = 'name'; name.textContent = `${row.name ?? ''} · ${row.market ?? ''}`;
  title.append(code, name);
  const score = document.createElement('span'); score.className = 'score'; score.textContent = `SCORE ${fmt(row.score)}`;
  head.append(title, score);
  const price = document.createElement('div'); price.className = 'price'; price.textContent = fmt(row.current_price ?? row.close);
  const directionLabel = document.createElement('span');
  directionLabel.className = `card-direction ${direction === '偏多' ? 'card-direction-long' : 'card-direction-short'}`;
  directionLabel.textContent = direction;
  const status = document.createElement('div'); status.className = 'status';
  status.textContent = `${row.price_status ?? ''}現價${row.current_price == null ? '（顯示盤後收盤）' : ''}`;
  const levels = document.createElement('div'); levels.className = 'levels';
  for (const [label, key] of [['進場', 'entry_trigger'], ['停損', 'stop_loss'], ['目標1', 'target1']]) {
    const box = document.createElement('div'); const small = document.createElement('span'); small.textContent = label;
    const strong = document.createElement('strong'); strong.textContent = fmt(row[key]); box.append(small, strong); levels.append(box);
  }
  const reason = document.createElement('p'); reason.className = 'reason'; reason.textContent = row.reason ?? '';
  node.append(head, price, directionLabel, status, levels, reason); return node;
}
function renderList(id, rows, direction) {
  const box = $(id); box.replaceChildren();
  if (!rows.length) { const empty = document.createElement('p'); empty.className = 'empty'; empty.textContent = '目前沒有符合規則的觀察標的。'; box.append(empty); return; }
  box.append(...rows.map(row => card(row, direction)));
}
function renderMarket(market) {
  $('market-risk').textContent = market?.risk?.status ?? '中性';
  $('market-rule').textContent = market?.risk?.rule ?? 'TAIFEX 資料無法取得，維持中性';
  $('market-fetched').textContent = market?.fetched_at ? `系統抓取：${new Date(market.fetched_at).toLocaleString('zh-TW')}` : '系統抓取：—';
  const box = $('market-list'); box.replaceChildren();
  if (market?.status !== 'available' || !market.items?.length) {
    const empty = document.createElement('p'); empty.className = 'empty'; empty.textContent = '期交所行情暫時無法取得，選股清單仍可使用。'; box.append(empty); return;
  }
  for (const item of market.items) {
    const node = document.createElement('article'); node.className = 'market-card';
    const heading = document.createElement('div'); heading.className = 'market-heading';
    const title = document.createElement('strong'); title.textContent = item.name;
    const contract = document.createElement('span'); contract.textContent = item.contract; heading.append(title, contract);
    const price = document.createElement('div'); price.className = 'market-price'; price.textContent = fmt(item.price);
    const move = document.createElement('div'); move.className = item.change > 0 ? 'market-up' : item.change < 0 ? 'market-down' : '';
    const sign = item.change > 0 ? '+' : '';
    move.textContent = `${sign}${fmt(item.change)} / ${item.change_pct == null ? '—' : `${sign}${item.change_pct.toFixed(2)}%`}`;
    const volume = document.createElement('small'); volume.textContent = `成交量 ${fmt(item.volume)}`;
    node.append(heading, price, move, volume); box.append(node);
  }
}

function renderV2Analysis(data) {
  const view = resolveV2Analysis(data);
  const summary = metrics => `交易 ${metrics.trades} · 勝率 ${metrics.winRate} · 平均報酬 ${metrics.averageReturn} · PF ${metrics.profitFactor}`;
  $('v2-quality').textContent = view.quality;
  $('v2-status').textContent = view.status;
  $('v2-long-metrics').textContent = summary(view.long);
  $('v2-short-metrics').textContent = summary(view.short);
}

async function load() {
  $('refresh').disabled = true; $('message').textContent = '';
  try {
    const response = await fetch('/api/dashboard', { cache: 'no-store' }); const data = await response.json();
    if (!response.ok) throw new Error(data.error ?? `HTTP ${response.status}`);
    const health = resolveDataHealth(data);
    $('data-date').textContent = data.data_date ?? '—';
    $('summary-date').textContent = data.data_date ?? '—';
    $('summary-long').textContent = String(data.long?.length ?? 0);
    $('summary-short').textContent = String(data.short?.length ?? 0);
    $('summary-night').textContent = data.global_market?.risk?.status ?? '中性';
    $('summary-source').textContent = data.data_source === 'github' ? 'GitHub 公開成果' : 'Cloudflare KV 備援';
    $('data-health').textContent = health.label;
    $('data-health').className = `status-badge status-${health.state}`;
    $('updated-at').textContent = data.updated_at ? `更新 ${new Date(data.updated_at).toLocaleString('zh-TW')}` : '更新 —';
    $('price-status').textContent = data.realtime_status === 'available' ? 'MIS 已連線（逐檔標示）' : 'MIS 無法使用，保留盤後清單';
    $('gate').textContent = data.strategy_gate?.status ?? '未啟用';
    const daily = data.daily_status;
    $('daily-status').textContent = daily?.status === 'success' ? '成功' : (daily?.status ?? '未知');
    $('data-source').textContent = data.data_source === 'github' ? 'GitHub 公開成果' : 'Cloudflare KV 備援';
    $('completed-at').textContent = daily?.completed_at ? new Date(daily.completed_at).toLocaleString('zh-TW') : '—';
    const testMetrics = resolveStrategyTestMetrics(data);
    const pf = testMetrics?.profit_factor == null ? (testMetrics ? '無虧損樣本' : '—') : fmt(testMetrics.profit_factor);
    $('test-metrics').textContent = `${percent(testMetrics?.win_rate)} / ${pf}`;
    renderV2Analysis(data);
    const warnings = [];
    if (daily?.status !== 'success') warnings.push('每日收盤後回測／優化尚未成功');
    if (health.state === 'stale') warnings.push('盤後資料已過期，07:40 備援可能尚未完成');
    $('message').textContent = warnings.length ? `注意：${warnings.join('；')}。` : '';
    renderMarket(data.global_market);
    renderList('long-list', data.long ?? [], '偏多'); renderList('short-list', data.short ?? [], '偏空');
  } catch (error) {
    $('data-health').textContent = '載入失敗';
    $('data-health').className = 'status-badge status-error';
    $('message').textContent = `資料載入失敗：${error.message}`;
  }
  finally { $('refresh').disabled = false; }
}
if (typeof document !== 'undefined') {
  const refreshController = createRefreshController({ load });
  $('refresh').addEventListener('click', refreshController.manual);
  refreshController.start();
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
}
