import contextlib
import logging
import os
import threading
from collections.abc import Callable, Mapping
from functools import wraps
from typing import Any, Literal, NamedTuple, Optional, get_type_hints

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from osupp.util import make_unstandardized_mods_from_lines
from requests.exceptions import HTTPError
from throttled import Throttled
from throttled.exceptions import LimitedError

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
    _download_beatmap,
    calculate_performance,
    catch_mod_entries,
    mania_mod_entries,
    osu_mod_entries,
    taiko_mod_entries,
    toml_load,
)

OSU_FILE_MAGIC = b"osu file format"

ST_SECRETS_PATH = "./.streamlit/secrets.toml"
ST_CONFIG_PATH = "./.streamlit/config.toml"

HOST = "127.0.0.1"
PORT = 59166

# 同时进行的计算请求数上限
MAX_CONCURRENT_CALCULATIONS = 4

# 一份能交给 osupp 解析的 .osu 至少要包含这些节，且 HitObjects 不能是空的
OSU_SECTION_MARKERS = (b"[General]", b"[Metadata]", b"[Difficulty]", b"[HitObjects]")

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
    "jump",
    "flow",
    "precision",
    "stamina",
    "rhythm_complexity",
)

# ruleset_id -> Performance NamedTuple，用于构建 /api/performance 的载荷
PERFORMANCE_TYPES: dict[int, type[NamedTuple]] = {
    0: OsuPerformance,
    1: TaikoPerformance,
    2: CatchPerformance,
    3: ManiaPerformance,
}

_PPSERVER_CONFIG = toml_load(ST_SECRETS_PATH).get("ppserver", {})
ALLOWED_API_KEYS: frozenset[str] = frozenset(_PPSERVER_CONFIG.get("allowed", []))

formatter = logging.Formatter(toml_load(ST_CONFIG_PATH)["logger"]["messageFormat"])
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

# 进程内串行化「查缓存 -> 删无效缓存 -> 重新下载 -> 读成 bytes」整个段落。
# 下载本身由 _download_beatmap 里的全局 filelock 串行化（跨进程），这把锁是为了
# 避免同一个 bid 的两个请求互相删掉对方刚下好的文件，或在别人重写文件时读到半成品
_beatmaps_lck = threading.Lock()


class BeatmapNotFoundError(Exception):
    """bid 对应的谱面在 osu! 上取不到"""

    def __init__(self, bid: int):
        super().__init__("beatmap %d not found" % bid)
        self.bid = bid


class InvalidParameterError(Exception):
    """客户端参数不合法，对外映射成 400"""


class ServiceUnavailableError(Exception):
    """服务不可用，对外映射成 503"""


def _read_basically_valid_beatmap(path: str) -> bytes | None:
    """读缓存并做基本校验；无效就返回 None，由调用方决定删除重下

    ⚠️ 由于 ppserver 并不持有 osu! api，所以不传递 Beatmap 对象，无从得知 checksum，
    只能靠内容判断。除文件头外还要求必备小节与非空 HitObjects，避免 429 错误页或
    只写了一半的截断文件被永久当成有效缓存。
    """
    try:
        with open(path, "rb") as fi_b:
            data = fi_b.read()
    except OSError:
        return None
    # 有些谱面带 UTF-8 BOM，剥掉再看文件头
    payload = data[3:] if data.startswith(b"\xef\xbb\xbf") else data
    if not payload.startswith(OSU_FILE_MAGIC):
        return None
    if not all(marker in data for marker in OSU_SECTION_MARKERS):
        return None
    return data if data.rsplit(b"[HitObjects]", 1)[-1].strip() else None


def _remove_file(path: str) -> None:
    """删文件；并发下可能已经被别人删掉，或者正被别人占着，静默失败"""
    with contextlib.suppress(OSError):
        os.unlink(path)


def _load_beatmap(bid: int) -> bytes:
    """返回谱面字节；缓存不可用就重下，下不到则抛 :class:`BeatmapNotFoundError`"""
    path = os.path.join(C.BEATMAPS_CACHE_DIRECTORY.value, "%d.osu" % bid)
    data = _read_basically_valid_beatmap(path)
    if data is not None:
        return data
    with _beatmaps_lck:
        data = _read_basically_valid_beatmap(path)  # 拿到锁后再看一眼，可能别人已经下好了
        if data is not None:
            return data
        _remove_file(path)
        # 这里只有 bid、没有 ossapi 的 Beatmap 对象（也就没有 checksum），所以直接走下载入口
        try:
            _download_beatmap(OSU_DOWNLOAD_LCK, bid)
        except HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                raise BeatmapNotFoundError(bid) from e
            raise ServiceUnavailableError("beatmap download is temporarily unavailable") from e
        except Exception as e:
            raise ServiceUnavailableError("beatmap download is temporarily unavailable") from e
        data = _read_basically_valid_beatmap(path)
        if data is None:
            # 同样的位置可能是 404 / 429 的错误页，别留着
            _remove_file(path)
            raise BeatmapNotFoundError(bid)
        return data


def _parse_mods(mods: str, ruleset_id: Literal[0, 1, 2, 3], beatmap_path: str | bytes) -> tuple[list[str], list[str]]:
    """把以分号分隔的 mods 解析成 osu-tools 需要的 ``(mods, mod_options)``"""
    # slot 参数只是用来给 mods 列表打头，这里用 "SP" 占位，解析完再剔除
    unstandardized_mods = make_unstandardized_mods_from_lines(mods=mods.split(";"))
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
        raise InvalidParameterError("unknown performance parameter(s): %s" % ", ".join(unknown_params))  # style: allow
    hints = get_type_hints(performance_type)
    # accuracy_percent 是浮点，其余计数类字段是整型
    try:
        kwargs = {name: float(raw) if hints[name] is float else int(raw) for name, raw in extra_params.items()}
        # noinspection argument-list
        return performance_type(**kwargs)
    except (TypeError, ValueError) as e:
        raise InvalidParameterError("invalid performance parameter(s): %s" % e) from e  # style: allow


def _run_calculation(bid: int, ruleset_id: int, mods_param: str, performance_tup: Optional[NamedTuple]) -> dict:
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
                raise InvalidParameterError("invalid ruleset id: %d" % ruleset_id)
        beatmap_bytes = _load_beatmap(bid)
        mods, mod_options = _parse_mods(mods_param, ruleset_id, beatmap_bytes)
        calculator = calculate_performance(
            beatmap_path=beatmap_bytes,
            ruleset=ruleset,
            mods=mods,
            mod_options=mod_options,
            # 这里超时自动熔断，在线服务需优先保障常规谱面
            # 极端谱面应及时熔断，不影响正常请求
            allow_cancel=True,
        )
        try:
            difficulty_attributes = next(calculator)
            if performance_tup is None:
                ex_diff_attr = difficulty_attributes._get_pure()
                for key in DIFFICULTY_EXTRA_KEYS:
                    ex_diff_attr[key] = difficulty_attributes["__ek_%s" % key]
                # 友好的 length 表达
                # todo: 需要排查上游，这个和 osu! web 的数据会有大概 1s 的误差（PS：其实在其他地方，JavaScript/C#/Python 的 round 误差可能比这个更严重）
                ex_diff_attr["hit_length_min_sec"] = "%d:%02d" % divmod(ex_diff_attr["hit_length_adj"] // 1000, 60)
                ex_diff_attr["drain_length_min_sec"] = "%d:%02d" % divmod(ex_diff_attr["drain_length_adj"] // 1000, 60)
                return ex_diff_attr
            performance_attributes = calculator.send(performance_tup)
            return performance_attributes._get_pure()
        finally:
            calculator.close()


# 将 auth 过程做成装饰器
def auth_required(func: Callable) -> Callable:
    @wraps(func)
    def wrapper(request: Request, *args, **kwargs):
        params = dict(request.query_params)
        try:
            api_key = params.pop("k")
        except KeyError:
            return JSONResponse({"error": "invalid_api_key", "message": "api key missing"}, status_code=403)
        if api_key not in ALLOWED_API_KEYS:
            return JSONResponse({"error": "invalid_api_key", "message": "api key not allowed"}, status_code=403)

        #: api key 由管理员发放、无自助注册，本身就是用来识别调用方的，按约定保留完整值
        _logger.info("handling request from %s: %s" % (api_key, params))

        try:
            # noinspection argument-list
            with Throttled(key=api_key, quota="12/m"):
                return func(request, *args, _params=params, **kwargs)
        except LimitedError as e:
            return JSONResponse({"error": "rate_limit_exceeded", "message": str(e)}, status_code=429)

    return wrapper


def _parse_int_param(params: Mapping[str, str], name: str) -> int:
    raw = params.get(name)
    if raw is None or raw == "":
        raise InvalidParameterError("missing parameter '%s'" % name)
    try:
        return int(raw)
    except ValueError as e:
        raise InvalidParameterError("parameter '%s' must be an integer" % name) from e


@auth_required
def _calculate(_request: Request, *, with_performance: bool, _params: Mapping[str, str]) -> Any:
    """两个端点的公共流程：校验 key -> 记日志 -> 解析参数 -> 计算并映射异常"""
    try:
        # 参数在下载谱面之前全部校验完，坏请求立刻回 400
        bid = _parse_int_param(_params, "b")
        ruleset_id = _parse_int_param(_params, "m")
        if ruleset_id not in PERFORMANCE_TYPES:
            raise InvalidParameterError("invalid ruleset id '%d'" % ruleset_id)
        # 如果不传递 performance，则返回 difficulty attributes，反之返回 performance attributes
        # 永远不会返回 beatmap info
        performance_tup = _build_performance(_params, ruleset_id) if with_performance else None
        return _run_calculation(bid, ruleset_id, _params.get("mods", ""), performance_tup)
    except InvalidParameterError as e:
        return JSONResponse({"error": "invalid_parameter", "message": str(e)}, status_code=400)
    except BeatmapNotFoundError as e:
        return JSONResponse({"error": "beatmap_not_found", "message": str(e)}, status_code=404)
    except ServiceUnavailableError as e:
        return JSONResponse({"error": "service_unavailable", "message": str(e)}, status_code=503)
    except Exception as e:
        # 内部异常的完整信息只留在服务端日志，不回显给调用方
        _logger.exception("calculation failed with params %s" % _params, exc_info=e)
        return JSONResponse({"error": "internal_error", "message": "internal server error"}, status_code=500)


@auth_required
def _entries(_request: Request, *, _params: Mapping[str, str]) -> Any:
    match _params:
        case {"ruleset": "osu"}:
            return osu_mod_entries
        case {"ruleset": "taiko"}:
            return taiko_mod_entries
        case {"ruleset": "catch"}:
            return catch_mod_entries
        case {"ruleset": "mania"}:
            return mania_mod_entries
        case _:
            return JSONResponse({"error": "ruleset_not_found", "message": "unknown ruleset"}, status_code=404)


app = FastAPI(
    title="osuawa ppserver",
    description="A simple osupp Web API",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
# noinspection bad-argument-type
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/difficulty")
def difficulty(request: Request) -> Any:
    return _calculate(request, with_performance=False)


@app.get("/api/performance")
def performance(request: Request) -> Any:
    return _calculate(request, with_performance=True)


@app.get("/api/entries")
def entries(request: Request) -> Any:
    return _entries(request)


if __name__ == "__main__":
    # 不允许多 workers
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning", access_log=False)
