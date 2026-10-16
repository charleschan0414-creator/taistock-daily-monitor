const CODES = ['TX', 'ZEF', 'CDF', 'UNF', 'SXF'];
const NAMES = { TX: '臺股期貨', ZEF: '小型電子期貨', CDF: '台積電期貨', UNF: '那斯達克100期貨', SXF: '費城半導體期貨' };
const TAIFEX_URL = 'https://www.taifex.com.tw/cht/quotesApi/getQuotes?objId=12';

function numeric(value) {
  const cleaned = String(value ?? '').replaceAll(',', '').trim();
  if (!cleaned) return null;
  const parsed = Number(cleaned);
  return Number.isFinite(parsed) ? parsed : null;
}

export function marketRisk(rows) {
  const signals = ['TX', 'UNF', 'SXF'].map(code => rows.find(row => row.code === code)?.change);
  if (signals.some(value => !Number.isFinite(value))) {
    return { status: '中性', rule: 'TX／UNF／SXF 資料不足，維持中性' };
  }
  if (signals.filter(value => value > 0).length >= 2) {
    return { status: '風險偏多', rule: 'TX／UNF／SXF 三者中至少二項上漲' };
  }
  if (signals.filter(value => value < 0).length >= 2) {
    return { status: '風險偏空', rule: 'TX／UNF／SXF 三者中至少二項下跌' };
  }
  return { status: '中性', rule: 'TX／UNF／SXF 未有至少二項同向' };
}

export function parseTaifexQuotes(payload) {
  if (!Array.isArray(payload)) return [];
  const rows = [];
  for (const code of CODES) {
    const item = payload.find(row => String(row?.contract ?? '').startsWith(code));
    const price = numeric(item?.price);
    const change = numeric(item?.updown);
    const volume = numeric(item?.ttlvol);
    if (!item) continue;
    const previous = price != null && change != null ? price - change : null;
    rows.push({
      code,
      contract: String(item.contract),
      name: NAMES[code],
      price,
      change,
      change_pct: previous && change != null ? Number((change / previous * 100).toFixed(4)) : null,
      volume,
    });
  }
  return rows;
}

export async function fetchTaifexMarket(fetcher = fetch, now = () => new Date()) {
  const response = await fetcher(TAIFEX_URL, {
    headers: { accept: 'application/json' },
    cf: { cacheTtl: 60, cacheEverything: true },
  });
  if (!response.ok) throw new Error(`TAIFEX HTTP ${response.status}`);
  const items = parseTaifexQuotes(await response.json());
  if (!items.length) throw new Error('Invalid TAIFEX payload');
  return {
    status: 'available',
    fetched_at: now().toISOString(),
    notice: '期交所行情，可能延遲',
    items,
    risk: marketRisk(items),
  };
}
