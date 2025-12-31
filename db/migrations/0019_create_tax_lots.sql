-- Migration: 0019_create_tax_lots.sql
-- Purpose: Tax lot tracking tables (P4T7)
-- Date: 2025-12-29

-- Extension guard for gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE tax_lots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id VARCHAR(255) NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    quantity DECIMAL(18, 8) NOT NULL,
    cost_per_share DECIMAL(18, 8) NOT NULL,
    total_cost DECIMAL(18, 4) NOT NULL,
    acquired_at TIMESTAMPTZ NOT NULL,
    acquisition_type VARCHAR(20) NOT NULL,
    remaining_quantity DECIMAL(18, 8) NOT NULL,
    closed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE tax_lot_dispositions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lot_id UUID NOT NULL REFERENCES tax_lots(id),
    quantity DECIMAL(18, 8) NOT NULL,
    cost_basis DECIMAL(18, 4) NOT NULL,
    proceeds_per_share DECIMAL(18, 8) NOT NULL,
    total_proceeds DECIMAL(18, 4) NOT NULL,
    disposed_at TIMESTAMPTZ NOT NULL,
    disposition_type VARCHAR(20) NOT NULL,
    destination_order_id TEXT,
    idempotency_key TEXT NOT NULL,
    realized_gain_loss DECIMAL(18, 4) NOT NULL,
    holding_period VARCHAR(20) NOT NULL,
    wash_sale_disallowed DECIMAL(18, 4) NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_tax_lot_dispositions_idempotency UNIQUE (idempotency_key)
);

CREATE TABLE tax_wash_sale_adjustments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    disposition_id UUID NOT NULL REFERENCES tax_lot_dispositions(id),
    replacement_lot_id UUID NOT NULL REFERENCES tax_lots(id),
    matching_shares DECIMAL(18, 8) NOT NULL,
    disallowed_loss DECIMAL(18, 4) NOT NULL,
    holding_period_adjustment_days INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(disposition_id, replacement_lot_id)
);

CREATE TABLE tax_user_settings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id VARCHAR(255) NOT NULL UNIQUE,
    cost_basis_method VARCHAR(20) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_tax_lots_user_symbol ON tax_lots(user_id, symbol);
CREATE INDEX idx_tax_lots_acquired_at ON tax_lots(acquired_at);
CREATE INDEX idx_tax_lots_remaining
    ON tax_lots(user_id, symbol, remaining_quantity)
    WHERE remaining_quantity > 0;

CREATE INDEX idx_tax_lot_dispositions_lot_id ON tax_lot_dispositions(lot_id);
CREATE INDEX idx_tax_lot_dispositions_disposed_at ON tax_lot_dispositions(disposed_at);

CREATE INDEX idx_tax_wash_sale_adjustments_disposition
    ON tax_wash_sale_adjustments(disposition_id);
CREATE INDEX idx_tax_wash_sale_adjustments_replacement
    ON tax_wash_sale_adjustments(replacement_lot_id);
