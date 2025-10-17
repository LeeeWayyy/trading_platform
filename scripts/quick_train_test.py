"""
Quick training test with real data.

This script runs a fast training test to verify the pipeline works end-to-end.
Uses reduced configuration for speed.
"""

from pathlib import Path
from strategies.alpha_baseline.config import StrategyConfig, DataConfig, ModelConfig, TrainingConfig
from strategies.alpha_baseline.train import BaselineTrainer

print("=" * 60)
print("Quick Training Test with Real Data")
print("=" * 60)

# Configure for fast training
config = StrategyConfig(
    data=DataConfig(
        symbols=["AAPL", "MSFT", "GOOGL"],
        data_dir=Path("data/adjusted"),
        # Use recent data only (faster)
        train_start="2023-01-01",
        train_end="2023-12-31",
        valid_start="2024-01-01",
        valid_end="2024-06-30",
        test_start="2024-07-01",
        test_end="2024-12-31",
    ),
    model=ModelConfig(
        num_boost_round=10,  # Very few rounds for speed
        learning_rate=0.05,
        max_depth=4,  # Shallow trees
        verbose=0,
    ),
    training=TrainingConfig(
        early_stopping_rounds=5,
        experiment_name="quick_test",
        model_dir=Path("artifacts/models_test"),
    ),
)

print("\nConfiguration:")
print(f"  Symbols: {config.data.symbols}")
print(f"  Train: {config.data.train_start} to {config.data.train_end}")
print(f"  Valid: {config.data.valid_start} to {config.data.valid_end}")
print(f"  Test: {config.data.test_start} to {config.data.test_end}")
print(f"  Boost rounds: {config.model.num_boost_round}")
print(f"  Max depth: {config.model.max_depth}")

print("\nStarting training...")
print("-" * 60)

# Train without MLflow for speed
trainer = BaselineTrainer(config, use_mlflow=False)

try:
    # This will load data, train model, and evaluate
    trainer.train()

    print("\n" + "=" * 60)
    print("Quick Training Test Results")
    print("=" * 60)

    print(f"\nBest iteration: {trainer.best_iteration}")

    print("\nMetrics:")
    for name, value in trainer.metrics.items():
        if isinstance(value, (int, float)):
            print(f"  {name}: {value:.6f}")
        else:
            print(f"  {name}: {value}")

    print("\n" + "=" * 60)
    print("Training test PASSED! ✓")
    print("=" * 60)

    # Quick validation check
    if trainer.metrics['valid_ic'] > -0.5:  # Very lenient threshold for synthetic data
        print("\n✓ Model trained successfully")
        print(f"✓ Validation IC: {trainer.metrics['valid_ic']:.4f}")
    else:
        print("\n⚠ Warning: IC is very negative (may be due to synthetic data)")

except Exception as e:
    print(f"\n❌ Training test FAILED: {e}")
    import traceback
    traceback.print_exc()
    exit(1)
