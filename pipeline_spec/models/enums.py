from enum import StrEnum


class DataType(StrEnum):
    STRING = "string"
    INTEGER = "integer"
    LONG = "long"
    FLOAT = "float"
    DOUBLE = "double"
    BOOLEAN = "boolean"
    DATE = "date"
    TIMESTAMP = "timestamp"
    DECIMAL = "decimal"


class FileFormat(StrEnum):
    PARQUET = "parquet"
    CSV = "csv"
    JSON = "json"
    DELTA = "delta"
    ORC = "orc"
    AVRO = "avro"
    JDBC = "jdbc"


class SaveMode(StrEnum):
    OVERWRITE = "overwrite"
    APPEND = "append"
    IGNORE = "ignore"
    ERROR_IF_EXISTS = "error_if_exists"


class JoinType(StrEnum):
    INNER = "inner"
    LEFT = "left"
    RIGHT = "right"
    FULL = "full"
    CROSS = "cross"
    LEFT_SEMI = "left_semi"
    LEFT_ANTI = "left_anti"


class AggregateFunction(StrEnum):
    COUNT = "count"
    SUM = "sum"
    AVG = "avg"
    MIN = "min"
    MAX = "max"
    COUNT_DISTINCT = "count_distinct"
    FIRST = "first"
    LAST = "last"


class SortDirection(StrEnum):
    ASC = "asc"
    DESC = "desc"


class NullOrdering(StrEnum):
    NULLS_FIRST = "nulls_first"
    NULLS_LAST = "nulls_last"


class ConstraintType(StrEnum):
    NOT_NULL = "not_null"
    UNIQUE = "unique"
    RANGE = "range"
    IN_SET = "in_set"
    REGEX_MATCH = "regex_match"
    CUSTOM_SQL = "custom_sql"


class ConstraintLevel(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class OperationType(StrEnum):
    READ = "read"
    FILTER = "filter"
    SELECT = "select"
    WITH_COLUMNS = "with_columns"
    JOIN = "join"
    AGGREGATE = "aggregate"
    DEDUPLICATE = "deduplicate"
    SORT = "sort"
    UNION = "union"
    WRITE = "write"
