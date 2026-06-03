"""
输入安全清洗：防 Prompt 注入、控制字符攻击、超长输入。

企业级纵深防御的一环 — LLM 层不做输入清洗是 OWASP Top 10 for LLM Apps 的头号风险。
"""

import re
import unicodedata
from utils.logger import logger

# 常见 Prompt 注入特征（启发式检测，非穷举）
_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|directives?)",
    r"disregard\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?)",
    r"you\s+are\s+now\s+(a\s+)?(different|new|another)",
    r"forget\s+(all|everything|your)\s+(instructions?|training|rules?)",
    r"system\s*(prompt|message|instruction):",
    r"<\|im_start\|>|<\|im_end\|>",  # LLM 特殊 token 注入
    r"\[system\]\(|\[/system\]",       # 角色切换注入
]

_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)

MAX_QUERY_LENGTH = 2000  # 与 AnalyzeRequest.query max_length 一致


def sanitize_user_input(text: str) -> tuple[str, bool]:
    """清洗用户输入，返回 (safe_text, was_flagged)。

    清洗步骤：
    1. 移除 Unicode 方向控制字符（RTLO 等 Bidi 攻击）
    2. NFKC 规范化（全角/半角统一，防同形字符绕过）
    3. 截断至 MAX_QUERY_LENGTH
    4. 检测常见 Prompt 注入模式（warning 级，不阻断）

    Returns:
        (safe_text, was_flagged) — was_flagged=True 表示检测到可疑模式并已记录告警。
    """
    if not text:
        return "", False

    # Step 1: 移除 Unicode 控制字符（Bidi / zero-width / 等）
    cleaned = "".join(ch for ch in text if unicodedata.category(ch) not in ("Cf", "Cc"))
    # 保留常见空白字符
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", "", cleaned)

    # Step 2: NFKC 规范化
    normalized = unicodedata.normalize("NFKC", cleaned)

    # Step 3: 截断
    if len(normalized) > MAX_QUERY_LENGTH:
        normalized = normalized[:MAX_QUERY_LENGTH]
        logger.info(f"用户输入截断至 {MAX_QUERY_LENGTH} 字符")

    # Step 4: 注入特征检测
    was_flagged = False
    if _INJECTION_RE.search(normalized):
        logger.warning(f"检测到疑似 Prompt 注入: {normalized[:100]}...")
        was_flagged = True

    return normalized.strip(), was_flagged
