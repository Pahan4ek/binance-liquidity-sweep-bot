from typing import List, Tuple, Optional
from src.models import Candle, SwingLevel, SweepSignal

def find_swings(candles: List[Candle], left: int = 3, right: int = 3) -> List[SwingLevel]:
    """
    Identifies fractal swing highs and swing lows and tracks their mitigation status.
    A swing level is mitigated (deactivated) when a subsequent candle CLOSES beyond it.
    """
    total = len(candles)
    if total < left + right + 1:
        return []

    swings: List[SwingLevel] = []

    for i in range(left, total - right):
        pivot = candles[i]
        
        # Check Swing High
        is_high = True
        for j in range(1, left + 1):
            if candles[i - j].high >= pivot.high:
                is_high = False
                break
        if is_high:
            for j in range(1, right + 1):
                if candles[i + j].high >= pivot.high:
                    is_high = False
                    break

        if is_high:
            swings.append(SwingLevel(
                time=pivot.open_time,
                price=pivot.high,
                level_type="high",
                bar_index=i,
                active=True
            ))

        # Check Swing Low
        is_low = True
        for j in range(1, left + 1):
            if candles[i - j].low <= pivot.low:
                is_low = False
                break
        if is_low:
            for j in range(1, right + 1):
                if candles[i + j].low <= pivot.low:
                    is_low = False
                    break

        if is_low:
            swings.append(SwingLevel(
                time=pivot.open_time,
                price=pivot.low,
                level_type="low",
                bar_index=i,
                active=True
            ))

    # Evaluate mitigation on historical bars
    for sw in swings:
        # Check all candles that closed after the swing was formed (after bar_index + right)
        eval_start = sw.bar_index + right + 1
        for k in range(eval_start, total):
            c = candles[k]
            if not c.is_closed:
                continue
            if sw.level_type == "high" and c.close > sw.price:
                sw.active = False
                sw.mitigated_at = c.open_time
                break
            elif sw.level_type == "low" and c.close < sw.price:
                sw.active = False
                sw.mitigated_at = c.open_time
                break

    return swings

def get_active_swings_near_price(
    swings: List[SwingLevel],
    price: float,
    max_candidates: int = 5
) -> Tuple[List[SwingLevel], List[SwingLevel]]:
    """
    Returns active (unmitigated) swing highs and swing lows sorted by proximity to price.
    Closest levels to the current price come first.
    """
    active_highs = [
        s for s in swings
        if s.active and s.level_type == "high" and s.price >= price
    ]
    active_lows = [
        s for s in swings
        if s.active and s.level_type == "low" and s.price <= price
    ]

    # Sort by absolute distance to current price (closest first)
    active_highs.sort(key=lambda s: abs(s.price - price))
    active_lows.sort(key=lambda s: abs(s.price - price))

    return active_highs[:max_candidates], active_lows[:max_candidates]

def check_candle_for_sweeps(
    current_candle: Candle,
    prior_closed_candles: List[Candle],
    active_highs: List[SwingLevel],
    active_lows: List[SwingLevel],
    volume_multiplier: float = 1.5,
    min_delta_ratio: float = 0.1,
    min_score: int = 4
) -> Optional[SweepSignal]:
    """
    Evaluates whether the current closed candle executed a Liquidity Sweep setup.
    Unified logic used by both live engine and backtest.
    """
    if len(prior_closed_candles) < 20:
        return None

    # Baseline average volume calculated strictly on prior closed candles (excluding current)
    avg_vol = sum(c.volume for c in prior_closed_candles[-20:]) / 20.0
    if avg_vol <= 0:
        return None

    vol_ratio = current_candle.volume / avg_vol
    c_high = current_candle.high
    c_low = current_candle.low
    c_close = current_candle.close
    delta = current_candle.delta
    delta_ratio = current_candle.delta_ratio
    oi_delta = current_candle.oi_delta

    best_signal: Optional[SweepSignal] = None

    # 1. Check BEARISH SWEEPS (wick breaks above Swing High, closes back inside)
    for high_level in active_highs:
        if c_high > high_level.price and c_close < high_level.price:
            score = 0
            reasons = []

            # Volume factor (max 2)
            if vol_ratio >= volume_multiplier:
                score += 2
                reasons.append(f"Volume spike x{vol_ratio:.1f} (>= {volume_multiplier}x)")
            elif vol_ratio >= 1.1:
                score += 1
                reasons.append(f"Above average volume x{vol_ratio:.1f}")
            else:
                reasons.append(f"Low volume x{vol_ratio:.1f}")

            # Delta factor (max 2)
            if delta < 0 and delta_ratio >= min_delta_ratio:
                score += 2
                reasons.append(f"Strong sell delta ({delta:+.1f}, {delta_ratio*100:.1f}%)")
            elif delta < 0:
                score += 1
                reasons.append(f"Mild sell delta ({delta:+.1f})")
            else:
                reasons.append(f"Positive delta ({delta:+.1f})")

            # Open Interest factor (max 2)
            if oi_delta is not None:
                if oi_delta < 0:
                    score += 2
                    reasons.append(f"OI dropped ({oi_delta:+.0f}) -> stops flushed")
                else:
                    reasons.append(f"OI did not drop ({oi_delta:+.0f})")

            if score >= min_score and (best_signal is None or score > best_signal.score):
                best_signal = SweepSignal(
                    direction="short",
                    time=current_candle.open_time,
                    candle_close=c_close,
                    level_price=high_level.price,
                    extreme_price=c_high,
                    stop_loss=c_high,
                    score=score,
                    reasons=reasons,
                    delta=delta,
                    delta_ratio=delta_ratio,
                    volume_ratio=vol_ratio,
                    oi_delta=oi_delta
                )

    # 2. Check BULLISH SWEEPS (wick breaks below Swing Low, closes back inside)
    for low_level in active_lows:
        if c_low < low_level.price and c_close > low_level.price:
            score = 0
            reasons = []

            # Volume factor (max 2)
            if vol_ratio >= volume_multiplier:
                score += 2
                reasons.append(f"Volume spike x{vol_ratio:.1f} (>= {volume_multiplier}x)")
            elif vol_ratio >= 1.1:
                score += 1
                reasons.append(f"Above average volume x{vol_ratio:.1f}")
            else:
                reasons.append(f"Low volume x{vol_ratio:.1f}")

            # Delta factor (max 2)
            if delta > 0 and delta_ratio >= min_delta_ratio:
                score += 2
                reasons.append(f"Strong buy delta (+{delta:.1f}, {delta_ratio*100:.1f}%)")
            elif delta > 0:
                score += 1
                reasons.append(f"Mild buy delta (+{delta:.1f})")
            else:
                reasons.append(f"Negative delta ({delta:+.1f})")

            # Open Interest factor (max 2)
            if oi_delta is not None:
                if oi_delta < 0:
                    score += 2
                    reasons.append(f"OI dropped ({oi_delta:+.0f}) -> stops flushed")
                else:
                    reasons.append(f"OI did not drop ({oi_delta:+.0f})")

            if score >= min_score and (best_signal is None or score > best_signal.score):
                best_signal = SweepSignal(
                    direction="long",
                    time=current_candle.open_time,
                    candle_close=c_close,
                    level_price=low_level.price,
                    extreme_price=c_low,
                    stop_loss=c_low,
                    score=score,
                    reasons=reasons,
                    delta=delta,
                    delta_ratio=delta_ratio,
                    volume_ratio=vol_ratio,
                    oi_delta=oi_delta
                )

    return best_signal
