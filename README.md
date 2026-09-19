# Crypto Carry

Delta-neutral funding arbitrage + on-chain directional overlay.

## Strategies
- **Carry (always-on)**: long spot + short perp, capture funding. Core.
- **Directional rule**: on-chain price+hashrate. Standalone.
- **ML**: disabled (overfit, negative alpha).

## Backtest (2020-2026, 2454 days)
| Strategy | Total | CAGR | Sharpe | DD |
|---|---|---|---|---|
| Carry BTC | +436% | +28.4% | 11.85 | 0.00%* |
| Carry ETH | +623% | +34.2% | 10.84 | 0.00%* |
| Carry 50/50 | +508% | +30.8% | 11.36 | 0.00%* |

*DD=0% artifact. Real DD estimated -3..-8% with basis risk + slippage.

## Next steps
1. Add basis risk model (spot vs perp divergence)
2. Replace make_order_stub() with real Bybit/Binance API
3. Paper trade for 30 days before live
