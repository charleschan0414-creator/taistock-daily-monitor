import test from 'node:test';
import assert from 'node:assert/strict';
import { resolveV2Analysis } from '../public/app.js';

test('V2成本檢驗讀取daily_status下selected的標準成本holdout指標', () => {
  const view = resolveV2Analysis({
    daily_status: {
      v2_analysis: {
        decision: { enabled: true, status: '品質門檻通過' },
        selected: {
          long: { standard: { holdout: { trades: 42, win_rate: 0.61, average_return: 0.0123, profit_factor: 1.8 } } },
          short: { standard: { holdout: { trades: 31, win_rate: 0.52, average_return: -0.002, profit_factor: 0.91 } } },
        },
      },
    },
  });

  assert.equal(view.available, true);
  assert.equal(view.quality, '通過');
  assert.equal(view.status, '品質門檻通過');
  assert.deepEqual(view.long, { trades: 42, winRate: '61.00%', averageReturn: '1.23%', profitFactor: '1.8' });
  assert.deepEqual(view.short, { trades: 31, winRate: '52.00%', averageReturn: '-0.20%', profitFactor: '0.91' });
});

test('V2成本檢驗相容根層directions且缺少欄位顯示尚未完成', () => {
  const rootView = resolveV2Analysis({
    v2_analysis: {
      decision: { enabled: false },
      directions: {
        long: { standard_holdout: { trades: 8, win_rate: 0.5, average_return: 0, profit_factor: 1 } },
      },
    },
  });
  assert.equal(rootView.available, true);
  assert.equal(rootView.quality, '未通過');
  assert.equal(rootView.status, '尚未完成');
  assert.deepEqual(rootView.long, { trades: 8, winRate: '50.00%', averageReturn: '0.00%', profitFactor: '1' });
  assert.deepEqual(rootView.short, {
    trades: '尚未完成', winRate: '尚未完成', averageReturn: '尚未完成', profitFactor: '尚未完成',
  });

  const missingView = resolveV2Analysis({ daily_status: { status: 'success' } });
  assert.equal(missingView.available, false);
  assert.equal(missingView.quality, '尚未完成');
  assert.equal(missingView.status, '尚未完成');
});

test('V2成本檢驗讀取directions holdout standard且辨識無虧損樣本', () => {
  const view = resolveV2Analysis({
    daily_status: {
      v2_analysis: {
        directions: {
          short: { holdout: { standard: { trades: 35, win_rate: 1, average_return: 0.02, profit_factor: null } } },
        },
      },
    },
  });
  assert.deepEqual(view.short, {
    trades: 35, winRate: '100.00%', averageReturn: '2.00%', profitFactor: '無虧損樣本',
  });
});
