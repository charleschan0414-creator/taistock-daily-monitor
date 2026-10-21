from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from urllib.parse import urlencode
from .core import BASELINE_WEIGHTS, evaluate_candidates, is_common_stock
from .data import parse_twse_quotes, parse_tpex_quotes, ensure_same_date, _date, _number
from .strategy_optimize import apply_strategy_gate
from .technical import (analyze_candlestick_window, assess_candlestick_conflict,
                        calculate_technical_research)

TWSE_QUOTES = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TWSE_INST = "https://www.twse.com.tw/rwd/zh/fund/T86?response=json&selectType=ALLBUT0999"
TPEX_QUOTES = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
TPEX_INST = "https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading"


def attach_candlestick_research(rows):
    """附加只供顯示的K線研究欄位，不改動任何執行欄位。"""
    for row in rows:
        row["candlestick_research"] = assess_candlestick_conflict(
            row.get("technical_research", {}), row.get("signal_direction"))
        row["candlestick_window"] = analyze_candlestick_window(
            row.get("technical_research", {}), row.get("signal_direction"))
    return rows


class Scanner:
    def __init__(self, client, workers=6, progress=None, weights=None, optimization=None, strategy_gate=None):
        self.client, self.workers = client, workers
        self.progress = progress or (lambda *_: None)
        self.weights = dict(weights or BASELINE_WEIGHTS)
        self.optimization = optimization or {"status":"學習中 0/30", "settled_count":0,
                                             "minimum_samples":30, "weights":self.weights, "version":1}
        self.strategy_gate = strategy_gate or {
            "enabled": False, "status": "未啟用｜保留基準訊號",
            "directions": {"long": True, "short": True},
        }

    def run(self, limit=100):
        self.progress("下載上市、上櫃行情與法人資料…", 5)
        twq = [q for q in parse_twse_quotes(self.client.json(TWSE_QUOTES)) if is_common_stock(q.code, q.name) and q.close > 0]
        tpq = [q for q in parse_tpex_quotes(self.client.json(TPEX_QUOTES)) if is_common_stock(q.code, q.name) and q.close > 0]
        dates = {q.date for q in twq + tpq}
        if len(dates) != 1:
            raise ValueError(f"上市與上櫃行情日期不一致：{sorted(dates)}")
        data_date = dates.pop()
        twi_raw, tpi_raw = self.client.json(TWSE_INST), self.client.json(TPEX_INST)
        twi_date = datetime.strptime(str(twi_raw["date"]), "%Y%m%d").strftime("%Y-%m-%d")
        tpi_dates = {_date(x["Date"]) for x in tpi_raw}
        if len(tpi_dates) != 1:
            raise ValueError(f"上櫃法人資料含多個日期：{sorted(tpi_dates)}")
        ensure_same_date(data_date, twi_date, "上市")
        ensure_same_date(data_date, tpi_dates.pop(), "上櫃")
        institutions = {str(x[0]).strip(): int(_number(x[-1])) for x in twi_raw.get("data", [])}
        institutions.update({str(x["SecuritiesCompanyCode"]).strip(): int(_number(x.get("TotalDifference"))) for x in tpi_raw})
        leaders = sorted(twq + tpq, key=lambda q: (-q.volume, q.code))[:limit]
        self.progress(f"已取得成交量前{len(leaders)}，下載個股歷史…", 15)
        rows, errors = [], []
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(self._history, q, data_date): (rank, q) for rank, q in enumerate(leaders, 1)}
            for done, future in enumerate(as_completed(futures), 1):
                rank, quote = futures[future]
                try:
                    history = future.result()
                    if history and history[-1][0] == data_date:
                        bars = [{"date": x[0], "open": x[3], "high": x[4], "low": x[5], "close": x[2]}
                                for x in history[-6:]]
                        rows.append({"code":quote.code, "name":quote.name, "market":quote.market, "date":data_date,
                                     "close":quote.close, "volume":quote.volume, "prev_volume":history[-2][1] if len(history) >= 2 else 0,
                                     "closes":[x[2] for x in history], "institutional_net":institutions.get(quote.code, 0),
                                     "volume_rank":rank,
                                     "technical_research": calculate_technical_research(bars, data_date)})
                    else:
                        errors.append(f"{quote.code} 歷史資料缺少目標日期 {data_date}")
                except Exception as exc:
                    errors.append(f"{quote.code} 歷史資料失敗：{exc}")
                self.progress(f"個股歷史 {done}/{len(leaders)}（失敗 {len(errors)}）", 15 + int(75 * done / max(1, len(leaders))))
        evaluated = evaluate_candidates(rows, self.weights)
        attach_candlestick_research(evaluated)
        pre_gate_counts = {
            "long": sum(x["signal_direction"] == "long" for x in evaluated),
            "short": sum(x["signal_direction"] == "short" for x in evaluated),
        }
        signals = apply_strategy_gate(evaluated, self.strategy_gate)
        long_signals = [x for x in signals if x["signal_direction"] == "long"]
        short_signals = [x for x in signals if x["signal_direction"] == "short"]
        universe = [{"code":q.code, "name":q.name, "market":q.market,
                     "date":q.date, "close":q.close, "volume":q.volume}
                    for q in sorted(twq + tpq, key=lambda quote: quote.code)]
        return {"data_date":data_date, "top100_count":len(leaders), "history_success_count":len(rows),
                "qualified_count":len(long_signals), "qualified":long_signals, "top10":long_signals[:10],
                "long_top10":long_signals[:10], "short_top10":short_signals[:10], "signals":signals,
                "universe":universe, "long_count":len(long_signals), "short_count":len(short_signals),
                "pre_gate_counts": pre_gate_counts,
                "filtered_out_counts": {"long": pre_gate_counts["long"] - len(long_signals),
                                        "short": pre_gate_counts["short"] - len(short_signals)},
                "optimization":self.optimization, "strategy_gate": self.strategy_gate, "errors":errors,
                "sources":[TWSE_QUOTES,TWSE_INST,TPEX_QUOTES,TPEX_INST],
                "updated_at":datetime.now().astimezone().isoformat(timespec="seconds")}

    def run_from_days(self, days, limit=100):
        """以官方逐日批次資料產生當日訊號，避免逐檔請求造成交易所限流。"""
        ordered = sorted(days, key=lambda row: row["date"])
        if len(ordered) < 6:
            raise ValueError("批次日更至少需要 6 個完整交易日")
        current = ordered[-1]
        data_date = current["date"]
        ranked = sorted(current["quotes"].values(),
                        key=lambda row: (-row["volume"], row["code"]))
        rows, errors, history_skipped = [], [], []
        for rank, quote in enumerate(ranked, 1):
            if len(rows) >= limit:
                break
            history = [({**item, "date": day["date"]} if item is not None else None)
                       for day in ordered[-6:]
                       for item in [day["quotes"].get(quote["code"])]]
            previous = ordered[-2]["quotes"].get(quote["code"])
            if any(item is None for item in history) or previous is None:
                history_skipped.append({"code": quote["code"], "name": quote.get("name", ""),
                                        "volume_rank": rank, "reason": "批次歷史不足6個交易日"})
                continue
            rows.append({**quote, "date": data_date, "prev_volume": previous["volume"],
                         "closes": [item["close"] for item in history],
                         "institutional_net": current["institutions"].get(quote["code"], 0),
                         "volume_rank": rank,
                         "technical_research": calculate_technical_research(history, data_date)})
        evaluated = evaluate_candidates(rows, self.weights)
        attach_candlestick_research(evaluated)
        pre_gate_counts = {
            "long": sum(row["signal_direction"] == "long" for row in evaluated),
            "short": sum(row["signal_direction"] == "short" for row in evaluated),
        }
        signals = apply_strategy_gate(evaluated, self.strategy_gate)
        long_signals = [row for row in signals if row["signal_direction"] == "long"]
        short_signals = [row for row in signals if row["signal_direction"] == "short"]
        universe = [{"code": q["code"], "name": q["name"], "market": q["market"],
                     "date": data_date, "close": q["close"], "volume": q["volume"]}
                    for q in sorted(current["quotes"].values(), key=lambda row: row["code"])]
        return {"data_date": data_date, "top100_count": len(rows),
                "history_success_count": len(rows), "qualified_count": len(long_signals),
                "qualified": long_signals, "top10": long_signals[:10],
                "long_top10": long_signals[:10], "short_top10": short_signals[:10],
                "signals": signals, "universe": universe, "long_count": len(long_signals),
                "short_count": len(short_signals), "pre_gate_counts": pre_gate_counts,
                "filtered_out_counts": {"long": pre_gate_counts["long"] - len(long_signals),
                                        "short": pre_gate_counts["short"] - len(short_signals)},
                "optimization": self.optimization,
                "strategy_gate": self.strategy_gate,
                "errors": errors, "history_skipped": history_skipped,
                "sources": ["官方逐日批次資料"],
                "updated_at": datetime.now().astimezone().isoformat(timespec="seconds")}

    def _history(self, quote, data_date):
        target = datetime.strptime(data_date, "%Y-%m-%d")
        current_month = target.replace(day=1)

        def fetch(month, current=False):
            if quote.market == "上市":
                query_date = data_date.replace("-", "") if current else month.strftime("%Y%m01")
                url = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY?" + urlencode({"response":"json","date":query_date,"stockNo":quote.code})
                rows = self.client.json(url).get("data", [])
                multiplier = 1
            else:
                url = "https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock?" + urlencode({"code":quote.code,"date":month.strftime("%Y/%m/01"),"id":""})
                tables = self.client.json(url).get("tables", [])
                rows = tables[0].get("data", []) if tables else []
                multiplier = 1000
            return [(_date(r[0]), int(_number(r[1]) * multiplier), _number(r[6]),
                     _number(r[3]), _number(r[4]), _number(r[5])) for r in rows if len(r) > 6]

        parsed = fetch(current_month, current=True)
        if len([x for x in parsed if x[0] <= data_date]) < 6:
            previous_month = (current_month - timedelta(days=1)).replace(day=1)
            parsed = fetch(previous_month) + parsed
        return sorted({x[0]: x for x in parsed if x[0] <= data_date}.values(), key=lambda x: x[0])
