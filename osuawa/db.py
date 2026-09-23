from collections.abc import Mapping, Sequence
from dataclasses import asdict, fields
from datetime import datetime
from typing import Any, Optional, get_origin

import orjson
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from .utils import CompletedSimpleScoreInfo, SCORE_COLUMN_OVERRIDES, SCORE_STATISTICS_DEFAULTS, ScoreStatistics, _build_update_ignore, _build_upsert, _unwrap_optional

__all__ = (
    "SCORE_BIGINT_COLUMNS",
    "SCORE_COLUMN_OVERRIDES",
    "SCORE_PRIMARY_KEY",
    "SCORE_STATISTICS_DEFAULTS",
    "SCORE_TABLE",
    "build_score_add_column_sql",
    "build_score_insert_sql",
    "scores_compact_to_params",
    "create_db_engine",
    "get_score_column_types",
    "get_score_insert_pairs",
    "resolve_db_url",
    "score_count_query",
    "score_id_list_query",
    "score_rows_query",
    "score_users_query",
    "sync_score_columns",
)

SCORE_TABLE = "SCORE"
SCORE_PRIMARY_KEY = "SCORE_ID"  # 主键列名
SCORE_BIGINT_COLUMNS = frozenset({SCORE_PRIMARY_KEY, "BID", "USER_ID"})  # 这些列在 DDL 里是 BIGINT（其余 int 字段是 INT）

#: Python 类型 -> SQL 类型。这张表只用三方言下同形、同语义的类型，所以建表语句与方言无关：
#:
#: - ``BIGINT`` / ``INT`` / ``TEXT``：三家都有，语义一致
#: - ``DOUBLE PRECISION``：sqlite（REAL 亲和）、mysql（DOUBLE 的同义词）、postgresql（float8）
#:   都接受且都是 8 字节。**不要写 ``REAL``** —— PostgreSQL 的 REAL 是 4 字节，epoch 秒
#:   （约 1.79e9）在这个精度下分辨率是 256 秒，pp 的相对误差约 1e-8，会超过重算脚本
#:   1e-6 的比对容差，让「检查比对」永远收敛不了
#: - JSON 类字段（mods / statistics）统一存 TEXT，三家写法一致
#: - datetime 存的是 epoch 秒（不是时间类型），所以也是 DOUBLE PRECISION
_SCORE_SQL_TYPES: dict[type, str] = {
    int: "INT",
    float: "DOUBLE PRECISION",
    bool: "INT",
    str: "TEXT",
    datetime: "DOUBLE PRECISION",
    list: "TEXT",
    dict: "TEXT",
    ScoreStatistics: "TEXT",
}


#: ``SCORE_COLUMN_OVERRIDES`` / ``SCORE_STATISTICS_DEFAULTS`` 定义在 osuawa.utils（数据类旁边），
#: 这里只是转出来给外部用：db.py 依赖 utils.py，utils.py 不能再反过来依赖 db.py。


def get_score_column_types() -> list[tuple[str, str]]:
    """返回 SCORE 表的 [(列名, SQL 类型), ...]（含 SCORE_ID 主键列）

    与方言无关：类型表 ``_SCORE_SQL_TYPES`` 只用三家方言下同形同语义的类型。
    """
    columns: list[tuple[str, str]] = [(SCORE_PRIMARY_KEY, "BIGINT")]
    for field in fields(CompletedSimpleScoreInfo):
        name = SCORE_COLUMN_OVERRIDES.get(field.name, field.name.lstrip("_").upper())
        if name in SCORE_BIGINT_COLUMNS:
            columns.append((name, "BIGINT"))
            continue
        field_type = _unwrap_optional(field.type)
        origin = get_origin(field_type) or field_type
        try:
            columns.append((name, _SCORE_SQL_TYPES[origin]))
        except KeyError:
            raise RuntimeError(
                "字段 %s（类型 %s）没有对应的 SQL 类型，请在 osuawa/db.py 的 _SCORE_SQL_TYPES 里显式补上" % (field.name, field_type),
            ) from None
    return columns


def get_score_insert_pairs() -> list[tuple[str, str]]:
    """返回 SCORE 表的 [(列名, SQL 绑定参数名), ...]"""
    pairs: list[tuple[str, str]] = [(SCORE_PRIMARY_KEY, "score_id")]
    for field in fields(CompletedSimpleScoreInfo):
        pairs.append(
            (
                SCORE_COLUMN_OVERRIDES.get(field.name, field.name.lstrip("_").upper()),
                field.name.lstrip("_"),
            ),
        )
    return pairs


def build_score_add_column_sql(columns: Sequence[tuple[str, str]]) -> list[str]:
    return ["ALTER TABLE %s ADD COLUMN %s %s" % (SCORE_TABLE, name, sql_type) for name, sql_type in columns]


def build_score_insert_sql(dialect: str, on_conflict: str = "ignore") -> str:
    pairs = get_score_insert_pairs()
    body = "INSERT INTO %s (%s) VALUES (%s)" % (
        SCORE_TABLE,
        ", ".join(column for column, _ in pairs),
        ", ".join(":%s" % param for _, param in pairs),
    )
    if on_conflict == "update":
        return "%s %s" % (
            body,
            _build_upsert(dialect, [column for column, _ in pairs if column != SCORE_PRIMARY_KEY], [SCORE_PRIMARY_KEY]),
        )
    return _build_update_ignore(dialect, body, [SCORE_PRIMARY_KEY])


def sync_score_columns(engine: Engine, *, dry_run: bool = False) -> tuple[bool, list[tuple[str, str]], list[str]]:
    """把 SCORE 表的结构对齐到 ``CompletedSimpleScoreInfo``

    表不存在时按推导出的 DDL 建表；已存在时补上数据类里有、表里没有的列
    （ALTER TABLE ADD COLUMN，新列对旧行是 NULL，随后由重算脚本填上）。

    只对齐列的存不存在，不管类型是否一致。

    :param engine: 数据库
    :param dry_run: 为 True 时只报告不执行
    :return: ``(是否建了表, [(新增列名, 类型), ...], [表里多余、数据类已没有的列, ...])``
    """
    inspector = inspect(engine)
    expected = get_score_column_types()
    if not inspector.has_table(SCORE_TABLE):
        if not dry_run:
            with engine.begin() as conn:
                _columns = ", ".join("%s %s" % (name, sql_type) for name, sql_type in get_score_column_types())
                conn.execute(text("CREATE TABLE IF NOT EXISTS %s(%s, PRIMARY KEY (%s));" % (SCORE_TABLE, _columns, SCORE_PRIMARY_KEY)))
        return True, expected, []
    existing = {column["name"].upper() for column in inspector.get_columns(SCORE_TABLE)}
    missing = [(name, sql_type) for name, sql_type in expected if name not in existing]
    extra = sorted(existing - {name for name, _ in expected})
    if missing and not dry_run:
        for statement in build_score_add_column_sql(missing):
            # mysql 的 DDL 是隐式提交的，这里的 begin() 对它是空操作，留着只是让 sqlite 一致
            with engine.begin() as conn:
                conn.execute(text(statement))
    return False, missing, extra


def scores_compact_to_params(score_id: str, info: CompletedSimpleScoreInfo) -> dict[str, Any]:
    """把数据类转成 executemany 需要的绑定参数字典

    转换规则与 daemon 写入时一致：``datetime`` → epoch 秒，``bool`` → int，
    ``list``/``dict`` → JSON 文本，``None`` 保持 None。
    """
    params = asdict(
        info,
        dict_factory=lambda items: {k.lstrip("_"): (None if v is None else (v.timestamp() if isinstance(v, datetime) else (int(v) if isinstance(v, bool) else (orjson.dumps(v).decode("utf-8") if isinstance(v, (list, dict)) else v)))) for k, v in items},
    )
    params["score_id"] = score_id
    return params


def resolve_db_url(secrets: Mapping[str, Any]) -> tuple[str, str, Optional[str]]:
    """从 streamlit secrets 解析数据库连接

    返回的方言已经去掉 ``+driver`` 后缀（``mysql+pymysql`` → ``mysql``），给下游挑 SQL 语法用；
    URL 本身保持配置里怎么写就怎么用。

    :param secrets: st.secrets dict
    :return: (url, dialect, ca_path)
    """
    connection = secrets["connections"]["osuawa"]
    url = connection.get("url")
    if url is not None:
        dialect = url.split("://")[0].split("+")[0]
        return url, dialect, None
    dialect = connection["dialect"]
    if dialect == "mysql":
        dialect += "+pymysql"
    url = "%s://%s:%s@%s:%s/%s" % (
        dialect,
        connection["username"],
        connection["password"],
        connection["host"],
        connection["port"],
        connection["database"],
    )
    try:
        ca_path = connection["create_engine_kwargs"]["connect_args"]["ssl"]["ca"]
    except KeyError:
        ca_path = None
    return url, dialect.split("+")[0], ca_path


def create_db_engine(url: str, ca_path: Optional[str] = None, **overrides: Any) -> Engine:
    """建立数据库 engine

    默认参数来自 daemon 的踩坑经验：daemon 大部分时间阻塞在 brpop 上、完全不碰数据库，
    连接池里的连接会长时间闲置，MySQL 的 wait_timeout（以及链路中的 NAT / 云负载均衡 /
    防火墙）会单方面掐断空闲连接，下次复用时才抛 ``OperationalError 2006``。

    - ``pool_pre_ping``：每次取出连接前先探活，失活则丢弃并透明重建（关键）
    - ``pool_recycle``：兜底，主动回收超过 30 分钟的连接

    离线脚本可以覆盖 ``pool_size`` / ``max_overflow`` 等参数。
    """
    kwargs: dict[str, Any] = {
        "pool_pre_ping": True,
        "pool_recycle": 1800,
        "pool_size": 2,
        "max_overflow": 0,
        "connect_args": {"ssl_ca": ca_path} if ca_path is not None else {},
    }
    kwargs.update(overrides)
    return create_engine(url, **kwargs)


def score_id_list_query(where: str = "") -> str:
    """待重算的 BID 列表查询

    刻意不提供 LIMIT / OFFSET 分页：MySQL 不允许 OFFSET 不带 LIMIT，写出来就不是通用 SQL。
    """
    sql = "SELECT DISTINCT BID FROM %s" % SCORE_TABLE
    if where:
        sql += " WHERE %s" % where
    return sql + " ORDER BY BID"


def score_rows_query(where: str) -> str:
    """按 BID 取成绩行的查询（显式列名，不依赖列顺序）"""
    sql = "SELECT %s FROM %s" % (", ".join([column for column, _ in get_score_column_types()]), SCORE_TABLE)
    if where:
        sql += " WHERE %s" % where
    return sql


def score_count_query() -> str:
    """全表成绩行数（用来核对一次重算是否真的覆盖了整张表）"""
    return "SELECT COUNT(*) FROM %s" % SCORE_TABLE


def score_users_query() -> str:
    """有成绩记录的用户列表（daemon 的 update 命令与前端取用户下拉用同一条）"""
    return "SELECT DISTINCT USER_ID FROM %s ORDER BY USER_ID" % SCORE_TABLE
