const $ = id => document.getElementById(id);
const fmt = value => value == null ? '—' : new Intl.NumberFormat('zh-TW', { maximumFractionDigits: 2 }).format(value);
const percent = value => Number.isFinite(value) ? `${(value * 100).toFixed(2)}%` : '—';
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

function card(row) {
  const node = document.createElement('article'); node.className = 'card';
  const head = document.createElement('div'); head.className = 'card-head';
  const title = document.createElement('div');
  const code = document.createElement('span'); code.className = 'symbol'; code.textContent = row.code;
  const name = document.createElement('span'); name.className = 'name'; name.textContent = `${row.name ?? ''} · ${row.market ?? ''}`;
  title.append(code, name);
  const score = document.createElement('span'); score.className = 'score'; score.textContent = `SCORE ${fmt(row.score)}`;
  head.append(title, score);
  const price = document.createElement('div'); price.className = 'price'; price.textContent = fmt(row.current_price ?? row.close);
  const status = document.createElement('div'); status.className = 'status';
  status.textContent = `${row.price_status}現價${row.current_price == null ? '（顯示盤後收盤）' : ''}`;
  const levels = document.createElement('div'); levels.className = 'levels';
  for (const [label, key] of [['進場', 'entry_trigger'], ['停損', 'stop_loss'], ['目標1', 'target1']]) {
    const box = document.createElement('div'); const small = document.createElement('span'); small.textContent = label;
    const strong = document.createElement('strong'); strong.textContent = fmt(row[key]); box.append(small, strong); levels.append(box);
  }
  const reason = document.createElement('p'); reason.className = 'reason'; reason.textContent = row.reason ?? '';
  node.append(head, price, status, levels, reason); return node;
}
function renderList(id, rows) {
  const box = $(id); box.replaceChildren();
  if (!rows.length) { const empty = document.createElement('p'); empty.className = 'empty'; empty.textContent = '目前沒有符合規則的觀察標的。'; box.append(empty); return; }
  box.append(...rows.map(card));
}
async function load() {
  $('refresh').disabled = true; $('message').textContent = '';
  try {
    const response = await fetch('/api/dashboard', { cache: 'no-store' }); const data = await response.json();
    if (!response.ok) throw new Error(data.error ?? `HTTP ${response.status}`);
    $('data-date').textContent = data.data_date ?? '—';
    $('updated-at').textContent = data.updated_at ? new Date(data.updated_at).toLocaleString('zh-TW') : '—';
    $('price-status').textContent = data.realtime_status === 'available' ? 'MIS 已連線（逐檔標示）' : 'MIS 無法使用，保留盤後清單';
    $('gate').textContent = data.strategy_gate?.status ?? '未啟用';
    const daily = data.daily_status;
    $('daily-status').textContent = daily?.status === 'success' ? '成功' : (daily?.status ?? '未知');
    $('data-source').textContent = data.data_source === 'github' ? 'GitHub 公開成果' : 'Cloudflare KV 備援';
    $('completed-at').textContent = daily?.completed_at ? new Date(daily.completed_at).toLocaleString('zh-TW') : '—';
    const testMetrics = daily?.strategy?.candidate?.test;
    const pf = testMetrics?.profit_factor == null ? (testMetrics ? '無虧損樣本' : '—') : fmt(testMetrics.profit_factor);
    $('test-metrics').textContent = `${percent(testMetrics?.win_rate)} / ${pf}`;
    const warnings = [];
    if (daily?.status !== 'success') warnings.push('每日收盤後回測/優化尚未成功');
    if (missedTradingDays(data.data_date) > 0) warnings.push('盤後資料已過期，07:40 備援可能尚未完成');
    $('message').textContent = warnings.length ? `注意：${warnings.join('；')}。` : '';
    renderList('long-list', data.long ?? []); renderList('short-list', data.short ?? []);
  } catch (error) { $('message').textContent = `資料載入失敗：${error.message}`; }
  finally { $('refresh').disabled = false; }
}
$('refresh').addEventListener('click', load); load();
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
