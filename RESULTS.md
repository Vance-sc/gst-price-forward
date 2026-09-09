# Seasonality backtest results

Weights: rel_value 0.35, momentum 0.22, volume 0.18, cs_spread 0.13, seasonality 0.12

## Before (v2 audit 2026-09-09)
| Horizon | LOCK mean | HOLD mean |
|---------|-----------|-----------|
| 30d | +2.32% | −0.54% |
| 60d | +4.33% | −1.79% |

## After (v2+seas)
| Horizon | LOCK mean (hit, n) | HOLD mean (hit, n) | vs ALL |
|---------|-------------------|--------------------|--------|
| 30d | +2.52% (0.62, 520) | −0.56% (0.43, 564) | LOCK > ALL ✓ |
| 60d | +4.78% (0.68, 516) | −1.78% (0.40, 557) | LOCK > ALL ✓ |

Pass criterion met. Diesmillo 60d LOCK now beats SPLIT (was inverted on plain v2).
