"""Render a :class:`Signal` as the canonical human-readable trade card."""

from __future__ import annotations

from app.data.schemas import Side, Signal


def format_signal(sig: Signal) -> str:
    lines: list[str] = ["-" * 32, "", f"Market Status:\n{sig.market_status.value}", ""]
    lines.append(f"Trade Decision:\n{sig.decision.value}")
    lines.append("")
    lines.append(f"Confidence:\n{sig.confidence:.0%}")
    lines.append("")

    if sig.decision in (Side.BUY, Side.SELL) and sig.risk:
        r = sig.risk
        lines += [
            f"Entry Zone:\n{r.entry_low} - {r.entry_high}", "",
            f"Stop Loss:\n{r.stop_loss}", "",
            f"Take Profit 1:\n{r.take_profit_1}", "",
            f"Take Profit 2:\n{r.take_profit_2}", "",
            f"Risk Reward:\n1 : {r.risk_reward}", "",
            f"Position Size:\n{r.position_size} units ({r.account_risk_pct}% equity risk)", "",
        ]

    lines += [
        f"Expected Holding Time:\n{sig.expected_holding}", "",
        f"Trend Strength:\n{sig.trend_strength}", "",
        f"Probability:\n{sig.probability}", "",
        "Reason:", "",
    ]
    lines += [f"• {r}" for r in sig.reasons] or ["• (none)"]
    lines += ["", "Potential Risks:", ""]
    lines += [f"• {r}" for r in sig.risks] or ["• (none identified)"]
    lines += ["", "Final Recommendation:", "", sig.final_recommendation, "", "-" * 32]
    return "\n".join(lines)
