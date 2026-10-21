import {
  candidateDates, evaluateCandidates, applyStrategyGate, parseTpexDay, parseTpexInstitutions,
  parseTwseDay, parseTwseInstitutions, payloadDate, resolveGate, resolveWeights,
} from './market.js';

const UA = 'Mozilla/5.0';
const jsonHeaders = { accept: 'application/json', 'user-agent': UA };
const isoCompact = day => day.replaceAll('-', '');

export function officialUrls(day) {
  const compact = isoCompact(day); const slash = day.replaceAll('-', '/');
  return {
    twse_quotes: `https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date=${compact}&type=ALLBUT0999&response=json`,
    twse_institutions: `https://www.twse.com.tw/rwd/zh/fund/T86?date=${compact}&selectType=ALLBUT0999&response=json`,
    tpex_quotes: `https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date=${encodeURIComponent(slash)}&type=EW&response=json`,
    tpex_institutions: `https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade?type=Daily&sect=EW&date=${encodeURIComponent(slash)}&id=&response=json`,
  };
}

async function fetchJson(fetcher, url) {
  const response = await fetcher(url, { headers: jsonHeaders });
  if (!response.ok) throw new Error(`官方來源 HTTP ${response.status}`);
  return response.json();
}

export async function fetchOfficialDay(day, fetcher = fetch) {
  const urls = officialUrls(day);
  const entries = await Promise.all(Object.entries(urls).map(async ([name, url]) => [name, await fetchJson(fetcher, url)]));
  const payloads = Object.fromEntries(entries); const compact = isoCompact(day);
  if (Object.values(payloads).some(payload => payloadDate(payload) !== compact)) return null;
  const twse = parseTwseDay(payloads.twse_quotes); const tpex = parseTpexDay(payloads.tpex_quotes);
  if (!Object.keys(twse).length || !Object.keys(tpex).length) return null;
  return { date: day, quotes: { ...twse, ...tpex }, institutions: {
    ...parseTwseInstitutions(payloads.twse_institutions), ...parseTpexInstitutions(payloads.tpex_institutions),
  }};
}

export function buildLatest(days, rawGate = null, updatedAt = new Date().toISOString(), rawWeights = null) {
  const ordered = [...days].sort((a, b) => a.date.localeCompare(b.date));
  if (ordered.length < 6) throw new Error('至少需要6個完整交易日');
  const recent = ordered.slice(-6); const current = recent.at(-1);
  const leaders = Object.values(current.quotes).sort((a, b) => b.volume - a.volume || a.code.localeCompare(b.code)).slice(0, 100);
  const rows = [];
  for (let index = 0; index < leaders.length; index++) {
    const quote = leaders[index]; const history = recent.slice(-5).map(day => day.quotes[quote.code]);
    const previous = recent.at(-2).quotes[quote.code];
    if (history.some(x => !x) || !previous) continue;
    rows.push({ ...quote, date: current.date, prev_volume: previous.volume, closes: history.map(x => x.close),
      institutional_net: current.institutions[quote.code] ?? 0, volume_rank: index + 1 });
  }
  const gate = resolveGate(rawGate); const weights = resolveWeights(rawWeights);
  const qualified = applyStrategyGate(evaluateCandidates(rows, weights), gate);
  const long = qualified.filter(x => x.signal_direction === 'long');
  const short = qualified.filter(x => x.signal_direction === 'short');
  return { data_date: current.date, updated_at: updatedAt, top100_count: leaders.length,
    history_success_count: rows.length, qualified_count: qualified.length, qualified,
    top10: long.slice(0, 10), long_top10: long.slice(0, 10), short_top10: short.slice(0, 10),
    signals: [...long.slice(0, 10), ...short.slice(0, 10)], long_count: long.length, short_count: short.length,
    strategy_gate: gate, scoring_weights: weights,
    execution_eligibility: { version: 'official-day-trade-v1', enforced: false, source_date: null,
      filtered_count: null, filtered_out: [], notice: 'Cloudflare備援未驗證官方當沖資格，不提供可執行名單。' },
    sources: ['TWSE MI_INDEX', 'TWSE T86', 'TPEx dailyQuotes', 'TPEx dailyTrade'] };
}

function csvCell(value) {
  const text = String(value ?? ''); return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}
export function latestCsv(latest) {
  const fields = ['date','code','name','market','signal_direction','close','ma5','entry_trigger','stop_loss','target1','target2','risk_pct','score','reason'];
  const lines = [fields.join(',')];
  const signals = latest.execution_eligibility?.enforced === true
    && (latest.signals ?? []).every(row => row?.day_trading_eligibility?.eligible === true)
    ? latest.signals ?? [] : [];
  for (const row of signals) lines.push(fields.map(k => csvCell(k === 'date' ? row.date ?? latest.data_date : row[k])).join(','));
  return `${lines.join('\n')}\n`;
}

export function datesToCheck(now, knownDates = []) {
  const newest = [...knownDates].sort().at(-1);
  const candidates = candidateDates(now, 14);
  return newest ? candidates.filter(day => day > newest) : candidates;
}

export async function updateRolling(env, now = new Date(), fetcher = fetch) {
  const stored = await env.DATA.get('rolling', 'json'); const days = Array.isArray(stored) ? stored : [];
  const known = new Set(days.map(x => x.date)); let added = null; const errors = [];
  for (const day of datesToCheck(now, [...known])) {
    if (known.has(day)) continue;
    try {
      const result = await fetchOfficialDay(day, fetcher);
      if (result) { days.push(result); added = day; break; }
    } catch (error) { errors.push(`${day}: ${error.message}`); }
  }
  days.sort((a, b) => a.date.localeCompare(b.date)); const rolling = days.slice(-6);
  if (added) await env.DATA.put('rolling', JSON.stringify(rolling));
  if (rolling.length >= 6 && added) {
    const gate = await env.DATA.get('strategy:gate', 'json');
    const weights = await env.DATA.get('strategy:weights', 'json');
    const latest = buildLatest(rolling, gate, new Date().toISOString(), weights);
    await env.DATA.put('latest', JSON.stringify(latest));
    await env.DATA.put('download:latest.csv', latestCsv(latest));
  }
  const status = { ran_at: now.toISOString(), added, retained_dates: rolling.map(x => x.date), errors };
  await env.DATA.put('cron:last', JSON.stringify(status));
  return status;
}
