CREATE TABLE IF NOT EXISTS positions (
  symbol TEXT PRIMARY KEY,
  qty NUMERIC,
  avg_price NUMERIC
);

CREATE TABLE IF NOT EXISTS orders (
  client_order_id TEXT PRIMARY KEY,
  symbol TEXT,
  qty NUMERIC,
  status TEXT,
  broker_order_id TEXT
);

CREATE TABLE IF NOT EXISTS pnl (
  date DATE PRIMARY KEY,
  realized NUMERIC,
  unrealized NUMERIC
);
