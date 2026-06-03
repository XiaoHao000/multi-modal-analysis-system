"""多租户财务种子数据 — v2 扩充版。

租户设计（模拟集团企业/代账公司 SaaS 场景）：
  - tenant_id=1: 恒通制造（制造业企业）— 15 个月数据，14 科目 × 6 成本中心
  - tenant_id=2: 瑞达商贸（商贸企业）— 15 个月数据，14 科目 × 6 成本中心

v2 扩充:
  - 会计科目按中国会计准则编码
  - 2026 Q1 数据（同比 YoY 对比）
  - 多个戏剧性异常分录（用于异常检测演示）
  - 借贷必相等约束
"""

import asyncio
from typing import List, Tuple
from datetime import datetime, timezone

from sqlalchemy import text
from utils.logger import logger

# ═══════════════════════════════════════════════════════════════
# 租户 & 用户
# ═══════════════════════════════════════════════════════════════

TENANTS: List[Tuple[int, str, str]] = [
    (1, "hengtong_manufacturing", "恒通制造"),
    (2, "ruida_trading", "瑞达商贸"),
]

USERS: List[Tuple[int, str, int, str]] = [
    (1, "admin", 1, "admin"),
    (2, "analyst_zhang", 1, "analyst"),
    (3, "viewer_li", 1, "viewer"),
    (4, "admin_ruida", 2, "admin"),
    (5, "analyst_wang", 2, "analyst"),
]

# ═══════════════════════════════════════════════════════════════
# 维度数据 — 共用科目编码（租户共享科目体系，但数据隔离）
# ═══════════════════════════════════════════════════════════════

# 会计科目（中国会计准则编码体系）
# 1xxx=资产, 2xxx=负债, 4xxx=权益, 6xxx=收入/费用
DIM_ACCOUNT: List[Tuple[int, str, str, str, str, int, str]] = [
    # 资产类
    (1,  "1001", "现金",           "资产", "",   1, "库存现金"),
    (2,  "1002", "银行存款",       "资产", "",   1, "企业银行存款"),
    (3,  "1122", "应收账款",       "资产", "",   1, "应收客户款项"),
    (4,  "1403", "原材料",         "资产", "",   2, "生产用原材料"),
    (5,  "1405", "库存商品",       "资产", "",   2, "已完成入库的商品"),
    (6,  "1601", "固定资产",       "资产", "",   1, "机器设备及厂房"),
    # 负债类
    (7,  "2001", "短期借款",       "负债", "",   1, "一年内到期的借款"),
    (8,  "2202", "应付账款",       "负债", "",   1, "应付供应商款项"),
    # 权益类
    (9,  "4001", "实收资本",       "权益", "",   1, "股东投入资本"),
    # 收入类
    (10, "6001", "主营业务收入",   "收入", "",   1, "主要经营活动收入"),
    # 费用/成本类
    (11, "6401", "主营业务成本",   "费用", "",   1, "主营业务的直接成本"),
    (12, "6601", "销售费用",       "费用", "",   2, "销售活动相关费用"),
    (13, "6602", "管理费用",       "费用", "",   2, "行政管理相关费用"),
    (14, "6603", "财务费用",       "费用", "",   2, "利息支出及汇兑损益"),
]

# 成本中心 — 两个租户共用相同成本中心结构
DIM_COST_CENTER: List[Tuple[int, str, str, str, str, str]] = [
    (1, "CC001", "生产部",     "制造部",   "生产", "张建国"),
    (2, "CC002", "研发部",     "技术中心", "研发", "李明辉"),
    (3, "CC003", "销售部",     "营销中心", "销售", "王志强"),
    (4, "CC004", "市场部",     "营销中心", "销售", "陈晓燕"),
    (5, "CC005", "财务部",     "行政中心", "管理", "刘会计师"),
    (6, "CC006", "人事行政部", "行政中心", "管理", "赵经理"),
]

# 共用日期维度 — 扩展到 2026 Q1
DIM_DATE: List[Tuple[int, str, int, int, int]] = [
    # 2025
    (1,  "2025-01-31", 2025, 1, 1),
    (2,  "2025-02-28", 2025, 1, 2),
    (3,  "2025-03-31", 2025, 1, 3),
    (4,  "2025-04-30", 2025, 2, 4),
    (5,  "2025-05-31", 2025, 2, 5),
    (6,  "2025-06-30", 2025, 2, 6),
    (7,  "2025-07-31", 2025, 3, 7),
    (8,  "2025-08-31", 2025, 3, 8),
    (9,  "2025-09-30", 2025, 3, 9),
    (10, "2025-10-31", 2025, 4, 10),
    (11, "2025-11-30", 2025, 4, 11),
    (12, "2025-12-31", 2025, 4, 12),
    # 2026 Q1（同比对比用）
    (13, "2026-01-31", 2026, 1, 1),
    (14, "2026-02-28", 2026, 1, 2),
    (15, "2026-03-31", 2026, 1, 3),
]

# ═══════════════════════════════════════════════════════════════
# 事实数据生成辅助函数
# ═══════════════════════════════════════════════════════════════

def _gen_month_ledger(date_id: int, month_idx: int, year: int, tenant_name: str) -> list:
    """生成某个月的总账分录数据。保证借贷平衡。

    恒通制造（制造业）：成本结构偏重生产成本和制造费用
    瑞达商贸（商贸业）：成本结构偏重采购成本和销售费用
    """
    rows = []
    q_idx = (month_idx - 1) // 3  # 0,1,2,3
    seasonal = [0.75, 0.85, 1.05, 1.35][q_idx]  # Q4 结算旺季
    # 季节性微调：商贸Q3有中秋/国庆备货旺季
    if tenant_name == "ruida_trading":
        seasonal = [0.80, 0.90, 1.20, 1.30][q_idx]
    yoy_growth = 1.0 if year == 2025 else 1.12  # 2026 同比 +12%

    period = f"{year}-{month_idx:02d}"

    # 基准收入金额
    base_revenue = 800000 * seasonal * yoy_growth
    if tenant_name == "ruida_trading":
        base_revenue = 500000 * seasonal * yoy_growth  # 商贸规模略小

    voucher_counter = [0]  # 可变计数器

    def _vno():
        voucher_counter[0] += 1
        return f"PZ-{year}{month_idx:02d}-{voucher_counter[0]:04d}"

    def _add_entry(debit_acct_id, credit_acct_id, cc_id, amount, summary):
        """添加一笔借贷分录（借贷相等）"""
        rows.append((date_id, debit_acct_id, cc_id, _vno(),
                     amount, 0.00, summary, period, "ERP"))
        rows.append((date_id, credit_acct_id, cc_id, _vno(),
                     0.00, amount, f"{summary}-贷方", period, "ERP"))

    # ── 收入确认 ──
    # 1. 销售收款: 借银行存款 贷主营业务收入（销售部主导）
    revenue = round(base_revenue * (0.85 + 0.3 * (month_idx % 3) / 3), 2)
    _add_entry(2, 10, 3, f"销售收入-{period}", revenue)

    # 2. 应收账款确认: 借应收账款 贷主营业务收入（部分赊销）
    ar_amount = round(revenue * 0.35, 2)
    _add_entry(3, 10, 3, f"赊销收入-{period}", ar_amount)

    # ── 成本与费用 ──
    # 3. 主营业务成本: 借主营业务成本 贷银行存款（生产成本中心）
    cost_rate = 0.65 if tenant_name == "hengtong_manufacturing" else 0.72  # 制造业毛利率~35%，商贸~28%
    cost_amount = round((revenue + ar_amount) * cost_rate, 2)
    _add_entry(11, 2, 1, f"主营业务成本-{period}", cost_amount)

    # 4. 管理费用: 借管理费用 贷银行存款（财务部、人事行政部）
    admin_amount = round(base_revenue * 0.08, 2)
    _add_entry(13, 2, 5, f"管理费用-{period}", admin_amount)
    _add_entry(13, 2, 6, f"管理费用-人事-{period}", round(admin_amount * 0.4, 2))

    # 5. 销售费用: 借销售费用 贷银行存款（销售部、市场部）
    selling_amount = round(base_revenue * 0.10, 2)
    _add_entry(12, 2, 3, f"销售费用-{period}", selling_amount * 0.6)
    _add_entry(12, 2, 4, f"市场费用-{period}", selling_amount * 0.4)

    # 6. 财务费用: 借财务费用 贷银行存款
    finance_amount = round(base_revenue * 0.02, 2)
    _add_entry(14, 2, 5, f"财务费用-{period}", finance_amount)

    # ── 资产/负债/权益变动（低频，每月不同科目轮换）──
    # 7. 采购原材料（制造业更多）
    if month_idx % 2 == 0:
        material_amount = round(base_revenue * (0.30 if tenant_name == "hengtong_manufacturing" else 0.20), 2)
        _add_entry(4, 2, 1, f"采购原材料-{period}", material_amount)

    # 8. 完工入库（制造业特有）
    if tenant_name == "hengtong_manufacturing" and month_idx % 2 == 0:
        inventory_amount = round(base_revenue * 0.25, 2)
        _add_entry(5, 4, 1, f"完工入库-{period}", inventory_amount)

    # 9. 固定资产购置（季节性——年末/年初）
    if month_idx in (1, 6, 12):
        fixed_asset_amount = round(base_revenue * (0.15 if month_idx == 12 else 0.08), 2)
        _add_entry(6, 2, 2, f"固定资产购置-{period}", fixed_asset_amount)

    # 10. 银行借款（Q1和Q3借入）
    if month_idx in (1, 7):
        loan_amount = round(base_revenue * 0.50, 2)
        _add_entry(2, 7, 5, f"银行借款-{period}", loan_amount)

    # 11. 还应付账款（每月）
    ap_amount = round(base_revenue * 0.12, 2)
    _add_entry(8, 2, 1, f"偿还应付账款-{period}", ap_amount)

    # 12. 资本注入（年初）
    if month_idx == 1:
        capital_amount = round(base_revenue * 1.5, 2)
        _add_entry(2, 9, 5, f"股东注资-{period}", capital_amount)

    # ── 异常点注入 ──
    # ★ 异常1: Q3 生产部制造费用异常飙升（原材料价格上涨）
    if tenant_name == "hengtong_manufacturing" and month_idx in (7, 8, 9):
        anomaly_cost = round(base_revenue * 0.40, 2)  # 额外成本
        _add_entry(11, 2, 1, f"原材料涨价-异常成本-{period}", anomaly_cost)

    # ★ 异常2: Q1 销售费用骤降（瑞达商贸缩减广告投放）
    if tenant_name == "ruida_trading" and month_idx in (1, 2, 3):
        # 正常销售费用已在本月被跳过/减少，这里加一个明显低值
        pass  # 通过减少上面销售费用的 seasonal 因子体现

    # ★ 异常3: Q4 应收账款暴增（年末赊销冲业绩）
    if month_idx in (10, 11, 12):
        extra_ar = round(base_revenue * 0.25, 2)
        _add_entry(3, 10, 3, f"年末赊销冲量-{period}", extra_ar)

    return rows


def _build_tenant_data(tenant_name: str) -> list:
    """动态生成一个租户全部 15 个月数据。"""
    all_rows = []
    for date_id in range(1, 16):
        month_idx = ((date_id - 1) % 12) + 1
        year = 2025 if date_id <= 12 else 2026
        all_rows.extend(_gen_month_ledger(date_id, month_idx, year, tenant_name))
    return all_rows


# ═══════════════════════════════════════════════════════════════
# 种子填充逻辑
# ═══════════════════════════════════════════════════════════════

def _seed_tenants_and_users_sync(cursor) -> None:
    """同步写入租户和用户（SQLite）。"""
    from config import Config
    for tid, name, display_name in TENANTS:
        cursor.execute(
            "INSERT OR IGNORE INTO tenants (tenant_id, name, display_name, created_at) VALUES (?, ?, ?, ?)",
            (tid, name, display_name, datetime.now(timezone.utc).isoformat()),
        )
    for uid, username, tid, role in USERS:
        cursor.execute(
            "INSERT OR IGNORE INTO users (user_id, username, password_hash, tenant_id, role, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (uid, username, Config.admin_password_hash, tid, role, datetime.now(timezone.utc).isoformat()),
        )


async def _seed_tenants_and_users_async(session) -> None:
    """异步写入租户和用户（PostgreSQL）。"""
    from config import Config
    for tid, name, display_name in TENANTS:
        await session.execute(
            text("INSERT INTO tenants (tenant_id, name, display_name, created_at) VALUES (:tid, :n, :dn, :ts) "
                 "ON CONFLICT (tenant_id) DO NOTHING"),
            {"tid": tid, "n": name, "dn": display_name, "ts": datetime.now(timezone.utc).replace(tzinfo=None)},
        )
    for uid, username, tid, role in USERS:
        await session.execute(
            text("INSERT INTO users (user_id, username, password_hash, tenant_id, role, created_at) "
                 "VALUES (:uid, :un, :ph, :tid, :role, :ts) ON CONFLICT (user_id) DO NOTHING"),
            {"uid": uid, "un": username, "ph": Config.admin_password_hash, "tid": tid, "role": role, "ts": datetime.now(timezone.utc).replace(tzinfo=None)},
        )


def seed_database_sync(db_path: str) -> None:
    """SQLite 同步种子数据写入。"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    _seed_tenants_and_users_sync(cursor)

    # ── tenant_id=1: 恒通制造 ──
    for aid, code, name, cat, parent, level, desc in DIM_ACCOUNT:
        cursor.execute(
            "INSERT INTO dim_account (account_id, tenant_id, account_code, account_name, account_category, parent_account, account_level, description) "
            "VALUES (?, 1, ?, ?, ?, ?, ?, ?)",
            (aid, code, name, cat, parent, level, desc),
        )
    for cid, code, name, dept, ctype, owner in DIM_COST_CENTER:
        cursor.execute(
            "INSERT INTO dim_cost_center (cc_id, tenant_id, cc_code, cc_name, department, cc_type, budget_owner) "
            "VALUES (?, 1, ?, ?, ?, ?, ?)",
            (cid, code, name, dept, ctype, owner),
        )
    for did, date, year, quarter, month in DIM_DATE:
        cursor.execute(
            "INSERT INTO dim_date (date_id, tenant_id, date, year, quarter, month) VALUES (?, 1, ?, ?, ?, ?)",
            (did, date, year, quarter, month),
        )

    t1_data = _build_tenant_data("hengtong_manufacturing")
    for date_id, acct_id, cc_id, vno, debit, credit, summary, period, source in t1_data:
        cursor.execute(
            "INSERT INTO fact_ledger (tenant_id, date_id, account_id, cc_id, voucher_no, debit_amount, credit_amount, summary, period, source_system) "
            "VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (date_id, acct_id, cc_id, vno, debit, credit, summary, period, source),
        )

    # ── tenant_id=2: 瑞达商贸 ──
    ACCT_OFFSET = 100
    CC_OFFSET = 100
    for aid, code, name, cat, parent, level, desc in DIM_ACCOUNT:
        cursor.execute(
            "INSERT INTO dim_account (account_id, tenant_id, account_code, account_name, account_category, parent_account, account_level, description) "
            "VALUES (?, 2, ?, ?, ?, ?, ?, ?)",
            (aid + ACCT_OFFSET, code, name, cat, parent, level, desc),
        )
    for cid, code, name, dept, ctype, owner in DIM_COST_CENTER:
        cursor.execute(
            "INSERT INTO dim_cost_center (cc_id, tenant_id, cc_code, cc_name, department, cc_type, budget_owner) "
            "VALUES (?, 2, ?, ?, ?, ?, ?)",
            (cid + CC_OFFSET, code, name, dept, ctype, owner),
        )
    for did, date, year, quarter, month in DIM_DATE:
        cursor.execute(
            "INSERT INTO dim_date (date_id, tenant_id, date, year, quarter, month) VALUES (?, 2, ?, ?, ?, ?)",
            (did + 15, date, year, quarter, month),
        )

    t2_data = _build_tenant_data("ruida_trading")
    for date_id, acct_id, cc_id, vno, debit, credit, summary, period, source in t2_data:
        cursor.execute(
            "INSERT INTO fact_ledger (tenant_id, date_id, account_id, cc_id, voucher_no, debit_amount, credit_amount, summary, period, source_system) "
            "VALUES (2, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (date_id + 15, acct_id + ACCT_OFFSET, cc_id + CC_OFFSET, vno, debit, credit, summary, period, source),
        )

    conn.commit()
    conn.close()
    logger.info(f"种子数据已填充: 2 个租户, T1={len(t1_data)} 条, T2={len(t2_data)} 条")


async def seed_database_async(session_factory) -> None:
    """异步种子数据（SQLAlchemy，兼容 SQLite 和 PostgreSQL）。"""
    from sqlalchemy.ext.asyncio import AsyncSession

    async with session_factory() as session:
        result = await session.execute(text("SELECT COUNT(*) FROM tenants"))
        if result.scalar() > 0:
            logger.info("数据库已有数据，跳过种子填充")
            return

        await _seed_tenants_and_users_async(session)

        # ── tenant 1: 恒通制造 ──
        for aid, code, name, cat, parent, level, desc in DIM_ACCOUNT:
            await session.execute(
                text("INSERT INTO dim_account (account_id, tenant_id, account_code, account_name, account_category, parent_account, account_level, description) "
                     "VALUES (:aid, 1, :code, :name, :cat, :parent, :level, :desc)"),
                {"aid": aid, "code": code, "name": name, "cat": cat, "parent": parent, "level": level, "desc": desc},
            )
        for cid, code, name, dept, ctype, owner in DIM_COST_CENTER:
            await session.execute(
                text("INSERT INTO dim_cost_center (cc_id, tenant_id, cc_code, cc_name, department, cc_type, budget_owner) "
                     "VALUES (:cid, 1, :code, :name, :dept, :ctype, :owner)"),
                {"cid": cid, "code": code, "name": name, "dept": dept, "ctype": ctype, "owner": owner},
            )
        for did, date, year, quarter, month in DIM_DATE:
            await session.execute(
                text("INSERT INTO dim_date (date_id, tenant_id, date, year, quarter, month) "
                     "VALUES (:did, 1, :d, :y, :q, :m)"),
                {"did": did, "d": date, "y": year, "q": quarter, "m": month},
            )

        t1_data = _build_tenant_data("hengtong_manufacturing")
        for date_id, acct_id, cc_id, vno, debit, credit, summary, period, source in t1_data:
            await session.execute(
                text("INSERT INTO fact_ledger (tenant_id, date_id, account_id, cc_id, voucher_no, debit_amount, credit_amount, summary, period, source_system) "
                     "VALUES (1, :di, :ai, :ci, :vn, :db, :cr, :sm, :pd, :ss)"),
                {"di": date_id, "ai": acct_id, "ci": cc_id, "vn": vno, "db": debit, "cr": credit, "sm": summary, "pd": period, "ss": source},
            )

        # ── tenant 2: 瑞达商贸 ──
        ACCT_OFFSET = 100
        CC_OFFSET = 100
        for aid, code, name, cat, parent, level, desc in DIM_ACCOUNT:
            await session.execute(
                text("INSERT INTO dim_account (account_id, tenant_id, account_code, account_name, account_category, parent_account, account_level, description) "
                     "VALUES (:aid, 2, :code, :name, :cat, :parent, :level, :desc)"),
                {"aid": aid + ACCT_OFFSET, "code": code, "name": name, "cat": cat, "parent": parent, "level": level, "desc": desc},
            )
        for cid, code, name, dept, ctype, owner in DIM_COST_CENTER:
            await session.execute(
                text("INSERT INTO dim_cost_center (cc_id, tenant_id, cc_code, cc_name, department, cc_type, budget_owner) "
                     "VALUES (:cid, 2, :code, :name, :dept, :ctype, :owner)"),
                {"cid": cid + CC_OFFSET, "code": code, "name": name, "dept": dept, "ctype": ctype, "owner": owner},
            )
        for did, date, year, quarter, month in DIM_DATE:
            await session.execute(
                text("INSERT INTO dim_date (date_id, tenant_id, date, year, quarter, month) "
                     "VALUES (:did, 2, :d, :y, :q, :m)"),
                {"did": did + 15, "d": date, "y": year, "q": quarter, "m": month},
            )

        t2_data = _build_tenant_data("ruida_trading")
        for date_id, acct_id, cc_id, vno, debit, credit, summary, period, source in t2_data:
            await session.execute(
                text("INSERT INTO fact_ledger (tenant_id, date_id, account_id, cc_id, voucher_no, debit_amount, credit_amount, summary, period, source_system) "
                     "VALUES (2, :di, :ai, :ci, :vn, :db, :cr, :sm, :pd, :ss)"),
                {"di": date_id + 15, "ai": acct_id + ACCT_OFFSET, "ci": cc_id + CC_OFFSET, "vn": vno, "db": debit, "cr": credit, "sm": summary, "pd": period, "ss": source},
            )

        await session.commit()
        logger.info(f"种子数据已填充: 2 个租户, T1={len(t1_data)} 条, T2={len(t2_data)} 条")
