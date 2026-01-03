# tax

## Identity
- **Type:** Library
- **Port:** N/A
- **Container:** N/A

## Interface
### Public Interface (Exported Classes & Functions)
| Class/Function | Parameters | Returns | Description |
|----------------|------------|---------|-------------|
| `TaxLossHarvester` | db_pool, wash_detector | service | Find tax-loss harvesting opportunities. |
| `HarvestingOpportunity` | fields | model | Candidate loss to harvest. |
| `HarvestingRecommendation` | fields | model | Aggregate recommendation and summary. |
| `WashSaleDetector` | db_pool | service | Identify wash sale adjustments. |
| `WashSaleMatch` | fields | model | Matched buy/sell pair. |
| `WashSaleAdjustment` | fields | model | Adjustment details. |
| `Form8949Exporter` | db_pool | exporter | Export IRS Form 8949 rows. |
| `Form8949Row` | fields | model | 8949 line item. |
| `TaxReportRow` | fields | model | Generic tax report row. |
| `AsyncConnectionPool` | - | protocol | DB pool protocol abstraction. |

## Behavioral Contracts
### TaxLossHarvester.find_opportunities(...)
**Purpose:** Identify unrealized losses suitable for harvesting.

**Preconditions:**
- Tax lots exist for user.
- Current prices provided for symbols.

**Postconditions:**
- Returns `HarvestingRecommendation` with opportunities and totals.

**Behavior:**
1. Load open tax lots from DB.
2. Compute unrealized PnL per lot.
3. Filter by loss threshold and wash sale risk.
4. Compute illustrative tax savings (example rates).

**Raises:**
- DB errors propagate.

### WashSaleDetector.check_wash_sales(...)
**Purpose:** Identify wash sale adjustments across buy/sell windows.

**Preconditions:**
- Transaction history available.

**Postconditions:**
- Returns list of `WashSaleAdjustment`.

**Behavior:**
1. Query buys/sells within 30-day window.
2. Match lots and compute disallowed loss.

**Raises:**
- DB errors propagate.

### Invariants
- Harvesting recommendations always include forward repurchase restriction (30 days).
- Wash sale windows follow IRS 30-day rule.

### State Machine (if stateful)
```
[Lots Loaded] --> [Evaluated] --> [Recommendation]
```
- **States:** lots loaded, evaluated, recommendation.
- **Transitions:** recompute on new prices.

## Data Flow
```
DB tax lots + prices -> harvesting analysis -> recommendation
```
- **Input format:** DB rows + price map.
- **Output format:** Recommendation models and export rows.
- **Side effects:** None (read-only analysis).

## Usage Examples
### Example 1: Find harvesting opportunities
```python
harvester = TaxLossHarvester(db_pool, wash_detector)
recommendation = await harvester.find_opportunities(user_id, current_prices)
```

### Example 2: Export Form 8949
```python
exporter = Form8949Exporter(db_pool)
rows = await exporter.generate(user_id, tax_year=2025)
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Missing price | symbol not in price map | Warning; lot skipped |
| No losses | all PnL >= 0 | Empty opportunity list |
| Wash sale risk | recent buys | Marked as wash sale risk |

## Dependencies
- **Internal:** `libs.tax.protocols`
- **External:** Postgres, Decimal math

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DEFAULT_MIN_LOSS_THRESHOLD` | No | 100 | Minimum loss for harvesting. |

## Error Handling
- DB errors propagate to caller.
- Validation errors in data are surfaced via exceptions.

## Observability (Services only)
### Health Check
- **Endpoint:** N/A
- **Checks:** N/A

### Metrics
| Metric Name | Type | Labels | Description |
|-------------|------|--------|-------------|
| N/A | - | - | Library has no metrics. |

## Security
- Uses DB access controls; no secrets handled directly.

## Testing
- **Test Files:** `tests/libs/tax/`
- **Run Tests:** `pytest tests/libs/tax -v`
- **Coverage:** N/A

## Related Specs
- `risk_management.md`
- `execution_gateway.md`

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Metadata
- **Last Updated:** 2026-01-03
- **Source Files:** `libs/tax/__init__.py`, `libs/tax/tax_loss_harvesting.py`, `libs/tax/wash_sale_detector.py`, `libs/tax/form_8949.py`, `libs/tax/export.py`, `libs/tax/protocols.py`
- **ADRs:** N/A
