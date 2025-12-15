import pytest

from apps.web_console.auth.rate_limiter import RateLimiter


class FakePipeline:
    def __init__(self, store, key):
        self.store = store
        self.key = key
        self.ops = []

    def zadd(self, key, mapping):
        self.ops.append(("zadd", key, mapping))
        return self

    def zremrangebyscore(self, key, minv, maxv):
        self.ops.append(("zrem", key, minv, maxv))
        return self

    def zcard(self, key):
        self.ops.append(("zcard", key))
        return self

    def expire(self, key, ttl):
        self.ops.append(("expire", key, ttl))
        return self

    async def execute(self):
        results = []
        for op in self.ops:
            if op[0] == "zadd":
                _, key, mapping = op
                self.store.setdefault(key, {})
                self.store[key].update(mapping)
                results.append(1)
            elif op[0] == "zrem":
                _, key, _, maxv = op
                self.store.setdefault(key, {})
                self.store[key] = {m: ts for m, ts in self.store[key].items() if ts > maxv}
                results.append(1)
            elif op[0] == "zcard":
                _, key = op
                self.store.setdefault(key, {})
                results.append(len(self.store[key]))
            elif op[0] == "expire":
                results.append(True)
        return results


class FakeRedis:
    def __init__(self):
        self.store = {}

    def pipeline(self):
        return FakePipeline(self.store, None)

    async def ping(self):
        return True

    async def eval(self, script, num_keys, *args):
        """Simulate Lua script execution for rate limiting."""
        # args: key, now, window, max_requests, member
        key = args[0]
        now = int(args[1])
        window = int(args[2])
        member = args[4]

        # Initialize key store if needed
        self.store.setdefault(key, {})

        # Add new request
        self.store[key][member] = now

        # Remove old entries outside window
        cutoff = now - window
        self.store[key] = {m: ts for m, ts in self.store[key].items() if ts > cutoff}

        # Return count
        return len(self.store[key])


class FailingRedis:
    def pipeline(self):
        raise RuntimeError("redis_down")

    async def eval(self, *args):
        raise RuntimeError("redis_down")

    async def ping(self):
        return False


@pytest.mark.asyncio()
async def test_rate_limiter_allows_within_window():
    redis = FakeRedis()
    rl = RateLimiter(redis_client=redis)

    allowed, remaining = await rl.check_rate_limit("user", "login", 3, 60)
    assert allowed is True
    assert remaining >= 0

    # Exhaust on the 4th attempt now that limits are inclusive
    await rl.check_rate_limit("user", "login", 3, 60)
    allowed, remaining = await rl.check_rate_limit("user", "login", 3, 60)
    assert allowed is True
    assert remaining == 0

    allowed, remaining = await rl.check_rate_limit("user", "login", 3, 60)
    assert allowed is False
    assert remaining <= 0


@pytest.mark.asyncio()
async def test_rate_limiter_health_check():
    rl = RateLimiter(redis_client=FakeRedis())
    assert await rl.health_check() is True


@pytest.mark.asyncio()
async def test_rate_limiter_denies_when_redis_unavailable_by_default():
    rl = RateLimiter(redis_client=FailingRedis())

    allowed, remaining = await rl.check_rate_limit("user", "login", 1, 60)

    assert allowed is False
    assert remaining == 0


@pytest.mark.asyncio()
async def test_rate_limiter_allows_when_explicitly_configured_to_allow_on_failure():
    rl = RateLimiter(redis_client=FailingRedis(), fallback_mode="allow")

    allowed, remaining = await rl.check_rate_limit("user", "metrics", 1, 60, fallback_mode="allow")

    assert allowed is True
    assert remaining == 1


@pytest.mark.asyncio()
async def test_rate_limiter_per_call_override_to_deny():
    # Instance default would allow, but explicit fallback_mode should deny
    rl = RateLimiter(redis_client=FailingRedis(), fallback_mode="allow")

    allowed, remaining = await rl.check_rate_limit("user", "login", 1, 60, fallback_mode="deny")

    assert allowed is False
    assert remaining == 0


@pytest.mark.asyncio()
async def test_rate_limiter_per_call_override_to_allow_when_default_denies():
    # Instance default denies on Redis failure, but per-call override should allow
    rl = RateLimiter(redis_client=FailingRedis(), fallback_mode="deny")

    allowed, remaining = await rl.check_rate_limit("user", "metrics", 1, 60, fallback_mode="allow")

    assert allowed is True
    assert remaining == 1
