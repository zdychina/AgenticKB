"""配置读写审计（BUG-05 4.4：本 bug 的利用路径是 GET，审计必须覆盖读）。

落盘：log_dir()/audit.log（JSON 行，RotatingFileHandler 5MB×3，惰性初始化）。
内容只记 user/ip/method/path 与被掩码（或被恢复）的键名列表——绝不记值。
写审计失败必须静默：审计可用性不得拖垮业务请求。
audit.log 由 logs.py 从 /api/v1/logs 的列表与读取中排除（审计与被审计对象不同窗）。
"""
from __future__ import annotations

import datetime
import json
import logging
import logging.handlers
import threading

from starlette.requests import Request

from main_control_service.logs import log_dir

_logger = logging.getLogger("cmkb.audit")
_init_lock = threading.Lock()
_initialized = False


def _ensure_handler() -> None:
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if _initialized:
            return
        try:
            directory = log_dir()
            directory.mkdir(parents=True, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(
                directory / "audit.log",
                maxBytes=5 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
            _logger.addHandler(handler)
            _logger.setLevel(logging.INFO)
            _logger.propagate = False
            _initialized = True
        except Exception:  # noqa: BLE001 —— 审计初始化失败绝不阻断业务
            _logger.propagate = False


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    client = request.client
    return client.host if client else ""


def _emit(request: Request, event: str, path: str, keys: list[str]) -> None:
    try:
        _ensure_handler()
        user = getattr(request.state, "user", None) or {}
        entry = {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "event": event,
            "user": user.get("username") or "",
            "ip": _client_ip(request),
            "method": request.method,
            "path": path,
            "keys": keys,
        }
        _logger.info(json.dumps(entry, ensure_ascii=False))
    except Exception:  # noqa: BLE001 —— 同上：审计失败静默
        pass


def log_config_read(request: Request, masked_paths: list[str], path: str) -> None:
    """浏览器（非内部）读含敏感键的配置：记 user/IP/路径/被掩码键名（不记值）。"""
    _emit(request, "config_read", path, masked_paths)


def log_config_write(request: Request, preserved_paths: list[str], path: str) -> None:
    """配置 PUT：记被"掩码回传=保留原值"处理的键名（不记值）。"""
    _emit(request, "config_write", path, preserved_paths)


def _reset_for_tests() -> None:
    """测试专用：清掉已初始化的 handler 与标记（配合 CMKB_LOG_DIR monkeypatch）。"""
    global _initialized
    with _init_lock:
        for handler in list(_logger.handlers):
            _logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:  # noqa: BLE001
                pass
        _initialized = False
