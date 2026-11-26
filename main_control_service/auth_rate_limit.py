"""BUG-04：auth 入口进程内限流（防暴力破解与账号枚举）。

- LoginThrottle：以 (username.lower(), client_ip) 为键的滑动失败窗口 + 锁定。
  窗口内失败达阈值 → 锁定期间 429 + Retry-After；成功即清零。
- FixedWindowRateLimiter：以 client_ip 为键的固定窗口计数（identify 防批量枚举）。

状态在进程内、重启归零——当前单容器 All-in-One 部署足够；多实例部署需换
Redis/共享存储（docs/bugs/BUG-04 §4.1）。时钟用 time.monotonic（不受系统
改时影响），测试可注入假时钟。
"""
from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable

#: 键数超过该值时触发一次过期清理——防伪造源 IP/随机用户名把计数表撑爆内存。
_PRUNE_AT = 4096


class LoginThrottle:
    """登录失败限流：滑动窗口内连续失败 N 次 → 锁定一段时间。"""

    def __init__(
        self,
        *,
        fail_limit: int = 5,
        fail_window: float = 300.0,
        lock_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fail_limit = fail_limit
        self._fail_window = fail_window
        self._lock_seconds = lock_seconds
        self._clock = clock
        self._fails: dict[tuple[str, str], deque[float]] = {}
        self._locked_until: dict[tuple[str, str], float] = {}

    def locked_for(self, key: tuple[str, str]) -> float:
        """剩余锁定秒数；未锁定返回 0。"""
        return max(0.0, self._locked_until.get(key, 0.0) - self._clock())

    def record_failure(self, key: tuple[str, str]) -> float:
        """记一次登录失败。窗口内累计达阈值 → 进入锁定并返回锁定时长，否则返回 0。"""
        now = self._clock()
        fails = self._fails.setdefault(key, deque())
        fails.append(now)
        window_start = now - self._fail_window
        while fails and fails[0] <= window_start:
            fails.popleft()
        if len(fails) < self._fail_limit:
            if len(self._fails) > _PRUNE_AT:
                self._prune_fails(now)
            return 0.0
        self._fails.pop(key, None)
        self._locked_until[key] = now + self._lock_seconds
        if len(self._locked_until) > _PRUNE_AT:
            self._prune_locks(now)
        return self._lock_seconds

    def record_success(self, key: tuple[str, str]) -> None:
        """登录成功：清零该键的失败计数与锁定。"""
        self._fails.pop(key, None)
        self._locked_until.pop(key, None)

    def _prune_fails(self, now: float) -> None:
        stale = now - self._fail_window
        for k in [k for k, f in self._fails.items() if not f or f[-1] <= stale]:
            del self._fails[k]

    def _prune_locks(self, now: float) -> None:
        for k in [k for k, until in self._locked_until.items() if until <= now]:
            del self._locked_until[k]


class FixedWindowRateLimiter:
    """固定窗口计数限流：窗口内允许 limit 次，超出即拒（identify 防枚举）。"""

    def __init__(
        self,
        *,
        limit: int,
        window: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limit = limit
        self._window = window
        self._clock = clock
        self._hits: dict[str, tuple[float, int]] = {}

    def allow(self, key: str) -> bool:
        now = self._clock()
        started, count = self._hits.get(key, (now, 0))
        if now - started >= self._window:  # 窗口过期 → 重置
            started, count = now, 0
        if count >= self._limit:
            if len(self._hits) > _PRUNE_AT:
                self._prune(now)
            return False
        self._hits[key] = (started, count + 1)
        return True

    def retry_after(self, key: str) -> float:
        """当前窗口剩余秒数（429 的 Retry-After 用）。"""
        entry = self._hits.get(key)
        if entry is None:
            return 0.0
        return max(0.0, self._window - (self._clock() - entry[0]))

    def _prune(self, now: float) -> None:
        for k in [k for k, (s, _) in self._hits.items() if now - s >= self._window]:
            del self._hits[k]
