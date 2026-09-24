"""ppserver: 对外提供 osupp 计算结果的 Web API

- ``/api/difficulty``：``next(calculator)``，即难度属性；另带出 strains_of_skills
  与 timeline_of_skills 两个扩展数据，其余 ``__ek_*`` 扩展键丢弃
- ``/api/performance``：在难度属性之后 ``send(<Performance>)``，即表现属性

通用查询参数：

- ``k``：API key，必须在 ``./.streamlit/secrets.toml`` 的 ``[ppserver].allowed`` 中
- ``b``：bid（Beatmap ID）
- ``m``：ruleset_id，0=osu 1=taiko 2=catch 3=mania
- ``mods``：以分号分隔的模组，如 ``HD;DT;DT_speed_change=1.3``。分号会先换成换行，再交给
  :func:`make_unstandardized_mods_from_lines` 宽松解析

``/api/performance`` 除上述参数外的所有查询参数，一律用来构建对应 Ruleset 的 Performance
NamedTuple（字段名见 ``osupp.performance``），如 ``accuracy_percent``、``misses``、``combo``。

报错处理只有三类：API key 不允许、bid 未找到、计算错误（参数错误也归在这里）。
"""

import contextlib
import logging
import os
import threading
from collections.abc import Mapping
from typing import Any, Literal, NamedTuple, get_type_hints

import toml
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from osuawa.utils import (
    C,
    CatchPerformance,
    CatchRuleset,
    ManiaPerformance,
    ManiaRuleset,
    OSU_DOWNLOAD_LCK,
    OsuPerformance,
    OsuRuleset,
    SimpleDifficultyAttribute,
    TaikoPerformance,
    TaikoRuleset,
    _download_osu,
    calculate_performance,
    make_unstandardized_mods_from_lines,
)

OSU_MAGIC = b"osu file format"

ST_SECRETS_PATH = "./.streamlit/secrets.toml"
ST_CONFIG_PATH = "./.streamlit/config.toml"

HOST = "127.0.0.1"
PORT = 59166

# 同时进行的计算请求数上限
MAX_CONCURRENT_CALCULATIONS = 4

# 通用参数（含 API key），其余查询参数在 /api/performance 中都被当作 Performance 字段
COMMON_PARAMS = frozenset({"k", "b", "m", "mods"})

# /api/difficulty 除基础难度属性外额外带出的 osupp 扩展键（在 Result 中带 __ek_ 前缀）
DIFFICULTY_EXTRA_KEYS = (
    "strains_of_skills",
    "timeline_of_skills",
    "cs_orig",
    "ar_orig",
    "od_orig",
    "hp_orig",
    "cs_adj",
    "ar_adj",
    "od_adj",
    "hp_adj",
    "most_common_bpm_orig",
    "clock_rate",
    "most_common_bpm_adj",
    "hit_length_orig",
    "drain_length_orig",
    "hit_length_adj",
    "drain_length_adj",
)

# ruleset_id -> Performance NamedTuple，用于构建 /api/performance 的载荷
PERFORMANCE_TYPES: dict[int, type[NamedTuple]] = {
    0: OsuPerformance,
    1: TaikoPerformance,
    2: CatchPerformance,
    3: ManiaPerformance,
}

ALLOWED_API_KEYS: list[str] = toml.load(ST_SECRETS_PATH)["ppserver"]["allowed"]

formatter = logging.Formatter(toml.load(ST_CONFIG_PATH)["logger"]["messageFormat"])
ch = logging.StreamHandler()
ch.setLevel(logging.DEBUG)
ch.setFormatter(formatter)
os.makedirs(C.LOGS.value, exist_ok=True)
fh = logging.FileHandler(os.path.join(C.LOGS.value, "ppserver.log"), encoding="utf-8")
fh.setFormatter(formatter)
_logger = logging.getLogger("ppserver")
_logger.setLevel(logging.DEBUG)
_logger.addHandler(ch)
_logger.addHandler(fh)

_calculation_semaphore = threading.BoundedSemaphore(MAX_CONCURRENT_CALCULATIONS)

# 进程内串行化「查缓存 -> 删无效缓存 -> 重新校验」这一小段。
# 下载本身由 _download_osu 里的全局 filelock 串行化（跨进程），这把锁只是为了避免
# 同一个 bid 的两个请求互相删掉对方刚下好的文件、把结果误判成 404
_beatmaps_lock = threading.Lock()


class BeatmapNotFoundError(Exception):
    """bid 对应的谱面在 osu! 上取不到"""

    def __init__(self, bid: int):
        super().__init__("beatmap %d not found" % bid)
        self.bid = bid


def _is_basically_valid_beatmap_file(path: str) -> bool:
    """判断缓存文件是不是一份基本有效的谱面

    Downloader 不检查 HTTP 状态码，所以当 osu! web 限流或 bid 不存在时，缓存里会留下
    0 字节的文件或一张错误页——这种文件必须当成「没缓存」，否则会一直卡在那儿。

    ⚠️ 由于 ppserver 并不持有 osu! api，所以不传递 Beatmap 对象，无从得知 checksum，
    必须通过简单地读取文件头来判断 .osu 文件是否基本合法
    """
    if not os.path.exists(path) or os.path.getsize(path) < len(OSU_MAGIC):
        return False
    with open(path, "rb") as fi_b:
        return fi_b.read(len(OSU_MAGIC)) == OSU_MAGIC


def _remove_file(path: str) -> None:
    """删文件；并发下可能已经被别人删掉，或者正被别人占着，静默失败"""
    with contextlib.suppress(OSError):
        os.unlink(path)


def _resolve_beatmap_path(bid: int) -> str:
    """返回谱面文件路径；缓存不可用就重下，下不到则抛 :class:`BeatmapNotFoundError`"""
    path = os.path.join(C.BEATMAPS_CACHE_DIRECTORY.value, "%d.osu" % bid)
    if _is_basically_valid_beatmap_file(path):
        return path
    with _beatmaps_lock:
        # 拿到锁之后再看一眼：同一个 bid 的并发请求里，可能已经有别人下好了
        if not _is_basically_valid_beatmap_file(path):
            _remove_file(path)
            # 这里只有 bid、没有 ossapi 的 Beatmap 对象（也就没有 checksum），所以直接走下载入口
            _download_osu(OSU_DOWNLOAD_LCK, bid)
            if not _is_basically_valid_beatmap_file(path):
                # 同样的位置可能是 404 的错误页，别留着
                _remove_file(path)
                raise BeatmapNotFoundError(bid)
    return path


def _parse_mods(mods: str, ruleset_id: Literal[0, 1, 2, 3], beatmap_path: str) -> tuple[list[str], list[str]]:
    """把以分号分隔的 mods 解析成 osu-tools 需要的 ``(mods, mod_options)``"""
    # slot 参数只是用来给 mods 列表打头，这里用 "SP" 占位，解析完再剔除
    unstandardized_mods = make_unstandardized_mods_from_lines("SP", mods.replace(";", "\n"))
    unstandardized_mods.remove({"acronym": "SP"})
    _standardized_mods, _mods_dict, osu_tool_mods, osu_tool_mod_options = SimpleDifficultyAttribute.validate_and_transform_mods(
        unstandardized_mods,
        ruleset_id,
        beatmap_path,
    )
    return osu_tool_mods, osu_tool_mod_options


def _build_performance(params: Mapping[str, str], ruleset_id: int) -> NamedTuple:
    """把通用参数之外的查询参数自动组装成对应 Ruleset 的 Performance NamedTuple"""
    performance_type = PERFORMANCE_TYPES[ruleset_id]
    extra_params = {name: value for name, value in params.items() if name not in COMMON_PARAMS}
    unknown_params = sorted(set(extra_params) - set(performance_type._fields))
    if unknown_params:
        raise ValueError("unknown performance parameter(s): %s" % ", ".join(unknown_params))
    hints = get_type_hints(performance_type)
    # accuracy_percent 是浮点，其余计数类字段是整型
    kwargs = {name: float(raw) if hints[name] is float else int(raw) for name, raw in extra_params.items()}
    # noinspection argument-list
    return performance_type(**kwargs)


def _run_calculation(bid: int, ruleset_id: int, params: Mapping[str, str], *, with_performance: bool) -> dict:
    """带着信号量跑一次 osupp 计算，返回可直接 JSON 化的 dict"""
    with _calculation_semaphore:
        match ruleset_id:
            case 0:
                ruleset = OsuRuleset()
            case 1:
                ruleset = TaikoRuleset()
            case 2:
                ruleset = CatchRuleset()
            case 3:
                ruleset = ManiaRuleset()
            case _:
                raise ValueError("ruleset id %d not supported" % ruleset_id)
        beatmap_path = _resolve_beatmap_path(bid)
        mods, mod_options = _parse_mods(params.get("mods", ""), ruleset_id, beatmap_path)
        calculator = calculate_performance(
            beatmap_path=beatmap_path,
            ruleset=ruleset,
            mods=mods,
            mod_options=mod_options,
            # 这里超时自动熔断，在线服务需优先保障常规谱面
            # 极端谱面应及时熔断，不影响正常请求
            allow_cancel=True,
        )
        try:
            difficulty_attributes = next(calculator)
            if not with_performance:
                ex_diff_attr = difficulty_attributes._get_pure()
                for key in DIFFICULTY_EXTRA_KEYS:
                    ex_diff_attr[key] = difficulty_attributes["__ek_%s" % key]
                # 友好的 length 表达
                # todo: 需要排查上游，这个和 osu! web 的数据会有大概 1s 的误差（PS：其实在其他地方，JavaScript/C#/Python 的 round 误差可能比这个更严重）
                ex_diff_attr["hit_length_min_sec"] = "%d:%02d" % divmod(ex_diff_attr["hit_length_adj"] // 1000, 60)
                ex_diff_attr["drain_length_min_sec"] = "%d:%02d" % divmod(ex_diff_attr["drain_length_adj"] // 1000, 60)
                return ex_diff_attr
            performance_attributes = calculator.send(_build_performance(params, ruleset_id))
            return performance_attributes._get_pure()
        finally:
            calculator.close()


def _calculate(request: Request, *, with_performance: bool) -> Any:
    """两个端点的公共流程：校验 key -> 记日志 -> 解析参数 -> 计算并映射异常"""
    params = dict(request.query_params)

    try:
        api_key = params.pop("k")
    except KeyError:
        return JSONResponse({"error": "invalid_api_key", "message": "api key missing"}, status_code=403)
    if api_key not in ALLOWED_API_KEYS:
        return JSONResponse({"error": "invalid_api_key", "message": "api key not allowed"}, status_code=403)

    _logger.info("handling request from %s: %s" % (api_key, params))

    try:
        try:
            bid = int(params.get("b", ""))
            ruleset_id = int(params.get("m", ""))
        except ValueError as e:
            raise ValueError("'b' and 'm' must be integers") from e

        return _run_calculation(bid, ruleset_id, params, with_performance=with_performance)
    except BeatmapNotFoundError as e:
        return JSONResponse({"error": "beatmap_not_found", "message": str(e)}, status_code=404)
    except Exception as e:
        _logger.exception("calculation failed with params %s" % params)
        return JSONResponse({"error": "calculation_failed", "message": str(e)}, status_code=500)


app = FastAPI(title="osuawa ppserver", description="A simple osupp Web API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)


@app.get("/api/difficulty")
def difficulty(request: Request) -> Any:
    return _calculate(request, with_performance=False)


@app.get("/api/performance")
def performance(request: Request) -> Any:
    return _calculate(request, with_performance=True)


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning", access_log=False)
