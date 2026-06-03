"""add_indexes

Revision ID: 002
Revises: 69e776e6c2ae
Create Date: 2026-05-12

为 fact_ledger 外键列和维度表常用过滤列添加索引，
提升常见查询模式（JOIN + WHERE account_category/cc_name）的性能。
"""

from typing import Sequence, Union

from alembic import op

revision: str = "002"
down_revision: Union[str, Sequence[str], None] = "69e776e6c2ae"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # fact_ledger FK 列 — JOIN 和 WHERE 过滤热点
    op.create_index("idx_fact_date_id", "fact_ledger", ["date_id"])
    op.create_index("idx_fact_account_id", "fact_ledger", ["account_id"])
    op.create_index("idx_fact_cc_id", "fact_ledger", ["cc_id"])
    op.create_index("idx_fact_period", "fact_ledger", ["period"])

    # 维度表常用过滤列
    op.create_index("idx_account_category", "dim_account", ["account_category"])
    op.create_index("idx_cc_name", "dim_cost_center", ["cc_name"])


def downgrade() -> None:
    op.drop_index("idx_cc_name", table_name="dim_cost_center")
    op.drop_index("idx_account_category", table_name="dim_account")
    op.drop_index("idx_fact_period", table_name="fact_ledger")
    op.drop_index("idx_fact_cc_id", table_name="fact_ledger")
    op.drop_index("idx_fact_account_id", table_name="fact_ledger")
    op.drop_index("idx_fact_date_id", table_name="fact_ledger")
