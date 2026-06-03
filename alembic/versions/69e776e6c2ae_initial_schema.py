"""initial_schema

Revision ID: 69e776e6c2ae
Revises:
Create Date: 2026-05-12 08:49:22.399138

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '69e776e6c2ae'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 租户和用户表
    op.create_table(
        'tenants',
        sa.Column('tenant_id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False, unique=True),
        sa.Column('display_name', sa.String(length=200), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('tenant_id'),
    )
    op.create_table(
        'users',
        sa.Column('user_id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('username', sa.String(length=64), nullable=False, unique=True),
        sa.Column('password_hash', sa.String(length=256), nullable=False),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.tenant_id'), nullable=False),
        sa.Column('role', sa.String(length=20), nullable=False, server_default='analyst'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('user_id'),
    )
    # 维度表
    op.create_table(
        'dim_date',
        sa.Column('date_id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.tenant_id'), nullable=False),
        sa.Column('date', sa.String(length=20), nullable=False),
        sa.Column('year', sa.Integer(), nullable=False),
        sa.Column('quarter', sa.Integer(), nullable=False),
        sa.Column('month', sa.Integer(), nullable=False),
        sa.Column('day', sa.Integer(), nullable=True),
        sa.Column('week_day', sa.String(length=10), nullable=True),
        sa.Column('is_month_end', sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint('date_id'),
    )
    op.create_table(
        'dim_account',
        sa.Column('account_id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.tenant_id'), nullable=False),
        sa.Column('account_code', sa.String(length=20), nullable=False, unique=True),
        sa.Column('account_name', sa.String(length=200), nullable=False),
        sa.Column('account_category', sa.String(length=50), nullable=False),
        sa.Column('parent_account', sa.String(length=20), nullable=True),
        sa.Column('account_level', sa.Integer(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('account_id'),
    )
    op.create_table(
        'dim_cost_center',
        sa.Column('cc_id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.tenant_id'), nullable=False),
        sa.Column('cc_code', sa.String(length=20), nullable=False, unique=True),
        sa.Column('cc_name', sa.String(length=100), nullable=False),
        sa.Column('department', sa.String(length=100), nullable=True),
        sa.Column('cc_type', sa.String(length=50), nullable=True),
        sa.Column('budget_owner', sa.String(length=50), nullable=True),
        sa.PrimaryKeyConstraint('cc_id'),
    )
    # 事实表
    op.create_table(
        'fact_ledger',
        sa.Column('ledger_id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.tenant_id'), nullable=False),
        sa.Column('date_id', sa.Integer(), sa.ForeignKey('dim_date.date_id'), nullable=False),
        sa.Column('account_id', sa.Integer(), sa.ForeignKey('dim_account.account_id'), nullable=False),
        sa.Column('cc_id', sa.Integer(), sa.ForeignKey('dim_cost_center.cc_id'), nullable=False),
        sa.Column('voucher_no', sa.String(length=50), nullable=True),
        sa.Column('debit_amount', sa.Numeric(18, 2), nullable=True, server_default='0'),
        sa.Column('credit_amount', sa.Numeric(18, 2), nullable=True, server_default='0'),
        sa.Column('balance', sa.Numeric(18, 2), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('period', sa.String(length=10), nullable=True),
        sa.Column('source_system', sa.String(length=50), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('ledger_id'),
    )


def downgrade() -> None:
    op.drop_table('fact_ledger')
    op.drop_table('dim_cost_center')
    op.drop_table('dim_account')
    op.drop_table('dim_date')
    op.drop_table('users')
    op.drop_table('tenants')
