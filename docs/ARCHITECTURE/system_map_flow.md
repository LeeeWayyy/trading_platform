# System Architecture - Data Flow

```mermaid
flowchart TB
  subgraph presentation["Presentation Layer"]
    svc_auth_service["Auth Service"]
    svc_web_console_ng["Web Console Ng"]
  end
  subgraph orchestration["Orchestration"]
    svc_orchestrator["Orchestrator"]
  end
  subgraph core["Core Services"]
    svc_alert_worker["Alert Worker"]
    svc_backtest_worker["Backtest Worker"]
    svc_execution_gateway["Execution Gateway"]
    svc_market_data_service["Market Data Service"]
    svc_model_registry["Model Registry"]
    svc_signal_service["Signal Service"]
  end
  subgraph domain["Domain Logic"]
    strat_alpha_baseline["Alpha Baseline"]
    lib_analytics["Analytics"]
    strat_backtest["Backtest"]
    lib_data["Data"]
    strat_ensemble["Ensemble"]
    lib_models["Models"]
    lib_trading["Trading"]
  end
  subgraph infra["Infrastructure"]
    lib_common["Common"]
    lib_core["Core"]
    lib_platform["Platform"]
    lib_web_console_data["Web Console Data"]
    lib_web_console_services["Web Console Services"]
    ext_alpaca[("Alpaca API")]
    ext_grafana[("Grafana")]
    ext_postgres[("PostgreSQL")]
    ext_prometheus[("Prometheus")]
    ext_redis[("Redis")]
  end

  svc_orchestrator -->|request signals| svc_signal_service
  svc_signal_service -->|compute alpha| strat_alpha_baseline
  svc_orchestrator -->|execute orders| svc_execution_gateway
  svc_execution_gateway -->|submit orders| ext_alpaca
  ext_alpaca -->|order updates| svc_execution_gateway
  svc_market_data_service -->|publish quotes| ext_redis
  ext_redis -->|subscribe quotes| svc_signal_service
  svc_web_console_ng -->|manual trading| svc_execution_gateway
  svc_web_console_ng -->|realtime updates| ext_redis
  ext_redis -->|positions/orders| svc_web_console_ng
  svc_alert_worker -->|alert events| ext_redis
  lib_trading -->|circuit breaker state| ext_redis
  svc_web_console_ng -->|strategy/model management| lib_web_console_services
  svc_signal_service -->|strategy active check| ext_postgres

  %% Styling
  classDef external fill:#f9f,stroke:#333,stroke-width:2px
  class ext_alpaca external
  class ext_redis external
  class ext_postgres external
  class ext_prometheus external
  class ext_grafana external
```

## Legend

- **Boxes**: Internal services and libraries
- **Cylinders**: External systems (databases, APIs)
- **Arrows**: Data flow direction

