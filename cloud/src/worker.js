import { updateRolling } from './cron.js';

const DOWNLOADS = new Set(['latest.csv', 'backtest_100d.csv']);
const RAW_BASE = 'https://raw.githubusercontent.com/charleschan0414-creator/taistock-daily-monitor/main/public_data/';
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
async function latestData(env, fetcher) {
  try {
    const value = await githubJson('latest.json', fetcher);
    if (!value || typeof value.data_date !== 'string' || !Array.isArray(value.long_top10 ?? value.top10)) throw new Error('Invalid GitHub latest');
    return { value, source: 'github' };
  } catch {
    return { value: await env.DATA.get('latest', 'json'), source: 'kv' };
  }
}

export async function dashboard(env, fetcher = fetch) {
  const loaded = await latestData(env, fetcher);
  const latest = loaded.value;
  if (!latest) return json({ error: '尚未初始化，請先執行 npm run seed' }, 503);
  let dailyStatus;
  try { dailyStatus = await githubJson('daily_status.json', fetcher); }
  catch { dailyStatus = await env.DATA.get('cron:last', 'json'); }
  const long = (latest.long_top10 ?? latest.top10 ?? []).slice(0, 10);
  const short = (latest.short_top10 ?? []).slice(0, 10); const rows = [...long, ...short];
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
  return json({ data_date: latest.data_date, updated_at: latest.updated_at, data_source: loaded.source,
    daily_status: dailyStatus ?? null, realtime_status: status,
    strategy_gate: latest.strategy_gate ?? { enabled: false, status: '未提供策略閘門' },
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
        return json({ ok: Boolean(loaded.value), data_source: loaded.source,
          data_date: loaded.value?.data_date ?? null, updated_at: loaded.value?.updated_at ?? null,
          daily_status: dailyStatus ?? null }, loaded.value ? 200 : 503);
      }
      if (url.pathname.startsWith('/download/')) {
        let name; try { name = decodeURIComponent(url.pathname.slice('/download/'.length)); } catch { return json({ error: 'Not Found' }, 404); }
        if (!DOWNLOADS.has(name)) return json({ error: 'Not Found' }, 404);
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
