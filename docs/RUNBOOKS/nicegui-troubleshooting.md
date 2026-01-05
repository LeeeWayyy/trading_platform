# NiceGUI Troubleshooting Runbook

**Last Updated:** 2026-01-04
**Related:** [nicegui-architecture](../CONCEPTS/nicegui-architecture.md), [nicegui-deployment](./nicegui-deployment.md)

## Common Errors and Solutions

### 1. "Session expired" after login

**Symptoms:**
- User redirected to login immediately after authenticating
- "Session expired" error message

**Diagnosis:**
```bash
# Check Redis connectivity
redis-cli -h $REDIS_HOST ping

# Check session keys
redis-cli -h $REDIS_HOST keys "session:*" | head -5
```

**Resolution:**
1. Verify Redis is accessible from the container
2. Check `REDIS_URL` environment variable
3. Verify session TTL configuration

### 2. WebSocket connection fails

**Symptoms:**
- Real-time updates not working
- Browser console shows WebSocket errors

**Diagnosis:**
```bash
# Check nginx config
nginx -t

# Check WebSocket upgrade headers
curl -i -H "Upgrade: websocket" -H "Connection: Upgrade" \
  http://localhost:8080/socket.io/
```

**Resolution:**
1. Verify nginx has WebSocket upgrade config
2. Check proxy timeouts (increase if needed)
3. Verify no firewall blocking WebSocket

### 3. Database connection errors

**Symptoms:**
- Pages fail to load data
- "Connection refused" errors in logs

**Diagnosis:**
```bash
# Check DB connectivity
psql $DATABASE_URL -c "SELECT 1"

# Check connection pool
curl -s http://localhost:8080/health | jq .database
```

**Resolution:**
1. Verify `DATABASE_URL` is correct
2. Check DB is accepting connections
3. Verify connection pool settings

### 4. High memory usage

**Symptoms:**
- Container memory growing over time
- OOM kills

**Diagnosis:**
```bash
# Check container stats
docker stats web-console-ng

# Check active connections
curl -s http://localhost:8080/metrics | grep connection
```

**Resolution:**
1. Check for timer/callback leaks
2. Restart container
3. Review ClientLifecycleManager usage

### 5. Slow page loads

**Symptoms:**
- Pages take >2s to load
- Users complain of slowness

**Diagnosis:**
```bash
# Check service response times
curl -w "@curl-format.txt" -o /dev/null -s http://localhost:8080/

# Check database query times
# Look at logs for slow queries
```

**Resolution:**
1. Enable query logging
2. Add database indexes
3. Implement caching

## Debug Logging Configuration

```python
# config.py
import logging

logging.getLogger("apps.web_console_ng").setLevel(logging.DEBUG)
logging.getLogger("nicegui").setLevel(logging.DEBUG)
```

Enable via environment:
```bash
export LOG_LEVEL=DEBUG
```

## WebSocket Debugging

Browser DevTools:
1. Network tab > WS filter
2. Check messages being sent/received
3. Look for connection drops

## Session Issues Diagnosis

```bash
# List all sessions
redis-cli -h $REDIS_HOST keys "session:*"

# Inspect session data
redis-cli -h $REDIS_HOST get "session:<id>"

# Check session TTL
redis-cli -h $REDIS_HOST ttl "session:<id>"
```

## Performance Profiling

```python
# Enable profiling
import cProfile
import pstats

with cProfile.Profile() as pr:
    # Code to profile
    pass

stats = pstats.Stats(pr)
stats.sort_stats(pstats.SortKey.TIME)
stats.print_stats(10)
```

## Log Analysis Commands

```bash
# Find errors
docker logs web-console-ng 2>&1 | grep -i error

# Find slow requests (>1s)
docker logs web-console-ng 2>&1 | grep -E "took [0-9]{4,}ms"

# Count errors by type
docker logs web-console-ng 2>&1 | grep -i error | \
  sed 's/.*\(Error[A-Za-z]*\).*/\1/' | sort | uniq -c | sort -rn
```

## Database/Redis Connectivity Issues

### PostgreSQL
```bash
# Test connection
psql $DATABASE_URL -c "SELECT 1"

# Check max connections
psql $DATABASE_URL -c "SHOW max_connections"

# Check current connections
psql $DATABASE_URL -c "SELECT count(*) FROM pg_stat_activity"
```

### Redis
```bash
# Test connection
redis-cli -h $REDIS_HOST ping

# Check memory usage
redis-cli -h $REDIS_HOST info memory

# Check connected clients
redis-cli -h $REDIS_HOST client list | wc -l
```

## Escalation Path

1. **L1**: Check logs, restart container
2. **L2**: Analyze metrics, check dependencies
3. **L3**: Code-level debugging, involve dev team
