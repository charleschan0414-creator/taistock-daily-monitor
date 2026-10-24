import test from 'node:test';
import assert from 'node:assert/strict';
import { createWorker } from '../src/worker.js';

class KV {
  constructor(values = {}) { this.values = new Map(Object.entries(values)); }
  async get(key, type) { const value = this.values.get(key) ?? null; return type === 'json' && value ? JSON.parse(value) : value; }
  async put(key, value) { this.values.set(key, value); }
  async delete(key) { this.values.delete(key); }
}
const latest = {
  data_date: '2026-10-15', updated_at: '2026-10-15T09:00:00Z',
  long_top10: [{ code: '2330', market: '上市', close: 100, day_trading_eligibility: { eligible: true, source_date: '2026-10-15' } }],
  short_top10: [{ code: '6488', market: '上櫃', close: 200, day_trading_eligibility: { eligible: true, source_date: '2026-10-15' } }],
  execution_eligibility: { enforced: true, source_date: '2026-10-15', filtered_count: 0, filtered_out: [] },
  pre_gate_counts: { long: 10, short: 8 },
  filtered_out_counts: { long: 9, short: 7 },
};

function env(fetcher) {
  return { DATA: new KV({ latest: JSON.stringify(latest), 'download:latest.csv': 'a,b\n1,2' }),
    ASSETS: { fetch: async () => new Response('asset') }, fetcher };
}

test('dashboard加入TAIFEX全球市場情境與系統抓取時間', async () => {
  const taifex = [
    { contract: 'TX106', price: '100', ttlvol: '1,000', contractName: '臺股期貨', updown: '1' },
    { contract: 'ZEF106', price: '100', ttlvol: '900', contractName: '小型電子期貨', updown: '1' },
    { contract: 'CDF106', price: '100', ttlvol: '800', contractName: '台積電期貨', updown: '1' },
    { contract: 'UNF126', price: '100', ttlvol: '700', contractName: '那斯達克100期貨', updown: '2' },
    { contract: 'SXF126', price: '100', ttlvol: '600', contractName: '費城半導體期貨', updown: '-1' },
  ];
  const fetcher = async url => {
    const value = String(url);
    if (value.includes('getQuotes?objId=12')) return new Response(JSON.stringify(taifex));
    throw new Error('other source unavailable');
  };
  const body = await (await createWorker(fetcher).fetch(new Request('https://x/api/dashboard'), env(fetcher))).json();
  assert.equal(body.global_market.status, 'available');
  assert.match(body.global_market.fetched_at, /^\d{4}-\d\d-\d\dT/);
  assert.equal(body.global_market.items.length, 5);
  assert.equal(body.global_market.risk.status, '風險偏多');
  assert.equal(body.global_market.notice, '期交所行情，可能延遲');
  assert.deepEqual(body.pre_gate_counts, { long: 10, short: 8 });
  assert.deepEqual(body.filtered_out_counts, { long: 9, short: 7 });
  assert.equal(body.execution_eligibility.enforced, true);
});

test('dashboard優先讀固定GitHub Raw latest且不接受請求URL覆寫', async () => {
  const calls = [];
  const githubLatest = { ...latest, data_date: '2026-10-16', execution_eligibility: { ...latest.execution_eligibility, source_date: '2026-10-16' },
    long_top10: [{ code: '9999', market: '上市', close: 50, day_trading_eligibility: { eligible: true, source_date: '2026-10-16' } }], short_top10: [] };
  const githubStatus = { status: 'success', completed_at: '2026-10-16T16:20:00+08:00', strategy: { candidate: { test: { win_rate: .42, profit_factor: 1.2 } } } };
  const fetcher = async url => {
    calls.push(String(url));
    if (String(url).includes('/daily_status.json')) return new Response(JSON.stringify(githubStatus), { status: 200 });
    if (String(url).startsWith('https://raw.githubusercontent.com/charleschan0414-creator/taistock-daily-monitor/main/public_data/latest.json')) {
      return new Response(JSON.stringify(githubLatest), { status: 200 });
    }
    throw new Error('MIS unavailable');
  };
  const body = await (await createWorker(fetcher).fetch(
    new Request('https://example.com/api/dashboard?source=https://evil.example/steal'), env(fetcher))).json();
  assert.equal(body.long[0].code, '9999');
  assert.equal(body.data_source, 'github');
  assert.deepEqual(body.daily_status, githubStatus);
  assert.ok(calls[0].startsWith('https://raw.githubusercontent.com/charleschan0414-creator/taistock-daily-monitor/main/public_data/latest.json'));
  assert.equal(calls.some(url => url.includes('evil.example')), false);
});

test('dashboard在MIS失敗時仍回KV清單並標示非即時', async () => {
  const worker = createWorker(async () => { throw new Error('MIS down'); });
  const res = await worker.fetch(new Request('https://example.com/api/dashboard'), env());
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.long[0].code, '2330');
  assert.equal(body.long[0].realtime, false);
  assert.equal(body.realtime_status, 'unavailable');
  assert.equal(body.data_source, 'kv');
  assert.equal(body.global_market.status, 'unavailable');
  assert.equal(body.global_market.fetched_at, null);
  assert.equal(res.headers.get('access-control-allow-origin'), null);
});

test('dashboard對未經官方當沖資格驗證的KV資料隱藏可執行清單', async () => {
  const unverified = { ...latest };
  delete unverified.execution_eligibility;
  const fetcher = async () => { throw new Error('GitHub down'); };
  const e = env(fetcher);
  e.DATA = new KV({ latest: JSON.stringify(unverified) });
  const res = await createWorker(fetcher).fetch(new Request('https://example.com/api/dashboard'), e);
  const body = await res.json();
  assert.equal(res.status, 200);
  assert.deepEqual(body.long, []);
  assert.deepEqual(body.short, []);
  assert.equal(body.execution_eligibility.enforced, false);
  assert.match(body.execution_eligibility.notice, /不顯示可執行名單/);
});

test('dashboard在休市或兩市場未同步時沿用資料但清空可執行名單', async () => {
  const paused = {
    ...latest,
    data_date: '2026-10-22',
    market_session: { status: 'paused_no_new_official_session', requested_date: '2026-10-23',
      latest_official_date: '2026-10-22' },
    execution_eligibility: { enforced: true, source_date: '2026-10-22', requested_date: '2026-10-23',
      execution_for_signal_date: 'paused_no_new_official_session', filtered_count: 2, filtered_out: [] },
    long_top10: [{ ...latest.long_top10[0], day_trading_eligibility: {
      eligible: false, source_date: '2026-10-22', reason: 'no_new_official_session' } }],
    short_top10: [{ ...latest.short_top10[0], day_trading_eligibility: {
      eligible: false, source_date: '2026-10-22', reason: 'no_new_official_session' } }],
  };
  const fetcher = async () => { throw new Error('GitHub down'); };
  const e = env(fetcher);
  e.DATA = new KV({ latest: JSON.stringify(paused) });
  const body = await (await createWorker(fetcher).fetch(new Request('https://x/api/dashboard'), e)).json();
  assert.deepEqual(body.long, []);
  assert.deepEqual(body.short, []);
  assert.equal(body.observation_long.length, 1);
  assert.equal(body.observation_short.length, 1);
  assert.equal(body.observation_long[0].display_status, '休市沿用｜不可執行');
  assert.deepEqual(body.market_session, paused.market_session);
  assert.equal(body.execution_eligibility.enforced, true);
  assert.equal(body.execution_eligibility.execution_for_signal_date, 'paused_no_new_official_session');

  const disasterClosed = {
    ...paused,
    data_collection: { latest_official_quote_date: '2026-10-22',
      session_checked_at: '2026-10-23T06:30:00+08:00' },
    market_session: { ...paused.market_session, closure_reason: 'taipei_disaster_stop_work',
      closure_source: 'https://www.dgpa.gov.tw/typh/daily/nds.html' },
    execution_eligibility: { ...paused.execution_eligibility, eligible_count: 0 },
    long_top10: paused.long_top10.map(row => ({ ...row, day_trading_eligibility: {
      ...row.day_trading_eligibility, reason: 'market_closed' } })),
    short_top10: paused.short_top10.map(row => ({ ...row, day_trading_eligibility: {
      ...row.day_trading_eligibility, reason: 'market_closed' } })),
  };
  const disasterEnv = env(fetcher);
  disasterEnv.DATA = new KV({ latest: JSON.stringify(disasterClosed) });
  const disasterBody = await (await createWorker(fetcher).fetch(
    new Request('https://x/api/dashboard'), disasterEnv)).json();
  assert.deepEqual(disasterBody.long, []);
  assert.deepEqual(disasterBody.short, []);
  assert.equal(disasterBody.observation_long.length, 1);
  assert.equal(disasterBody.observation_short.length, 1);
  assert.equal(disasterBody.execution_eligibility.enforced, true);
  assert.equal(disasterBody.execution_eligibility.eligible_count, 0);
  assert.equal(disasterBody.market_session.closure_reason, 'taipei_disaster_stop_work');
  assert.deepEqual(disasterBody.data_collection, disasterClosed.data_collection);

  const mixedRows = { ...disasterClosed,
    long_top10: [...disasterClosed.long_top10, {
      ...disasterClosed.long_top10[0], code: '9998', day_trading_eligibility: {
        eligible: false, source_date: '2026-10-21', reason: 'market_closed' } }],
    short_top10: [...disasterClosed.short_top10, {
      ...disasterClosed.short_top10[0], code: '9999', day_trading_eligibility: {
        eligible: false, source_date: '2026-10-22', reason: 'unknown' } }],
  };
  const mixedEnv = env(fetcher);
  mixedEnv.DATA = new KV({ latest: JSON.stringify(mixedRows) });
  const mixedBody = await (await createWorker(fetcher).fetch(
    new Request('https://x/api/dashboard'), mixedEnv)).json();
  assert.deepEqual(mixedBody.observation_long.map(row => row.code),
    [disasterClosed.long_top10[0].code]);
  assert.deepEqual(mixedBody.observation_short.map(row => row.code),
    [disasterClosed.short_top10[0].code]);

  const malformedTime = { ...disasterClosed, data_collection: {
    ...disasterClosed.data_collection, session_checked_at: '2026-10-23Tgarbage' } };
  const malformedTimeEnv = env(fetcher);
  malformedTimeEnv.DATA = new KV({ latest: JSON.stringify(malformedTime) });
  const malformedTimeBody = await (await createWorker(fetcher).fetch(
    new Request('https://x/api/dashboard'), malformedTimeEnv)).json();
  assert.equal(malformedTimeBody.market_session, null);
  assert.equal(malformedTimeBody.execution_eligibility.enforced, false);

  const malformed = { ...paused, market_session: { ...paused.market_session, requested_date: '2026-99-99' } };
  const badEnv = env(fetcher);
  badEnv.DATA = new KV({ latest: JSON.stringify(malformed) });
  const badResponse = await createWorker(fetcher).fetch(new Request('https://x/api/dashboard'), badEnv);
  assert.equal(badResponse.status, 200);
  const badBody = await badResponse.json();
  assert.equal(badBody.market_session, null);
  assert.equal(badBody.execution_eligibility.enforced, false);
});

test('dashboard拒絕不可能的官方資格日期', async () => {
  const invalid = { ...latest, data_date: '2026-02-28', short_top10: [],
    execution_eligibility: { ...latest.execution_eligibility, source_date: '2026-02-30' },
    long_top10: [{ ...latest.long_top10[0], day_trading_eligibility: { eligible: true, source_date: '2026-02-30' } }],
  };
  const fetcher = async () => { throw new Error('GitHub down'); };
  const e = env(fetcher);
  e.DATA = new KV({ latest: JSON.stringify(invalid) });
  const body = await (await createWorker(fetcher).fetch(new Request('https://example.com/api/dashboard'), e)).json();
  assert.deepEqual(body.long, []);
  assert.equal(body.execution_eligibility.enforced, false);
});

test('dashboard產生上市與上櫃MIS channel並合併現價', async () => {
  let called = '';
  const worker = createWorker(async url => {
    if (String(url).includes('getStockInfo.jsp')) called = String(url);
    return new Response(JSON.stringify({ msgArray: [
      { c: '2330', z: '101', tlong: '1' }, { c: '6488', z: '-', y: '199', tlong: '2' },
    ] }), { headers: { 'content-type': 'application/json' } });
  });
  const body = await (await worker.fetch(new Request('https://example.com/api/dashboard'), env())).json();
  assert.match(called, /tse_2330\.tw/);
  assert.match(called, /otc_6488\.tw/);
  assert.equal(body.long[0].current_price, 101);
  assert.equal(body.short[0].current_price, 199);
  assert.equal(body.short[0].realtime, false);
});

test('health優先回報GitHub資料來源、發布版本與每日流水線狀態', async () => {
  const status = { status: 'success', data_date: '2026-10-16', completed_at: '2026-10-16T16:20:00+08:00', health: { desktop_version: '2026.10.21.1' } };
  const fetcher = async url => {
    if (String(url).includes('/software_manifest.json')) return new Response(JSON.stringify({ version: '2026.10.22.3' }));
    if (String(url).includes('/latest.json')) return new Response(JSON.stringify({ ...latest, data_date: '2026-10-16' }));
    if (String(url).includes('/daily_status.json')) return new Response(JSON.stringify(status));
    throw new Error('unexpected URL');
  };
  const e = env(fetcher);
  const body = await (await createWorker(fetcher).fetch(new Request('https://x/api/health'), e)).json();
  assert.equal(body.ok, true);
  assert.equal(body.data_source, 'github');
  assert.equal(body.data_date, '2026-10-16');
  assert.equal(body.desktop_version, '2026.10.22.3');
  assert.deepEqual(body.daily_status, status);
});

test('latest CSV只由已驗證JSON產生且不接受未驗證KV備援', async () => {
  const github = async url => {
    if (String(url).includes('/latest.json')) return new Response(JSON.stringify(latest));
    throw new Error('other source unavailable');
  };
  const verified = await createWorker(github).fetch(new Request('https://x/download/latest.csv'), env(github));
  const csv = await verified.text();
  assert.equal(verified.status, 200);
  assert.match(csv, /2330/);
  assert.match(csv, /6488/);

  const failed = async () => { throw new Error('GitHub down'); };
  const unverified = { ...latest };
  delete unverified.execution_eligibility;
  const e = env(failed);
  e.DATA = new KV({ latest: JSON.stringify(unverified), 'download:latest.csv': 'legacy,unsafe\n' });
  const blocked = await createWorker(failed).fetch(new Request('https://x/download/latest.csv'), e);
  assert.equal(blocked.status, 503);
  assert.match((await blocked.json()).error, /當沖資格未驗證/);
});

test('其他下載檔優先GitHub且失敗才回KV', async () => {
  const github = async () => new Response('github,csv\n', { status: 200 });
  const fromGithub = await createWorker(github).fetch(new Request('https://x/download/backtest_100d.csv'), env(github));
  assert.equal(await fromGithub.text(), 'github,csv\n');
});

test('只允許GET與固定下載白名單且拒絕路徑穿越', async () => {
  const worker = createWorker(async () => new Response('{}'));
  assert.equal((await worker.fetch(new Request('https://x/api/dashboard', { method: 'POST' }), env())).status, 405);
  assert.equal((await worker.fetch(new Request('https://x/download/secret.txt'), env())).status, 404);
  assert.equal((await worker.fetch(new Request('https://x/download/%2e%2e%2fsecret'), env())).status, 404);
  const ok = await worker.fetch(new Request('https://x/download/latest.csv'), env());
  assert.equal(ok.status, 200);
  assert.match(ok.headers.get('content-disposition'), /latest\.csv/);
});
