import test from 'node:test';
import assert from 'node:assert/strict';
import { createRefreshController } from '../public/refresh.js';

test('60秒刷新只在頁面可見時排程且不重疊請求', async () => {
  let visibilityHandler;
  const documentRef = {
    hidden: false,
    addEventListener(type, handler) { if (type === 'visibilitychange') visibilityHandler = handler; },
    removeEventListener() {},
  };
  let intervalCallback; let intervalMs; let cleared = 0; let loads = 0; let release;
  const load = () => { loads += 1; return new Promise(resolve => { release = resolve; }); };
  const controller = createRefreshController({
    documentRef, load,
    setIntervalFn(callback, ms) { intervalCallback = callback; intervalMs = ms; return 7; },
    clearIntervalFn() { cleared += 1; },
  });

  controller.start();
  assert.equal(intervalMs, 60_000);
  intervalCallback();
  assert.equal(loads, 1);
  release(); await Promise.resolve();
  documentRef.hidden = true; visibilityHandler();
  assert.equal(cleared, 1);
  documentRef.hidden = false; visibilityHandler();
  assert.equal(intervalMs, 60_000);
});
