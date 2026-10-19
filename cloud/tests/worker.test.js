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
  long_top10: [{ code: '2330', market: '上市', close: 100 }],
  short_top10: [{ code: '6488', market: '上櫃', close: 200 }],
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
});

test('dashboard優先讀固定GitHub Raw latest且不接受請求URL覆寫', async () => {
  const calls = [];
  const githubLatest = { ...latest, data_date: '2026-10-16', long_top10: [{ code: '9999', market: '上市', close: 50 }] };
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

test('health優先回報GitHub資料來源與每日流水線狀態', async () => {
  const status = { status: 'success', data_date: '2026-10-16', completed_at: '2026-10-16T16:20:00+08:00' };
  const fetcher = async url => {
    if (String(url).includes('/latest.json')) return new Response(JSON.stringify({ ...latest, data_date: '2026-10-16' }));
    if (String(url).includes('/daily_status.json')) return new Response(JSON.stringify(status));
    throw new Error('unexpected URL');
  };
  const e = env(fetcher);
  const body = await (await createWorker(fetcher).fetch(new Request('https://x/api/health'), e)).json();
  assert.equal(body.ok, true);
  assert.equal(body.data_source, 'github');
  assert.equal(body.data_date, '2026-10-16');
  assert.deepEqual(body.daily_status, status);
});

test('下載固定白名單優先GitHub且失敗才回KV', async () => {
  const github = async url => new Response(String(url).includes('/latest.csv') ? 'github,csv\n' : '', { status: 200 });
  const fromGithub = await createWorker(github).fetch(new Request('https://x/download/latest.csv'), env(github));
  assert.equal(await fromGithub.text(), 'github,csv\n');
  const failed = async () => { throw new Error('GitHub down'); };
  const fromKv = await createWorker(failed).fetch(new Request('https://x/download/latest.csv'), env(failed));
  assert.equal(await fromKv.text(), 'a,b\n1,2');
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
