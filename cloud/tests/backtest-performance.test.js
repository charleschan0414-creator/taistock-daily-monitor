import test from 'node:test';
import assert from 'node:assert/strict';
import { resolveBacktestPerformance } from '../public/app.js';

const metric = (trades, winRate, averageReturn, profitFactor, maxDrawdown) => ({
  trades,
  win_rate: winRate,
  average_return: averageReturn,
  profit_factor: profitFactor,
  max_drawdown: maxDrawdown,
  long: { trades: trades - 2, win_rate: winRate + 0.01, average_return: averageReturn + 0.001, profit_factor: profitFactor + 0.1, max_drawdown: maxDrawdown + 0.01 },
  short: { trades: 2, win_rate: winRate - 0.02, average_return: averageReturn - 0.001, profit_factor: profitFactor - 0.1, max_drawdown: maxDrawdown - 0.01 },
});

test('100日成本後績效優先讀取daily_status三情境並格式化整體多空', () => {
  const view = resolveBacktestPerformance({ daily_status: { backtest: {
    period: { from: '2026-05-22', to: '2026-10-14' },
    metrics: metric(10, 0.55, 0.01, 1.4, -0.08),
    metrics_scenarios: {
      gross: metric(10, 0.55, 0.01, 1.4, -0.08),
      standard: metric(10, 0.5, 0.0056, 1.2, -0.1),
      stress: metric(10, 0.45, 0.0046, 1.1, -0.12),
    },
    costs: { gross: 0, standard: 0.00435, stress: 0.00535 },
  } } });

  assert.equal(view.available, true);
  assert.equal(view.legacy, false);
  assert.equal(view.period, '2026-05-22 ～ 2026-10-14');
  assert.equal(view.labels.standard, 'standard 0.435%');
  assert.equal(view.labels.stress, 'stress 0.535%');
  assert.equal(view.standard.overall, '成交 10 · 勝率 50.00% · 平均 0.56% · PF 1.2 · 最大回撤 -10.00%');
  assert.match(view.standard.long, /^成交 8/);
  assert.match(view.standard.short, /^成交 2/);
  assert.match(view.gross.overall, /勝率 55.00%/);
  assert.match(view.stress.overall, /最大回撤 -12.00%/);
});

test('100日成本後績效相容舊版gross-only與缺少回測', () => {
  const legacy = resolveBacktestPerformance({ daily_status: { backtest: {
    period: { from: '2026-01-01', to: '2026-05-01' },
    metrics: metric(7, 0.4, -0.002, 0.8, -0.2),
  } } });
  assert.equal(legacy.available, true);
  assert.equal(legacy.legacy, true);
  assert.match(legacy.gross.overall, /成交 7/);
  assert.equal(legacy.standard.overall, '舊報告未提供');
  assert.equal(legacy.stress.overall, '舊報告未提供');

  const missing = resolveBacktestPerformance({ daily_status: { status: 'success' } });
  assert.equal(missing.available, false);
  assert.equal(missing.standard.overall, '尚未完成');
});
