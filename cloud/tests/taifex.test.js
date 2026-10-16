import test from 'node:test';
import assert from 'node:assert/strict';
import { fetchTaifexMarket, marketRisk, parseTaifexQuotes } from '../src/taifex.js';

const officialRows = [
  { contract: 'TX106', price: '46,140', ttlvol: '40,941', contractName: '臺股期貨', updown: '-1,144' },
  { contract: 'ZEF106', price: '2,875.00', ttlvol: '583', contractName: '小型電子期貨', updown: '-88.10' },
  { contract: 'CDF106', price: '2,384.00', ttlvol: '2,860', contractName: '台積電期貨', updown: '-47.00' },
  { contract: 'UNF126', price: '29,374.00', ttlvol: '285', contractName: '那期達克100期貨', updown: '-802.00' },
  { contract: 'SXF126', price: '11,211.50', ttlvol: '93', contractName: '費城半導體期貨', updown: '-465.50' },
  { contract: 'OTHER', price: '1', ttlvol: '2', contractName: '其他', updown: '0' },
];

test('TAIFEX解析器只保留五項指定期貨並正規化官方數字', () => {
  const rows = parseTaifexQuotes(officialRows);
  assert.deepEqual(rows.map(row => row.code), ['TX', 'ZEF', 'CDF', 'UNF', 'SXF']);
  assert.equal(rows.find(row => row.code === 'UNF').name, '那斯達克100期貨');
  assert.deepEqual(rows[0], {
    code: 'TX', contract: 'TX106', name: '臺股期貨', price: 46140,
    change: -1144, change_pct: -2.4194, volume: 40941,
  });
});

test('TAIFEX解析器在前價為零或欄位缺值時保留項目且不產生無效漲跌幅', () => {
  const rows = parseTaifexQuotes([
    { contract: 'TX106', price: '5', ttlvol: '', contractName: '臺股期貨', updown: '5' },
    { contract: 'UNF126', price: '', ttlvol: '10', contractName: '那斯達克100期貨', updown: '1' },
  ]);
  assert.equal(rows.find(row => row.code === 'TX').change_pct, null);
  assert.equal(rows.find(row => row.code === 'TX').volume, null);
  assert.equal(rows.find(row => row.code === 'UNF').price, null);
  assert.equal(rows.find(row => row.code === 'UNF').change_pct, null);
});

test('市場風險只看TX、UNF、SXF多數方向且資料不足時中性', () => {
  assert.deepEqual(marketRisk([
    { code: 'TX', change: 10 }, { code: 'UNF', change: 2 }, { code: 'SXF', change: -1 }, { code: 'CDF', change: -99 },
  ]), { status: '風險偏多', rule: 'TX／UNF／SXF 三者中至少二項上漲' });
  assert.equal(marketRisk([{ code: 'TX', change: -1 }, { code: 'UNF', change: -2 }, { code: 'SXF', change: 0 }]).status, '風險偏空');
  assert.equal(marketRisk([{ code: 'TX', change: 1 }]).status, '中性');
});

test('TAIFEX fetcher只呼叫官方固定端點並記錄系統抓取時間', async () => {
  let called;
  const fetcher = async (url, options) => {
    called = { url, options };
    return new Response(JSON.stringify(officialRows));
  };
  const result = await fetchTaifexMarket(fetcher, () => new Date('2026-10-17T01:02:03Z'));
  assert.equal(called.url, 'https://www.taifex.com.tw/cht/quotesApi/getQuotes?objId=12');
  assert.equal(called.options.headers.accept, 'application/json');
  assert.equal(result.status, 'available');
  assert.equal(result.fetched_at, '2026-10-17T01:02:03.000Z');
  assert.equal(result.notice, '期交所行情，可能延遲');
  assert.equal(result.items.length, 5);
  assert.equal(result.risk.status, '風險偏空');
});
