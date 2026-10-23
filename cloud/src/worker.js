import { updateRolling } from './cron.js';
import { fetchTaifexMarket } from './taifex.js';

const DOWNLOADS = new Set(['latest.csv', 'backtest_100d.csv']);
const RAW_BASE = 'https://raw.githubusercontent.com/charleschan0414-creator/taistock-daily-monitor/main/public_data/';
const RELEASE_MANIFEST = 'https://raw.githubusercontent.com/charleschan0414-creator/taistock-daily-monitor/main/software_manifest.json';
const securityHeaders = {
  'x-content-type-options': 'nosniff',
  'referrer-policy': 'same-origin',
  'content-security-policy': "default-src 'self'; connect-src 'self'; img-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; manifest-src 'self'",
};
const json = (body, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { ...securityHeaders, 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' },
});

function misChannel(row) { return `${row.market === '上櫃' ? 'otc' : 'tse'}_${row.code}.tw`; }
function priceOf(item) {
  const current = Number(item?.z); if (Number.isFinite(current) && current > 0) return { price: current, realtime: true };
  const fallback = Number(item?.y); return { price: Number.isFinite(fallback) && fallback > 0 ? fallback : null, realtime: false };
}

function rawUrl(name) {
  const bucket = Math.floor(Date.now() / 300000);
  return `${RAW_BASE}${name}?v=${bucket}`;
}
async function githubJson(name, fetcher) {
  const response = await fetcher(rawUrl(name), { headers: { accept: 'application/json' }, cf: { cacheTtl: 300, cacheEverything: true } });
  if (!response.ok) throw new Error(`GitHub HTTP ${response.status}`);
  return response.json();
}
async function desktopReleaseVersion(fetcher, dailyStatus) {
  try {
    const bucket = Math.floor(Date.now() / 300000);
    const response = await fetcher(`${RELEASE_MANIFEST}?v=${bucket}`, {
      headers: { accept: 'application/json' }, cf: { cacheTtl: 300, cacheEverything: true },
    });
    if (!response.ok) throw new Error(`GitHub HTTP ${response.status}`);
    const manifest = await response.json();
    if (typeof manifest?.version !== 'string' || !/^\d{4}\.\d{2}\.\d{2}\.\d+$/.test(manifest.version)) {
      throw new Error('Invalid software manifest version');
    }
    return manifest.version;
  } catch {
    return dailyStatus?.health?.desktop_version ?? null;
  }
}
async function latestData(env, fetcher) {
  try {
    const value = await githubJson('latest.json', fetcher);
    if (!value || typeof value.data_date !== 'string' || !Array.isArray(value.long_top10 ?? value.top10)) throw new Error('Invalid GitHub latest');
    return { value, source: 'github' };
  } catch {
    return { value: await env.DATA.get('latest', 'json'), source: 'kv' };
  }
}

function taipeiDateOfTimestamp(value) {
  if (typeof value !== 'string'
      || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value)) return null;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return null;
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-US', { timeZone: 'Asia/Taipei',
    year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(parsed)
    .filter(part => part.type !== 'literal').map(part => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day}`;
}

function pausedSession(latest) {
  const session = latest?.market_session;
  const execution = latest?.execution_eligibility;
  const iso = value => {
    if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
    const parsed = new Date(`${value}T00:00:00Z`);
    return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
  };
  if (session?.status !== 'paused_no_new_official_session'
      || execution?.execution_for_signal_date !== 'paused_no_new_official_session'
      || execution?.enforced !== true || !iso(latest?.data_date)
      || !iso(session.requested_date) || session.requested_date <= latest.data_date
      || session.latest_official_date !== latest.data_date
      || execution.requested_date !== session.requested_date
      || execution.source_date !== latest.data_date) return null;
  const closureSources = {
    weekend: 'calendar',
    twse_holiday: 'https://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule',
    taipei_disaster_stop_work: 'https://www.dgpa.gov.tw/typh/daily/nds.html',
  };
  const hasClosure = session.closure_reason !== undefined || session.closure_source !== undefined;
  if (hasClosure && closureSources[session.closure_reason] !== session.closure_source) return null;
  const collection = latest?.data_collection;
  if (hasClosure && (collection?.latest_official_quote_date !== latest.data_date
      || taipeiDateOfTimestamp(collection?.session_checked_at) !== session.requested_date)) return null;
  return { status: session.status, requested_date: session.requested_date,
    latest_official_date: session.latest_official_date,
    ...(hasClosure ? { closure_reason: session.closure_reason,
      closure_source: session.closure_source } : {}),
    notice: typeof session.notice === 'string' ? session.notice : undefined };
}

function executableLists(latest) {
  const eligibility = latest?.execution_eligibility;
  const long = latest?.long_top10 ?? latest?.top10;
  const short = latest?.short_top10;
  const sourceDate = eligibility?.source_date;
  const parsedDate = typeof sourceDate === 'string' ? new Date(`${sourceDate}T00:00:00Z`) : null;
  const validDate = typeof sourceDate === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(sourceDate)
    && !Number.isNaN(parsedDate?.getTime()) && parsedDate.toISOString().slice(0, 10) === sourceDate
    && sourceDate >= latest.data_date;
  const rows = Array.isArray(long) && Array.isArray(short) ? [...long, ...short] : [];
  const paused = pausedSession(latest);
  const pausedReason = paused?.closure_reason ? 'market_closed' : 'no_new_official_session';
  const pausedRows = paused && rows.every(row => row?.day_trading_eligibility?.eligible === false
    && row.day_trading_eligibility.source_date === latest.data_date
    && row.day_trading_eligibility.reason === pausedReason);
  if (pausedRows) return { long: [], short: [], eligibility };
  const verified = eligibility?.enforced === true && validDate
    && Array.isArray(eligibility.filtered_out)
    && rows.every(row => row?.day_trading_eligibility?.eligible === true
      && row.day_trading_eligibility.source_date === sourceDate);
  if (verified) return { long: long.slice(0, 10), short: short.slice(0, 10), eligibility };
  return { long: [], short: [], eligibility: {
    version: 'official-day-trade-v1', enforced: false, source_date: sourceDate ?? null,
    filtered_count: null, filtered_out: [],
    notice: '官方當沖資格未驗證，不顯示可執行名單。',
  } };
}

function csvCell(value) {
  const text = String(value ?? '');
  return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

function executableCsv(latest) {
  const executable = executableLists(latest);
  if (executable.eligibility.enforced !== true) return null;
  const fields = ['date','code','name','market','signal_direction','close','ma5','entry_trigger','stop_loss','target1','target2','risk_pct','score','reason'];
  const lines = [fields.join(',')];
  for (const row of [...executable.long, ...executable.short]) {
    lines.push(fields.map(key => csvCell(key === 'date' ? row.date ?? latest.data_date : row[key])).join(','));
  }
  return `${lines.join('\n')}\n`;
}

export async function dashboard(env, fetcher = fetch) {
  const loaded = await latestData(env, fetcher);
  const latest = loaded.value;
  if (!latest) return json({ error: '尚未初始化，請先執行 npm run seed' }, 503);
  let dailyStatus;
  try { dailyStatus = await githubJson('daily_status.json', fetcher); }
  catch { dailyStatus = await env.DATA.get('cron:last', 'json'); }
  const executable = executableLists(latest);
  const marketSession = pausedSession(latest);
  const long = executable.long;
  const short = executable.short; const rows = [...long, ...short];
  const prices = new Map(); let status = 'unavailable';
  if (rows.length) {
    try {
      const exCh = rows.map(misChannel).join('|');
      const url = `https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=${encodeURIComponent(exCh)}&json=1&delay=0`;
      const response = await fetcher(url, { headers: { accept: 'application/json', 'user-agent': 'Mozilla/5.0' } });
      if (!response.ok) throw new Error(`MIS HTTP ${response.status}`);
      const payload = await response.json();
      for (const item of payload.msgArray ?? []) prices.set(String(item.c), priceOf(item));
      status = prices.size ? 'available' : 'unavailable';
    } catch { status = 'unavailable'; }
  }
  const merge = row => ({ ...row, current_price: prices.get(row.code)?.price ?? null,
    realtime: prices.get(row.code)?.realtime ?? false,
    price_status: prices.get(row.code)?.realtime ? '即時' : '非即時' });
  let globalMarket;
  try { globalMarket = await fetchTaifexMarket(fetcher); }
  catch { globalMarket = { status: 'unavailable', fetched_at: null, notice: '期交所行情，可能延遲', items: [],
    risk: { status: '中性', rule: 'TAIFEX 資料無法取得，維持中性' } }; }
  return json({ data_date: latest.data_date, updated_at: latest.updated_at, data_source: loaded.source,
    market_session: marketSession,
    data_collection: marketSession?.closure_reason ? {
      latest_official_quote_date: latest.data_collection.latest_official_quote_date,
      session_checked_at: latest.data_collection.session_checked_at,
    } : null,
    daily_status: dailyStatus ?? null, realtime_status: status, global_market: globalMarket,
    strategy_gate: latest.strategy_gate ?? { enabled: false, status: '未提供策略閘門' },
    pre_gate_counts: latest.pre_gate_counts ?? null,
    filtered_out_counts: latest.filtered_out_counts ?? null,
    execution_eligibility: executable.eligibility,
    long: long.map(merge), short: short.map(merge), disclaimer: '規則式研究觀察，非投資建議、獲利保證或下單服務。' });
}

export function createWorker(defaultFetcher = fetch) {
  return {
    async fetch(request, env) {
      const url = new URL(request.url);
      if (request.method !== 'GET' && request.method !== 'HEAD') return json({ error: 'Method Not Allowed' }, 405);
      if (url.pathname === '/api/dashboard') return dashboard(env, env.fetcher ?? defaultFetcher);
      if (url.pathname === '/api/health') {
        const fetcher = env.fetcher ?? defaultFetcher;
        const loaded = await latestData(env, fetcher);
        let dailyStatus;
        try { dailyStatus = await githubJson('daily_status.json', fetcher); }
        catch { dailyStatus = await env.DATA.get('cron:last', 'json'); }
        const desktopVersion = await desktopReleaseVersion(fetcher, dailyStatus);
        return json({ ok: Boolean(loaded.value), data_source: loaded.source,
          data_date: loaded.value?.data_date ?? null, updated_at: loaded.value?.updated_at ?? null,
          desktop_version: desktopVersion,
          execution_eligibility: loaded.value ? executableLists(loaded.value).eligibility : null,
          daily_status: dailyStatus ?? null }, loaded.value ? 200 : 503);
      }
      if (url.pathname.startsWith('/download/')) {
        let name; try { name = decodeURIComponent(url.pathname.slice('/download/'.length)); } catch { return json({ error: 'Not Found' }, 404); }
        if (!DOWNLOADS.has(name)) return json({ error: 'Not Found' }, 404);
        if (name === 'latest.csv') {
          const loaded = await latestData(env, env.fetcher ?? defaultFetcher);
          const value = loaded.value ? executableCsv(loaded.value) : null;
          if (value == null) return json({ error: '官方當沖資格未驗證，拒絕提供可執行名單。' }, 503);
          return new Response(request.method === 'HEAD' ? null : value, { headers: { ...securityHeaders,
            'content-type': 'text/csv; charset=utf-8', 'content-disposition': 'attachment; filename="latest.csv"',
            'cache-control': 'no-store' } });
        }
        let value = null;
        try {
          const remote = await (env.fetcher ?? defaultFetcher)(rawUrl(name), { headers: { accept: 'text/csv' }, cf: { cacheTtl: 300, cacheEverything: true } });
          if (!remote.ok) throw new Error(`GitHub HTTP ${remote.status}`);
          value = await remote.text();
        } catch { value = await env.DATA.get(`download:${name}`); }
        if (value == null) return json({ error: 'Not Found' }, 404);
        return new Response(request.method === 'HEAD' ? null : value, { headers: { ...securityHeaders,
          'content-type': 'text/csv; charset=utf-8', 'content-disposition': `attachment; filename="${name}"`,
          'cache-control': 'public, max-age=300' } });
      }
      const asset = await env.ASSETS.fetch(request); const headers = new Headers(asset.headers);
      for (const [key, value] of Object.entries(securityHeaders)) headers.set(key, value);
      return new Response(request.method === 'HEAD' ? null : asset.body, { status: asset.status, statusText: asset.statusText, headers });
    },
    async scheduled(controller, env, ctx) {
      ctx.waitUntil(updateRolling(env, new Date(controller.scheduledTime), env.fetcher ?? defaultFetcher));
    },
  };
}

export default createWorker();
