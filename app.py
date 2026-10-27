#!/usr/bin/env python
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from taistock.backup import create_backup, restore_backup
from taistock.health import export_diagnostics
from taistock.backtest import (OfficialHistoryFetcher, build_backtest,
                               required_history_days, write_backtest_outputs)
from taistock.broker_risk import enrich_broker_risk
from taistock.day_trading import (fetch_and_apply_execution_eligibility,
                                  pause_execution_for_closed_market)
from taistock.market_calendar import latest_official_quote_date, market_session_status
from taistock.net import HttpClient
from taistock.notify import notify, build_summary
from taistock.scanner import Scanner
from taistock.storage import save_result, load_optimizer_state
from taistock.strategy_optimize import load_strategy_gate, optimize_strategy

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
TAIPEI = timezone(timedelta(hours=8))


def app_version():
    try:
        return str(json.loads((ROOT / "software_manifest.json").read_text(encoding="utf-8"))["version"])
    except (OSError, ValueError, KeyError, TypeError):
        return "unknown"


def data_end_date(now=None):
    """Return the latest date eligible for complete close data in Taipei."""
    current = now or datetime.now(TAIPEI)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    local = current.astimezone(TAIPEI)
    target = local.date() if local.hour >= 16 else local.date() - timedelta(days=1)
    return target.isoformat()


def latest_complete_date(histories):
    if not histories:
        raise ValueError("no complete official trading days collected")
    return histories[-1]["date"]


def validate_daily_scan(result, expected_data_date):
    """Reject partial scans before any latest output or learning state is replaced."""
    required_lists = ("errors", "long_top10", "short_top10")
    if (not isinstance(result, dict) or not isinstance(result.get("data_date"), str)
            or any(not isinstance(result.get(key), list) for key in required_lists)):
        raise ValueError("每日更新缺少必要欄位；保留前次成功結果")
    if result.get("top100_count") != 100 or result.get("history_success_count") != 100 or result.get("errors"):
        raise ValueError("每日更新未達100檔歷史資料完整門檻；保留前次成功結果")
    if result.get("data_date") != expected_data_date:
        raise ValueError("資料日期不是最新完整官方交易日；保留前次成功結果")
    for direction in ("long", "short"):
        rows = result.get(f"{direction}_top10", [])
        if len(rows) > 10:
            raise ValueError(f"{direction}觀察名單超過10檔")
        for row in rows:
            risk = row.get("risk_pct")
            if not isinstance(risk, (int, float)) or not 1.5 <= risk <= 5:
                raise ValueError("單筆風險必須介於1.5%至5%")
            entry, stop, target = row.get("entry_trigger"), row.get("stop_loss"), row.get("target1")
            prices = (entry, stop, target)
            valid = all(isinstance(value, (int, float)) for value in prices)
            if valid:
                valid = stop < entry < target if direction == "long" else target < entry < stop
            if not valid:
                raise ValueError(f"{direction}進場、停損及目標價格方向無效")


def carry_forward_if_market_closed(data_dir=DATA, now=None, client=None):
    """Use the last trading-day observation when both official markets have not advanced.

    Official TWSE/TPEx source dates are the closure evidence.  A mismatched or
    malformed source still raises and leaves the prior file untouched.
    """
    current = now or datetime.now(TAIPEI)
    if not isinstance(current, datetime) or current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    requested_date = current.astimezone(TAIPEI).date().isoformat()
    path = Path(data_dir) / "latest.json"
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
        data_date = datetime.strptime(previous["data_date"], "%Y-%m-%d").date().isoformat()
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if requested_date <= data_date:
        return None

    network = client or HttpClient()
    calendar = market_session_status(network, requested_date)
    try:
        official_quote_date = latest_official_quote_date(network)
    except ValueError:
        if calendar.get("status") == "closed":
            raise
        return None
    if official_quote_date != data_date:
        return None
    if calendar.get("status") == "closed":
        checked = pause_execution_for_closed_market(
            previous, requested_date, calendar.get("reason", "official_market_closed"),
            calendar.get("source", "official_market_calendar"))
    else:
        checked = fetch_and_apply_execution_eligibility(network, previous, requested_date)
    session = checked.get("market_session") or {}
    if session.get("status") != "paused_no_new_official_session":
        return None
    if session.get("latest_official_date") != data_date:
        raise ValueError("休市沿用日期與最後完整資料日不一致")

    collection = dict(checked.get("data_collection") or {})
    collection.update({
        "latest_complete_date": data_date,
        "latest_official_quote_date": data_date,
        "carried_forward": True,
        "session_checked_at": current.astimezone(TAIPEI).isoformat(timespec="seconds"),
    })
    checked["data_collection"] = collection
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(json.dumps(checked, ensure_ascii=False, indent=2,
                                        allow_nan=False) + "\n",
                             encoding="utf-8", newline="\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return checked


def update(send_notification=False):
    def progress(text, pct): print(f"[{pct:3d}%] {text}", flush=True)
    progress("核對官方市場是否產生新交易日…", 2)
    paused = carry_forward_if_market_closed(DATA)
    if paused is not None:
        session = paused["market_session"]
        print(session["notice"])
        print("狀態：休市／暫停沿用｜所有可執行訊號已清空")
        if send_notification:
            fallback = notify(build_summary(paused), DATA)
            print("通知：" + (f"Toast 失敗，已寫入 {fallback}" if fallback else "Windows Toast 已送出"))
        return paused
    state = load_optimizer_state(DATA)
    strategy_gate = load_strategy_gate(DATA)
    end_date = data_end_date()
    fetcher = OfficialHistoryFetcher(
        HttpClient(timeout=35, retries=6), DATA / "backtest_cache", throttle=0.35,
        progress=lambda text, ratio: progress(text, int(ratio * 75)))
    histories = fetcher.collect(end_date, 6)
    result = Scanner(HttpClient(), progress=progress, weights=state["weights"], optimization=state,
                     strategy_gate=strategy_gate).run_from_days(histories, limit=100)
    validate_daily_scan(result, latest_complete_date(histories))
    execution_date = datetime.now(TAIPEI).date().isoformat()
    progress(f"核對 {execution_date} 官方當沖資格…", 90)
    calendar = market_session_status(HttpClient(), execution_date)
    if calendar.get("status") == "closed":
        if latest_official_quote_date(HttpClient()) != result["data_date"]:
            raise ValueError("休市日掃描結果不是兩市場最新完整官方行情")
        result = pause_execution_for_closed_market(
            result, execution_date, calendar.get("reason", "official_market_closed"),
            calendar.get("source", "official_market_calendar"))
    else:
        result = fetch_and_apply_execution_eligibility(HttpClient(), result, execution_date)
    validate_daily_scan(result, latest_complete_date(histories))
    progress("檢查觀察名單的分點短線風險（研究顯示、不計分）…", 92)
    result = enrich_broker_risk(result)
    result["data_collection"] = {"complete_trading_days": len(histories),
                                 "latest_complete_date": latest_complete_date(histories),
                                 "network_requests": fetcher.requests, "cache_hits": fetcher.cache_hits}
    if calendar.get("status") == "closed":
        result["data_collection"].update({
            "latest_official_quote_date": result["data_date"],
            "session_checked_at": datetime.now(TAIPEI).isoformat(timespec="seconds"),
        })
    result = save_result(result, DATA)
    print(f"資料日期: {result['data_date']}\n成交量前100實得: {result['top100_count']}｜偏多: {result['long_count']}｜偏空: {result['short_count']}")
    for label, rows in (("偏多", result["long_top10"]), ("偏空", result["short_top10"])):
        print(f"\n{label}觀察（最多10名）")
        for i, row in enumerate(rows, 1):
            print(f"{i:2d}. {row['code']} {row['name']}｜分數 {row['score']:.2f}｜觸發 {row['entry_trigger']}｜停損 {row['stop_loss']}｜{row['reason']}")
    print("自我優化：" + result["optimization"]["status"])
    print("策略閘門：" + result["strategy_gate"]["status"])
    print("聲明：規則式研究觀察，非投資建議，不保證獲利；程式不會自動下單。")
    if send_notification:
        fallback = notify(build_summary(result), DATA)
        print("通知：" + (f"Toast 失敗，已寫入 {fallback}" if fallback else "Windows Toast 已送出"))
    return result


def run_backtest(days=100):
    if days < 1:
        raise ValueError("回測交易日數須大於 0")
    end_date = data_end_date()
    total = required_history_days(days)
    print(f"抓取截至 {end_date} 的 {total} 個四源完整交易日（可續跑快取）…", flush=True)
    fetcher = OfficialHistoryFetcher(
        HttpClient(timeout=35, retries=6), DATA / "backtest_cache", throttle=0.35,
        progress=lambda text, ratio: print(f"[{ratio * 100:5.1f}%] {text}", flush=True))
    histories = fetcher.collect(end_date, total)
    result = build_backtest(histories, signal_days=days)
    result["data_collection"] = {"complete_trading_days": len(histories),
                                 "latest_complete_date": latest_complete_date(histories),
                                 "network_requests": fetcher.requests, "cache_hits": fetcher.cache_hits}
    write_backtest_outputs(result, DATA, days)
    metrics = result["metrics"]
    print(f"回測期間: {result['period']['from']} ~ {result['period']['to']}（{metrics['signal_days']} 信號日）")
    for scenario in ("gross", "standard", "stress"):
        scenario_metrics = result["metrics_scenarios"][scenario]
        cost = result["costs"][scenario]
        print(f"{scenario}（每筆已成交雙邊總成本 {cost:.3%}）")
        for label, values in (("整體", scenario_metrics), ("偏多", scenario_metrics["long"]),
                              ("偏空", scenario_metrics["short"])):
            factor = "無虧損樣本" if values["profit_factor"] is None else f"{values['profit_factor']:.4f}"
            print(f"  {label}: 訊號 {values['signals']}｜成交 {values['trades']}｜未觸發 {values['untriggered']}｜未結算 {values['unresolved']}｜"
                  f"勝率 {values['win_rate']:.2%}｜平均報酬 {values['average_return']:.4%}｜中位數 {values['median_return']:.4%}｜"
                  f"累積訊號批次指標 {values['cumulative_return']:.4%}｜最大回撤 {values['max_drawdown']:.4%}｜PF {factor}")
    portfolio = result.get("portfolio_standard", {})
    if portfolio:
        print("保守帳戶模型（研究估算）："
              f"期初資金 {portfolio['initial_capital']:,.0f}｜期末資金 {portfolio['final_capital']:,.0f}｜"
              f"淨報酬 {portfolio['net_return']:.2%}｜最大回撤 {portfolio['max_drawdown']:.2%}｜"
              f"最大同時持倉 {portfolio['peak_positions']}｜成本 {portfolio['costs']:,.0f}")
    print(f"資料：網路請求 {fetcher.requests}｜快取命中 {fetcher.cache_hits}")
    print("成本假設：" + result["cost_assumption"])
    print("限制：訊號於收盤後形成，只能在次一交易日當沖並於收盤前強制沖銷；日 OHLC 無法重建盤中先後，採停損優先、target1 優先。上列為訊號批次，不是帳戶報酬；空方假設可取得券源且未含借券費。")
    print("聲明：歷史規則回測不代表未來績效，不保證獲利。")
    return result


def run_strategy_optimization(backtest=None, incumbent_gate=None, write_output=True):
    if backtest is None:
        source = DATA / "backtest_100d.json"
        if not source.exists():
            raise FileNotFoundError("找不到 data/backtest_100d.json，請先執行 python app.py --backtest 100")
        backtest = json.loads(source.read_text(encoding="utf-8"))
    # Standalone research must never overwrite the active scanner report.
    output_path = DATA / "strategy_optimization_candidate.json" if write_output else None
    report = optimize_strategy(backtest, output_path, incumbent_gate=incumbent_gate)
    print(f"時間切分: {report['train_dates']['from']} ~ {report['train_dates']['to']}（70日訓練）｜"
          f"{report['test_dates']['from']} ~ {report['test_dates']['to']}（30日保留測試）")
    print(f"有限網格: {report['candidate_count']} 組｜訓練成交門檻 {report['minimum_samples']['train_trades']}｜"
          f"測試成交門檻 {report['minimum_samples']['test_trades']}")
    for split in ("train", "test"):
        for label, section in (("基準", report["baseline"]), ("候選", report["candidate"])):
            for scenario in ("gross", "standard", "stress"):
                values = section[split][scenario]
                pf = "無虧損樣本" if values["profit_factor"] is None else f"{values['profit_factor']:.4f}"
                print(f"{split} {label} {scenario}: 訊號 {values['signals']}｜成交 {values['trades']}｜勝率 {values['win_rate']:.2%}｜"
                      f"平均 {values['average_return']:.4%}｜中位數 {values['median_return']:.4%}｜"
                      f"批次累積 {values['cumulative_signal_batch_metric']:.4%}｜最大回撤 {values['max_drawdown']:.4%}｜PF {pf}")
    print("決策：" + report["decision"]["status"])
    print("限制：保留測試只作最終啟用判定；歷史結果不保證提高未來勝率或報酬。")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="台股成交量前100條件篩選（研究觀察工具，非投資建議）")
    parser.add_argument("--update", action="store_true", help="更新資料後結束（不開啟視窗）")
    parser.add_argument("--notify", action="store_true", help="更新完成後發送 Windows 通知；須搭配 --update")
    parser.add_argument("--backtest", nargs="?", const=100, type=int, metavar="DAYS",
                        help="以官方逐日資料執行次一交易日限定當沖回測（預設 100 信號日；收盤強制沖銷）")
    parser.add_argument("--optimize-strategy", action="store_true",
                        help="產生研究候選報告；不修改正式策略（前70日訓練、後30日描述）")
    parser.add_argument("--backup", nargs="?", const="", metavar="PATH",
                        help="安全備份白名單內的個人資料；未指定路徑時存入 backups 資料夾")
    parser.add_argument("--restore", type=Path, metavar="PATH",
                        help="驗證並安全還原備份；較新的現有資料不會被覆蓋")
    parser.add_argument("--diagnostics", nargs="?", const="", metavar="PATH",
                        help="匯出匿名系統健康診斷；未指定路徑時存入 diagnostics 資料夾")
    args = parser.parse_args(argv)
    if args.notify and not args.update: parser.error("--notify 須搭配 --update")
    operations = sum((bool(args.update), args.backtest is not None, bool(args.optimize_strategy),
                      args.backup is not None, args.restore is not None, args.diagnostics is not None))
    if operations > 1:
        parser.error("--update、--backtest、--optimize-strategy、--backup、--restore、--diagnostics 不可共用")
    if args.diagnostics is not None:
        now = datetime.now(TAIPEI)
        destination = (ROOT / "diagnostics" / f"health-{now:%Y%m%d-%H%M%S}.json"
                       if args.diagnostics == "" else Path(args.diagnostics))
        try: result = export_diagnostics(DATA, app_version(), destination, now)
        except Exception as exc:
            print(f"診斷匯出失敗：{exc}", file=sys.stderr); return 1
        print(f"匿名診斷已匯出：{result}")
        return 0
    if args.backup is not None:
        destination = None if args.backup == "" else Path(args.backup)
        try: result = create_backup(ROOT, destination)
        except Exception as exc:
            print(f"備份失敗：{exc}", file=sys.stderr); return 1
        print(f"備份完成：{result['path']}｜檔案數：{result['file_count']}")
        return 0
    if args.restore is not None:
        try: result = restore_backup(ROOT, args.restore)
        except Exception as exc:
            print(f"還原失敗：{exc}", file=sys.stderr); return 1
        print(f"還原完成：{result['path']}｜檔案數：{result['file_count']}")
        return 0
    if args.backtest is not None:
        try: run_backtest(args.backtest)
        except Exception as exc:
            print(f"回測失敗：{exc}", file=sys.stderr); return 1
        return 0
    if args.optimize_strategy:
        try: run_strategy_optimization()
        except Exception as exc:
            print(f"策略優化失敗：{exc}", file=sys.stderr); return 1
        return 0
    if args.update:
        try: update(args.notify)
        except Exception as exc:
            print(f"更新失敗：{exc}", file=sys.stderr); return 1
        return 0
    from taistock.ui import Dashboard
    Dashboard(DATA).mainloop(); return 0


if __name__ == "__main__": raise SystemExit(main())
