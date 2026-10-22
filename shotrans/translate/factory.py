"""按配置造翻译客户端：主服务 / 备用服务共用一套 OpenAI 兼容客户端（不再写死「vllm」）。
内部服务名沿用旧值（路由缓存键、状态栏、历史记录都认它）：主服务 "vllm"，备用服务 "fallback"。"""
from __future__ import annotations

from ..services import normalize_base_url
from .vllm import VllmClient

ROLE_NAMES = {"primary": "vllm", "backup": "fallback"}


def service_client(c, role: str) -> VllmClient | None:
    if role not in ROLE_NAMES:
        raise ValueError(f"不认识的服务角色：{role!r}")
    svc = c.primary if role == "primary" else c.backup
    if not svc.configured:
        return None
    if role == "primary":
        t = (c.connect_timeout, c.first_token_timeout, c.stall_timeout)
    else:
        t = (c.backup_connect_timeout, c.backup_first_token_timeout, c.backup_stall_timeout)
    # 手改配置写成 …/v1/chat/completions 也能用：和设置界面、测试连接一样先去掉多余的后缀
    return VllmClient(normalize_base_url(svc.base_url), svc.api_key.strip(), *t, c.domain_hint, name=ROLE_NAMES[role],
                      model=svc.model, thinking_off=svc.thinking_off, thinking_style=svc.thinking_style)
