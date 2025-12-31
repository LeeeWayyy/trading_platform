"""Tax lot tracking and reporting utilities.

Provides wash sale detection, tax-loss harvesting recommendations,
and IRS Form 8949 export functionality.
"""

from __future__ import annotations

from libs.tax.export import TaxReportRow
from libs.tax.form_8949 import Form8949Exporter, Form8949Row
from libs.tax.protocols import AsyncConnectionPool
from libs.tax.tax_loss_harvesting import (
    HarvestingOpportunity,
    HarvestingRecommendation,
    TaxLossHarvester,
)
from libs.tax.wash_sale_detector import (
    WashSaleAdjustment,
    WashSaleDetector,
    WashSaleMatch,
)

__all__ = [
    "AsyncConnectionPool",
    "Form8949Exporter",
    "Form8949Row",
    "HarvestingOpportunity",
    "HarvestingRecommendation",
    "TaxLossHarvester",
    "TaxReportRow",
    "WashSaleAdjustment",
    "WashSaleDetector",
    "WashSaleMatch",
]
