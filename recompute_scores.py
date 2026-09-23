import argparse
import asyncio
import json
import logging
import os
import time
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from typing import Any, Optional

import orjson
import toml
from ossapi.ossapiv2_async import Domain, Scope
from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine

from osuawa import Osuawa
from osuawa.db import SCORE_PRIMARY_KEY, build_score_insert_sql, create_db_engine, get_score_insert_pairs, resolve_db_url, score_count_query, score_id_list_query, score_rows_query, scores_compact_to_params, sync_score_columns
from osuawa.utils import (
    C,
    CompletedSimpleScoreInfo,
    SimpleScoreInfo,
    calc_beatmap_attributes_batch,
)

# logging，样式与 run_daemon.py 一致
st_config = toml.load("./.streamlit/config.toml")
formatter = logging.Formatter(st_config["logger"]["messageFormat"])
ch = logging.StreamHandler()
ch.setLevel(logging.DEBUG)
ch.setFormatter(formatter)
os.makedirs(C.LOGS.value, exist_ok=True)
fh = logging.FileHandler(os.path.join(C.LOGS.value, "recompute_scores.log"), encoding="utf-8")
fh.setFormatter(formatter)
logger = logging.getLogger("recompute")
logger.setLevel(logging.DEBUG)
logger.addHandler(ch)
logger.addHandler(fh)

#: (数据库列名, 绑定参数名)，主键不参与比对（它在库里是 int、在绑定参数里是 str）
COMPARE_PAIRS = tuple(pair for pair in get_score_insert_pairs() if pair[0] != SCORE_PRIMARY_KEY)

#: 这些列存的是 JSON 文本，比对时要按内容比（键顺序不同不算差异）
JSON_COLUMNS = frozenset({"MODS", "STATISTICS"})

#: 每批处理的谱面数
CHUNK_SIZE = 200


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="重算 SCORE 表", formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true", help="正式执行重算并写回数据库（默认只做检查比对）")
    return parser.parse_args(argv)


def differs(column: str, old: Any, new: Any) -> bool:
    """比对库里旧值与新算出的值；浮点按容差比，JSON 按内容比"""
    if column in JSON_COLUMNS:
        old = orjson.loads(old) if old else None
        new = orjson.loads(new) if new else None
        return old != new
    if old is None or new is None:
        return old is not new
    try:
        return abs(float(old) - float(new)) > 1e-6
    except (TypeError, ValueError):
        return old != new


def load_bids(engine: Engine) -> Sequence[int]:
    """取出待重算的谱面列表（没有 WHERE，就是全表）"""
    with engine.connect() as conn:
        return conn.execute(text(score_id_list_query(""))).scalars().all()


def count_rows(engine: Engine) -> int:
    """表里的成绩行数，用来核对本次重算是否真的覆盖了全表"""
    with engine.connect() as conn:
        return int(conn.execute(text(score_count_query())).scalar_one())


def load_rows(engine: Engine, bids: Sequence[int]) -> dict[str, Mapping[str, Any]]:
    """取出这批谱面的全部成绩行，返回 ``score_id -> 原始行``（列名已大写）"""
    values = {"chunk_bids": list(bids)}
    statement = text(score_rows_query("BID IN :chunk_bids")).bindparams(bindparam("chunk_bids", expanding=True))
    with engine.connect() as conn:
        rows = conn.execute(statement, values).all()
    return {str(row._mapping["SCORE_ID"]): {str(key).upper(): value for key, value in row._mapping.items()} for row in rows}


def iter_computed(engine: Engine, awa: Osuawa, expected_rows: int) -> Iterator[tuple[dict[str, Mapping[str, Any]], dict[str, CompletedSimpleScoreInfo]]]:
    """按谱面分批重算，逐批产出 ``(原始行, 重算结果)``

    ``原始行`` 是 ``score_id -> 库里的旧值（列名大写）``，供检查比对使用。
    """
    bid_list = load_bids(engine)
    total = len(bid_list)
    if not bid_list and expected_rows:
        raise RuntimeError("SCORE 表里有 %d 行成绩，却一个谱面都查不出来" % expected_rows)
    done_rows = 0
    logger.info("待重算 %d 个谱面 / %d 行成绩，每批 %d 个谱面" % (total, expected_rows, CHUNK_SIZE))
    for start in range(0, total, CHUNK_SIZE):
        chunk = bid_list[start : start + CHUNK_SIZE]
        beatmaps = awa.run_coro(awa.async_get_beatmaps_dict(chunk))
        rows = load_rows(engine, chunk)
        scores_compact: dict[str, SimpleScoreInfo] = {score_id: SimpleScoreInfo.from_row(row) for score_id, row in rows.items()}
        absent = sorted({score.bid for score in scores_compact.values()} - set(beatmaps))
        if absent:
            raise RuntimeError("这些谱面取不到（API 查不到，可能已下架）：%s" % ", ".join(str(bid) for bid in absent[:8]))
        computed = calc_beatmap_attributes_batch(beatmaps, scores_compact)
        if set(computed) != set(rows):
            missing = sorted(set(rows) - set(computed))
            extra = sorted(set(computed) - set(rows))
            problems = []
            if missing:
                problems.append("%d 行没算出结果（%s）" % (len(missing), ", ".join(missing[:8])))
            if extra:
                problems.append("多出 %d 个库里没有的 score_id（%s）" % (len(extra), ", ".join(extra[:8])))
            raise RuntimeError("这批 %d 行对不上：%s。重算是全量覆盖，缺行会让这些成绩停在旧值上，所以直接终止" % (len(rows), "；".join(problems)))
        done_rows += len(rows)
        logger.info("进度：行 %d/%d，谱面 %d/%d" % (done_rows, expected_rows, min(start + CHUNK_SIZE, total), total))
        yield rows, computed


def check_scores(engine: Engine, awa: Osuawa, expected_rows: int) -> None:
    """检查比对：重算一遍，与库里的旧值逐列比对，只报告不写库"""
    total_rows = 0
    columns: Counter[str] = Counter()
    for rows, computed in iter_computed(engine, awa, expected_rows):
        for score_id, completed in computed.items():
            raw = rows[score_id]
            new = scores_compact_to_params(score_id, completed)
            diff = [column for column, param in COMPARE_PAIRS if differs(column, raw.get(column), new.get(param))]
            total_rows += 1
            if not diff:
                continue
            columns.update(diff)
    result_filename = "check_result_%s.json" % int(time.time())
    logger.info("比对完成：共 %d 行（表里 %d 行），有差异的列与行数已写入 %s" % (total_rows, expected_rows, result_filename))
    with open(result_filename, "w", encoding="utf-8") as f:
        json.dump(columns, f, ensure_ascii=False, indent=4)


def recompute_scores(engine: Engine, awa: Osuawa, dialect: str, expected_rows: int) -> None:
    """正式执行重算：重算并按主键 upsert 无条件覆盖写回，每批提交一次事务"""
    insert_sql = text(build_score_insert_sql(dialect, "update"))
    written = 0
    logger.warning("重算开始：共 %d 行 %d 个字段" % (expected_rows, len(COMPARE_PAIRS)))
    for _, computed in iter_computed(engine, awa, expected_rows):
        batch = [scores_compact_to_params(score_id, completed) for score_id, completed in computed.items()]
        if not batch:
            continue
        with engine.begin() as conn:
            conn.execute(insert_sql, batch)
        written += len(batch)
    logger.info("重算完成：%d 行已覆盖写回" % written)
    if written != expected_rows:
        logger.warning("写回 %d 行，与开始时读到的 %d 行对不上；差额一般是重算期间 daemon 新插入或删除了行" % (written, expected_rows))


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    secrets = toml.load("./.streamlit/secrets.toml")
    url, dialect, ca_path = resolve_db_url(secrets)
    engine = create_db_engine(url, ca_path)
    logger.info("数据库已连接：%s" % url.split("@")[-1])

    created, added, extra = sync_score_columns(engine)
    if created:
        logger.error("SCORE 表不存在，已建好空表")
        return 2
    if added:
        logger.warning("SCORE 表自动补列：%s" % ", ".join("%s %s" % column for column in added))
    if extra:
        logger.warning("SCORE 表存在多余的列：%s" % ", ".join(extra))

    expected_rows = count_rows(engine)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    awa = Osuawa(
        loop,
        secrets["args"]["client_id"],
        secrets["args"]["client_secret"],
        None,
        [Scope.PUBLIC.value],
        Domain.OSU.value,
        "recompute",  # 与 daemon 分开，避免互相覆盖 token 缓存文件
        None,
        None,
    )
    if args.write:
        recompute_scores(engine, awa, dialect, expected_rows)
    else:
        check_scores(engine, awa, expected_rows)
    engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
