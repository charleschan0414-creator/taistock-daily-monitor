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

export function resolveTodayResult(data) {
  const result = data?.daily_status?.today_result;
  const validated = data?.daily_status?.strategy_freeze?.cost_validated === true;
  if (!result || result.status !== 'complete') {
    return { available: false, counts: '尚未完成', performance: '尚未完成',
      validation: validated ? '已通過成本驗證' : '未通過成本驗證｜僅供研究', dates: '—' };
  }
  const standard = result.standard ?? {};
  const pf = Number.isFinite(standard.profit_factor) ? standard.profit_factor.toFixed(3) : '—';
  return {
    available: true,
    counts: `${result.signals ?? 0} 筆訊號｜${result.triggered ?? 0} 已觸發｜${result.untriggered ?? 0} 未觸發`,
    performance: `平均 ${percent(standard.average_return)}｜PF ${pf}｜成本 ${percent(standard.cost_rate)}`,
    validation: validated ? '已通過成本驗證' : '未通過成本驗證｜僅供研究',
    dates: `訊號日 ${result.signal_date ?? '—'}｜結算日 ${result.trade_date ?? '—'}`,
  };
}

export function resolveStrategyCampaign(data) {
  const campaign = data?.daily_status?.strategy_campaign;
  if (!campaign) return { status: '等待資料', period: '—', baseline: '—', current: '—', candidate: '尚未發現' };
  const status = { active: '進行中', complete: '已完成', not_started: '尚未開始' }[campaign.status] ?? '等待資料';
  const metricText = metrics => {
    const pf = Number.isFinite(metrics?.profit_factor) ? Number(metrics.profit_factor).toFixed(3) : '—';
    return `勝率 ${percent(metrics?.win_rate)}｜PF ${pf}`;
  };
  const progress = campaign.candidate_found
    ? `候選觀察日 ${campaign.candidate_forward_signal_days ?? 0}（非績效核准）`
    : `研究新訊號日 ${campaign.unique_signal_days ?? 0}/${campaign.minimum_unique_signal_days ?? 20}`;
  return {
    status,
    period: `${campaign.start_date ?? '—'} ～ ${campaign.end_date ?? '—'}`,
    baseline: metricText(campaign.baseline),
    current: metricText(campaign.current),
    candidate: `${campaign.candidate_found ? '發現歷史候選｜待人工審核｜不自動啟用'
      : '尚未發現合格候選'}｜${progress}`,
  };
}

const backtestMetricText = (metrics, unavailable = '尚未完成') => {
  if (!metrics) return unavailable;
  const pf = Number.isFinite(metrics.profit_factor)
    ? String(metrics.profit_factor)
    : metrics.profit_factor === null && metrics.trades > 0 ? '無虧損樣本' : '—';
  return `成交 ${metrics.trades ?? 0} · 勝率 ${percent(metrics.win_rate)} · 平均 ${percent(metrics.average_return ?? metrics.average_net_return)} · PF ${pf} · 最大回撤 ${percent(metrics.max_drawdown)}`;
};
const backtestScenario = (metrics, unavailable) => ({
  overall: backtestMetricText(metrics, unavailable),
  long: backtestMetricText(metrics?.long, unavailable),
  short: backtestMetricText(metrics?.short, unavailable),
});
export function resolveBacktestPerformance(data) {
  const backtest = data?.daily_status?.backtest ?? data?.backtest;
  const empty = backtestScenario(null, '尚未完成');
  if (!backtest) {
    return { available: false, legacy: false, period: '—', labels: { gross: 'gross（未扣成本）', standard: 'standard 0.435%', stress: 'stress 0.535%' }, gross: empty, standard: empty, stress: empty };
  }
  const scenarios = backtest.metrics_scenarios;
  const costs = { gross: 0, standard: 0.00435, stress: 0.00535, ...(backtest.costs ?? {}) };
  const unavailable = scenarios ? '尚未完成' : '舊報告未提供';
  const period = backtest.period?.from && backtest.period?.to ? `${backtest.period.from} ～ ${backtest.period.to}` : '—';
  const costPercent = value => Number.isFinite(value) ? `${(value * 100).toFixed(3)}%` : '—';
  return {
    available: true,
    legacy: !scenarios,
    period,
    labels: { gross: scenarios ? 'gross（未扣成本）' : 'gross（舊報告，未扣成本）', standard: `standard ${costPercent(costs.standard)}`, stress: `stress ${costPercent(costs.stress)}` },
    gross: backtestScenario(scenarios?.gross ?? backtest.metrics, unavailable),
    standard: backtestScenario(scenarios?.standard, unavailable),
    stress: backtestScenario(scenarios?.stress, unavailable),
  };
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

export function friendlyLoadError() {
  return '資料暫時無法載入，請稍後重新整理。';
}

export function resolveLoadFailure() {
  return {
    count: '—',
    note: '資料載入失敗｜不可執行',
    eligibility: '官方當沖資格無法驗證；目前清單不可執行。',
  };
}

function card(row, direction) {
  const node = document.createElement('article'); node.className = 'card';
  const head = document.createElement('div'); head.className = 'card-head';
  const title = document.createElement('div');
  const code = document.createElement('span'); code.className = 'symbol'; code.textContent = row.code;
  const name = document.createElement('span'); name.className = 'name'; name.textContent = `${row.name ?? ''} · ${row.market ?? ''}`;
  title.append(code, name);
  const directionLabel = document.createElement('span');
  directionLabel.className = `card-direction ${direction === '偏多' ? 'card-direction-long' : 'card-direction-short'}`;
  directionLabel.textContent = direction;
  head.append(title, directionLabel);
  const eligibility = document.createElement('div');
  const eligible = row.day_trading_eligibility?.eligible === true;
  eligibility.className = `eligibility-status ${eligible ? 'eligibility-ok' : 'eligibility-error'}`;
  eligibility.textContent = eligible ? '✓ 官方當沖資格已通過' : '⚠ 官方當沖資格資料異常';
  const priceLabel = document.createElement('span'); priceLabel.className = 'card-primary-label'; priceLabel.textContent = '觸發價（未觸發不交易）';
  const price = document.createElement('div'); price.className = 'price card-primary-price'; price.textContent = fmt(row.entry_trigger);
  const status = document.createElement('div'); status.className = 'status';
  status.textContent = `現價 ${fmt(row.current_price ?? row.close)}${row.current_price == null ? '（盤後收盤）' : ''}｜${row.price_status ?? '待更新'}`;
  const levels = document.createElement('div'); levels.className = 'levels';
  for (const [label, key] of [['停損／回補', 'stop_loss'], ['目標1', 'target1']]) {
    const box = document.createElement('div'); const small = document.createElement('span'); small.textContent = label;
    const strong = document.createElement('strong'); strong.textContent = fmt(row[key]); box.append(small, strong); levels.append(box);
  }
  const candle = row.candlestick_research;
  const candleConflict = candle?.status === 'direction_conflict';
  const broker = row.broker_risk_research;
  const warnings = document.createElement('div'); warnings.className = 'signal-warnings';
  if (candleConflict) {
    const warning = document.createElement('p'); warning.className = 'signal-warning';
    warning.textContent = `⚠ K線方向衝突｜${candle?.message ?? '研究警示，不直接改分或排除'}`; warnings.append(warning);
  }
  if (broker?.warning) {
    const warning = document.createElement('p'); warning.className = 'signal-warning';
    warning.textContent = `⚠ 分點短線風險｜${broker.risk_level === 'high' ? '高度注意' : '注意'}｜研究不計分`; warnings.append(warning);
  }
  const details = document.createElement('details'); details.className = 'card-details';
  const detailsTitle = document.createElement('summary'); detailsTitle.textContent = '查看原因與研究細節';
  const reason = document.createElement('p'); reason.className = 'reason'; reason.textContent = `入選原因｜${row.reason ?? '—'}`;
  const score = document.createElement('p'); score.className = 'score-detail'; score.textContent = `研究排序｜${fmt(row.score)}`;
  const research = document.createElement('div'); research.className = 'technical-research';
  const researchTitle = document.createElement('strong');
  researchTitle.textContent = candleConflict ? 'K線與技術研究｜方向衝突｜不計分' : 'K線與技術研究｜不計分｜未啟用';
  const indicators = row.technical_research?.indicators;
  const researchText = document.createElement('p');
  researchText.textContent = indicators
    ? `RSI5 ${fmt(indicators.rsi_5)}｜ATR5 ${fmt(indicators.atr_5_pct)}%｜${indicators.candle_body_pct > 0 ? '紅K' : indicators.candle_body_pct < 0 ? '黑K' : '十字K'} ${fmt(indicators.candle_body_pct)}%｜較前收 ${fmt(indicators.close_change_pct)}%｜收盤位置 ${fmt(indicators.close_location_pct)}%｜距前5日高 ${fmt(indicators.close_vs_prior5_high_pct)}%｜距前5日低 ${fmt(indicators.close_vs_prior5_low_pct)}%`
    : '資料不足；不以日K假裝盤中確認。';
  const candleText = document.createElement('p');
  candleText.textContent = candle?.message ?? 'K線衝突資料尚未完成；研究欄位不直接改分或排除。';
  research.append(researchTitle, researchText, candleText);
  const brokerResearch = document.createElement('div');
  brokerResearch.className = `broker-risk-research ${broker?.warning ? 'broker-risk-warning' : ''}`;
  const brokerTitle = document.createElement('strong');
  brokerTitle.textContent = broker?.warning
    ? `⚠ 分點短線風險研究｜${broker.risk_level === 'high' ? '高度注意' : '注意'}｜不計分`
    : '分點短線風險研究｜無有效警示／資料不足｜不計分';
  const brokerText = document.createElement('p');
  const branchText = [...(broker?.watched_buyers ?? []), ...(broker?.watched_sellers ?? [])]
    .map(item => `${item.branch} ${item.side === 'buy' ? '買超' : '賣超'}${fmt(Math.abs(item.net_lots))}張／週轉占比${fmt(item.turnover_share_pct)}%`)
    .join('；');
  brokerText.textContent = broker?.status === 'available'
    ? `${branchText || '未發現達警示門檻的重點觀察分點'}。分點為多人彙總，短線型標籤非官方認定；只能提示風險，不能保證避免倒貨。`
    : '分點資料不可用或日期不一致；不據此調整排序或名單。';
  brokerResearch.append(brokerTitle, brokerText);
  details.append(detailsTitle, reason, score, research, brokerResearch);
  node.append(head, eligibility, priceLabel, price, status, levels, warnings, details); return node;
}
function renderList(id, rows, direction) {
  const box = $(id); box.replaceChildren();
  if (!rows.length) { const empty = document.createElement('p'); empty.className = 'empty'; empty.textContent = '目前沒有符合規則的觀察標的。'; box.append(empty); return; }
  box.append(...rows.map(row => card(row, direction)));
}
function renderListError(id) {
  const box = $(id); box.replaceChildren();
  const empty = document.createElement('p'); empty.className = 'empty';
  empty.textContent = '資料載入失敗，暫無可執行名單。'; box.append(empty);
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

function renderBacktestPerformance(data) {
  const view = resolveBacktestPerformance(data);
  $('backtest-period').textContent = view.period;
  $('backtest-standard-label').textContent = view.labels.standard;
  $('backtest-gross-label').textContent = view.labels.gross;
  $('backtest-stress-label').textContent = view.labels.stress;
  $('backtest-standard-overall').textContent = view.standard.overall;
  $('backtest-standard-long').textContent = view.standard.long;
  $('backtest-standard-short').textContent = view.standard.short;
  $('backtest-gross-overall').textContent = view.gross.overall;
  $('backtest-gross-long').textContent = view.gross.long;
  $('backtest-gross-short').textContent = view.gross.short;
  $('backtest-stress-overall').textContent = view.stress.overall;
  $('backtest-stress-long').textContent = view.stress.long;
  $('backtest-stress-short').textContent = view.stress.short;
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
    $('summary-long').textContent = String(data.long?.length ?? 0);
    $('summary-short').textContent = String(data.short?.length ?? 0);
    const execution = data.execution_eligibility;
    const excluded = execution?.filtered_out ?? [];
    const excludedText = excluded.map(row => `${row.code}${row.name ?? ''}（${row.reason === 'sell_first_suspended' ? '暫停先賣後買' : '非當沖標的'}）`).join('、');
    $('execution-eligibility-note').textContent = execution?.enforced
      ? `官方當沖資格 ${execution.source_date}｜已排除 ${execution.filtered_count ?? excluded.length} 檔${excludedText ? `：${excludedText}` : ''}。${execution.execution_for_signal_date === 'next_session_verified' ? '已核對次一交易日資格。' : '目前為訊號日資格，次一交易日開盤前仍會重新核對。'}`
      : '官方當沖資格尚未驗證；不可把清單視為可執行當沖建議。';
    $('execution-eligibility-note').className = `execution-eligibility-note${execution?.enforced ? '' : ' status-error'}`;
    const preGate = data.pre_gate_counts ?? {};
    $('summary-long-note').textContent = Number.isFinite(preGate.long)
      ? `通過策略閘門｜基礎候選 ${preGate.long}` : '通過策略閘門';
    $('summary-short-note').textContent = Number.isFinite(preGate.short)
      ? `通過策略閘門｜基礎候選 ${preGate.short}` : '通過策略閘門';

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
    const today = resolveTodayResult(data);
    $('today-result-counts').textContent = today.counts;
    $('today-result-performance').textContent = today.performance;
    $('today-result-performance').className = today.available && daily?.today_result?.standard?.average_return < 0 ? 'metric-failure' : '';
    $('strategy-validation').textContent = today.validation;
    $('strategy-validation').className = daily?.strategy_freeze?.cost_validated === true ? 'metric-success' : 'metric-failure';
    $('today-result-dates').textContent = today.dates;
    const campaign = resolveStrategyCampaign(data);
    $('campaign-status').textContent = campaign.status;
    $('campaign-period').textContent = campaign.period;
    $('campaign-baseline').textContent = campaign.baseline;
    $('campaign-current').textContent = campaign.current;
    $('campaign-candidate').textContent = campaign.candidate;
    renderBacktestPerformance(data);
    renderV2Analysis(data);
    const warnings = [];
    if (daily?.status !== 'success') warnings.push('每日收盤後回測／優化尚未成功');
    if (health.state === 'stale') warnings.push('盤後資料已過期，07:40 備援可能尚未完成');
    $('message').textContent = warnings.length ? `注意：${warnings.join('；')}。` : '';
    renderMarket(data.global_market);
    renderList('long-list', data.long ?? [], '偏多'); renderList('short-list', data.short ?? [], '偏空');
  } catch (error) {
    const failure = resolveLoadFailure();
    $('data-health').textContent = '載入失敗';
    $('data-health').className = 'status-badge status-error';
    $('message').textContent = friendlyLoadError(error);
    $('updated-at').textContent = '尚未更新';
    $('summary-long').textContent = failure.count; $('summary-short').textContent = failure.count;
    $('summary-long-note').textContent = failure.note;
    $('summary-short-note').textContent = failure.note;
    $('execution-eligibility-note').textContent = failure.eligibility;
    $('execution-eligibility-note').className = 'execution-eligibility-note status-error';
    renderListError('long-list'); renderListError('short-list');
  }
  finally { $('refresh').disabled = false; }
}
if (typeof document !== 'undefined') {
  const refreshController = createRefreshController({ load });
  $('refresh').addEventListener('click', refreshController.manual);
  refreshController.start();
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
}
