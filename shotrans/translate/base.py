"""翻译服务共用的类型。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Job:
    idx: int
    text: str  # 已经换好 ⟨n⟩ 占位符的原文


class TranslateError(Exception):
    """kind：connect / first_token / stall / http / auth（401/403，key 无效或未填）/ format。
    done：出错前已经交出去的段落编号。"""

    def __init__(self, kind: str, detail: str = "", done=frozenset()):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail
        self.done = frozenset(done)


LANG_NAMES = {"en2zh": ("英文", "简体中文"), "zh2en": ("简体中文", "英文")}
