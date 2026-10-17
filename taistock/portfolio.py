"""Conservative account-level simulation using actual entry and exit dates."""
from __future__ import annotations

import math


def simulate_portfolio(trades, initial_capital=1_000_000, risk_per_trade=0.005,
                       max_positions=10, max_new_per_direction_per_day=5,
                       max_notional_per_position=0.20, cost_rate=0.00435):
    eligible = [dict(row) for row in trades
                if row.get("status") == "settled" and row.get("triggered")
                and all(row.get(key) is not None for key in
                        ("entry_date", "exit_date", "entry", "exit", "stop_loss"))]
    eligible.sort(key=lambda row: (row["entry_date"], -float(row.get("score") or 0),
                                   float(row.get("volume_rank") or math.inf), str(row.get("code", ""))))
    dates = sorted({row[key] for row in eligible for key in ("entry_date", "exit_date")})
    cash = float(initial_capital)
    open_positions = []
    realized = []
    skipped = []
    curve = []
    gross_total = costs_total = 0.0
    peak_positions = 0
    peak_notional = 0.0
    direction_peaks = {"long": 0.0, "short": 0.0}

    def close(position):
        nonlocal cash, gross_total, costs_total
        row = position["trade"]
        shares = position["shares"]
        gross = ((float(row["exit"]) - float(row["entry"])) * shares
                 if row["direction"] == "long"
                 else (float(row["entry"]) - float(row["exit"])) * shares)
        cost = position["notional"] * cost_rate
        cash += position["notional"] + gross - cost
        gross_total += gross
        costs_total += cost
        realized.append({**row, "shares": shares, "notional": position["notional"],
                         "gross_pnl": gross, "cost": cost, "net_pnl": gross - cost})

    for date in dates:
        exiting = [position for position in open_positions
                   if position["trade"]["exit_date"] == date]
        for position in exiting:
            close(position)
            open_positions.remove(position)
        new_by_direction = {"long": 0, "short": 0}
        for row in [item for item in eligible if item["entry_date"] == date]:
            direction = row["direction"]
            if any(position["trade"].get("code") == row.get("code") for position in open_positions):
                skipped.append({**row, "reason": "duplicate_code"})
                continue
            if new_by_direction.get(direction, 0) >= max_new_per_direction_per_day:
                skipped.append({**row, "reason": "daily_direction_limit"})
                continue
            if len(open_positions) >= max_positions:
                skipped.append({**row, "reason": "max_positions"})
                continue
            equity = cash + sum(position["notional"] for position in open_positions)
            entry_price = float(row["entry"])
            per_share_risk = abs(entry_price - float(row["stop_loss"]))
            if entry_price <= 0 or per_share_risk <= 0:
                skipped.append({**row, "reason": "invalid_risk"})
                continue
            risk_shares = math.floor((equity * risk_per_trade) / per_share_risk)
            notional_shares = math.floor((equity * max_notional_per_position) / entry_price)
            cash_shares = math.floor(cash / entry_price)
            shares = min(risk_shares, notional_shares, cash_shares)
            shares = (shares // 1000) * 1000
            if shares < 1000:
                skipped.append({**row, "reason": "insufficient_capital"})
                continue
            notional = float(row["entry"]) * shares
            cash -= notional
            position = {"trade": row, "shares": shares, "notional": notional}
            open_positions.append(position)
            new_by_direction[direction] = new_by_direction.get(direction, 0) + 1
            peak_positions = max(peak_positions, len(open_positions))
            current_notional = sum(item["notional"] for item in open_positions)
            peak_notional = max(peak_notional, current_notional)
            for side in direction_peaks:
                side_notional = sum(item["notional"] for item in open_positions
                                    if item["trade"]["direction"] == side)
                direction_peaks[side] = max(direction_peaks[side], side_notional)
            if row["exit_date"] == date:
                close(position)
                open_positions.remove(position)
        equity = cash + sum(position["notional"] for position in open_positions)
        curve.append({"date": date, "equity": equity, "cash": cash,
                      "open_positions": len(open_positions)})

    final = cash + sum(position["notional"] for position in open_positions)
    peak = float(initial_capital)
    max_drawdown = 0.0
    for point in curve:
        peak = max(peak, point["equity"])
        max_drawdown = min(max_drawdown, point["equity"] / peak - 1)
    return {
        "initial_capital": float(initial_capital), "final_capital": final,
        "total_return": final / initial_capital - 1, "net_return": final / initial_capital - 1,
        "max_drawdown": max_drawdown, "realized_trades": realized, "skipped": skipped,
        "peak_positions": peak_positions, "open_positions": len(open_positions),
        "gross_pnl": gross_total, "net_pnl": gross_total - costs_total,
        "costs": costs_total, "equity_curve": curve, "realized_only_equity": True,
        "model": {"risk_per_trade": risk_per_trade, "max_positions": max_positions,
                  "max_new_per_direction_per_day": max_new_per_direction_per_day,
                  "max_notional_per_position": max_notional_per_position,
                  "cost_rate": cost_rate, "board_lot_shares": 1000},
        "event_order": "existing_exits_then_priority_entries_then_same_day_exits",
        "exposure": {
            "peak_notional": peak_notional,
            "peak_fraction_of_initial_capital": peak_notional / initial_capital,
            "by_direction": {
                side: {"peak_notional": direction_peaks[side],
                       "realized_trades": sum(row["direction"] == side for row in realized),
                       "gross_pnl": sum(row["gross_pnl"] for row in realized if row["direction"] == side),
                       "net_pnl": sum(row["net_pnl"] for row in realized if row["direction"] == side)}
                for side in ("long", "short")
            },
        },
        "borrow_fees_included": False, "short_availability_mode": "assumed_available",
    }
