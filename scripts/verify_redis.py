"""
Verify Upstash Redis connectivity and Celery broker compatibility.

Usage:
    export REDIS_URL="rediss://default:xxxx@endpoint:6379"
    python scripts/verify_redis.py

Or pass directly:
    python scripts/verify_redis.py "rediss://default:xxxx@endpoint:6379"
"""

from __future__ import annotations

import os
import sys
import time


def get_url() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1]
    url = os.environ.get("REDIS_URL", "")
    if not url:
        print("Usage: python scripts/verify_redis.py <redis-url>")
        print("   or: export REDIS_URL=... && python scripts/verify_redis.py")
        sys.exit(1)
    return url


def mask_url(url: str) -> str:
    """Hide password in display."""
    if "@" not in url:
        return url
    prefix, rest = url.split("@", 1)
    if ":" in prefix:
        scheme_user = prefix.rsplit(":", 1)[0]
        return f"{scheme_user}:****@{rest}"
    return url


def main() -> None:
    url = get_url()
    print(f"Redis URL: {mask_url(url)}\n")

    total_pass = 0
    total_fail = 0

    def check(ok: bool, label: str, detail: str = "") -> None:
        nonlocal total_pass, total_fail
        if ok:
            total_pass += 1
            print(f"  [PASS] {label}")
        else:
            total_fail += 1
            msg = f"  [FAIL] {label}"
            if detail:
                msg += f" — {detail}"
            print(msg)

    # ------------------------------------------------------------------
    # 1. Basic redis-py connection
    # ------------------------------------------------------------------
    print("== Redis Connection ==")
    try:
        import redis
    except ImportError:
        print("  [FAIL] redis package not installed. Run: pip install redis")
        sys.exit(1)

    try:
        r = redis.from_url(url, decode_responses=True, socket_connect_timeout=10)
        pong = r.ping()
        check(pong, "PING → PONG")
    except Exception as e:
        check(False, "PING", str(e))
        print("\nCannot continue without a connection.")
        sys.exit(1)

    # ------------------------------------------------------------------
    # 2. SET / GET
    # ------------------------------------------------------------------
    print("\n== Basic Operations ==")
    test_key = "_pokant_verify_test"
    test_val = f"ok_{int(time.time())}"

    try:
        r.set(test_key, test_val, ex=30)
        got = r.get(test_key)
        check(got == test_val, f"SET/GET (wrote '{test_val}', read '{got}')")
    except Exception as e:
        check(False, "SET/GET", str(e))

    try:
        r.delete(test_key)
        check(r.get(test_key) is None, "DELETE (key removed)")
    except Exception as e:
        check(False, "DELETE", str(e))

    # ------------------------------------------------------------------
    # 3. LIST operations (Celery uses BRPOP/LPUSH on queues)
    # ------------------------------------------------------------------
    print("\n== Celery Queue Simulation ==")
    queue_key = "_pokant_verify_queue"
    try:
        r.delete(queue_key)
        r.lpush(queue_key, "task_1", "task_2")
        length = r.llen(queue_key)
        check(length == 2, f"LPUSH + LLEN (queue length={length})")
    except Exception as e:
        check(False, "LPUSH/LLEN", str(e))

    try:
        val = r.rpop(queue_key)
        check(val == "task_1", f"RPOP (got '{val}', FIFO order correct)")
        r.delete(queue_key)
    except Exception as e:
        check(False, "RPOP", str(e))

    # ------------------------------------------------------------------
    # 4. Tier-based queue names (what Celery will actually use)
    # ------------------------------------------------------------------
    print("\n== Tier Queue Names ==")
    for queue in ["tasks:free", "tasks:startup", "tasks:enterprise"]:
        verify_key = f"_pokant_verify_{queue}"
        try:
            r.lpush(verify_key, "test")
            r.delete(verify_key)
            check(True, f"queue '{queue}' (colon-separated key works)")
        except Exception as e:
            check(False, f"queue '{queue}'", str(e))

    # ------------------------------------------------------------------
    # 5. Server info
    # ------------------------------------------------------------------
    print("\n== Server Info ==")
    try:
        info = r.info("server")
        version = info.get("redis_version", info.get("upstash_version", "unknown"))
        check(True, f"server version: {version}")
    except Exception as e:
        # Upstash may restrict INFO; that's OK
        print(f"  [SKIP] INFO command restricted ({e})")

    try:
        info_mem = r.info("memory")
        used = info_mem.get("used_memory_human", "unknown")
        check(True, f"memory used: {used}")
    except Exception:
        print("  [SKIP] memory info restricted")

    # ------------------------------------------------------------------
    # 6. Celery broker connection test
    # ------------------------------------------------------------------
    print("\n== Celery Broker ==")
    try:
        from celery import Celery
        app = Celery("verify", broker=url)
        app.conf.update(
            broker_connection_retry_on_startup=True,
            broker_transport_options={"visibility_timeout": 900},
        )
        conn = app.connection()
        conn.ensure_connection(max_retries=1, timeout=10)
        check(True, "Celery broker connection established")
        conn.close()
    except ImportError:
        print("  [SKIP] celery not installed (pip install celery)")
    except Exception as e:
        check(False, "Celery broker connection", str(e))

    # ------------------------------------------------------------------
    # 7. TLS check
    # ------------------------------------------------------------------
    print("\n== Security ==")
    is_tls = url.startswith("rediss://")
    check(is_tls, f"TLS {'enabled' if is_tls else 'DISABLED'} ({'rediss://' if is_tls else 'redis://'})")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    total = total_pass + total_fail
    print(f"\n{'=' * 50}")
    print(f"RESULTS: {total_pass}/{total} passed, {total_fail} failed")
    if total_fail == 0:
        print("Redis is ready for production!")
    else:
        print("Fix the failures above before deploying.")
    print(f"{'=' * 50}")

    r.close()
    sys.exit(0 if total_fail == 0 else 1)


if __name__ == "__main__":
    main()
