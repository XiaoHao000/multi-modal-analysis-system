import json
import os
from typing import Dict, List
from config import Config
from utils.logger import logger

_DEFAULT_INTENT_SCHEMA = {
    "analysis_types": ["趋势分析", "对比分析", "排名分析", "占比分析", "异常检测"],
    "metrics": ["借方金额", "贷方金额", "收入", "费用", "毛利率", "净利率"],
    "dimensions": ["会计科目", "科目类别", "成本中心", "部门", "时间(按季度)", "时间(按月份)"],
}

_DEFAULT_KNOWLEDGE = [
    "毛利率计算公式：(主营业务收入 - 主营业务成本) / 主营业务收入 * 100%。收入查 fact_ledger 中收入科目的 credit_amount，成本查费用科目的 debit_amount。",
    "净利率计算公式：(收入 - 成本 - 费用) / 收入 * 100%。费用包括销售费用(6601)、管理费用(6602)、财务费用(6603)。",
    "资产负债率 = 负债总额 / 资产总额 * 100%。一般制造业 40%-60% 为合理区间，超过 70% 需警惕偿债风险。",
    "流动比率 = 流动资产 / 流动负债。大于 2.0 为优秀，小于 1.0 表示短期偿债能力不足。",
    "ROE（净资产收益率）= 净利润 / 平均净资产 * 100%。制造业 ROE >= 15% 为优秀，是杜邦分析体系的核心指标。",
    "应收账款周转率 = 赊销收入 / 平均应收账款余额。周转率越高说明回款越快，贸易企业通常 > 12次/年。",
    "借贷平衡约束：会计恒等式'有借必有贷，借贷必相等'。在任何时间点，总借方金额 = 总贷方金额。",
    "主营业务收入 = SUM(account_category='收入' 的 credit_amount)。JOIN dim_account 按 account_category 筛选，GROUP BY period 做月度趋势。",
]


def _parse_knowledge_file(filepath: str) -> tuple:
    """解析知识文件，返回 (entries: list[str], intent_schema: dict)。

    兼容两种格式：
      - 旧格式: ["条目1", "条目2", ...]
      - 新格式: {"entries": [...], "intent_schema": {...}}
    """
    if not os.path.isfile(filepath):
        return None, None

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"知识文件读取失败 ({filepath}): {e}")
        return None, None

    if isinstance(data, list):
        # 旧格式：纯文本条目列表
        text_entries = [item for item in data if isinstance(item, str)]
        logger.info(f"从文件加载 {len(text_entries)} 条业务知识 (旧格式): {filepath}")
        return text_entries, None

    if isinstance(data, dict):
        entries = data.get("entries", [])
        intent_schema = data.get("intent_schema")
        if isinstance(entries, list) and all(isinstance(item, str) for item in entries):
            logger.info(f"从文件加载 {len(entries)} 条业务知识 (新格式): {filepath}")
            return entries, intent_schema

    logger.warning(f"知识文件格式无法识别 ({filepath})，使用内置默认值")
    return None, None


def load_business_knowledge() -> list[str]:
    """从 JSON 文件加载业务知识条目，失败时回退到内置默认值。"""
    entries, _ = _parse_knowledge_file(Config.knowledge_file_path)
    if entries is not None:
        return entries
    logger.info(f"知识文件不存在或格式错误 ({Config.knowledge_file_path})，使用内置默认值")
    return _DEFAULT_KNOWLEDGE


def reload_knowledge() -> list[str]:
    """强制重新加载知识文件（用于热更新），同步更新模块级缓存变量。"""
    global BUSINESS_KNOWLEDGE
    logger.info("正在热加载业务知识…")
    fresh = load_business_knowledge()
    BUSINESS_KNOWLEDGE = fresh
    return fresh


def get_intent_options() -> Dict[str, List[str]]:
    """从知识文件读取意图分析的候选值（分析类型、指标、维度），解析失败时使用内置默认值。

    业务人员在 knowledge.json 的 intent_schema 中新增指标/维度后，
    系统重启自动生效，无需修改代码。
    """
    _, schema = _parse_knowledge_file(Config.knowledge_file_path)
    if schema and all(k in schema for k in ("analysis_types", "metrics", "dimensions")):
        return {
            "analysis_types": schema["analysis_types"],
            "metrics": schema["metrics"],
            "dimensions": schema["dimensions"],
        }
    return dict(_DEFAULT_INTENT_SCHEMA)  # 浅拷贝


# 模块级变量，兼容旧 import
BUSINESS_KNOWLEDGE = load_business_knowledge()
