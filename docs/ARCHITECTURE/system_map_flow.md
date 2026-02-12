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

  %% Click links to documentation
  click svc_alert_worker "../SPECS/services/alert_worker.md"
  click svc_auth_service "../SPECS/services/auth_service.md"
  click svc_backtest_worker "../SPECS/services/backtest_worker.md"
  click svc_execution_gateway "../SPECS/services/execution_gateway.md"
  click svc_market_data_service "../SPECS/services/market_data_service.md"
  click svc_model_registry "../SPECS/services/model_registry.md"
  click svc_orchestrator "../SPECS/services/orchestrator.md"
  click svc_signal_service "../SPECS/services/signal_service.md"
  click svc_web_console_ng "../SPECS/services/web_console_ng.md"
  click lib_analytics "../SPECS/libs/analytics.md"
  click lib_common "../SPECS/libs/common.md"
  click lib_core "../SPECS/libs/core.md"
  click lib_data "../SPECS/libs/data.md"
  click lib_models "../SPECS/libs/models.md"
  click lib_platform "../SPECS/libs/platform.md"
  click lib_trading "../SPECS/libs/trading.md"
  click lib_web_console_data "../SPECS/libs/web_console_data.md"
  click lib_web_console_services "../SPECS/libs/web_console_services.md"
  click strat_alpha_baseline "../SPECS/strategies/alpha_baseline.md"
  click strat_backtest "../SPECS/strategies/backtest.md"
  click strat_ensemble "../SPECS/strategies/ensemble.md"
  click ext_redis "../SPECS/infrastructure/redis.md"
  click ext_postgres "../SPECS/infrastructure/postgres.md"
  click ext_prometheus "../SPECS/infrastructure/prometheus.md"
  click ext_grafana "../SPECS/infrastructure/grafana.md"

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
- Click any node to view its specification

