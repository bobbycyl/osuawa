import os.path
import shutil
import time
from functools import partial
from typing import Any, Optional, TYPE_CHECKING, cast
from uuid import uuid4

import orjson
import pandas as pd
import requests
import streamlit as st
from clayutil.futil import compress_as_zip
from clayutil.validator import validate_type
from osupp.performance import calculate_performance
from st_aggrid import AgGrid, ColumnsAutoSizeMode, GridOptionsBuilder, JsCode
from streamlit import logger

from osuawa import C, OsuPlaylist
from osuawa.components import (
    awa_query as _awa_query,
    get_session_id,
    init_page,
    load_value,
    memorized_selectbox,
    mods_generator,
    push_task_with_session_state,
    refresh,
    save_value, draw_clustered_bars,
)
from osuawa.osuawa import Osuawa
from osuawa.utils import (
    BeatmapSpec,
    BeatmapToUpdate,
    RedisTaskId,
    SimpleDifficultyAttribute, _create_tmp_playlist_p,
    available_mods, get_playlist_lastupdate,
    make_unstandardized_mods_from_lines,
    read_injected_code,
    safe_norm,
)

validate_restricted_identifier = partial(validate_type, type_=str, min_value=1, max_value=16, predicate=str.isidentifier)

if TYPE_CHECKING:

    def _(_text: str) -> str: ...

    st.session_state.awa = cast(Osuawa, st.session_state.awa)
    st.session_state.redis_tasks = cast(list[RedisTaskId], st.session_state.redis_tasks)

init_page(_("Playlist Generator") + " - osuawa")
with st.sidebar:
    if st.button(
        _("Mod Generator"),
        use_container_width=True,
        icon=":material/sync_alt:",
        disabled=not st.session_state.basic_interaction_enabled,
    ):

        @st.dialog(_("Mod Generator"), width="medium")
        def _mods_generator(ret_type=None):
            return mods_generator(ret_type)

        _mods_generator()
    st.toggle(
        _("New Style"),
        key="new_style",
        value=True,
        disabled=not st.session_state.basic_interaction_enabled,
    )

uid = get_session_id()
row_style_with_dup = JsCode(read_injected_code("row_style_with_dup.js"))
row_style = JsCode(read_injected_code("row_style.js"))
slot_cell_style_js = JsCode(read_injected_code("slot_cell_style.js") % orjson.dumps(OsuPlaylist.mod_color).decode())
copy_on_click_js = JsCode(read_injected_code("copy_on_click.js"))
image_link_renderer = JsCode(read_injected_code("image_link_renderer.js") % ("../../app/" + C.UPLOADED_DIRECTORY.value.strip("./") + "/online/darkened-backgrounds/"))
monaco_editor = JsCode(read_injected_code("monaco_editor.js"))
st.markdown(read_injected_code("st_aggrid_style.css"), unsafe_allow_html=True)


@st.cache_data(ttl=60)
def beatmap_database_query(sql: str, show_spinner: bool | str = False, params: Optional[Any] = None, **kwargs) -> pd.DataFrame:
    return _awa_query("BEATMAP", sql, show_spinner, params, **kwargs)


def default(obj):
    if hasattr(obj, "_fields"):
        return list(obj)
    raise TypeError


def push_beatmap_task(_b: list[BeatmapToUpdate]) -> str:
    # todo: 由于 streamlit 的刷新机制，basic_interaction_enabled 在这里设置是无效的
    return push_task_with_session_state("beatmap %s" % orjson.dumps(_b, option=orjson.OPT_PASSTHROUGH_SUBCLASS, default=default).decode())


def generate_playlist(filename: str, css_style: Optional[int] = None):
    # 由于这个有实时性要求，因此不挪到后台处理
    playlist = OsuPlaylist(st.session_state.awa, filename, css_style=css_style)
    return playlist.generate()


@st.dialog(_("Export selection as a playlist"))
def export_filtered_playlist():
    if selected_rows is None or len(selected_rows) == 0:
        st.error(_("no beatmaps selected"))
        return
    parsed_mods_list = [orjson.loads(x) for x in selected_rows["RAW_MODS"]]
    specs_x = [
        BeatmapSpec(bid, mods, skill, "", note, 0, "", "", 0.0)
        for bid, mods, skill, note in zip(
            selected_rows["BID"],
            parsed_mods_list,
            selected_rows["SKILL_SLOT"],
            selected_rows["NOTES"],
            strict=True,
        )
    ]  # 直接用解析好的列表
    try:
        tmp_playlist_filename_x = _create_tmp_playlist_p(uid, specs_x)
    except ValueError as e:
        st.error(e)
        return
    st.code("\n".join([str(bid) for bid in selected_rows["BID"]]))
    with open(tmp_playlist_filename_x, "r", encoding="utf-8") as fi:
        st.code(fi.read(), language="properties")


NEEDED_ATTR_MAPPING = {
    "star_rating": "SR",
    "max_combo": "MaxCb",
    "aim_difficulty": "Aim",
    "speed_difficulty": "Speed",
    "reading_difficulty": "Reading",
    "__ek_cs_adj": "CS",
    "__ek_ar_adj": "AR",
    "__ek_od_adj": "OD",
    "__ek_hp_adj": "HP",
    "__ek_most_common_bpm_adj": "BPM",
    "__ek_hit_length_adj": "LEN",
}


# noinspection
@st.cache_data(ttl=300)
def calc_attr(data) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    processed: dict[str, dict[str, Any]] = {}
    skill_type_mapping: dict[str, list[str]] = {}  # {NM: [NM1, NM2, ...], FM: [FM1, FM2, ..., FM1 (HR), FM1 (HD), ...]}
    # todo: 由于 osupp 设计问题，读取谱面现在只能重复进行。后续考虑优化
    for i, row in enumerate(data.itertuples(), start=1):
        _bid = int(row.BID)
        _raw_mods = orjson.loads(row.RAW_MODS)
        _raw_mods = _raw_mods.copy() if _raw_mods[0]["acronym"] in available_mods else _raw_mods[1:].copy()
        _slot = row.SKILL_SLOT
        skill_type = _slot[:2]
        _path = os.path.join(C.BEATMAPS_CACHE_DIRECTORY.value, "%d.osu" % _bid)
        _standardized_mods, _mods_dict, osu_tool_mods, osu_tool_mod_options = SimpleDifficultyAttribute.validate_and_transform_mods(
            _raw_mods, beatmap_path=_path
        )
        calculator = calculate_performance(
            beatmap_path=_path,
            mods=osu_tool_mods,
            mod_options=osu_tool_mod_options,
        )
        try:
            ex_diff_attr = next(calculator)
        finally:
            calculator.close()
        key = "%s<br>(%d/l%d)" % (_slot, _bid, i)
        processed[key] = {NEEDED_ATTR_MAPPING[attr]: ex_diff_attr[attr] for attr in NEEDED_ATTR_MAPPING}
        skill_type_mapping.setdefault(skill_type, []).append(key)

        if skill_type in ("FM", "F+", "SP"):  # 额外计算 bid(HR) bid(HD) bid(EZ)
            calculator_hr = calculate_performance(
                beatmap_path=_path,
                mods=osu_tool_mods + ["HR"],
                mod_options=osu_tool_mod_options,
            )
            calculator_hd = calculate_performance(
                beatmap_path=_path,
                mods=osu_tool_mods + ["HD"],
                mod_options=osu_tool_mod_options,
            )

            calculator_ez = calculate_performance(
                beatmap_path=_path,
                mods=osu_tool_mods + ["EZ"],
                mod_options=osu_tool_mod_options,
            )
            try:
                ex_diff_attr_hr = next(calculator_hr)
                ex_diff_attr_hd = next(calculator_hd)
                ex_diff_attr_ez = next(calculator_ez)
            finally:
                calculator_hr.close()
                calculator_hd.close()
                calculator_ez.close()
            processed[key + " (HR)"] = {NEEDED_ATTR_MAPPING[attr]: ex_diff_attr_hr[attr] for attr in NEEDED_ATTR_MAPPING}
            skill_type_mapping[skill_type].append(key + " (HR)")
            processed[key + " (HD)"] = {NEEDED_ATTR_MAPPING[attr]: ex_diff_attr_hd[attr] for attr in NEEDED_ATTR_MAPPING}
            skill_type_mapping[skill_type].append(key + " (HD)")
            processed[key + " (EZ)"] = {NEEDED_ATTR_MAPPING[attr]: ex_diff_attr_ez[attr] for attr in NEEDED_ATTR_MAPPING}
            skill_type_mapping[skill_type].append(key + " (EZ)")
    return processed, skill_type_mapping

@st.fragment
def show_statistics():
    processed, skill_type_mapping = calc_attr(df)

    df_for_stats = pd.DataFrame.from_dict(processed, orient="index").astype(float)
    df_for_stats["skill_type"] = df_for_stats.index.str[:2]
    stats_filter = st.multiselect(_("Slot Filter"), skill_type_mapping.keys(), default=skill_type_mapping.keys())
    indexes_filter = st.multiselect(_("Index Filter"), NEEDED_ATTR_MAPPING.values(), default=("SR", "Aim", "Speed", "Reading"))
    # apply filter
    df_for_stats = df_for_stats[df_for_stats["skill_type"].isin(stats_filter)]
    st.table(df_for_stats.drop(columns=["skill_type"]).mean().rename("Mean"))
    df_for_stats = df_for_stats[indexes_filter]
    fig = draw_clustered_bars(df_for_stats, title="Statistics of the data query", xaxis_title="Skill Type", yaxis_title="Values")
    st.plotly_chart(fig)


@st.fragment(run_every="15s")
def check_playlist_lastupdate():
    _playlist_lastupdate = get_playlist_lastupdate()
    if "playlist_lastupdate" not in st.session_state:
        st.session_state.playlist_lastupdate = _playlist_lastupdate
    else:
        if _playlist_lastupdate > st.session_state.playlist_lastupdate:
            st.toast(_("Playlist had been updated. Shall we refresh?"))


if st.session_state.perm >= 1:
    if "online_playlist_info" not in st.session_state or not st.session_state.online_playlist_info:
        st.info(_("Auto refresh is disabled on this page. Click the `%s` button to refresh manually.") % _("Refresh"))
        st.session_state.online_playlist_info = True

    st.markdown(_("## Online Playlist Creator"))
    # online_playlist_slot = st.empty()
    check_playlist_lastupdate()
    available_pools = beatmap_database_query(
        """SELECT DISTINCT POOL
           FROM BEATMAP
           ORDER BY POOL""",
        show_spinner=_("querying available pools"),
    )["POOL"].to_list()
    if len(available_pools) == 0:
        available_pools.append("_DEFAULT_POOL")

    with st.form(_("Add beatmap")):
        col1, col2 = st.columns(2)
        with col1:
            urls_input = st.text_input(_("Beatmap URLs or IDs, separated by spaces"))
            slot_input = st.text_input(_("Slot"))
            notes_input = st.text_input(_("Notes"))
        with col2:
            # 由于 form 不允许组件设置 on_change，因此这里要手动实现记忆功能
            load_value("gen_form_pool", "_DEFAULT_POOL")
            _pool_index: Optional[int] = None
            if len(available_pools) > 0:
                try:
                    _pool_index = available_pools.index(st.session_state.gen_form_pool)
                except ValueError:
                    _pool_index = 0
            pool_input = st.selectbox(_("Pool (text before '-' is considered series)"), available_pools, index=_pool_index, accept_new_options=True)
            if "modgen_ret" in st.session_state and len(st.session_state.modgen_ret) > 0:
                st.session_state.gen_form_mod_settings = "\n".join(st.session_state.modgen_ret.pop()[0])
            st.text_area(
                _("Mod Settings"),
                height="stretch",
                key="gen_form_mod_settings",
                placeholder="Tip: You can use %s to set mods." % _("Mod Generator"),
            )
            # status_input = st.slider(_("Status"), 0, 2, 0)
        submitted = st.form_submit_button(_("Add"), use_container_width=True)
        if submitted:
            st.session_state.gen_form_pool = pool_input
            save_value("gen_form_pool")
            specs_input: list[BeatmapSpec] = []
            if slot_input is None or slot_input == "":
                st.error(_("blank slot not allowed"))
            elif urls_input is None or urls_input == "":
                st.error(_("blank beatmap not allowed"))
            else:
                # SLOTS 自动大写
                slot_input = slot_input[:2].upper() + slot_input[2:]
                urls_input_split = urls_input.split()
                raw_mods_input = make_unstandardized_mods_from_lines(slot_input, st.session_state.gen_form_mod_settings or "")

                # 为了代码可读性和便于后续修改，这里没有直接生成 BeatmapToUpdate 列表，而是做了两次循环
                for url_input in urls_input_split:
                    # 处理 BID
                    bid_input = int(url_input.rsplit("/", 1)[-1])
                    specs_input.append(
                        BeatmapSpec(
                            bid_input,
                            raw_mods_input,
                            slot_input,
                            st.session_state.gen_form_pool or "",
                            notes_input or "",
                            0,
                            "",
                            st.session_state.username,
                            time.time(),
                        ),
                    )
                if len(specs_input) == 0:
                    st.toast(_("no beatmaps input"))
                else:
                    st.toast(
                        push_beatmap_task(
                            [
                                BeatmapToUpdate(
                                    action="add",
                                    name=uid,
                                    beatmap=spec_input,
                                    old_bid=None,
                                    old_mods=None,
                                )
                                for spec_input in specs_input
                            ],
                        ),
                    )

    with st.container(border=True):
        filter_col1, filter_col2, filter_col3, ctrl_col1 = st.columns([3, 3, 9, 4])
        with filter_col1:
            memorized_selectbox(_("Pool"), "gen_filter_pool", ["-"] + available_pools, "-")
        with filter_col2:
            memorized_selectbox(_("Status"), "gen_filter_status", [-1, 0, 1, 2], -1)
        with filter_col3:
            st.text_input(
                _("Search"),
                key="gen_filter_search",
                placeholder=_("Search in BID, slot, notes etc."),
            )
        with ctrl_col1:
            highlight_dup = st.checkbox(_("Highlight duplicates"), value=True)
            match_slot_sort = st.checkbox(_("Match sorting"), value=False)

    # 查询重复的 BID 与曲目，用于后期查询结果表格渲染。重复的 BID 行要标记为红色，重复的曲目行要标记为黄色
    # 按 SERIES 选定查询范围
    # U_ARTIST + U_TITLE 用于曲目识别
    if st.session_state.gen_filter_pool != "-":
        cur_series = st.session_state.gen_filter_pool.split("-")[0]
        duplicate_bids = beatmap_database_query(
            """SELECT BID
               FROM BEATMAP
               WHERE SERIES = :series
               GROUP BY BID
               HAVING COUNT(*) > 1""",
            params={"series": cur_series},
            show_spinner=_("querying duplicate BIDs"),
        )["BID"].to_list()
        duplicate_songs_raw = beatmap_database_query(
            """SELECT U_ARTIST, U_TITLE, COUNT(*)
               FROM BEATMAP
               WHERE SERIES = :series
               GROUP BY U_ARTIST, U_TITLE
               HAVING COUNT(*) > 1""",
            params={"series": cur_series},
            show_spinner=_("querying duplicate song names"),
        )
    else:
        # 如果不指定 POOL，就回退到原始模式
        duplicate_bids = beatmap_database_query(
            """SELECT BID
               FROM BEATMAP
               GROUP BY BID
               HAVING COUNT(*) > 1""",
            show_spinner=_("querying duplicate BIDs"),
        )["BID"].to_list()
        duplicate_songs_raw = beatmap_database_query(
            """SELECT U_ARTIST, U_TITLE, COUNT(*)
               FROM BEATMAP
               GROUP BY U_ARTIST, U_TITLE
               HAVING COUNT(*) > 1""",
            show_spinner=_("querying duplicate song names"),
        )
    duplicate_songs = (duplicate_songs_raw["U_ARTIST"] + " " + duplicate_songs_raw["U_TITLE"]).to_list()

    # pool 和 status 的查询使用 SQL 完成
    # noinspection SqlConstantExpression
    filter_query = """SELECT *
                      FROM BEATMAP
                      WHERE 1 = 1"""
    filter_params = {}
    if st.session_state.gen_filter_pool != "-":
        filter_query += " AND POOL = :pool"
        filter_params["pool"] = st.session_state.gen_filter_pool
    if st.session_state.gen_filter_status != -1:
        filter_query += " AND STATUS = :status"
        filter_params["status"] = st.session_state.gen_filter_status
    df: pd.DataFrame = beatmap_database_query(filter_query, show_spinner=_("querying the playlist"), params=filter_params)

    # keywords 的筛选用 pandas 完成，从 bid、sid、info、slot、mods、notes 中查找包含输入内容的条目
    if st.session_state.gen_filter_search:
        keyword_lower = st.session_state.gen_filter_search.lower()
        target_cols = ["BID", "SID", "INFO", "SKILL_SLOT", "MODS", "NOTES"]
        mask = df[target_cols].apply(
            lambda x: x.astype(str).str.lower().str.contains(keyword_lower, na=False).any(),
            axis=1,
        )
        df = df[mask]

    # 新增 JS 辅助列
    df["_is_dup_bid"] = df["BID"].isin(duplicate_bids)
    df["_is_dup_song"] = (df["U_ARTIST"] + " " + df["U_TITLE"]).isin(duplicate_songs)

    if match_slot_sort:
        # 新增 SLOT 排序辅助列
        # 拆分 SKILL_SLOT 列为 _slot_name (:2) 和 _slot_index (2:) 两列，
        # _slot_name 向左填充 0，直至达到 SLOT_MAX_LEN - 2 位
        # 排序方式：
        # 1. 首先按照 _slot_name 排序，顺序为 NM -> HD -> HR -> DT -> FM -> F+ -> 其他未列明字段 -> TB
        # 2. 然后按照 _slot_index 排序，因为向左填充 0 了所以直接按 alphabet 排序即可
        df["_slot_name"] = df["SKILL_SLOT"].str[:2]
        df["_slot_index"] = df["SKILL_SLOT"].str[2:].str.zfill(int(C.SLOT_MAX_LEN.value) - 2)
        custom_order = ["NM", "HD", "HR", "DT", "FM", "F+"]
        last_special = "TB"
        other_names = sorted(
            set(df["_slot_name"].unique()) - set(custom_order) - {last_special},
        )
        category_order = custom_order + other_names + [last_special]
        # 这里不能使用 pd.Categorical：pandas 3 下 Categorical 列在 Arrow 中是 dictionary<values=large_string>，
        # 而 Streamlit 的组件序列化只 downcast 顶层的 large 类型、不会处理 dictionary 内部类型，
        # st_aggrid 前端内置的旧版 Arrow JS 无法解码 LargeUtf8，会导致表格直接渲染为空白
        slot_order = {name: index for index, name in enumerate(category_order)}
        df["_slot_name_cat"] = df["_slot_name"].map(slot_order).astype("int64")
        df.sort_values(by=["_slot_name_cat", "_slot_index", "ADD_TS"], inplace=True)
        # 删除辅助列
        df.drop(columns=["_slot_name", "_slot_index", "_slot_name_cat"], inplace=True)

    # 使用 streamlit-aggrid 实现可交互表格
    # 创建 LINK 列
    df["LINK"] = "https://osu.ppy.sh/b/" + df["BID"].astype(str)
    # 创建 ADD_DATETIME 列，它是由 ADD_TS (来自于 time.time() 的 UTC 时间浮点数) 转换为含时区信息的 ISO Format（时区 = st.session_state.awa.tz）
    df["ADD_DATETIME"] = pd.to_datetime(df["ADD_TS"], unit="s").dt.tz_localize("UTC").dt.tz_convert(st.session_state.awa.tz).dt.strftime("%Y-%m-%d %H:%M:%S %Z%z")
    desired_col_order = [
        "BID",
        "SKILL_SLOT",
        "MODS",
        "LINK",
        "INFO",
        "SR",
        "BPM",
        "HIT_LENGTH",
        "MAX_COMBO",
        "CS",
        "AR",
        "OD",
        "POOL",
        "SUGGESTOR",
        "NOTES",
        "ADD_DATETIME",
        "COMMENTS",
        "RAW_MODS",
        "STATUS",
        "SID",
        "ADD_TS",
    ]
    # 最终列名应该为 desired_col_order + 未在 desired_col_order 中的原始列名（保持原始顺序）
    df: pd.DataFrame = df[desired_col_order + [col for col in df.columns if col not in desired_col_order]].copy()

    gb = GridOptionsBuilder.from_dataframe(df)
    gb.configure_selection(selection_mode="multiple", use_checkbox=True, suppressRowClickSelection=True)
    gb.configure_grid_options(
        **dict(
            domLayout="normal",
            rowHeight=32,
            getRowStyle=row_style_with_dup if highlight_dup else row_style,
            # autoSizeStrategy={'type': 'fitCellContents'},
            suppressAutoSize=True,
            enableCellTextSelection=True,
            ensureDomOrder=True,
            components={
                "codeEditor": monaco_editor,
            },
        ),
    )
    gb.configure_default_column(
        cellStyle={"padding-left": "4px", "padding-right": "4px"},
        resizable=True,
        suppressSizeToFit=True,
    )
    gb.configure_column(
        field="LINK",
        header_name="Cover",
        cellRenderer=image_link_renderer,
        cellStyle={"padding": "0px"},
        width=70,
        pinned="left",
        editable=False,
        suppressSizeToFit=True,
    )
    # 由于 MODS 列使用的是 readable_mods 的表示，是不可变的
    # 因此用户可以更改的是与 mods_input 对应的 RAW_MODS 列
    # 可修改列: SKILL_SLOT, STATUS, COMMENTS, POOL, NOTES, RAW_MODS,
    cell_rules = {"clickable-cell-style": "true"}  # 这里的 "true" 是 JS 表达式
    gb.configure_column(
        "BID",
        header_name="BID",
        width=88,
        cellClassRules=cell_rules,
        cellStyle={"cursor": "copy"},
        onCellClicked=copy_on_click_js,
    )
    gb.configure_column(
        "SKILL_SLOT",
        header_name="Slot",
        editable=True,
        cellStyle=slot_cell_style_js,
        width=48,
    )
    gb.configure_column("MODS", header_name="Mods", width=68)
    gb.configure_column("INFO", header_name="Beatmap", width=350)
    gb.configure_column("SR", width=88)
    gb.configure_column("BPM", width=50)
    gb.configure_column("HIT_LENGTH", header_name="Drain", width=50)
    gb.configure_column("MAX_COMBO", header_name="Combo", width=52)
    gb.configure_column("CS", width=38)
    gb.configure_column("AR", width=38)
    gb.configure_column("OD", width=38)
    gb.configure_column("POOL", header_name="Pool", editable=True, width=70)
    gb.configure_column("SUGGESTOR", header_name="Suggestor", width=85)
    gb.configure_column("NOTES", header_name="Notes", editable=True, width=138)
    gb.configure_column("ADD_DATETIME", header_name="Added at", width=118)
    gb.configure_column(
        "COMMENTS",
        header_name="Comments",
        editable=True,
        wrapText=True,
        width=250,
        cellStyle={"white-space": "pre-wrap"},
        autoHeight=False,
        cellEditor="agLargeTextCellEditor",
        cellEditorPopup=True,
    )
    gb.configure_column(
        "RAW_MODS",
        header_name="Raw Mods",
        editable=True,
        wrapText=True,
        width=288,
        cellStyle={
            "font-family": "monospace",
            "white-space": "pre-wrap",
        },
        cellEditor="codeEditor",
        cellEditorParams={
            "language": "json",
            "theme": "vs-dark",
            "columnName": "mods",
        },
        cellEditorPopup=True,
    )
    gb.configure_column(
        "STATUS",
        header_name="Status",
        editable=True,
        cellEditor="agSelectCellEditor",
        cellEditorParams={"values": [0, 1, 2]},
        width=25,
        filter=False,
    )

    # 隐藏列
    for col in df.columns:
        if col[0] == "_" or col in ["SID", "ADD_TS", "U_ARTIST", "U_TITLE", "SERIES"]:
            gb.configure_column(col, hide=True)

    grid_options = gb.build()

    if "aggrid_key" not in st.session_state:
        st.session_state.aggrid_key = str(uuid4())
    st.session_state.aggrid_key2 = st.session_state.aggrid_key + str(st.session_state.gen_filter_pool) + str(st.session_state.gen_filter_status) + st.session_state.gen_filter_search + str(highlight_dup) + str(match_slot_sort)
    grid_response = AgGrid(
        df,
        gridOptions=grid_options,
        columns_auto_size_mode=ColumnsAutoSizeMode.NO_AUTOSIZE,
        update_on=[
            "cellValueChanged",  # 单元格值改变时触发 (编辑)
            "selectionChanged",  # 选择行改变时触发 (删除)
        ],
        height=800,
        width="100%",
        # show_toolbar=True,
        allow_unsafe_jscode=True,
        key=st.session_state.aggrid_key2,
    )
    # st.write(df)
    edited_df = grid_response.data.copy() if grid_response.data is not None else pd.DataFrame()
    selected_rows = grid_response.selected_rows

    col_save_n_refresh, col_blank, col_del = st.columns(spec=[0.6, 0.12, 0.28], gap="large")
    with col_save_n_refresh, st.container(border=False, horizontal=True):
        if st.button(_("Commit"), use_container_width=True, icon=":material/database_upload:"):
            EDITABLE = ["SKILL_SLOT", "STATUS", "COMMENTS", "POOL", "NOTES", "RAW_MODS"]
            specs_recalculate: list[BeatmapSpec] = []
            olds_to_drop: list[tuple[str, bool]] = []  # (old MODS, RAW_MODS changed)

            # 先索引原始 df，加快查找效率
            # noinspection PyUnresolvedReferences
            orig_indexed = {(int(row.BID), str(row.MODS)): {c: getattr(row, c) for c in EDITABLE + ["MODS", "SUGGESTOR", "ADD_TS"]} for row in df.itertuples()}  # ty:ignore[unresolved-attribute]
            specs_recalculate_valid = True
            for edited_row in edited_df[["BID", "MODS"] + EDITABLE].itertuples():
                edited_bid = int(edited_row.BID)
                edited_mods = str(edited_row.MODS)
                # 由于 MODS 在这里尚未更改，因此还是可以根据 BID + MODS 的组合定位原始表格中的对应行
                orig = orig_indexed.get((edited_bid, edited_mods))
                if orig is None:
                    st.toast(_("(%d %s) not found, skipped") % (edited_bid, edited_mods))
                    continue
                # 在可修改列中如果有任意一项被修改了，那么就添加到重算列表中
                if (
                    safe_norm(edited_row.SKILL_SLOT) != safe_norm(orig["SKILL_SLOT"])
                    or safe_norm(edited_row.STATUS, int) != safe_norm(orig["STATUS"], int)
                    or safe_norm(edited_row.COMMENTS) != safe_norm(orig["COMMENTS"])
                    or safe_norm(edited_row.POOL) != safe_norm(orig["POOL"])
                    or safe_norm(edited_row.NOTES) != safe_norm(orig["NOTES"])
                    or safe_norm(edited_row.RAW_MODS) != safe_norm(orig["RAW_MODS"])
                ):
                    # 由于 MODS 由 RAW_MODS 生成，因此 RAW_MODS 改变时，主键即改变
                    # 故如果 RAW_MODS 修改了，那么就认为需要先删除原始记录，然后再添加新记录
                    new_primary = safe_norm(edited_row.RAW_MODS) != safe_norm(orig["RAW_MODS"])
                    try:
                        specs_recalculate.append(
                            BeatmapSpec(
                                edited_bid,
                                orjson.loads(edited_row.RAW_MODS),
                                str(edited_row.SKILL_SLOT),
                                str(edited_row.POOL),
                                str(edited_row.NOTES),
                                int(edited_row.STATUS),
                                str(edited_row.COMMENTS),
                                str(orig["SUGGESTOR"]),
                                float(orig["ADD_TS"]),
                            ),
                        )
                    except orjson.JSONDecodeError:
                        st.toast(_("invalid JSON: %s") % edited_row.RAW_MODS)
                        specs_recalculate_valid = False
                        break
                    except (ValueError, TypeError):
                        st.toast(_("invalid value: %s") % edited_row.RAW_MODS)
                        specs_recalculate_valid = False
                        break
                    olds_to_drop.append((str(orig["MODS"]), new_primary))
            # 同理，为了代码可读性和便于后续修改，这里没有直接生成 BeatmapToUpdate 列表，而是做了两次循环
            if len(specs_recalculate) == 0:
                st.toast(_("no changes made"))
            elif not specs_recalculate_valid:
                pass
            else:
                beatmaps_to_update: list[BeatmapToUpdate] = []
                for old_to_drop, beatmap_to_upsert in zip(olds_to_drop, specs_recalculate, strict=True):
                    if old_to_drop[1]:
                        beatmaps_to_update.append(
                            BeatmapToUpdate(
                                action="update1",
                                name=uid,
                                beatmap=beatmap_to_upsert,
                                old_bid=None,
                                old_mods=old_to_drop[0],
                            ),
                        )
                    else:
                        beatmaps_to_update.append(
                            BeatmapToUpdate(
                                action="update0",
                                name=uid,
                                beatmap=beatmap_to_upsert,
                                old_bid=None,
                                old_mods=None,
                            ),
                        )
                st.toast(push_beatmap_task(beatmaps_to_update))
        if st.button(_("Refresh"), use_container_width=True, icon=":material/refresh:"):
            refresh(beatmap_database_query)
        if st.button(_("Export"), use_container_width=True, icon=":material/file_export:"):
            export_filtered_playlist()

    with col_del, st.container(border=False, horizontal_alignment="right"):
        if st.button(
            _("Delete"),
            type="primary",
            use_container_width=True,
            icon=":material/delete:",
        ):
            if selected_rows is None or len(selected_rows) == 0:
                st.toast(_("no beatmaps selected"))
            else:
                required_rows = selected_rows[["BID", "MODS"]]
                beatmaps_to_delete: list[BeatmapToUpdate] = []
                for row in required_rows.itertuples(index=False):
                    beatmaps_to_delete.append(
                        BeatmapToUpdate(
                            action="delete",
                            name=uid,
                            beatmap=None,
                            old_bid=int(row.BID),
                            old_mods=str(row.MODS),
                        ),
                    )
                st.toast(push_beatmap_task(beatmaps_to_delete))

    with st.expander(_("Statistics")):
        show_statistics()

st.divider()

st.markdown(_("## Generate from a file"))
uploaded_file = st.file_uploader(_("Choose a file"), type=["properties"])
if uploaded_file is not None:
    if isinstance(uploaded_file, list):
        st.error("multiple files not allowed")
        st.stop()
    playlist_name = os.path.splitext(uploaded_file.name)[0]
    session_path = os.path.join(C.UPLOADED_DIRECTORY.value, uid)
    if not os.path.exists(session_path):
        os.mkdir(session_path)
    playlist_filename = os.path.join(session_path, "%s.properties" % playlist_name)
    html_filename = os.path.join(session_path, "%s.html" % playlist_name)
    covers_dir = os.path.join(session_path, "%s.covers" % playlist_name)
    csv_filename = os.path.join(session_path, "%s.csv" % playlist_name)
    css_filename = os.path.join(session_path, "style.css")
    zip_filename = os.path.join(C.UPLOADED_DIRECTORY.value, "%s.zip" % uid)
    content = uploaded_file.getvalue()

    with open(playlist_filename, "wb") as fo_b:
        fo_b.write(content)
    logger.get_logger(st.session_state.username).info("generating playlist %s at %s" % (playlist_name, session_path))
    if st.session_state["new_style"]:
        table = generate_playlist(playlist_filename, 1)
    else:
        table = generate_playlist(playlist_filename)
        for pic in [
            x[0]
            for x in sorted(
                [(x, int(x[: x.find("-")])) for x in os.listdir(covers_dir)],
                key=lambda x: x[1],
            )
        ]:
            st.image(os.path.join(covers_dir, pic), caption=pic, width="stretch")
    st.divider()
    table.to_csv(csv_filename, encoding="utf-8", index=False)
    if os.path.exists("./playlists/style.css"):
        shutil.copy("./playlists/style.css", css_filename)
    st.dataframe(table, hide_index=True)
    compress_as_zip(session_path, zip_filename)
    with open(zip_filename, "rb") as zfi:
        st.download_button(label=_("Download the resources"), file_name="%s.zip" % uid, data=zfi)
