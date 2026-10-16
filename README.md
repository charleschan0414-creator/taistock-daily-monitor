# TaiStockScanner 台股每日多空觀察與 100 日回測

Windows 本機 Tkinter 工具，合併 TWSE 上市與 TPEx 上櫃官方資料，從成交量前 100 名產生規則式多空觀察。**只供研究，不是投資建議、不保證獲利，也不會自動下單。**

## 每日訊號

- 只納入 4 碼且不以 `00` 開頭的普通股；上市、上櫃合併後依成交股數取前 100。
- 至少 5 個交易日收盤；MA5 包含訊號日。
- 量增率 `=(當日量/前一交易日量-1)×100%`，須至少 20%；前日量為 0 不列入。
- 偏多：收盤高於 MA5 且三大法人合計買超；偏空：收盤低於 MA5 且三大法人合計賣超。
- 多空分開排名：量增 35%、距 MA5 絕對值 30%、法人買賣超絕對張數 25%、成交量名次 10%。先完成既有評分，再套用已通過保留測試的 `strategy_gate`；未通過或檔案無效時完全保留基準訊號。每方向最多顯示 10 名。
- 風險幅度是最近 5 日相鄰收盤報酬絕對值平均，限制 1.5%–5%；觸發偏移限制 0.3%–1%；目標是 1.5R、2R，價格按台股跳動單位四捨五入。

`top10`、`qualified` 保持偏多舊介面相容；完整欄位另有 `long_top10`、`short_top10`、`signals`、`universe`、`optimization`、`strategy_gate`。桌面 UI 與手機 `/api/dashboard` 會顯示閘門是否啟用、方向與門檻。

## 100 個交易日無前視回測

```bat
python app.py --backtest 100
```

CLI 不開 GUI，成功結束碼為 0。介面右上方「100日回測」按鈕在背景啟動同一命令；「100日回測」分頁可離線讀取上次結果。

### 假設與成交規則

1. 下載 110 個「TWSE 行情、TWSE 法人、TPEx 行情、TPEx 法人四源日期一致」的完整交易日：前 5 日供 MA5/前日量暖機，恰好 100 日形成訊號，最後 5 日供持有期判定。
2. 訊號只使用當日及以前資料，在收盤後形成，最早下一交易日才可能成交。
3. 偏多需 `high >= entry`；偏空需 `low <= entry`。未觸發會繼續觀察，最多 5 個交易日。
4. 觸發後逐日判斷。若同一 OHLC 日碰停損，保守採停損；若未碰停損但同日同時觸及 target1/target2，因日 OHLC 無法重建盤中先後，保守採 target1（不是 target2）；未碰出場價則第 5 日收盤退出。
5. 最後不足 5 個未來交易日或中間缺少該股票行情時標為 `unresolved`，完全不納入績效。輸出觸發後的 MFE/MAE。
6. 每個訊號日的已觸發交易，以最終報酬等權平均後按訊號日複利；這是「訊號批次指標」，不模擬資金占用與重疊部位，不能視為可直接實現的帳戶報酬。獲利因子是正報酬總和除以負報酬絕對總和。
7. **未計手續費、交易稅、滑價、借券費、融券限制；結果不是含成本績效。**

輸出：

- `data/backtest_100d.json`：期間、100 個唯一排序訊號日、總體/多/空統計、逐筆明細、walk-forward 權重歷程。
- `data/backtest_100d.csv`（並保留 `backtest_trades.csv` 相容檔）。
- `data/scanner.db` 的 `backtest_runs`、`backtest_trades`；只做增量建表，不刪既有資料。

統計包含總訊號、已觸發、未觸發、未結算、勝率、平均/中位報酬、累積複利、最大回撤、獲利因子，並分偏多/偏空。

## 70/30 保留測試品質閘門

```bat
python app.py --optimize-strategy
```

- 僅讀取修正成交假設後的 `data/backtest_100d.json`：最早 70 個訊號日是訓練集，最後 30 個訊號日是完全保留測試；候選選擇不讀取保留測試的報酬。
- 有限固定網格共 36 組：偏多最低分數 `{0,55,70}`、偏空最低分數 `{0,55,70}`、偏空啟用/停用、量增最低 `{20%,50%}`。不在全樣本搜尋連續參數。
- 每候選訓練成交至少 100 筆；選中候選保留測試成交至少 30 筆。訓練期另要求 PF>1，且最大回撤不可比訓練基準惡化超過 5 個百分點。
- 訓練目標是 `100×平均逐筆報酬 + 100×平均訊號日批次報酬 + 0.25×最大回撤 + 0.05×min(PF,3) + 0.01×勝率 - 交易過少懲罰`。勝率只有輔助權重，不直接最大化勝率。
- 選中候選只在下列保留測試門檻全數通過時啟用：成交至少 30、平均報酬不低於基準、最大回撤不比基準惡化超過 2 個百分點、PF>1。否則每日訊號完全保留基準。
- 稽核報告寫入 `data/strategy_optimization.json`，含版本、方法、日期、候選數、門檻、train/test 基準與候選、逐項檢查、決策與更新時間。
- 目前報告啟用：偏多分數門檻 0、偏空分數門檻 70、偏空啟用、量增至少 20%。這只是歷史保留測試通過，不保證未來勝率或報酬提高；重新產生 100 日回測後必須另行重跑本命令才會改變閘門。

### 官方端點與續跑

- TWSE 行情：`/rwd/zh/afterTrading/MI_INDEX?date=YYYYMMDD&type=ALLBUT0999&response=json`
- TWSE 三大法人：`/rwd/zh/fund/T86?date=YYYYMMDD&selectType=ALLBUT0999&response=json`
- TPEx 行情：`/www/zh-tw/afterTrading/dailyQuotes?date=YYYY/MM/DD&type=EW&response=json`
- TPEx 三大法人：`/www/zh-tw/insti/dailyTrade?type=Daily&sect=EW&date=YYYY/MM/DD&id=&response=json`

每一來源每日成功回應以 gzip 原文存入 `data/backtest_cache/YYYYMMDD_<source>.json.gz`。中斷後重跑會先讀成功快取，不重抓；損壞快取會刪除後重抓。HTTP 429、5xx 或網路錯誤會依 `Retry-After` 或指數退避重試，請求間另有節流。CLI 完成時列出本次網路請求與快取命中數。

## 嚴格時間順序 walk-forward

- 每個訊號日先只取 `exit_date < 當日` 的已觸發、已結算樣本；當天或未來結果絕不參與當天權重。
- 前 30 筆已結算樣本固定使用基準權重。之後只在固定候選權重中比較；候選未嚴格優於基準就保留基準。
- 最終建議寫入 `data/optimizer.json`，`source=walk_forward_backtest_100d`，包含樣本數、期間、舊/新權重、指標與更新時間。
- 每日即時學習另以「下一次成功更新交易日」結算：只結算上一個成功 `runs.data_date` 的訊號，欄位 `settlement_basis=next_successful_update_trading_day`。若中間漏跑，不會把更後面的任一日偽稱為隔日。

## 執行、資料與排程

```bat
run.bat
run.bat --update
run.bat --update --notify
python app.py --help
```

### 版本更新

- **雲端版／PWA**：直接重新開啟固定網址即可取得已發布的新版本；瀏覽器會自動更新網頁殼層，不需下載 ZIP。
- **桌面版**：從本版開始，每次執行 `run.bat` 都會先向固定官方來源 `charleschan0414-creator/taistock-daily-monitor` 查詢 `main` 當下的 40 位 commit SHA，再以同一 SHA 讀取 manifest 與下載壓縮檔。版本相同時只讀取小型 manifest，不下載完整程式；有新版才下載該不可變 commit 的壓縮檔，不使用可能不同步的分支壓縮檔。
- 更新只安裝 `software_manifest.json` 明列且 SHA256 驗證通過的程式檔，並以暫存、原子替換及失敗回復保護目前版本。release manifest 由 `build_software_manifest.py` 對 Git clean-filter 後的 blob bytes 產生，因此 Windows CRLF 工作樹與 GitHub archive 的 LF 內容不會造成誤判。`data/`、`public_data/`、`scanner.db`、`python_path.txt`、官方 cache、憑證與使用者設定永遠不會被覆蓋。
- 若網路、GitHub 或驗證失敗，會顯示警告並繼續啟動目前可用版本。只檢查不安裝可執行 `run.bat --check-only` 或 `python updater.py --check-only`。
- **先前下載舊 ZIP 的使用者**：舊版沒有更新器，需只做一次換成新版 ZIP。請解壓到新的固定資料夾；若要沿用舊資料，再複製自己的 `data/` 與 `python_path.txt` 到新版。之後由桌面版自動更新，不必再次整包替換。

每日結果寫入 `data/latest.json`、`latest.csv`、`scanner.db`。日更會重用最近 6 個完整交易日的官方逐日批次快取，只需補抓尚未快取的新交易日，避免逐檔大量請求觸發交易所 307/429 限流。現有 `TaiStockScannerDaily` 每天 08:30 只執行 `run.bat --update --notify`，不會每日重跑 100 日回測；本功能不重建或刪除排程。

## 429 故障排除

兩種 429 必須分開：

1. **Hermes `provider=openai-codex HTTP 429 usage limit`**：這是 AI 模型供應商的帳號/期間配額，不是台股 API，也不是 TaiStockScanner 邏輯錯誤。稍後重試、等待配額重置或調整模型方案/配額。TaiStockScanner 的 GUI、08:30 日更與回測本身不依賴 LLM，不受此 Hermes 429 影響。
2. **TWSE/TPEx HTTP 429**：官方市場資料端限流。程式會節流、尊重 `Retry-After`、指數退避，且保留已完成快取以便續跑；仍失敗時稍後再次執行相同命令。

## 即時現價與同 Wi-Fi 手機版

桌面版開啟上次成功清單後，會在背景抓取 TWSE MIS 官方報價；「更新現價」可手動更新，之後每 60 秒自動更新。上市使用 `tse_代號.tw`，上櫃使用 `otc_代號.tw`。現價缺值時會回退可用報價並明確標示「非即時」；交易時段以台北時區週一至週五 09:00–13:30 判斷，收盤後標示「收盤/非即時」。網路失敗保留最後成功值，不會卡住視窗。

桌面版、本機手機版與 Cloudflare 雲端版另整合 TAIFEX 官方夜盤行情端點，每 60 秒更新臺股期貨（TX）、小型電子期貨（ZEF）、台積電期貨（CDF）、那斯達克100期貨（UNF）及費城半導體期貨（SXF）。系統以 TX／UNF／SXF 至少兩項同向顯示「風險偏多／中性／偏空」市場情境；這是透明的夜盤脈動摘要，不會直接加入既有選股分數或改寫策略。頁面顯示的是系統抓取時間，並明示「期交所行情，可能延遲」；不可視為逐筆即時成交或投資建議。

手機版是跨平台 PWA 網頁，不是原生 App。啟動方式：

```bat
start_mobile.bat
start_mobile.bat --host 0.0.0.0 --port 8765
```

也可按桌面程式右上角「手機版」。伺服器會將 localhost 與偵測到的區網網址寫入 `data/mobile_url.txt`。手機與 Windows 電腦必須連同一 Wi-Fi，使用手機瀏覽器開啟區網網址：Android Chrome 選單選「加入主畫面」；iPhone Safari 點「分享」再選「加入主畫面」。電腦必須保持開機且伺服器正在執行，電腦關機後無法連線。若 Windows 防火牆詢問，僅在信任的私人網路允許 Python；程式不會自行新增或修改防火牆規則。

頁面每 60 秒更新，可切換偏多/偏空，並顯示現價、漲跌、觸發、停損及目標。可下載 `latest.csv` 與 `backtest_100d.csv`；其他檔案不開放。殼層會在瀏覽器允許 Service Worker 的安全環境中快取；API 不做永久快取，離線時只顯示瀏覽器最後資料並明示離線。部分手機瀏覽器會限制區網 HTTP 的 Service Worker，但仍可用「加入主畫面」建立捷徑。

`台股手機版.zip` 可單獨解壓使用，內含當次封裝的 `data`；後續若要讀主程式最新資料，請將內容放回 TaiStockScanner 根目錄。`台股每日觀察軟體.zip` 是完整備份，排除大型 `data/backtest_cache` 與 Python 快取。

### 使用行動數據連線（公開 Cloudflare Quick Tunnel）

```bat
start_remote_mobile.bat
```

此腳本會啟動本機手機伺服器，並在需要時從 Cloudflare 官方 GitHub Release 下載 `cloudflared.exe`，再顯示一個 `https://...trycloudflare.com` 公開網址。手機使用4G／5G即可開啟，不必與電腦使用同一Wi‑Fi；HTTPS也可讓PWA功能正常運作。

- **任何拿到網址的人都能查看唯讀儀表板及下載程式開放的兩個CSV。**伺服器不提供寫入、下單或任意檔案存取。
- 電腦、本機手機伺服器與Cloudflare Tunnel都必須保持執行。
- Quick Tunnel沒有可用性保證；重新啟動後網址會改變。當前網址會另外記錄於 `data/remote_mobile_url.txt`。
- 若要固定網址、登入驗證及長期服務，需另外申請Cloudflare帳號、網域與Named Tunnel。

現有每日 08:30 排程只執行日更，不會持續啟動手機伺服器或公開Tunnel。本功能不重建或刪除排程。

### 電腦關機仍可用的Cloudflare雲端版

固定HTTPS網址：

`https://taistock-cloud-monitor.taistock-monitor.workers.dev/`

雲端版由公開 GitHub repo `charleschan0414-creator/taistock-daily-monitor` 的 GitHub Actions 產生資料：台北交易日 16:10 主跑，隔日 07:40 備援再跑；兩個 cron 分別是 UTC `10 8 * * 1-5` 與 `40 23 * * 0-4`。免費 GitHub 排程可能延遲，頁面會顯示資料來源、資料日、最後成功時間與回測/優化狀態。成果與官方 gzip cache 都在公開 repo，任何人均可讀取，嚴禁放入 token、cookie、資料庫或憑證。

每次依序重跑 100 日無前視回測、70/30 策略閘門與當日清單。每日使用相同滾動窗口重新評估不等於新的獨立樣本外證據；`strategy_history.jsonl` 按資料日保留稽核。候選只有通過最後 30 日保留測試才啟用，且新設定只用於後續訊號，不回寫當期結果。未計手續費、交易稅、滑價、借券費與融券限制；不保證提高勝率或獲利。

Cloudflare Worker 只從固定 GitHub Raw 白名單讀取 `public_data/`，GitHub 失敗才回退既有 KV，並繼續以 TWSE MIS 合併現價、以 TAIFEX 官方端點取得夜盤與美股連動期貨情境。PWA 在頁面可見時每 60 秒刷新，切到背景會暫停以減少不必要請求。Windows 關機不影響 GitHub/Cloudflare 讀取。雲端原始碼、測試及說明在 `cloud/`；Cloudflare OAuth 憑證由 Wrangler 存放於使用者設定檔，不會放入專案或 ZIP。

## 驗證

```bat
python -m unittest discover -s tests -v
python -m py_compile app.py mobile_server.py taistock\*.py tests\*.py
python app.py --help
python mobile_server.py --help
```
