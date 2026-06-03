class AppException(Exception):
    """应用级异常基类"""
    code: str = "INTERNAL_ERROR"
    http_status: int = 500


class DatabaseError(AppException):
    code = "DATABASE_ERROR"


class QueryTimeoutError(DatabaseError):
    code = "QUERY_TIMEOUT"
    http_status = 504


class InvalidSQLTypeError(DatabaseError):
    code = "INVALID_SQL_TYPE"
    http_status = 400


class VectorStoreError(AppException):
    code = "VECTOR_STORE_ERROR"


class LLMError(AppException):
    code = "LLM_ERROR"
    http_status = 502


class ConfigError(AppException):
    code = "CONFIG_ERROR"
    http_status = 500
