import asyncio

import pytest

from apps.web_console.auth.rate_limiter import RateLimiter
from tests.apps.web_console.auth.test_rate_limiter import FakeRedis


@pytest.mark.asyncio
async def test_rate_limiter_handles_burst():
    redis = FakeRedis()
    rl = RateLimiter(redis_client=redis)

    tasks = [rl.check_rate_limit(f"user{i%3}", "api", 5, 30) for i in range(30)]
    results = await asyncio.gather(*tasks)
    assert all(isinstance(res[0], bool) for res in results)
