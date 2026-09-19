# Strategy Documentation — on-chain + funding carry

Auto-generated. Do not edit manually.

## Data

- Range: 2025-09-19 -> 2026-09-18
- Bars: 365

## Strategies

1. Rule (directional): long when price>SMA30 AND hashrate>SMA30
2. Carry (delta-neutral): long spot + short perp, capture funding
3. Hybrid: carry only when rule==1 (regime filter)

## Multi-period results

| K | i | start | end | BH% | rule_tot% | rule_a% | rule_sh | carry_tot% | carry_sh | hyb_tot% | hyb_a% | hyb_sh |
|---|---|-------|-----|-----|-----------|---------|---------|------------|----------|----------|--------|--------|
| 2 | 1 | 2025-09-19 | 2026-03-19 | -39.2 | -12.7 | +26.4 | -2.75 | +16.2 | +8.63 | -1.3 | +37.9 | -4.87 |
| 2 | 2 | 2026-03-20 | 2026-09-17 | +8.9 | +7.6 | -1.3 | +0.82 | +16.2 | +8.63 | -3.1 | -12.1 | -4.18 |
| 3 | 1 | 2025-09-19 | 2026-01-17 | -18.4 | -1.5 | +17.0 | -0.94 | +8.7 | +7.32 | -0.1 | +18.3 | -0.34 |
| 3 | 2 | 2026-01-18 | 2026-05-18 | -18.6 | -6.3 | +12.3 | -0.94 | +8.7 | +7.32 | +0.2 | +18.8 | +0.37 |
| 3 | 3 | 2026-05-19 | 2026-09-16 | -1.7 | +8.8 | +10.5 | +1.22 | +8.7 | +7.32 | -0.6 | +1.1 | -0.86 |
| 4 | 1 | 2025-09-19 | 2025-12-18 | -26.5 | -2.0 | +24.4 | -1.66 | +4.7 | +8.41 | -0.2 | +26.2 | -2.38 |
| 4 | 2 | 2025-12-19 | 2026-03-19 | -16.6 | -11.4 | +5.2 | -3.80 | +4.7 | +8.41 | +0.0 | +16.7 | +0.12 |
| 4 | 3 | 2026-03-20 | 2026-06-18 | -7.8 | -1.7 | +6.2 | -0.51 | +4.7 | +8.41 | -1.0 | +6.8 | -5.27 |
| 4 | 4 | 2026-06-19 | 2026-09-17 | +21.0 | +8.8 | -12.2 | +1.41 | +4.7 | +8.41 | -1.1 | -22.2 | -2.63 |

## Verdict

- Rule avg alpha: +9.8%
- Hybrid avg alpha: +10.2%
- Winner: HYBRID

## Notes

- Funding series synthesized with fixed seed for reproducibility
- Replace synth_funding() with real exchange data for production
- Fee assumption: 0.1% per rebalance
