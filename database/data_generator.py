"""
财务数据生成器 — 生成可配置行数的 fact_ledger 数据用于性能测试和长尾验证。

用法:
  python -m database.data_generator --rows 10000     # 生成 1 万行
  python -m database.data_generator --rows 100000    # 生成 10 万行
  python -m database.data_generator --rows 10000 --anomaly-rate 0.05  # 5% 异常点
"""

import argparse
import random
import sqlite3
import sys
from pathlib import Path

# 会计科目（中国会计准则编码体系）
# 编码规则: 1xxx=资产, 2xxx=负债, 4xxx=权益, 6xxx=收入, 6xxx=费用(62xx税费/64xx成本/66xx费用)
ACCOUNTS = [
    (1, "1001", "现金", "资产"),
    (2, "1002", "银行存款", "资产"),
    (3, "1122", "应收账款", "资产"),
    (4, "1403", "原材料", "资产"),
    (5, "1405", "库存商品", "资产"),
    (6, "1601", "固定资产", "资产"),
    (7, "2001", "短期借款", "负债"),
    (8, "2202", "应付账款", "负债"),
    (9, "4001", "实收资本", "权益"),
    (10, "6001", "主营业务收入", "收入"),
    (11, "6401", "主营业务成本", "费用"),
    (12, "6601", "销售费用", "费用"),
    (13, "6602", "管理费用", "费用"),
    (14, "6603", "财务费用", "费用"),
]

COST_CENTERS = [
    (1, "CC001", "生产部", "制造部", "生产", "张建国"),
    (2, "CC002", "研发部", "技术中心", "研发", "李明辉"),
    (3, "CC003", "销售部", "营销中心", "销售", "王志强"),
    (4, "CC004", "市场部", "营销中心", "销售", "陈晓燕"),
    (5, "CC005", "财务部", "行政中心", "管理", "刘会计师"),
    (6, "CC006", "人事行政部", "行政中心", "管理", "赵经理"),
]

# 各科目的月度基准金额范围（元）: (base, variance)
# 遵循会计恒等式：资产类有余额，收入/费用类每月发生
ACCOUNT_AMOUNTS = {
    "资产": (500000, 200000),    # 资产类科目余额 30万-70万
    "负债": (300000, 150000),    # 负债类科目余额 15万-45万
    "权益": (1000000, 500000),   # 权益类科目余额 50万-150万
    "收入": (800000, 400000),    # 收入类月度发生额 40万-120万
    "费用": (200000, 100000),    # 费用类月度发生额 10万-30万
}

# 季度系数 (Q1=1.0, Q2=1.15, Q3=1.35, Q4=1.6) — 制造业Q4为结算旺季
QUARTER_FACTOR = {1: 1.0, 2: 1.15, 3: 1.35, 4: 1.6}


def generate_dates(num_months: int = 36) -> list:
    """生成连续月份维度数据，从 2025-01 开始。"""
    dates = []
    for i in range(num_months):
        year = 2025 + i // 12
        month = (i % 12) + 1
        quarter = (month - 1) // 3 + 1
        date_str = f"{year}-{month:02d}-15"
        dates.append((i + 1, date_str, year, quarter, month))
    return dates


def generate_fact_rows(
    dim_dates: list,
    num_rows: int,
    anomaly_rate: float = 0.03,
    seed: int = 42,
) -> list:
    """生成 fact_ledger 行，保证借贷平衡。

    策略：每个会计期间（月份），为每对借贷科目生成匹配的借方/贷方分录。
    "有借必有贷，借贷必相等" — 每笔分录的 debit_amount = credit_amount。
    """
    rng = random.Random(seed)
    rows = []

    # 构建分录模板 (借方科目, 贷方科目, 摘要, 金额系数)
    entry_templates = [
        # (借方科目-资产增加, 贷方科目-资金来源, 摘要, 金额系数)
        ("1002", "6001", "销售收款-银行存款增加", 1.0),        # 借:银行存款 贷:主营业务收入
        ("1122", "6001", "赊销确认-应收账款", 0.6),             # 借:应收账款 贷:主营业务收入
        ("1403", "1002", "采购原材料", 0.5),                    # 借:原材料 贷:银行存款
        ("1405", "1002", "完工入库-库存商品", 0.4),             # 借:库存商品 贷:银行存款
        ("1601", "1002", "购置固定资产", 0.2),                   # 借:固定资产 贷:银行存款
        ("6601", "1002", "支付销售费用", 0.3),                   # 借:销售费用 贷:银行存款
        ("6602", "1002", "支付管理费用", 0.25),                 # 借:管理费用 贷:银行存款
        ("6603", "1002", "支付财务费用", 0.1),                   # 借:财务费用 贷:银行存款
        ("6401", "1002", "支付采购成本", 0.55),                  # 借:主营业务成本 贷:银行存款
        ("1002", "2001", "取得短期借款", 0.15),                 # 借:银行存款 贷:短期借款
        ("2202", "1002", "偿还应付账款", 0.3),                   # 借:应付账款 贷:银行存款
        ("1002", "4001", "收到投资款", 0.08),                   # 借:银行存款 贷:实收资本
    ]

    # 科目编码到ID的映射
    acct_code_to_id = {a[1]: a[0] for a in ACCOUNTS}

    # 为每个月生成分录
    for date_id, date_str, year, quarter, month in dim_dates:
        q_factor = QUARTER_FACTOR[quarter]

        for debit_code, credit_code, summary, amount_factor in entry_templates:
            debit_acct = next(a for a in ACCOUNTS if a[1] == debit_code)
            credit_acct = next(a for a in ACCOUNTS if a[1] == credit_code)

            # 取借方科目类别的基准金额
            debit_cat = debit_acct[3]  # account_category
            base, variance = ACCOUNT_AMOUNTS.get(debit_cat, (200000, 100000))

            # 计算金额：基准 × 季度系数 × 分录系数 × 随机波动(±20%)
            noise = rng.uniform(0.8, 1.2)
            amount = round(base * q_factor * amount_factor * noise, 2)
            # 确保最小金额 > 0
            amount = max(amount, 100.00)

            # 随机选择一个成本中心
            cc_id, cc_code, cc_name, dept, cc_type, owner = rng.choice(COST_CENTERS)

            period = f"{year}-{month:02d}"

            # 生成凭证号
            voucher_no = f"PZ-{year}{month:02d}-{rng.randint(10000, 99999)}"

            # 借方分录
            rows.append((
                date_id,
                acct_code_to_id[debit_code],
                cc_id,
                voucher_no,
                amount,     # debit_amount
                0.00,       # credit_amount
                None,       # balance (可后续计算)
                summary,
                period,
                "ERP",
            ))

            # 贷方分录 — 金额相同，方向相反
            # 贷方可能对应不同成本中心
            cc_id2, _, _, _, _, _ = rng.choice(COST_CENTERS)
            rows.append((
                date_id,
                acct_code_to_id[credit_code],
                cc_id2,
                voucher_no,
                0.00,       # debit_amount
                amount,     # credit_amount
                None,
                f"{summary}-贷方",
                period,
                "ERP",
            ))

        # 注入异常点
        if rng.random() < anomaly_rate:
            anomaly_acct = rng.choice(ACCOUNTS)
            cc_id, _, _, _, _, _ = rng.choice(COST_CENTERS)
            anomaly_type = rng.choice(["spike", "dip"])
            anomaly_period = period
            anomaly_voucher = f"PZ-ANOMALY-{year}{month:02d}-{rng.randint(10000, 99999)}"

            if anomaly_type == "spike":
                anomaly_amount = round(rng.uniform(100000, 500000), 2)
            else:
                anomaly_amount = round(rng.uniform(100, 5000), 2)

            rows.append((
                date_id,
                anomaly_acct[0],
                cc_id,
                anomaly_voucher,
                anomaly_amount,
                0.00,
                None,
                f"异常分录-{anomaly_type}",
                anomaly_period,
                "ERP-ANOMALY",
            ))
            rows.append((
                date_id,
                rng.choice(ACCOUNTS)[0],
                rng.choice(COST_CENTERS)[0],
                anomaly_voucher,
                0.00,
                anomaly_amount,
                None,
                f"异常分录-{anomaly_type}-贷方",
                anomaly_period,
                "ERP-ANOMALY",
            ))

    # 如果行数超过 num_rows，随机采样；如果不够，不处理
    if len(rows) > num_rows:
        rows = rng.sample(rows, num_rows)

    return rows


def generate_database(db_path: str, num_rows: int, anomaly_rate: float) -> None:
    """生成完整的 SQLite 数据库（维度表 + 事实表）。"""
    dim_dates = generate_dates()

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 建表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS dim_date (
            date_id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            year INTEGER NOT NULL,
            quarter INTEGER NOT NULL,
            month INTEGER NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS dim_account (
            account_id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_code TEXT NOT NULL UNIQUE,
            account_name TEXT NOT NULL,
            account_category TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS dim_cost_center (
            cc_id INTEGER PRIMARY KEY AUTOINCREMENT,
            cc_code TEXT NOT NULL UNIQUE,
            cc_name TEXT NOT NULL,
            department TEXT NOT NULL,
            cc_type TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS fact_ledger (
            ledger_id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_id INTEGER NOT NULL REFERENCES dim_date(date_id),
            account_id INTEGER NOT NULL REFERENCES dim_account(account_id),
            cc_id INTEGER NOT NULL REFERENCES dim_cost_center(cc_id),
            voucher_no TEXT NOT NULL,
            debit_amount REAL NOT NULL DEFAULT 0,
            credit_amount REAL NOT NULL DEFAULT 0,
            balance REAL,
            summary TEXT DEFAULT '',
            period TEXT DEFAULT '',
            source_system TEXT DEFAULT 'ERP'
        )
    """)

    # 维度数据
    for aid, code, name, cat in ACCOUNTS:
        cursor.execute(
            "INSERT INTO dim_account (account_id, account_code, account_name, account_category) VALUES (?, ?, ?, ?)",
            (aid, code, name, cat),
        )
    for cid, code, name, dept, ctype, owner in COST_CENTERS:
        cursor.execute(
            "INSERT INTO dim_cost_center (cc_id, cc_code, cc_name, department, cc_type) VALUES (?, ?, ?, ?, ?)",
            (cid, code, name, dept, ctype),
        )
    for did, d, y, q, m in dim_dates:
        cursor.execute(
            "INSERT INTO dim_date (date_id, date, year, quarter, month) VALUES (?, ?, ?, ?, ?)",
            (did, d, y, q, m),
        )

    # 事实数据
    fact_rows = generate_fact_rows(dim_dates, num_rows, anomaly_rate)
    cursor.executemany(
        "INSERT INTO fact_ledger (date_id, account_id, cc_id, voucher_no, debit_amount, credit_amount, balance, summary, period, source_system) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        fact_rows,
    )

    # 索引
    for col in ["date_id", "account_id", "cc_id"]:
        cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_fact_{col} ON fact_ledger({col})")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_fact_period ON fact_ledger(period)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_account_category ON dim_account(account_category)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_cc_name ON dim_cost_center(cc_name)")

    conn.commit()

    # 统计
    count = cursor.execute("SELECT COUNT(*) FROM fact_ledger").fetchone()[0]
    total_debit = cursor.execute("SELECT SUM(debit_amount) FROM fact_ledger").fetchone()[0]
    total_credit = cursor.execute("SELECT SUM(credit_amount) FROM fact_ledger").fetchone()[0]
    print(f"生成完成: {count} 行事实数据")
    print(f"借方总额: {total_debit:,.2f}  贷方总额: {total_credit:,.2f}")
    print(f"借贷差: {total_debit - total_credit if total_debit and total_credit else 0:,.2f}")
    conn.close()


def main():
    parser = argparse.ArgumentParser(description="生成可配置规模的财务数据集")
    parser.add_argument("--rows", type=int, default=10000, help="事实表行数 (默认 10000)")
    parser.add_argument("--anomaly-rate", type=float, default=0.03, help="异常点比例 (默认 0.03)")
    parser.add_argument("--output", type=str, default=None, help="输出路径 (默认覆盖 finance_data.db)")
    args = parser.parse_args()

    target = args.output or str(Path(__file__).resolve().parent.parent / "finance_data.db")
    print(f"目标: {target}, 行数: {args.rows}, 异常率: {args.anomaly_rate}")
    generate_database(target, args.rows, args.anomaly_rate)


if __name__ == "__main__":
    main()
