import { gunzipSync } from 'node:zlib';
import { readFileSync } from 'node:fs';
import {
  parseTpexDay, parseTpexInstitutions, parseTwseDay, parseTwseInstitutions, payloadDate,
} from '../src/market.js';

export const SOURCES = ['twse_quotes', 'twse_institutions', 'tpex_quotes', 'tpex_institutions'];

export function allCompleteDates(names) {
  const found = new Map();
  for (const name of names) {
    const match = name.match(/^(\d{8})_(twse_quotes|twse_institutions|tpex_quotes|tpex_institutions)\.json\.gz$/);
    if (match) (found.get(match[1]) ?? found.set(match[1], new Set()).get(match[1])).add(match[2]);
  }
  return [...found].filter(([, sources]) => SOURCES.every(s => sources.has(s))).map(([date]) => date).sort();
}

export function completeDates(names) {
  return allCompleteDates(names).slice(-6);
}

export function gateFromStrategy(value) {
  const decision = value?.decision;
  const gate = decision?.applied_gate;
  if (decision?.enabled !== true || !gate) return null;
  if (!Number.isFinite(gate.long_score_min) || !Number.isFinite(gate.short_score_min)
      || typeof gate.short_enabled !== 'boolean' || !Number.isFinite(gate.volume_growth_min)) return null;
  return { version: value.version ?? 1, enabled: true, status: String(decision.status ?? '策略閘門已啟用'),
    applied_gate: { long_score_min: gate.long_score_min, short_score_min: gate.short_score_min,
      short_enabled: gate.short_enabled, volume_growth_min: gate.volume_growth_min },
    directions: { long: true, short: gate.short_enabled }, updated_at: value.updated_at ?? null };
}

export function readPayload(path) {
  return JSON.parse(gunzipSync(readFileSync(path)).toString('utf8'));
}

export function buildDay(cacheDir, compact) {
  const payloads = Object.fromEntries(SOURCES.map(source => [source, readPayload(`${cacheDir}/${compact}_${source}.json.gz`)]));
  if (Object.values(payloads).some(payload => payloadDate(payload) !== compact)) throw new Error(`${compact} 四源日期不一致`);
  const twse = parseTwseDay(payloads.twse_quotes); const tpex = parseTpexDay(payloads.tpex_quotes);
  if (!Object.keys(twse).length || !Object.keys(tpex).length) throw new Error(`${compact} 行情資料為空`);
  return { date: `${compact.slice(0,4)}-${compact.slice(4,6)}-${compact.slice(6)}`,
    quotes: { ...twse, ...tpex }, institutions: {
      ...parseTwseInstitutions(payloads.twse_institutions), ...parseTpexInstitutions(payloads.tpex_institutions),
    }};
}
