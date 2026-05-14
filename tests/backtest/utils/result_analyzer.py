"""Backtest result analysis utilities."""

from __future__ import annotations

from typing import Any


class ResultAnalyzer:
    """Analyzes backtest results."""

    @staticmethod
    def calculate_metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
        """Calculate key backtest metrics from trades."""
        if not trades:
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "profit_factor": 0.0,
                "consecutive_wins": 0,
                "consecutive_losses": 0,
            }
        
        winning = [t for t in trades if t.get("pnl", 0) > 0]
        losing = [t for t in trades if t.get("pnl", 0) < 0]
        breakeven = [t for t in trades if t.get("pnl", 0) == 0]
        
        total_wins = sum(t.get("pnl", 0) for t in winning)
        total_losses = sum(abs(t.get("pnl", 0)) for t in losing)
        
        # Calculate consecutive wins/losses
        max_consecutive_wins = 0
        max_consecutive_losses = 0
        current_wins = 0
        current_losses = 0
        
        for trade in trades:
            pnl = trade.get("pnl", 0)
            if pnl > 0:
                current_wins += 1
                current_losses = 0
                max_consecutive_wins = max(max_consecutive_wins, current_wins)
            elif pnl < 0:
                current_losses += 1
                current_wins = 0
                max_consecutive_losses = max(max_consecutive_losses, current_losses)
            else:
                current_wins = 0
                current_losses = 0
        
        return {
            "total_trades": len(trades),
            "winning_trades": len(winning),
            "losing_trades": len(losing),
            "breakeven_trades": len(breakeven),
            "win_rate": len(winning) / len(trades) if trades else 0.0,
            "avg_win": total_wins / len(winning) if winning else 0.0,
            "avg_loss": total_losses / len(losing) if losing else 0.0,
            "total_wins": total_wins,
            "total_losses": total_losses,
            "profit_factor": total_wins / total_losses if total_losses > 0 else 0.0,
            "consecutive_wins": max_consecutive_wins,
            "consecutive_losses": max_consecutive_losses,
        }
    
    @staticmethod
    def calculate_drawdown(daily_equity: list[dict[str, Any]]) -> dict[str, Any]:
        """Calculate maximum drawdown from daily equity curve."""
        if not daily_equity:
            return {"max_drawdown": 0.0, "max_drawdown_pct": 0.0}
        
        equities = [day.get("equity", 0) for day in daily_equity]
        if not equities:
            return {"max_drawdown": 0.0, "max_drawdown_pct": 0.0}
        
        max_equity = equities[0]
        max_drawdown = 0.0
        max_drawdown_pct = 0.0
        
        for equity in equities:
            if equity > max_equity:
                max_equity = equity
            
            drawdown = max_equity - equity
            drawdown_pct = drawdown / max_equity if max_equity > 0 else 0.0
            
            if drawdown > max_drawdown:
                max_drawdown = drawdown
                max_drawdown_pct = drawdown_pct
        
        return {
            "max_drawdown": max_drawdown,
            "max_drawdown_pct": max_drawdown_pct,
        }
    
    @staticmethod
    def calculate_risk_metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
        """Calculate risk-related metrics."""
        if not trades:
            return {
                "avg_risk_per_trade": 0.0,
                "avg_r_multiple": 0.0,
                "risk_reward_ratio": 0.0,
            }
        
        risks = []
        rewards = []
        
        for trade in trades:
            entry = trade.get("entry_price", 0)
            stop = trade.get("stop_price", 0)
            exit_price = trade.get("exit_price", 0)
            direction = trade.get("direction", "LONG")
            
            if direction.upper() == "LONG":
                risk = entry - stop if entry > stop else 0
                reward = exit_price - entry if exit_price > entry else 0
            else:  # SHORT
                risk = stop - entry if stop > entry else 0
                reward = entry - exit_price if exit_price < entry else 0
            
            if risk > 0:
                risks.append(risk)
                if reward >= 0:
                    r_multiple = reward / risk
                    rewards.append(r_multiple)
        
        avg_risk = sum(risks) / len(risks) if risks else 0.0
        avg_reward = sum(rewards) / len(rewards) if rewards else 0.0
        
        return {
            "avg_risk_per_trade": avg_risk,
            "avg_r_multiple": avg_reward,
            "risk_reward_ratio": avg_reward / avg_risk if avg_risk > 0 else 0.0,
        }
    
    @staticmethod
    def analyze_by_symbol(trades: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Group and analyze results by symbol."""
        by_symbol = {}
        
        for trade in trades:
            symbol = trade.get("symbol", "UNKNOWN")
            if symbol not in by_symbol:
                by_symbol[symbol] = []
            by_symbol[symbol].append(trade)
        
        results = {}
        for symbol, symbol_trades in by_symbol.items():
            winning = [t for t in symbol_trades if t.get("pnl", 0) > 0]
            losing = [t for t in symbol_trades if t.get("pnl", 0) < 0]
            total_pnl = sum(t.get("pnl", 0) for t in symbol_trades)
            
            results[symbol] = {
                "total_trades": len(symbol_trades),
                "winning_trades": len(winning),
                "losing_trades": len(losing),
                "win_rate": len(winning) / len(symbol_trades) if symbol_trades else 0.0,
                "total_pnl": total_pnl,
                "avg_pnl": total_pnl / len(symbol_trades) if symbol_trades else 0.0,
            }
        
        return results
