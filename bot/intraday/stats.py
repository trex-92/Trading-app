import math
import statistics


def summarize(trades: list[dict], start_equity: float) -> dict:
    n = len(trades)
    out = {"n_trades": n, "start_equity": round(start_equity, 2)}
    if n == 0:
        return {**out, "total_pnl": 0.0, "return_pct": 0.0, "note": "no trades"}
    pnl = [t["pnl"] for t in trades]
    rs = [t["r"] for t in trades]
    wins, losses = [p for p in pnl if p > 0], [p for p in pnl if p < 0]
    eq = start_equity
    peak, max_dd = eq, 0.0
    for p in pnl:
        eq += p
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak * 100)
    mean_r = statistics.fmean(rs)
    se = statistics.stdev(rs) / math.sqrt(n) if n > 1 else float("nan")
    days = {t["date"] for t in trades}
    return {
        **out,
        "total_pnl": round(sum(pnl), 2), "return_pct": round(sum(pnl) / start_equity * 100, 2),
        "win_rate_pct": round(len(wins) / n * 100, 1), "avg_r": round(mean_r, 3),
        "expectancy_r_95ci": None if n < 2 else [round(mean_r - 1.96 * se, 3), round(mean_r + 1.96 * se, 3)],
        "profit_factor": round(sum(wins) / -sum(losses), 2) if losses else None,
        "avg_win_r": round(statistics.fmean([r for r in rs if r > 0]), 3) if any(r > 0 for r in rs) else None,
        "avg_loss_r": round(statistics.fmean([r for r in rs if r < 0]), 3) if any(r < 0 for r in rs) else None,
        "max_drawdown_pct": round(max_dd, 2), "days_traded": len(days),
        "avg_minutes_held": round(statistics.fmean(t["minutes_held"] for t in trades), 1),
        "total_costs": round(sum(t["costs"] for t in trades), 2),
    }
