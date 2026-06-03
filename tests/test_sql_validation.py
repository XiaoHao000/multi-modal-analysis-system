import pytest
from database.db_manager import DatabaseManager
from utils.exceptions import InvalidSQLTypeError


def test_valid_select_passes(db_manager):
    db_manager._validate_select_only("SELECT * FROM fact_ledger")
    db_manager._validate_select_only("  SELECT 1")
    db_manager._validate_select_only("with cte as (select 1) select * from cte")


def test_insert_is_rejected(db_manager):
    with pytest.raises(InvalidSQLTypeError):
        db_manager._validate_select_only("INSERT INTO fact_ledger VALUES (1,2,3,4,5,6)")


def test_drop_table_is_rejected(db_manager):
    with pytest.raises(InvalidSQLTypeError):
        db_manager._validate_select_only("DROP TABLE fact_ledger")


def test_comment_bypass_is_rejected(db_manager):
    with pytest.raises(InvalidSQLTypeError):
        db_manager._validate_select_only("-- harmless comment\nDROP TABLE fact_ledger")


@pytest.fixture
def db_manager():
    return DatabaseManager(database_url="sqlite+aiosqlite:///:memory:")
