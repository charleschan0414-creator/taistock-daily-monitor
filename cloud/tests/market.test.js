import test from 'node:test';
import assert from 'node:assert/strict';
import {
  parseTwseDay, parseTpexDay, parseTwseInstitutions, parseTpexInstitutions,
  payloadDate, evaluateCandidates, resolveGate, applyStrategyGate, candidateDates, resolveWeights,
} from '../src/market.js';
import { datesToCheck } from '../src/cron.js';

const twsePayload = { date: '20261015', tables: [{ fields: ['證券代號'], data: [
  ['2330', '台積電', '1,200', '', '', '10', '12', '9', '11'],
  ['0050', 'ETF', '9', '', '', '1', '1', '1', '1'],
]}] };
const tpexPayload = { date: '115/10/15', tables: [{ data: [
  ['6488', '環球晶', '500', '', '490', '510', '480', '', '2,000'],
]}] };

test('官方四源解析只保留四碼普通股並正規化數字', () => {
  assert.deepEqual(parseTwseDay(twsePayload)['2330'], {
    code: '2330', name: '台積電', market: '上市', volume: 1200,
    open: 10, high: 12, low: 9, close: 11,
  });
  assert.equal(parseTwseDay(twsePayload)['0050'], undefined);
  assert.equal(parseTpexDay(tpexPayload)['6488'].volume, 2000);
  assert.equal(payloadDate(tpexPayload), '20261015');

  const twseI = { data: [['2330', 'x', '-1,500']] };
  const tpexRow = Array(24).fill('0'); tpexRow[0] = '6488'; tpexRow[23] = '2,500';
  assert.equal(parseTwseInstitutions(twseI)['2330'], -1500);
  assert.equal(parseTpexInstitutions({ tables: [{ data: [tpexRow] }] })['6488'], 2500);
});

test('篩選規則為成交量前100、量增20%、MA5與法人同方向', () => {
  const rows = [
    { code: '1111', close: 12, volume: 150, prev_volume: 100, closes: [8, 9, 10, 11, 12], institutional_net: 1000, volume_rank: 1 },
    { code: '2222', close: 8, volume: 130, prev_volume: 100, closes: [12, 11, 10, 9, 8], institutional_net: -1000, volume_rank: 2 },
    { code: '3333', close: 12, volume: 119, prev_volume: 100, closes: [8, 9, 10, 11, 12], institutional_net: 1000, volume_rank: 3 },
  ];
  const got = evaluateCandidates(rows);
  assert.deepEqual(got.map(x => x.signal_direction), ['long', 'short']);
  assert.ok(got.every(x => x.risk_pct >= 1.5 && x.risk_pct <= 5));
  assert.ok(got[0].entry_trigger > got[0].close && got[0].stop_loss < got[0].entry_trigger);
  assert.ok(got[1].entry_trigger < got[1].close && got[1].stop_loss > got[1].entry_trigger);
});

test('策略閘門僅在明確啟用時套用分數、量增與空方開關', () => {
  assert.equal(resolveGate(null).enabled, false);
  assert.equal(resolveGate({ enabled: false, applied_gate: {} }).enabled, false);
  const gate = resolveGate({ enabled: true, status: 'validated', applied_gate: {
    long_score_min: 70, short_score_min: 55, short_enabled: false, volume_growth_min: 50,
  }});
  assert.equal(gate.enabled, true);
  const rows = [
    { signal_direction: 'long', score: 71, volume_growth_pct: 60 },
    { signal_direction: 'long', score: 69, volume_growth_pct: 60 },
    { signal_direction: 'short', score: 90, volume_growth_pct: 90 },
  ];
  assert.deepEqual(applyStrategyGate(rows, gate), [rows[0]]);
});

test('雲端評分使用已驗證optimizer權重，無效值回退基準', () => {
  assert.deepEqual(resolveWeights({ new_weights: {
    volume_growth_pct: .25, ma5_distance_pct: .20, institutional_lots: .45, volume_rank: .10,
  }}), { volume_growth_pct: .25, ma5_distance_pct: .20, institutional_lots: .45, volume_rank: .10 });
  assert.deepEqual(resolveWeights({ new_weights: {
    volume_growth_pct: .25, ma5_distance_pct: .20, institutional_lots: .45, volume_rank: .50,
  }}), { volume_growth_pct: .35, ma5_distance_pct: .30, institutional_lots: .25, volume_rank: .10 });
});

test('cron從台北昨天起往前產生平日日期', () => {
  assert.deepEqual(candidateDates(new Date('2026-10-19T00:35:00Z'), 4),
    ['2026-10-18', '2026-10-16', '2026-10-15', '2026-10-14']);
});

test('cron不會回補比rolling最新日更舊且終將被裁掉的日期', () => {
  assert.deepEqual(datesToCheck(new Date('2026-10-19T00:35:00Z'), ['2026-10-16']), ['2026-10-18']);
  assert.deepEqual(datesToCheck(new Date('2026-10-20T00:35:00Z'), ['2026-10-16']), ['2026-10-19']);
});
