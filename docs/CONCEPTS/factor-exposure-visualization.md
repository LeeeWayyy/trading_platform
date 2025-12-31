# Factor Exposure Visualization

## Plain English Explanation
Factor exposure visualization shows how much your portfolio is tilted toward
specific risk factors such as momentum, value, or size. Each factor is a
systematic driver of returns. A heatmap makes it easy to see which factors are
positive (green), neutral (white), or negative (red) across time.

## Why It Matters
Factor exposures explain *why* a portfolio behaves the way it does. If a
portfolio suddenly underperforms, the heatmap can reveal that exposure to a
falling factor (like momentum during a reversal) increased. This helps
portfolio managers adjust risk, diversify exposures, and avoid unintended bets
that can dominate performance.

## Common Pitfalls
- **Confusing raw values with standardized exposures:** Z-scores are relative
  to the cross-section. A positive z-score means above-average exposure, not a
  guaranteed positive return.
- **Ignoring short positions:** If you weight by absolute market value but drop
  the sign, you lose the directional meaning of long vs short exposure.
- **Mixing dates with different holdings:** If holdings are only current,
  historical exposure charts will repeat the same weights unless snapshots are
  stored.
- **Overreacting to small moves:** Tiny exposure changes can look dramatic on a
  color scale if you do not keep the heatmap centered at zero.

## Examples
### Portfolio-Level Exposure
- Holdings:
  - Stock A: weight 60%, momentum z-score 1.0
  - Stock B: weight 40%, momentum z-score 2.0
- Portfolio momentum exposure = 0.6 * 1.0 + 0.4 * 2.0 = **1.4**

### Short Exposure Example
- Stock C: weight -20% (short), value z-score 1.5
- Contribution = -0.2 * 1.5 = **-0.3** (shorting a positive value factor)

## Further Reading
- [Fama-French 3-Factor Model (Ken French Data Library)](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html)
- [Investopedia: Factor Investing](https://www.investopedia.com/terms/f/factor-investing.asp)
- [MSCI: Understanding Factor Exposures](https://www.msci.com/our-solutions/indexes/factor-indexes)
