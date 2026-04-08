"""
Shared constants for feature engineering across all strategy modules.

Centralised here to prevent drift between strategy implementations.
"""

# Epsilon for near-zero denominator guards.  Values below this threshold are
# treated as zero to avoid numerically unstable divisions that could produce
# extreme outliers on near-flat price windows.
FEATURE_EPSILON = 1e-12
