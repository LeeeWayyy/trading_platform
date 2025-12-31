# Tax Lot Tracking

Tax lot tracking records the individual acquisition “lots” that make up a
position. Each lot carries its own purchase date and cost basis, which are
required for calculating realized gains/losses and determining short‑term vs
long‑term tax treatment.

In the trading platform, tax lots support:
- **Cost basis reporting:** aggregate and per-lot cost basis for accurate P&L.
- **Holding period classification:** determine tax treatment based on acquisition date.
- **Auditability:** trace trades back to the lots they created or consumed.
- **User-facing analysis:** web console views of open and closed lots by symbol.

Tax lots are user-scoped to prevent cross-account leakage. Lots transition from
**open** to **closed** when the remaining quantity reaches zero, and that status
is surfaced in reporting and UI tables.
