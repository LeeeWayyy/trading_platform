# ADR-0026: Shadow Mode Model Validation

**Status:** Accepted
**Date:** 2025-12-17
**Deciders:** AI Assistant (Claude Code), reviewed by Gemini and Codex
**Tags:** signal-service, model-validation, safety, hot-swap

## Context

The signal service supports hot-swapping ML models via `ModelRegistry.reload_if_changed()`. Currently, validation only checks that the new model can produce a prediction using zeros as input. This is insufficient because:

1. **Corrupt weights:** A model with valid format but garbage weights passes basic validation
2. **No comparison:** New model outputs aren't compared against previous model
3. **Immediate activation:** Bad model goes live immediately, potentially generating harmful signals
4. **No rollback:** Once activated, reverting requires manual intervention

The system needs shadow mode validation that runs new models in parallel before promoting them.

## Decision

Implement shadow mode validation in the signal service layer:

### Components

1. **ShadowModeValidator** (`apps/signal_service/shadow_validator.py`):
   - Run both old and new models on N recent feature samples
   - Compare outputs: correlation, range, sign changes
   - Reject if correlation < 0.5 or outputs differ by >50%

2. **Integration with ModelRegistry**:
   - `ModelRegistry.reload_if_changed()` accepts a validator callback
   - Validator function passed from signal_service layer (has access to feature data)
   - Maintains ModelRegistry's generic nature

3. **Async Validation**:
   - Run validation in background task (don't block requests)
   - Keep old model active until validation passes
   - Atomic swap only after validation succeeds

### Key Design Choices

**Validation in signal_service, not libs/models:**
- Shadow validation needs access to recent feature data
- `libs/models/` should remain generic and reusable
- Signal service has feature cache access for realistic test samples

**Correlation-Based Comparison:**
- Rank correlation (Spearman) tolerates scale differences
- Threshold of 0.5 catches dramatic behavior changes
- Output range check catches models with wrong scale/units

**Background Validation:**
- Production traffic continues using old model during validation
- Validation samples drawn from recent feature cache (not live requests)
- Prevents latency impact on signal generation

**Emergency Override:**
- `SKIP_SHADOW_VALIDATION=true` env var for emergencies
- Logged as WARNING for audit trail

## Consequences

### Positive

- **Safety:** Bad models are caught before going live
- **Continuity:** Old model serves traffic during validation
- **Observability:** Validation results logged to metrics (`model_shadow_validation_passed/rejected`)
- **Flexibility:** Configurable thresholds and sample count

### Negative

- **Latency:** Model reload takes longer (validation time)
- **Resource Usage:** Running two models temporarily doubles memory for model weights
- **False Positives:** Legitimate model improvements may fail correlation check

### Risks

- **Feature Drift:** Validation samples may not represent current market conditions
- **Threshold Tuning:** 0.5 correlation threshold may need adjustment per model type

### Configuration

```python
SHADOW_VALIDATION_ENABLED = True  # default
SHADOW_SAMPLE_COUNT = 100         # number of recent samples to validate
SHADOW_CORRELATION_THRESHOLD = 0.5
SHADOW_MAX_DIVERGENCE_PCT = 0.5   # 50% max difference
```

## Related

- [ADR-0004: Signal Service Architecture](./0004-signal-service-architecture.md)
- [BUGFIX_RELIABILITY_SAFETY_IMPROVEMENTS.md](../TASKS/BUGFIX_RELIABILITY_SAFETY_IMPROVEMENTS.md)
