import { existsSync, readdirSync, readFileSync, unlinkSync, writeFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { buildLatest, latestCsv } from '../src/cron.js';
import { allCompleteDates, buildDay, gateFromStrategy } from './seed-lib.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const cloudDir = resolve(here, '..'); const dataDir = resolve(cloudDir, '../data');
const cacheDir = resolve(dataDir, 'backtest_cache');
const rolling = [];
for (const date of allCompleteDates(readdirSync(cacheDir)).reverse()) {
  try { rolling.push(buildDay(cacheDir.replaceAll('\\', '/'), date)); }
  catch (error) { console.warn(`略過 ${date}：${error.message}`); }
  if (rolling.length === 6) break;
}
rolling.sort((a, b) => a.date.localeCompare(b.date));
const dates = rolling.map(day => day.date.replaceAll('-', ''));
if (dates.length !== 6) throw new Error(`backtest_cache 只有 ${dates.length} 個日期一致的四源完整交易日，需要6日`);

let gate = null; const strategyPath = resolve(dataDir, 'strategy_optimization.json');
if (existsSync(strategyPath)) {
  gate = gateFromStrategy(JSON.parse(readFileSync(strategyPath, 'utf8')));
  if (!gate) console.warn('strategy_optimization.json 未明確啟用或權重無效，略過策略閘門');
}
let weights = null; const optimizerPath = resolve(dataDir, 'optimizer.json');
if (existsSync(optimizerPath)) weights = JSON.parse(readFileSync(optimizerPath, 'utf8'));
const latest = buildLatest(rolling, gate, new Date().toISOString(), weights);
const entries = [
  { key: 'rolling', value: JSON.stringify(rolling) },
  { key: 'latest', value: JSON.stringify(latest) },
  { key: 'download:latest.csv', value: existsSync(resolve(dataDir, 'latest.csv')) ? readFileSync(resolve(dataDir, 'latest.csv'), 'utf8') : latestCsv(latest) },
];
const backtestCsv = resolve(dataDir, 'backtest_100d.csv');
if (existsSync(backtestCsv)) entries.push({ key: 'download:backtest_100d.csv', value: readFileSync(backtestCsv, 'utf8') });
if (gate) entries.push({ key: 'strategy:gate', value: JSON.stringify(gate) });
if (weights) entries.push({ key: 'strategy:weights', value: JSON.stringify(weights) });

const bulkPath = resolve(cloudDir, '.seed-kv.json');
writeFileSync(bulkPath, JSON.stringify(entries));
const namespace = process.env.KV_NAMESPACE_ID;
const args = ['wrangler', 'kv', 'bulk', 'put', bulkPath, '--remote', ...(namespace ? [`--namespace-id=${namespace}`] : ['--binding=DATA'])];
try {
  if (process.env.SEED_DRY_RUN === '1') {
    console.log(`Seed驗證完成：${dates.join(', ')}；${entries.length} 個KV key；未連線Cloudflare`);
  } else {
    const wranglerBin = resolve(cloudDir, 'node_modules/wrangler/bin/wrangler.js');
    const result = spawnSync(process.execPath, [wranglerBin, ...args.slice(1)], { cwd: cloudDir, stdio: 'inherit' });
    if (result.error) throw result.error;
    if (result.status !== 0) process.exitCode = result.status ?? 1;
    else console.log(`Seed完成：${dates.join(', ')}；${entries.length} 個KV key`);
  }
} finally { unlinkSync(bulkPath); }
