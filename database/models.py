from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, Boolean, Text, Numeric
from sqlalchemy.orm import DeclarativeBase, relationship
from datetime import datetime, timezone


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    """租户表 — 企业级多租户隔离。每个租户有独立的财务数据视图。"""
    __tablename__ = "tenants"

    tenant_id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True, comment="租户名称，如 hengtong_manufacturing")
    display_name = Column(String(200), nullable=False, comment="显示名称，如 恒通制造")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class User(Base):
    """用户表 — 属于某个租户，一个租户可以有多个用户。"""
    __tablename__ = "users"

    user_id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(64), nullable=False, unique=True)
    password_hash = Column(String(256), nullable=False)
    tenant_id = Column(Integer, ForeignKey("tenants.tenant_id"), nullable=False)
    role = Column(String(20), nullable=False, default="analyst", comment="admin | analyst | viewer")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    tenant = relationship("Tenant")


# ========== 维度表 ==========

class DimDate(Base):
    """日期维度 — 财务分析通用时间维度。"""
    __tablename__ = "dim_date"

    date_id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.tenant_id"), nullable=False, index=True)
    date = Column(String(20), nullable=False, comment="日期字符串 YYYY-MM-DD")
    year = Column(Integer, nullable=False)
    quarter = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)
    day = Column(Integer, default=1)
    week_day = Column(String(10), default="")
    is_month_end = Column(Boolean, default=False, comment="是否月末日期")


class DimAccount(Base):
    """会计科目维度 — 原DimProduct，中国会计准则科目编码体系。"""
    __tablename__ = "dim_account"

    account_id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.tenant_id"), nullable=False, index=True)
    account_code = Column(String(20), unique=True, nullable=False, comment="科目编码如1001、1002")
    account_name = Column(String(200), nullable=False, comment="科目名称")
    account_category = Column(String(50), nullable=False, comment="科目类别：资产/负债/权益/收入/费用")
    parent_account = Column(String(20), default="", comment="上级科目编码")
    account_level = Column(Integer, default=1, comment="科目层级(1=总账/2=明细)")
    description = Column(Text, default="")


class DimCostCenter(Base):
    """成本中心维度 — 原DimRegion，部门/成本中心分析。"""
    __tablename__ = "dim_cost_center"

    cc_id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.tenant_id"), nullable=False, index=True)
    cc_code = Column(String(20), unique=True, nullable=False, comment="成本中心编码")
    cc_name = Column(String(100), nullable=False, comment="成本中心名称")
    department = Column(String(100), default="", comment="所属部门")
    cc_type = Column(String(50), default="", comment="类型：生产/研发/销售/管理")
    budget_owner = Column(String(50), default="", comment="预算负责人")


# ========== 事实表 ==========

class FactLedger(Base):
    """总账事实表 — 原FactSales，记录每笔会计分录（借方/贷方）。"""
    __tablename__ = "fact_ledger"

    ledger_id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.tenant_id"), nullable=False, index=True)
    date_id = Column(Integer, ForeignKey("dim_date.date_id"), nullable=False)
    account_id = Column(Integer, ForeignKey("dim_account.account_id"), nullable=False)
    cc_id = Column(Integer, ForeignKey("dim_cost_center.cc_id"), nullable=False)
    voucher_no = Column(String(50), default="", comment="凭证号")
    debit_amount = Column(Numeric(18, 2), default=0, comment="借方金额")
    credit_amount = Column(Numeric(18, 2), default=0, comment="贷方金额")
    balance = Column(Numeric(18, 2), nullable=True, comment="余额")
    summary = Column(Text, default="", comment="摘要")
    period = Column(String(10), default="", comment="会计期间 YYYY-MM")
    source_system = Column(String(50), default="", comment="数据来源")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    account = relationship("DimAccount")
    cost_center = relationship("DimCostCenter")
    date = relationship("DimDate")
