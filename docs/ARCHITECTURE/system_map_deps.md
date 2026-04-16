# System Architecture - Dependencies

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
  end

  lib_core -.-> lib_platform
  lib_core -.-> lib_trading
  lib_data -.-> lib_core
  lib_data -.-> lib_platform
  lib_models -.-> lib_data
  lib_platform -.-> lib_core
  lib_platform -.-> lib_data
  lib_trading -.-> lib_core
  lib_trading -.-> lib_data
  lib_trading -.-> lib_models
  lib_web_console_data -.-> lib_core
  lib_web_console_data -.-> lib_data
  lib_web_console_data -.-> lib_platform
  lib_web_console_services -.-> lib_core
  lib_web_console_services -.-> lib_data
  lib_web_console_services -.-> lib_models
  lib_web_console_services -.-> lib_platform
  lib_web_console_services -.-> lib_trading
  lib_web_console_services -.-> lib_web_console_data
  svc_alert_worker -.-> lib_platform
  svc_auth_service -.-> lib_core
  svc_auth_service -.-> lib_platform
  svc_backtest_worker -.-> lib_trading
  svc_execution_gateway -.-> lib_core
  svc_execution_gateway -.-> lib_data
  svc_execution_gateway -.-> lib_platform
  svc_execution_gateway -.-> lib_trading
  svc_market_data_service -.-> lib_core
  svc_market_data_service -.-> lib_data
  svc_market_data_service -.-> lib_platform
  svc_model_registry -.-> lib_models
  svc_orchestrator -.-> lib_core
  svc_orchestrator -.-> lib_trading
  svc_signal_service -.-> lib_core
  svc_signal_service -.-> lib_platform
  svc_signal_service -.-> strat_alpha_baseline
  svc_web_console_ng -.-> lib_analytics
  svc_web_console_ng -.-> lib_common
  svc_web_console_ng -.-> lib_core
  svc_web_console_ng -.-> lib_data
  svc_web_console_ng -.-> lib_models
  svc_web_console_ng -.-> lib_platform
  svc_web_console_ng -.-> lib_trading
  svc_web_console_ng -.-> lib_web_console_data
  svc_web_console_ng -.-> lib_web_console_services
  svc_web_console_ng -.-> strat_alpha_baseline
```

## Legend

- **Dashed arrows**: Code dependencies (imports)
- Edges to common infrastructure libs (common, secrets, health) are hidden for clarity

