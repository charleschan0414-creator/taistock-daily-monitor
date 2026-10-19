import test from 'node:test';
import assert from 'node:assert/strict';
import { resolveTodayResult } from '../public/app.js';

test('今日結算清楚顯示觸發、成本後績效與未通過驗證', () => {
  const view = resolveTodayResult({ daily_status: {
    today_result: {
      status: 'complete', signal_date: '2026-10-16', trade_date: '2026-10-19',
      signals: 5, triggered: 4, untriggered: 1,
      standard: { cost_rate: 0.00435, average_return: -0.0096846, profit_factor: 0.716 },
    },
    strategy_freeze: { cost_validated: false },
  } });
  assert.equal(view.available, true);
  assert.equal(view.counts, '5 筆訊號｜4 已觸發｜1 未觸發');
  assert.match(view.performance, /平均 -0.97%/);
  assert.match(view.performance, /PF 0.716/);
  assert.equal(view.validation, '未通過成本驗證｜僅供研究');
  assert.equal(view.dates, '訊號日 2026-10-16｜結算日 2026-10-19');
});

test('缺少今日結算時不顯示零績效', () => {
  const view = resolveTodayResult({ daily_status: { status: 'success' } });
  assert.equal(view.available, false);
  assert.equal(view.performance, '尚未完成');
});
