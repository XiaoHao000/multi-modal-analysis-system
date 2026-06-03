"""multi_tenant

Revision ID: 003
Revises: 002
Create Date: 2026-05-26

多租户架构已在 initial_schema (69e776e6c2ae) 中内置，此迁移为空操作。
保留此版本号以维持迁移链完整性。
"""

from typing import Sequence, Union

from alembic import op


revision: str = "003"
down_revision: Union[str, Sequence[str], None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
