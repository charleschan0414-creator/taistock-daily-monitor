export const BASELINE_WEIGHTS = Object.freeze({
  volume_growth_pct: 0.35,
  ma5_distance_pct: 0.30,
  institutional_lots: 0.25,
  volume_rank: 0.10,
});

export function resolveWeights(raw) {
  const candidate = raw?.new_weights ?? raw;
  const keys = Object.keys(BASELINE_WEIGHTS);
  const valid = candidate && keys.every(key => Number.isFinite(candidate[key]) && candidate[key] >= 0)
    && Math.abs(keys.reduce((sum, key) => sum + candidate[key], 0) - 1) < 1e-9;
  return valid ? Object.fromEntries(keys.map(key => [key, candidate[key]])) : { ...BASELINE_WEIGHTS };
}

export function number(value) {
  if (typeof value === 'number') return Number.isFinite(value) ? value : 0;
  const cleaned = String(value ?? '').replaceAll(',', '').replace(/[＋+]/g, '').replace(/[−－]/g, '-').trim();
  const parsed = Number(cleaned);
  return Number.isFinite(parsed) ? parsed : 0;
}

export function isCommonStock(code) {
  return /^\d{4}$/.test(String(code)) && !String(code).startsWith('00');
}

function securityTable(payload) {
  return (payload?.tables ?? []).find(t => t?.fields?.[0] === '證券代號') ?? {};
}

export function parseTwseDay(payload) {
  const result = {};
  for (const row of securityTable(payload).data ?? []) {
    const code = String(row?.[0] ?? '').trim();
    if (!isCommonStock(code) || row.length < 9 || number(row[8]) <= 0) continue;
    result[code] = { code, name: String(row[1]).trim(), market: '上市', volume: Math.trunc(number(row[2])),
      open: number(row[5]), high: number(row[6]), low: number(row[7]), close: number(row[8]) };
  }
  return result;
}

export function parseTpexDay(payload) {
  const result = {};
  for (const row of payload?.tables?.[0]?.data ?? []) {
    const code = String(row?.[0] ?? '').trim();
    if (!isCommonStock(code) || row.length < 9 || number(row[2]) <= 0) continue;
    result[code] = { code, name: String(row[1]).trim(), market: '上櫃', volume: Math.trunc(number(row[8])),
      open: number(row[4]), high: number(row[5]), low: number(row[6]), close: number(row[2]) };
  }
  return result;
}

export function parseTwseInstitutions(payload) {
  return Object.fromEntries((payload?.data ?? []).filter(r => r?.length && isCommonStock(String(r[0]).trim()))
    .map(r => [String(r[0]).trim(), Math.trunc(number(r.at(-1)))]));
}

export function parseTpexInstitutions(payload) {
  return Object.fromEntries((payload?.tables?.[0]?.data ?? []).filter(r => r?.length > 23 && isCommonStock(String(r[0]).trim()))
    .map(r => [String(r[0]).trim(), Math.trunc(number(r[23]))]));
}

export function payloadDate(payload) {
  let value = String(payload?.date ?? '');
  if (!value) value = String((payload?.tables ?? []).find(t => t?.date)?.date ?? '');
  let compact = value.replaceAll('/', '').replaceAll('-', '');
  if (/^\d{7}$/.test(compact)) compact = String(Number(compact.slice(0, 3)) + 1911) + compact.slice(3);
  return compact.slice(0, 8);
}

export const BASELINE_GATE = Object.freeze({
  long_score_min: 0, short_score_min: 0, short_enabled: true, volume_growth_min: 20,
});

export function resolveGate(raw) {
  const gate = raw?.applied_gate;
  const valid = raw?.enabled === true && gate
    && Number.isFinite(gate.long_score_min) && Number.isFinite(gate.short_score_min)
    && typeof gate.short_enabled === 'boolean' && Number.isFinite(gate.volume_growth_min);
  return valid
    ? { enabled: true, status: String(raw.status ?? '已啟用'), applied_gate: { ...gate },
        version: raw.version ?? 1, updated_at: raw.updated_at ?? null }
    : { enabled: false, status: '未啟用｜保留基準訊號', applied_gate: { ...BASELINE_GATE },
        version: raw?.version ?? 1, updated_at: raw?.updated_at ?? null };
}

export function applyStrategyGate(rows, state) {
  const resolved = resolveGate(state);
  if (!resolved.enabled) return [...rows];
  const gate = resolved.applied_gate;
  return rows.filter(row => {
    const direction = row.signal_direction ?? row.direction;
    if (direction === 'short' && !gate.short_enabled) return false;
    const minimum = direction === 'long' ? gate.long_score_min : gate.short_score_min;
    return Number(row.score ?? 0) >= minimum && Number(row.volume_growth_pct ?? 0) >= gate.volume_growth_min;
  });
}

function tick(value) {
  if (value < 10) return .01;
  if (value < 50) return .05;
  if (value < 100) return .1;
  if (value < 500) return .5;
  if (value < 1000) return 1;
  return 5;
}
export function roundTwPrice(value) {
  const unit = tick(value);
  return Number((Math.floor(value / unit + .5 + Number.EPSILON) * unit).toFixed(2));
}
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

function tradeLevels(item, direction) {
  const closes = item.closes.slice(-5);
  const returns = closes.slice(1).map((v, i) => closes[i] > 0 ? Math.abs(v / closes[i] - 1) : 0);
  const volatility = returns.length ? returns.reduce((a, b) => a + b, 0) / returns.length * 100 : 0;
  const risk = Number(clamp(volatility, 1.5, 5).toFixed(2));
  const offset = Number(clamp(volatility / 3, .3, 1).toFixed(2));
  let entry, stop, target1, target2;
  if (direction === 'long') {
    entry = roundTwPrice(item.close * (1 + offset / 100));
    stop = roundTwPrice(entry * (1 - risk / 100));
    const r = entry - stop;
    target1 = roundTwPrice(entry + 1.5 * r); target2 = roundTwPrice(entry + 2 * r);
  } else {
    entry = roundTwPrice(item.close * (1 - offset / 100));
    stop = roundTwPrice(entry * (1 + risk / 100));
    const r = stop - entry;
    target1 = roundTwPrice(entry - 1.5 * r); target2 = roundTwPrice(entry - 2 * r);
  }
  return { signal_direction: direction,
    action_text: direction === 'long' ? '偏多規則式研究觀察：突破觸發價才觀察；非投資建議或獲利保證' : '偏空規則式研究觀察：跌破觸發價才觀察；非投資建議或獲利保證',
    entry_trigger: entry, stop_loss: stop, target1, target2, risk_pct: risk,
    entry_offset_pct: offset, volatility_proxy_pct: Number(volatility.toFixed(2)) };
}

export function evaluateCandidates(rows, rawWeights = BASELINE_WEIGHTS) {
  const weights = resolveWeights(rawWeights);
  const groups = { long: [], short: [] };
  for (const source of rows) {
    if ((source.closes?.length ?? 0) < 5 || source.prev_volume <= 0 || source.volume_rank > 100) continue;
    const ma5 = source.closes.slice(-5).reduce((a, b) => a + b, 0) / 5;
    const growth = (source.volume / source.prev_volume - 1) * 100;
    if (growth < 20) continue;
    const direction = source.close > ma5 && source.institutional_net > 0 ? 'long'
      : source.close < ma5 && source.institutional_net < 0 ? 'short' : null;
    if (!direction) continue;
    const distance = Math.abs((source.close / ma5 - 1) * 100);
    const item = { ...source, ma5: Number(ma5.toFixed(2)), volume_growth_pct: Number(growth.toFixed(2)),
      above_ma5_pct: Number((direction === 'long' ? distance : -distance).toFixed(2)),
      ma5_distance_pct: Number(distance.toFixed(2)),
      institutional_lots: Number((source.institutional_net / 1000).toFixed(2)),
      institutional_abs_lots: Number((Math.abs(source.institutional_net) / 1000).toFixed(2)) };
    Object.assign(item, tradeLevels(item, direction)); groups[direction].push(item);
  }
  for (const [direction, items] of Object.entries(groups)) {
    const percentile = (value, key) => items.filter(x => x[key] <= value).length / items.length * 100;
    for (const item of items) {
      item.score = Number((weights.volume_growth_pct * percentile(item.volume_growth_pct, 'volume_growth_pct')
        + weights.ma5_distance_pct * percentile(item.ma5_distance_pct, 'ma5_distance_pct')
        + weights.institutional_lots * percentile(item.institutional_abs_lots, 'institutional_abs_lots')
        + weights.volume_rank * Math.max(0, 101 - item.volume_rank)).toFixed(2));
      const side = direction === 'long' ? '高於' : '低於'; const flow = direction === 'long' ? '買超' : '賣超';
      item.reason = `量增${item.volume_growth_pct.toFixed(1)}%；收盤${side}MA5 ${item.ma5_distance_pct.toFixed(1)}%；法人${flow}${item.institutional_abs_lots.toFixed(1)}張；5日波動代理${item.volatility_proxy_pct.toFixed(2)}%`;
    }
    items.sort((a, b) => b.score - a.score || a.volume_rank - b.volume_rank || a.code.localeCompare(b.code));
  }
  return [...groups.long, ...groups.short];
}

export function candidateDates(now = new Date(), count = 14) {
  const taipei = new Date(now.getTime() + 8 * 3600_000);
  taipei.setUTCDate(taipei.getUTCDate() - 1);
  const out = [];
  while (out.length < count) {
    const weekday = taipei.getUTCDay();
    if (out.length === 0 || (weekday !== 0 && weekday !== 6)) out.push(taipei.toISOString().slice(0, 10));
    taipei.setUTCDate(taipei.getUTCDate() - 1);
  }
  return out;
}
