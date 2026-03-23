import os.path
import shutil
import time
from functools import partial
from time import sleep
from typing import Never, Optional, TYPE_CHECKING, cast

import orjson
import pandas as pd
import streamlit as st
from clayutil.futil import compress_as_zip
from clayutil.validator import validate_type
from sqlalchemy import text
from st_aggrid import AgGrid, ColumnsAutoSizeMode, GridOptionsBuilder, JsCode
from streamlit import logger

from osuawa import C, OsuPlaylist
from osuawa.components import get_redis_connection, get_session_id, init_page, load_value, memorized_selectbox, save_value
from osuawa.osuawa import Osuawa
from osuawa.utils import BeatmapSpec, BeatmapToUpdate, _create_tmp_playlist_p, _make_query_uppercase, generate_mods_from_lines, push_task, to_readable_mods

validate_restricted_identifier = partial(validate_type, type_=str, min_value=1, max_value=16, predicate=str.isidentifier)

if TYPE_CHECKING:

    def _(text: str) -> str: ...

    # noinspection PyTypeHints
    st.session_state.awa: Osuawa
    # noinspection PyTypeHints
    st.session_state.redis_tasks: list[str]

init_page(_("Playlist generator") + " - osuawa")
with st.sidebar:
    st.toggle(_("new style"), key="new_style", value=True)

conn = st.connection("osuawa", type="sql")
conn.query = _make_query_uppercase(conn.query)
r = get_redis_connection()
# todo: st.connection 为只读，写入由 daemon worker 负责
uid = get_session_id()
row_style_js_with_dup = JsCode(
    """function(params) {
    if (params.data._is_dup_bid) return { backgroundColor: 'crimson' };
    if (params.data._is_dup_song) return { backgroundColor: 'lemonchiffon' };
    if (params.data.STATUS == 1) return { backgroundColor: 'darkseagreen' };
    if (params.data.STATUS == 2) return { backgroundColor: 'powderblue' };
    return {};
}
""",
)
row_style_js = JsCode(
    """function(params) {
    if (params.data.STATUS == 1) return { backgroundColor: 'darkseagreen' };
    if (params.data.STATUS == 2) return { backgroundColor: 'powderblue' };
    return {};
}
""",
)
slot_cell_style_js = JsCode(
    f"""function(params) {{
    if (!params.value) return {{}};

    let rootMod = params.value.toString().substring(0, 2).toUpperCase();
    let colorMap = {orjson.dumps(OsuPlaylist.mod_color).decode()};

    if (colorMap.hasOwnProperty(rootMod) && colorMap[rootMod]) {{
        return {{
            "backgroundColor": colorMap[rootMod],
            "color": "white",
            "fontWeight": "bold"
        }};
    }} else {{
        return {{
            "backgroundColor": "#eb50eb",
            "color": "white",
            "fontWeight": "bold"
        }};
    }}
}}
""",
)
copy_on_click_js = JsCode(
    """function(event) {
    let target = event.event.target;
    while (target && !target.classList.contains('ag-cell')) {
        target = target.parentElement;
    }

    if (target && event.value !== undefined && event.value !== null) {
        navigator.clipboard.writeText(String(event.value))
            .then(() => {
                const oldTip = target.querySelector('.copy-tip');
                if (oldTip) oldTip.remove();

                const tip = document.createElement('span');
                tip.className = 'copy-tip';
                tip.innerText = 'Copied';

                Object.assign(tip.style, {
                    position: 'absolute',
                    left: '60%',
                    top: '50%',
                    transform: 'translate(-50%, -50%)',
                    backgroundColor: 'rgba(0, 0, 0, 0.75)',
                    color: 'white',
                    fontSize: '9px',
                    padding: '1px 5px',
                    borderRadius: '2px',
                    zIndex: '1000',
                    pointerEvents: 'none',
                    opacity: '0',
                    transition: 'opacity 0.15s linear'
                });

                target.appendChild(tip);

                requestAnimationFrame(() => {
                    tip.style.opacity = '1';
                });

                setTimeout(() => {
                    tip.style.opacity = '0';
                    setTimeout(() => tip.remove(), 150);
                }, 300);
            })
            .catch(err => {
                console.error('Failed to copy:', err);
            });
    }
}
""",
)
image_link_renderer = JsCode(
    """class ImageLinkRenderer {
    init(params) {
        this.eGui = document.createElement('a');
        this.eGui.href = params.value;
        this.eGui.target = '_blank';

        const img = document.createElement('img');
        img.src = "%s" + params.data.BID + ".jpg"; 
        img.style.height = "32px";
        img.style.width = "70px";
        img.style.objectFit = "cover";
        img.style.borderRadius = "0px";

        this.eGui.appendChild(img);
    }
    getGui() {
        return this.eGui;
    }
    refresh(params) {
        return false;
    }
}
""" % ("../../app/" + C.UPLOADED_DIRECTORY.value.strip("./") + "/online/darkened-backgrounds/"),
)
st.markdown(
    """<style>
    iframe[title="st_aggrid.ag_grid"] {
    }

    .ag-cell {
        transition: background-color 0.3s;
    }
</style>
""",
    unsafe_allow_html=True,
)


def default(obj):
    if hasattr(obj, "_fields"):
        return list(obj)
    raise TypeError


# noinspection PyUnreachableCode
def refresh(delay: Optional[float] = None, clear_cache: bool = False) -> Never:
    if delay:
        sleep(delay)
    conn.reset()
    if clear_cache:
        st.cache_data.clear()
    st.rerun()
    raise  # 给 mypy 看的补丁


@st.cache_data(show_spinner=False)
def generate_playlist(filename: str, css_style: Optional[int] = None):
    # 由于这个有实时性要求，因此不挪到后台处理
    playlist = OsuPlaylist(st.session_state.awa, filename, css_style=css_style)
    return playlist.generate()


def check_beatmap_exists(bid: int, mods: str) -> bool:
    bid = int(bid)
    mods = str(mods)
    with conn.session as s:
        res = s.execute(
            text(
                """SELECT COUNT(*)
                   FROM BEATMAP
                   WHERE BID = :bid
                     AND MODS = :mods""",
            ),
            {"bid": bid, "mods": mods},
        ).scalar()
    return res > 0


@st.dialog(_("Export selected as a playlist"))
def export_filtered_playlist():
    parsed_mods_list = [orjson.loads(x) for x in selected_rows["RAW_MODS"]]
    specs_x = [BeatmapSpec(bid, mods, skill, "", note, 0, "", "", 0.0) for bid, mods, skill, note in zip(selected_rows["BID"], parsed_mods_list, selected_rows["SKILL_SLOT"], selected_rows["NOTES"])]  # 直接用解析好的列表
    tmp_playlist_filename_x = _create_tmp_playlist_p(uid, specs_x)
    st.code("\n".join([str(bid) for bid in selected_rows["BID"]]))
    with open(tmp_playlist_filename_x, "r", encoding="utf-8") as fi:
        st.code(fi.read(), language="properties")


if st.session_state.perm >= 1:
    st.markdown(_("## Online Playlist Creator"))
    available_pools = conn.query(
        """SELECT DISTINCT POOL
           FROM BEATMAP
           ORDER BY POOL""",
        ttl=0,
    )["POOL"].to_list()
    if len(available_pools) == 0:
        available_pools.append("_DEFAULT_POOL")

    with st.form(_("Add beatmap")):
        col1, col2 = st.columns(2)
        with col1:
            urls_input = st.text_input(_("Beatmap URLs or IDs, split by spaces"))
            # SLOTS 自动大写
            slot_input = st.text_input(_("Slot"))
            if slot_input is None:
                st.error(_("blank slot not allowed"))
                st.stop()
            slot_input = slot_input.upper()
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
            pool_input = st.selectbox(_("Pool"), available_pools, index=_pool_index, accept_new_options=True)
            mod_settings_input = st.text_area(_("Mod Settings"), height="stretch")
            # status_input = st.slider(_("Status"), 0, 2, 0)
        submitted = st.form_submit_button(_("Add"), use_container_width=True)
        if submitted:
            st.session_state.gen_form_pool = pool_input
            save_value("gen_form_pool")
            specs_input: list[BeatmapSpec] = []
            if urls_input is None:
                st.error(_("blank beatmap not allowed"))
                st.stop()
            urls_input_split = urls_input.split()
            raw_mods_input = generate_mods_from_lines(slot_input, mod_settings_input or "")

            # 为了代码可读性和便于后续修改，这里没有直接生成 BeatmapToUpdate 列表，而是做了两次循环
            for url_input in urls_input_split:
                # 处理 BID
                bid_input = int(url_input.rsplit("/", 1)[-1])
                # 这里要提前转化 raw_mods 为 "; ".join(mods_ready)，一方面检验是否能序列化，另一方面查重并终止
                try:
                    mods_ready_input = to_readable_mods(raw_mods_input)
                except (orjson.JSONDecodeError, ValueError, KeyError):
                    st.error(_("invalid mods: %s") % raw_mods_input)
                    continue
                mods_input = "; ".join(mods_ready_input)
                if check_beatmap_exists(bid_input, mods_input):
                    st.warning(_("(%d %s) already exists, skipped" % (bid_input, mods_input)))
                    continue
                validate_restricted_identifier(st.session_state.gen_form_pool)
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
            # todo: 建议一个 empty，轮询状态
            st.session_state.redis_tasks.append(push_task(r, "beatmap %s" % orjson.dumps([BeatmapToUpdate(name=uid, beatmap=spec_input) for spec_input in specs_input], option=orjson.OPT_PASSTHROUGH_SUBCLASS, default=default).decode()))

    with st.container(border=True):
        filter_col1, filter_col2, filter_col3, ctrl_col1 = st.columns([3, 3, 9, 4])
        with filter_col1:
            memorized_selectbox(_("Pool"), "gen_filter_pool", ["-"] + available_pools, "-")
        with filter_col2:
            memorized_selectbox(_("Status"), "gen_filter_status", [-1, 0, 1, 2], -1)
        with filter_col3:
            st.text_input(_("Search"), key="gen_filter_search", placeholder=_("Search in BID, slot, notes and so on..."))
        with ctrl_col1:
            highlight_dup = st.checkbox(_("Highlight duplicates"), value=True)
            match_slot_sort = st.checkbox(_("Sort as match"), value=False)

    # 查询重复的 BID 与曲目，用于后期查询结果表格渲染。重复的 BID 行要标记为红色，重复的曲目行要标记为黄色
    # U_ARTIST + U_TITLE 用于曲目识别
    duplicate_bids = conn.query(
        """SELECT BID
           FROM BEATMAP
           GROUP BY BID
           HAVING COUNT(*) > 1""",
        ttl=0,
    )["BID"].to_list()
    duplicate_songs_raw = conn.query(
        """SELECT U_ARTIST, U_TITLE
           FROM BEATMAP
           GROUP BY U_ARTIST, U_TITLE
           HAVING COUNT(*) > 1""",
        ttl=0,
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
    df: pd.DataFrame = conn.query(filter_query, ttl=0, params=filter_params)

    # keywords 的筛选用 pandas 完成，从 bid、sid、info、slot、mods、notes 中查找包含输入内容的条目
    if st.session_state.gen_filter_search:
        keyword_lower = st.session_state.gen_filter_search.lower()
        target_cols = ["BID", "SID", "INFO", "SKILL_SLOT", "MODS", "NOTES"]
        mask = df[target_cols].apply(lambda x: x.astype(str).str.lower().str.contains(keyword_lower, na=False).any(), axis=1)
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
        df["_slot_name_cat"] = pd.Categorical(
            df["_slot_name"],
            categories=category_order,
            ordered=True,
        )
        df.sort_values(by=["_slot_name_cat", "_slot_index", "ADD_TS"], inplace=True)

    # 使用 streamlit-aggrid 实现可交互表格
    # 创建 LINK 列
    df["LINK"] = "https://osu.ppy.sh/b/" + df["BID"].astype(str)
    # 创建 ADD_DATETIME 列，它是由 ADD_TS (来自于 time.time() 的 UTC 时间浮点数) 转换为含时区信息的 ISO Format（时区 = st.session_state.awa.tz）
    df["ADD_DATETIME"] = cast(pd.Series, pd.to_datetime(df["ADD_TS"], unit="s")).dt.tz_localize("UTC").dt.tz_convert(st.session_state.awa.tz).dt.strftime("%Y-%m-%d %H:%M:%S %Z%z")
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
    df = df[desired_col_order]

    gb = GridOptionsBuilder.from_dataframe(df)
    gb.configure_selection(selection_mode="multiple", use_checkbox=True, suppressRowClickSelection=True)
    gb.configure_grid_options(
        **dict(
            domLayout="normal",
            rowHeight=32,
            getRowStyle=row_style_js_with_dup if highlight_dup else row_style_js,
            # autoSizeStrategy={'type': 'fitCellContents'},
            suppressSizeToFit=True,
            shouldPanelSectionsBeVisible=True,
            enableCellTextSelection=True,
            ensureDomOrder=True,
        ),
    )
    gb.configure_default_column(cellStyle={"padding-left": "4px", "padding-right": "4px"}, resizable=True, suppressSizeToFit=True)
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
    gb.configure_column("BID", header_name="BID", width=88, cellClassRules=cell_rules, cellStyle={"cursor": "copy"}, onCellClicked=copy_on_click_js)
    gb.configure_column("SKILL_SLOT", header_name="Slot", editable=True, cellStyle=slot_cell_style_js, width=48)
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
    gb.configure_column("COMMENTS", header_name="Comments", editable=True, wrapText=True, width=250, cellStyle={"white-space": "pre-wrap"}, autoHeight=False, cellEditor="agLargeTextCellEditor", cellEditorPopup=True)
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
        cellEditor="agLargeTextCellEditor",
        cellEditorPopup=True,
    )
    gb.configure_column("STATUS", header_name="Status", editable=True, cellEditor="agSelectCellEditor", cellEditorParams={"values": [0, 1, 2]}, width=25)

    # 隐藏列
    gb.configure_column("SID", hide=True)
    gb.configure_column("ADD_TS", hide=True)

    grid_options = gb.build()

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
        key="gen_playlist_grid",
    )
    if grid_response["data"]:
        edited_df = pd.DataFrame(grid_response["data"])
    else:
        edited_df = pd.DataFrame()

    selected_rows = grid_response["selected_rows"]

    col_save_n_refresh, col_blank, col_del = st.columns(spec=[0.6, 0.12, 0.28], gap="large")
    with col_save_n_refresh:
        with st.container(border=False, horizontal=True):
            if st.button(_("Commit"), use_container_width=True, icon=":material/database_upload:"):
                if edited_df.empty:
                    st.toast(_("no changes made"))
                else:
                    specs_recalculate: list[BeatmapSpec] = []
                    olds_to_drop: list[tuple[str, bool]] = []  # (old MODS, RAW_MODS changed)
                    for index, row in edited_df.iterrows():
                        edited_bid, edited_mods = row["BID"], row["MODS"]
                        # 由于 MODS 在这里尚未更改，因此还是可以根据 BID + MODS 的组合定位原始表格中的对应行
                        original_row = df.loc[(df["BID"] == edited_bid) & (df["MODS"] == edited_mods)]
                        if original_row.empty:
                            st.toast(_("(%d %s) not found, skipped") % (edited_bid, edited_mods))
                            continue
                        # 由于 BID + MODS 为主键，因此这里应该只有一个结果
                        original_row = original_row.iloc[0]
                        # 在可修改列中如果有任意一项被修改了，那么就添加到重算列表中
                        # 由于 MODS 由 RAW_MODS 生成，因此 RAW_MODS 改变时，主键即改变
                        # 故如果 RAW_MODS 修改了，那么就认为需要先删除原始记录，然后再添加新记录
                        new_primary = row["RAW_MODS"] != original_row["RAW_MODS"] and not (pd.isna(row["RAW_MODS"]) and pd.isna(original_row["RAW_MODS"]))
                        try:
                            specs_recalculate.append(BeatmapSpec(edited_bid, orjson.loads(row["RAW_MODS"]), row["SKILL_SLOT"], row["POOL"], row["NOTES"], row["STATUS"], row["COMMENTS"], original_row["SUGGESTOR"], original_row["ADD_TS"]))
                        except orjson.JSONDecodeError:
                            st.toast(_("invalid JSON: %s") % row["RAW_MODS"])
                            st.stop()
                        olds_to_drop.append((original_row["MODS"], new_primary))
                    # 同理，为了代码可读性和便于后续修改，这里没有直接生成 BeatmapToUpdate 列表，而是做了两次循环
                    beatmaps_to_upsert: list[BeatmapToUpdate] = []
                    for old_to_drop, beatmap_to_upsert in zip(olds_to_drop, specs_recalculate):
                        if old_to_drop[1]:
                            beatmaps_to_upsert.append(BeatmapToUpdate(name=uid, beatmap=beatmap_to_upsert, old_mods=old_to_drop[0]))
                        else:
                            beatmaps_to_upsert.append(BeatmapToUpdate(name=uid, beatmap=beatmap_to_upsert))
                    st.session_state.redis_tasks.append(push_task(r, "beatmap %s" % orjson.dumps(beatmaps_to_upsert, option=orjson.OPT_PASSTHROUGH_SUBCLASS, default=default).decode()))

                refresh(1.5)
            if st.button(_("Refresh"), use_container_width=True, icon=":material/refresh:"):
                refresh(clear_cache=True)
            if st.button(_("Export"), use_container_width=True, icon=":material/file_export:"):
                export_filtered_playlist()

    with col_del:
        with st.container(border=False, horizontal_alignment="right"):
            if st.button(_("Delete"), type="primary", use_container_width=True, icon=":material/delete:"):
                if len(selected_rows) == 0:
                    st.toast(_("no beatmaps selected"))
                else:
                    required_rows = selected_rows[["BID", "MODS"]]
                    beatmaps_to_delete: list[BeatmapToUpdate] = []
                    for row in required_rows.itertuples(index=False):
                        beatmaps_to_delete.append(BeatmapToUpdate(old_bid=row.BID, old_mods=row.MODS))
                    st.session_state.redis_tasks.append(push_task(r, "beatmap %s" % orjson.dumps(beatmaps_to_delete, option=orjson.OPT_PASSTHROUGH_SUBCLASS, default=default).decode()))
                refresh(1.5)

st.divider()

st.markdown(_("## Generate from a file"))
uploaded_file = st.file_uploader(_("choose a file"), type=["properties"])
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
        for pic in [x[0] for x in sorted([(x, int(x[: x.find("-")])) for x in os.listdir(covers_dir)], key=lambda x: x[1])]:
            st.image(os.path.join(covers_dir, pic), caption=pic, width="stretch")
    st.divider()
    table.to_csv(csv_filename, encoding="utf-8", index=False)
    if os.path.exists("./playlists/style.css"):
        shutil.copy("./playlists/style.css", css_filename)
    st.dataframe(table, hide_index=True)
    compress_as_zip(session_path, zip_filename)
    with open(zip_filename, "rb") as zfi:
        st.download_button(label=_("download the resources"), file_name="%s.zip" % uid, data=zfi)
