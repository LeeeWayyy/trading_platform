#!/bin/bash
# Fix all test imports to use new grouped library structure

# Core libraries
find tests -name "*.py" -type f -exec sed -i '' 's/"libs\.redis_client\./"libs.core.redis_client./g' {} +
find tests -name "*.py" -type f -exec sed -i '' "s/'libs\.redis_client\./'libs.core.redis_client./g" {} +
find tests -name "*.py" -type f -exec sed -i '' 's/from libs\.redis_client /from libs.core.redis_client /g' {} +
find tests -name "*.py" -type f -exec sed -i '' 's/import libs\.redis_client$/import libs.core.redis_client/g' {} +

find tests -name "*.py" -type f -exec sed -i '' 's/"libs\.common\./"libs.core.common./g' {} +
find tests -name "*.py" -type f -exec sed -i '' "s/'libs\.common\./'libs.core.common./g" {} +
find tests -name "*.py" -type f -exec sed -i '' 's/from libs\.common /from libs.core.common /g' {} +
find tests -name "*.py" -type f -exec sed -i '' 's/import libs\.common$/import libs.core.common/g' {} +

find tests -name "*.py" -type f -exec sed -i '' 's/"libs\.health\./"libs.core.health./g' {} +
find tests -name "*.py" -type f -exec sed -i '' "s/'libs\.health\./'libs.core.health./g" {} +
find tests -name "*.py" -type f -exec sed -i '' 's/from libs\.health /from libs.core.health /g' {} +
find tests -name "*.py" -type f -exec sed -i '' 's/import libs\.health$/import libs.core.health/g' {} +

# Platform libraries
find tests -name "*.py" -type f -exec sed -i '' 's/"libs\.secrets\./"libs.platform.secrets./g' {} +
find tests -name "*.py" -type f -exec sed -i '' "s/'libs\.secrets\./'libs.platform.secrets./g" {} +
find tests -name "*.py" -type f -exec sed -i '' 's/from libs\.secrets /from libs.platform.secrets /g' {} +
find tests -name "*.py" -type f -exec sed -i '' 's/import libs\.secrets$/import libs.platform.secrets/g' {} +

find tests -name "*.py" -type f -exec sed -i '' 's/"libs\.alerts\./"libs.platform.alerts./g' {} +
find tests -name "*.py" -type f -exec sed -i '' "s/'libs\.alerts\./'libs.platform.alerts./g" {} +
find tests -name "*.py" -type f -exec sed -i '' 's/from libs\.alerts /from libs.platform.alerts /g' {} +
find tests -name "*.py" -type f -exec sed -i '' 's/import libs\.alerts$/import libs.platform.alerts/g' {} +

find tests -name "*.py" -type f -exec sed -i '' 's/"libs\.auth\./"libs.platform.auth./g' {} +
find tests -name "*.py" -type f -exec sed -i '' "s/'libs\.auth\./'libs.platform.auth./g" {} +
find tests -name "*.py" -type f -exec sed -i '' 's/from libs\.auth /from libs.platform.auth /g' {} +
find tests -name "*.py" -type f -exec sed -i '' 's/import libs\.auth$/import libs.platform.auth/g' {} +

# Data libraries
find tests -name "*.py" -type f -exec sed -i '' 's/"libs\.data_pipeline\./"libs.data.data_pipeline./g' {} +
find tests -name "*.py" -type f -exec sed -i '' "s/'libs\.data_pipeline\./'libs.data.data_pipeline./g" {} +
find tests -name "*.py" -type f -exec sed -i '' 's/from libs\.data_pipeline /from libs.data.data_pipeline /g' {} +
find tests -name "*.py" -type f -exec sed -i '' 's/import libs\.data_pipeline$/import libs.data.data_pipeline/g' {} +

find tests -name "*.py" -type f -exec sed -i '' 's/"libs\.feature_store\./"libs.data.feature_store./g' {} +
find tests -name "*.py" -type f -exec sed -i '' "s/'libs\.feature_store\./'libs.data.feature_store./g" {} +
find tests -name "*.py" -type f -exec sed -i '' 's/from libs\.feature_store /from libs.data.feature_store /g' {} +
find tests -name "*.py" -type f -exec sed -i '' 's/import libs\.feature_store$/import libs.data.feature_store/g' {} +

find tests -name "*.py" -type f -exec sed -i '' 's/"libs\.duckdb_catalog\./"libs.data.duckdb_catalog./g' {} +
find tests -name "*.py" -type f -exec sed -i '' "s/'libs\.duckdb_catalog\./'libs.data.duckdb_catalog./g" {} +
find tests -name "*.py" -type f -exec sed -i '' 's/from libs\.duckdb_catalog /from libs.data.duckdb_catalog /g' {} +
find tests -name "*.py" -type f -exec sed -i '' 's/import libs\.duckdb_catalog$/import libs.data.duckdb_catalog/g' {} +

# Trading libraries
find tests -name "*.py" -type f -exec sed -i '' 's/"libs\.risk\./"libs.trading.risk./g' {} +
find tests -name "*.py" -type f -exec sed -i '' "s/'libs\.risk\./'libs.trading.risk./g" {} +
find tests -name "*.py" -type f -exec sed -i '' 's/from libs\.risk /from libs.trading.risk /g' {} +
find tests -name "*.py" -type f -exec sed -i '' 's/import libs\.risk$/import libs.trading.risk/g' {} +

find tests -name "*.py" -type f -exec sed -i '' 's/"libs\.risk_management\./"libs.trading.risk_management./g' {} +
find tests -name "*.py" -type f -exec sed -i '' "s/'libs\.risk_management\./'libs.trading.risk_management./g" {} +
find tests -name "*.py" -type f -exec sed -i '' 's/from libs\.risk_management /from libs.trading.risk_management /g' {} +
find tests -name "*.py" -type f -exec sed -i '' 's/import libs\.risk_management$/import libs.trading.risk_management/g' {} +

find tests -name "*.py" -type f -exec sed -i '' 's/"libs\.qlib_primitives\./"libs.trading.qlib_primitives./g' {} +
find tests -name "*.py" -type f -exec sed -i '' "s/'libs\.qlib_primitives\./'libs.trading.qlib_primitives./g" {} +
find tests -name "*.py" -type f -exec sed -i '' 's/from libs\.qlib_primitives /from libs.trading.qlib_primitives /g' {} +
find tests -name "*.py" -type f -exec sed -i '' 's/import libs\.qlib_primitives$/import libs.trading.qlib_primitives/g' {} +

echo "Fixed all test imports to use new grouped library structure"
