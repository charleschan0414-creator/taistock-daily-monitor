import test from 'node:test';
import assert from 'node:assert/strict';
import { allCompleteDates, completeDates, gateFromStrategy } from '../scripts/seed-lib.mjs';

test('seed可由完整日期往前回退以略過日期不一致資料', () => {
  const names = [];
  for (const date of ['20261001','20261002','20261005','20261006','20261007','20261008','20261009']) {
    for (const source of ['twse_quotes','twse_institutions','tpex_quotes','tpex_institutions']) names.push(`${date}_${source}.json.gz`);
  }
  assert.equal(allCompleteDates(names).length, 7);
});

test('seed只選最近六個四源齊全日期', () => {
  const names = [];
  for (const date of ['20261001','20261002','20261005','20261006','20261007','20261008','20261009']) {
    for (const source of ['twse_quotes','twse_institutions','tpex_quotes','tpex_institutions']) names.push(`${date}_${source}.json.gz`);
  }
  names.pop();
  assert.deepEqual(completeDates(names), ['20261001','20261002','20261005','20261006','20261007','20261008']);
});

test('seed策略檔必須decision.enabled true才上傳gate', () => {
  assert.equal(gateFromStrategy({ decision: { enabled: false, applied_gate: {} } }), null);
  const gate = gateFromStrategy({ version: 1, updated_at: '2026-10-16', decision: { enabled: true, status: 'ok', applied_gate: {
    long_score_min: 70, short_score_min: 0, short_enabled: false, volume_growth_min: 20,
  }}});
  assert.equal(gate.enabled, true);
  assert.equal(gate.applied_gate.short_enabled, false);
});
