# TaiStock Cloud Monitor（Cloudflare Worker + GitHub Raw + PWA）

Cloudflare 提供公開 HTTPS 唯讀 PWA；資料計算由公開 GitHub repo `charleschan0414-creator/taistock-daily-monitor` 的免費 GitHub Actions 執行，因此 Windows 關機仍可讀取最新已成功成果。服務不含下單功能。

## 排程與資料流

- 主流程：台北週一至週五 16:10；UTC cron `10 8 * * 1-5`。
- 早晨備援：台北週一至週五 07:40；UTC 前一日 cron `40 23 * * 0-4`。
- GitHub 免費排程可能延遲，PWA 會顯示資料日、來源、最後成功時間及每日狀態，過期或失敗會警告。
- 每次依序執行 100 日無前視回測、70/30 策略閘門優化及當日清單；任一步或最終完整性驗證失敗都不標示成功。
- `public_data/` 是固定公開白名單：`latest.json/.csv`、回測摘要 JSON/CSV、optimizer、strategy optimization、daily status。
- `data/strategy_history.jsonl` 依 `data_date` 冪等保存每日基準/候選 train/test 指標與實際 gate，避免把每日重疊窗口誤稱為新增獨立樣本外證據。
- `data/backtest_cache/*.json.gz` 由 git 持久化，只新增缺少的官方交易日，避免每日重抓 106 日（5 日暖機＋100 訊號日＋1 次日執行日）。

公開 repo 的資料任何人均可見。不得提交 token、cookie、`scanner.db`、Wrangler/Cloudflare 憑證或其他本機狀態。

## Worker 行為

- `GET /api/dashboard`：先讀固定 GitHub Raw `public_data/latest.json` 與 `daily_status.json`，約 5 分鐘 cache-busting；GitHub 失敗才回退 KV，清單仍以 TWSE MIS 合併現價。
- `GET /api/health`：回傳 `data_source`（`github`/`kv`）、`data_date` 與 `daily_status`；狀態 GitHub 失敗時回退 `cron:last`。
- `/download/latest.csv`、`/download/backtest_100d.csv`：固定白名單、GitHub 優先、KV 備援。
- 只允許 GET/HEAD，不接受使用者提供來源 URL 或檔案路徑，不設定跨來源 CORS；其他下載名與路徑穿越回 404。

## 本機驗證與部署

需要 Node 22 與 npm。不要把登入憑證放進 repo。

```bash
cd cloud
npm install
npm test
npx wrangler deploy --dry-run
```

正式部署（本次修改不會自動執行）：

```bash
npx wrangler login
npm run deploy
```

現有 KV 保留作 GitHub 無法連線時的備援；`npm run seed` 仍可由本機資料初始化 KV。Cloudflare Workers 免費 Cron 已停用，避免10毫秒CPU限制產生失敗日更；主要每日計算全部由 GitHub Actions 執行。

## 方法限制

70/30 的最後 30 個訊號日只用於候選啟用閘門；新設定僅套用後續訊號，不回寫當期。每日在高度重疊的 100 日窗口重新優化不代表持續取得新的獨立樣本外證據。回測未納手續費、交易稅、滑價、借券費與融券限制。規則式研究觀察不是投資建議，不保證提高勝率或獲利。
