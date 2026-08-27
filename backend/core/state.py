"""进程级单例状态的唯一来源（Single source of truth）。

本模块集中存放整个进程共享的可变单例（锁、缓存、事件对象）以及与之强耦合的
不可变常量集合。任何模块都不得再在自己的模块层级新建 ``threading.RLock()`` /
``threading.Lock()`` / ``threading.Event()`` 或进程级缓存 dict / set；统一从这里导入。

配置类值（DB_CONFIG、SERVER_HOST、CONSOLE_PASSWORD 等）放在 ``core/config.py``；
时区（APP_TIMEZONE）放在 ``core/time.py``，不要回流到本模块。
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Hashable

from cachetools import TTLCache


class KeyedLockManager:
    """按 key 取 ``RLock`` 的通用管理器。

    收敛原先五套「guard 锁 + dict[key]→RLock」的复制粘贴样板；
    用法：``with XXX_LOCKS.lock(key):``。
    """

    __slots__ = ("_guard", "_locks")

    def __init__(self) -> None:
        self._guard = threading.RLock()
        self._locks: Dict[Hashable, threading.RLock] = {}

    def lock(self, key: Hashable) -> threading.RLock:
        with self._guard:
            return self._locks.setdefault(key, threading.RLock())


# ---------------------------------------------------------------------------
# DB / 进程生命周期
# ---------------------------------------------------------------------------
# DB_LOCK 串行化所有直接读写 DB 的关键路径；STOP_EVENT 用于后台调度线程退出。
DB_LOCK = threading.RLock()
STOP_EVENT = threading.Event()


# ---------------------------------------------------------------------------
# 模型缓存（模型数据 + NewAPI 可用率，统一由 MODEL_CACHE_LOCK 守卫）
# ---------------------------------------------------------------------------
# MODEL_DATA_CACHE 缓存分组/模型清单；NEWAPI_UPTIME_CACHE 缓存各主站可用率。
# 两者共用 MODEL_CACHE_LOCK，NEWAPI_UPTIME_* 虽属 NewAPI 域但为避免死锁与
# 多锁竞争，仍由 MODEL_CACHE_LOCK 统一守卫。
MODEL_CACHE_TTL_SECONDS = 90
UPTIME_CACHE_TTL_SECONDS = 300
# 容器用 cachetools.TTLCache：有界内存 + 过期自动兜底；
# 条目内的 updated_monotonic 仍保留，业务侧的 age 计算与 SWR 逻辑不变。
MODEL_DATA_CACHE: TTLCache = TTLCache(maxsize=512, ttl=MODEL_CACHE_TTL_SECONDS)
MODEL_CACHE_REFRESHING: set[int] = set()
NEWAPI_UPTIME_CACHE: TTLCache = TTLCache(maxsize=512, ttl=UPTIME_CACHE_TTL_SECONDS)
NEWAPI_UPTIME_REFRESHING: set[str] = set()
MODEL_CACHE_LOCK = threading.RLock()


# ---------------------------------------------------------------------------
# NewAPI 用户侧 API 密钥列表缓存
# ---------------------------------------------------------------------------
# NewAPI 用户侧 API 密钥列表（/api/token/）缓存。渠道页会按多个主站渠道
# 连续匹配同一个上游账号，短期复用列表可避免重复分页请求。
NEWAPI_USER_TOKEN_LIST_CACHE_TTL_SECONDS = 15
NEWAPI_USER_TOKEN_LIST_CACHE: TTLCache = TTLCache(
    maxsize=512, ttl=NEWAPI_USER_TOKEN_LIST_CACHE_TTL_SECONDS
)
NEWAPI_USER_TOKEN_LIST_LOCK = threading.RLock()

# 上游用户分组（/api/user/self/groups）缓存。渠道页会对同一上游的多个渠道
# 逐一匹配，分组数据与渠道无关，短期复用可把 N 次上游请求收敛为 1 次。
NEWAPI_MATCH_GROUPS_CACHE_TTL_SECONDS = 30
NEWAPI_MATCH_GROUPS_CACHE: TTLCache = TTLCache(
    maxsize=256, ttl=NEWAPI_MATCH_GROUPS_CACHE_TTL_SECONDS
)
NEWAPI_MATCH_GROUPS_LOCK = threading.RLock()


# ---------------------------------------------------------------------------
# 主站渠道 key 读取（串行化 + 限流冷却 + 短期缓存）
# ---------------------------------------------------------------------------
# NewAPI 对 POST /api/channel/:id/key 通常有更严格的频控；渠道页不能并发轰炸
# 主站。所有主站 key 读取在进程内按站点串行，并留出最小间隔。
MAIN_CHANNEL_KEY_REQUEST_LOCK = threading.RLock()
MAIN_CHANNEL_KEY_LAST_REQUEST_AT: Dict[str, float] = {}
MAIN_CHANNEL_KEY_RATE_LIMIT_UNTIL: Dict[str, float] = {}
MAIN_CHANNEL_KEY_MIN_INTERVAL_SECONDS = 2.0
MAIN_CHANNEL_KEY_RATE_LIMIT_COOLDOWN_SECONDS = 30.0
ADMIN_KEY_SYNC_PROOF_BATCH_SIZE = 3
# 主站 key 在同一页面刷新周期内不会变化。短期缓存可避免页面重复加载或多个调用方
# 重复读取同一个渠道时再次触发主站的保护接口限流。
MAIN_CHANNEL_KEY_CACHE_TTL_SECONDS = 60
MAIN_CHANNEL_KEY_CACHE: TTLCache = TTLCache(
    maxsize=1024, ttl=MAIN_CHANNEL_KEY_CACHE_TTL_SECONDS
)

# 主站「全量渠道 key 刷新批次」：一次性后台任务（手动触发 / 2FA 验证后触发）。
# trigger 端点写入条目并派生单次 daemon 线程逐渠道刷新（受上面的串行 + 2s 最小
# 间隔 + 429 冷却保护）；进度条目由 /api/admin/sites 序列化轮询读取。键为
# admin_site_id。进程重启即失效，重新触发即可。
ADMIN_KEY_REFRESH_BATCH_LOCK = threading.RLock()
ADMIN_KEY_REFRESH_BATCHES: Dict[int, Dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# 浏览器会话锁（管理站 / NewAPI 普通站 / sub2api 管理端）
# ---------------------------------------------------------------------------
# Refresh tokens rotate on every successful dashboard refresh. Serialize by
# admin site so concurrent channel reads cannot race the same refresh cookie.
ADMIN_BROWSER_SESSION_LOCKS = KeyedLockManager()
# 普通监控站点与管理站的 refresh cookie 独立轮换，不能共用锁命名空间。
NEWAPI_SITE_BROWSER_SESSION_LOCKS = KeyedLockManager()
# sub2api 管理端 JWT 与普通监控站点登录态分开保存，并按主站串行轮换。
ADMIN_SUB2API_SESSION_LOCKS = KeyedLockManager()
ADMIN_SUB2API_EXPIRY_SKEW_SECONDS = 60
BROWSER_AUTH_MODE = "browser"


# ---------------------------------------------------------------------------
# sub2api 刷新令牌 + 站点鉴权
# ---------------------------------------------------------------------------
# sub2api refresh token 也可能轮换；同一个上游站点的并发请求必须串行刷新，
# 并在短时间内复用同一轮刷新结果，避免第二个请求继续使用已轮换的旧 refresh_token。
SUB2API_REFRESH_LOCKS = KeyedLockManager()
SUB2API_REFRESH_CACHE_TTL_SECONDS = 30.0
SUB2API_REFRESH_CACHE: TTLCache = TTLCache(
    maxsize=512, ttl=SUB2API_REFRESH_CACHE_TTL_SECONDS
)
# A refresh-token lock is not enough for a monitor request: a browser sync,
# a scheduled check, and a manual check can all update the same site row.  Keep
# the complete credential decision (reload -> request -> conditional write)
# serial for each ordinary sub2api site.
SUB2API_SITE_AUTH_LOCKS = KeyedLockManager()


# ---------------------------------------------------------------------------
# 会话同步（浏览器桥接扩展）
# ---------------------------------------------------------------------------
SESSION_SYNC_TTL_SECONDS = 120
SESSION_SYNC_TERMINAL_STATUSES = {
    "ready",
    "no_session",
    "expired",
    "permission_required",
    "extension_unavailable",
    "failed",
}
SESSION_SYNC_PAGE_FAILURES = {
    "EXTENSION_UNAVAILABLE": (
        "extension_unavailable",
        "未安装或未连接浏览器同步扩展",
    ),
    "ORIGIN_PERMISSION_REQUIRED": (
        "permission_required",
        "扩展需要该站点的读取权限",
    ),
    "COOKIE_PERMISSION_REQUIRED": (
        "permission_required",
        "扩展需要读取 NewAPI 登录 Cookie 的权限，请允许后重新同步",
    ),
    "SYNC_FAILED": ("failed", "登录态同步失败"),
}
SESSION_SYNC_MAX_BODY_BYTES = 40 * 1024
SESSION_SYNC_MAX_TOKEN_LENGTH = 16 * 1024
SESSION_SYNC_REQUEST_LOCK = threading.RLock()


# ---------------------------------------------------------------------------
# 控制台登录会话
# ---------------------------------------------------------------------------
CONSOLE_SESSIONS: Dict[str, float] = {}
CONSOLE_SESSIONS_LOCK = threading.RLock()


# ---------------------------------------------------------------------------
# 渠道发现导入边界
# ---------------------------------------------------------------------------
# Discovery imports are intentionally bounded so a malformed client cannot
# create an unbounded number of monitoring sites in one request.
MAX_DISCOVERY_IMPORT_ITEMS = 100
MAX_DISCOVERY_CHANNEL_IDS_PER_ITEM = 1000
MAX_DISCOVERY_INTERVAL_MINUTES = 1440
