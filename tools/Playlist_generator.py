import asyncio
import os.path
import shutil
import time
from functools import partial
from time import sleep
from typing import Never, Optional
from uuid import UUID

import orjson
import pandas as pd
import streamlit as st
from clayutil.futil import Properties, compress_as_zip
from clayutil.validator import validate_type
from sqlalchemy import text
from st_aggrid import AgGrid, ColumnsAutoSizeMode, GridOptionsBuilder, JsCode
from streamlit import logger
from streamlit.runtime.scriptrunner import get_script_run_ctx

from osuawa import C, OsuPlaylist
from osuawa.components import init_page_layout, load_value, memorized_selectbox, save_value
from osuawa.utils import generate_mods_from_lines, to_readable_mods

validate_restricted_identifier = partial(validate_type, type_=str, min_value=1, max_value=16, predicate=str.isidentifier)

init_page_layout(_("Playlist generator") + " - osuawa")
with st.sidebar:
    st.toggle(_("new style"), key="new_style", value=True)

conn = st.connection("osuawa", type="sql")
uid = UUID(get_script_run_ctx().session_id).hex
row_style_js = JsCode(
    """function(params) {
    if (params.data._is_dup_bid) return { backgroundColor: 'crimson' };
    if (params.data._is_dup_sid) return { backgroundColor: 'lemonchiffon' };
    if (params.data.STATUS == 1) return { backgroundColor: 'darkseagreen' };
    if (params.data.STATUS == 2) return { backgroundColor: 'powderblue' };
    return {};
}
""",
)
slot_cell_style_js = JsCode(
    f"""function(params) {{
    if (!params.value) return {{}};

    let prefix = params.value.toString().substring(0, 2).toUpperCase();
    let colorMap = {orjson.dumps(OsuPlaylist.mod_color).decode()};

    if (colorMap[prefix]) {{
        return {{
            "backgroundColor": colorMap[prefix],
            "color": "white",
            "fontWeight": "bold"
        }};
    }}

    return {{}};
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
"""
    % ("../../app/" + C.UPLOADED_DIRECTORY.value.strip("./") + "/online/darkened-backgrounds/"),
)
st.markdown(
    """<style>
    iframe[title="st_aggrid.ag_grid"] {
    }

    .ag-cell {
        transition: background-color 0.2s;
    }
</style>
""",
    unsafe_allow_html=True,
)


def refresh(delay: Optional[float] = None) -> Never:
    if delay:
        sleep(delay)
    st.cache_data.clear()
    conn.reset()
    st.rerun()


@st.cache_data(show_spinner=False)
def generate_playlist(filename: str, css_style: Optional[int] = None):
    playlist = OsuPlaylist(st.session_state.awa, filename, css_style=css_style)
    return playlist.generate()


def create_tmp_playlist(name: str, beatmap_specs: list[tuple[int, list, str, str, str, int, str, str, float]]) -> list[dict]:
    """创建临时课题。仅创建，不生成

    status: 0=未审核 1=已审核，2=已提名

    OsuPlaylist beatmap 与 beatmap_specs、数据库字段对应关系如下：

    ```
    | BID  | SID | Artist - Title (Creator) [Version] | Stars | SR  | BPM | Hit Length | Max Combo | CS  | AR  | OD  | Mods | Notes | slot       |        |          |      |           |          |        |
    | ---- | --- | ---------------------------------- | ----- | --- | --- | ---------- | --------- | --- | --- | --- | ---- | ----- | ---------- | ------ | -------- | ---- | --------- | -------- | ------ |
    | bids |     |                                    |       |     |     |            |           |     |     |     |      | notes | slot       | status | comments | pool | suggestor | raw_mods | add_ts |
    | BID  | SID | INFO                               |       | SR  | BPM | HIT_LENGTH | MAX_COMBO | CS  | AR  | OD  | MODS | NOTES | SKILL_SLOT | STATUS | COMMENTS | POOL | SUGGESTOR | RAW_MODS | ADD_TS |
    ```

    :param name: 课题名（在线环境中建议直接用 UID）
    :param beatmap_specs: bid, raw_mods, slot, pool, notes, status, comments, suggestor, add_ts
    :return: 一个谱面列表，为数据库字段优化了键名
    """
    # 所有在线谱面共用一个文件夹，设计之初是给一个团队使用的
    pool_path = os.path.join(C.UPLOADED_DIRECTORY.value, "online")
    if not os.path.exists(pool_path):
        os.mkdir(pool_path)
    # 创建一个临时谱面文件，以 name 为谱面名
    tmp_playlist_filename = str(os.path.join(pool_path, "%s.properties" % name))
    tmp_playlist_p = Properties(tmp_playlist_filename)
    tmp_playlist_p["custom_columns"] = '["mods", "slot"]'  # 一定要启用自定义列功能，不然不支持 slot
    # playlist Properties 文件格式如下：
    # bid = {"mods": mods, "slot": slot}
    # # notes（notes 为 None 则不加这行注释）
    # Properties 是一个 OrderedDict，往后依次添加内容即可。要注意 notes 必须以 \n 结尾，因为对于注释的解析是完整行
    for i, a in enumerate(beatmap_specs, start=1):
        tmp_playlist_p[str(a[0])] = orjson.dumps({"mods": a[1], "slot": a[2]}).decode()
        tmp_playlist_p["#%i" % (i * 2 - 1)] = "# %s\n" % a[4]
    tmp_playlist_p.dump()
    tmp_playlist = OsuPlaylist(st.session_state.awa, tmp_playlist_filename, css_style=1)  # 这里 css_style 不知道用哪一个好
    playlist_beatmaps_raw: list[dict] = asyncio.run(tmp_playlist.playlist_task())  # 这里面每一个 dict 都表示一个 playlist beatmap
    # playlist_beatmaps_raw 的顺序可能和传入的 specs 顺序不一致
    # 原始 beatmap 的键应包含如下
    # # BID, SID, Artist - Title (Creator) [Version], Stars, SR, BPM, Hit Length, Max Combo, CS, AR, OD, Mods, Notes, slot
    # 根据 # 键对其进行排序是有必要的。# 从 1 开始递增，它与传入的 specs 顺序一致
    playlist_beatmaps_raw.sort(key=lambda x: int(x["#"]))
    playlist_beatmaps_db = []
    for i, playlist_beatmap_raw in enumerate(playlist_beatmaps_raw):
        playlist_beatmaps_db.append(
            {
                "BID": int(playlist_beatmap_raw["BID"]),
                "SID": int(playlist_beatmap_raw["SID"]),
                "INFO": playlist_beatmap_raw["Artist - Title (Creator) [Version]"],
                "SKILL_SLOT": playlist_beatmap_raw["slot"],
                "SR": playlist_beatmap_raw["SR"],  # 相比 Stars，SR 不依赖特殊字体
                "BPM": playlist_beatmap_raw["BPM"],
                "HIT_LENGTH": playlist_beatmap_raw["Hit Length"],
                "MAX_COMBO": playlist_beatmap_raw["Max Combo"],
                "CS": playlist_beatmap_raw["CS"],
                "AR": playlist_beatmap_raw["AR"],
                "OD": playlist_beatmap_raw["OD"],
                "MODS": playlist_beatmap_raw["Mods"],
                "NOTES": playlist_beatmap_raw["Notes"],  # notes 在 OsuPlaylist 里有默认值处理
                "STATUS": beatmap_specs[i][5],
                "COMMENTS": beatmap_specs[i][6],
                "POOL": beatmap_specs[i][3],
                "SUGGESTOR": beatmap_specs[i][7],
                "RAW_MODS": orjson.dumps(beatmap_specs[i][1]).replace(b" ", b"").decode(),
                "ADD_TS": float(beatmap_specs[i][8]),
            },
        )
    # 删除临时文件
    if os.path.exists(tmp_playlist_filename):
        os.remove(tmp_playlist_filename)
    return playlist_beatmaps_db


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


def update_beatmap(beatmap: Optional[dict] = None, *, old_bid: Optional[int] = None, old_mods: Optional[str] = None) -> None:
    """更新课题谱面（包括删除）

    如果 beatmap 不为 None，则 old_bid 必须为 None

    :param beatmap: 欲更新课题谱面
    :param old_bid: 欲删除 BID
    :param old_mods: 欲删除 MODS
    :return:
    """
    if beatmap is not None:
        if old_bid is not None:
            raise ValueError("cannot update a beatmap with a different old bid")
        # 如果 beatmap 不为 None，old_bid 从 beatmap 中获取
        old_bid = beatmap["BID"]

    with conn.session as s:
        if old_bid is not None and old_mods is not None:
            # 有可能传入的是 numpy 类型，需要强制转化为原生类型
            old_bid = int(old_bid)
            old_mods = str(old_mods)
            s.execute(
                text(
                    """DELETE
                       FROM BEATMAP
                       WHERE BID = :bid
                         AND MODS = :mods""",
                ),
                {"bid": old_bid, "mods": old_mods},
            )
        elif beatmap is not None:
            s.execute(
                text(
                    """INSERT INTO BEATMAP (BID, SID, INFO, SKILL_SLOT, SR, BPM, HIT_LENGTH, MAX_COMBO, CS, AR, OD, MODS, NOTES, STATUS, COMMENTS, POOL, SUGGESTOR, RAW_MODS, ADD_TS)
                       VALUES (:BID, :SID, :INFO, :SKILL_SLOT, :SR, :BPM, :HIT_LENGTH, :MAX_COMBO, :CS, :AR, :OD, :MODS, :NOTES, :STATUS, :COMMENTS, :POOL, :SUGGESTOR, :RAW_MODS, :ADD_TS)
                       ON CONFLICT (BID, MODS)
                           DO UPDATE SET SKILL_SLOT = EXCLUDED.SKILL_SLOT,
                                         SR         = EXCLUDED.SR,
                                         BPM        = EXCLUDED.BPM,
                                         HIT_LENGTH = EXCLUDED.HIT_LENGTH,
                                         MAX_COMBO  = EXCLUDED.MAX_COMBO,
                                         CS         = EXCLUDED.CS,
                                         AR         = EXCLUDED.AR,
                                         OD         = EXCLUDED.OD,
                                         MODS       = EXCLUDED.MODS,
                                         NOTES      = EXCLUDED.NOTES,
                                         STATUS     = EXCLUDED.STATUS,
                                         COMMENTS   = EXCLUDED.COMMENTS,
                                         POOL       = EXCLUDED.POOL,
                                         SUGGESTOR  = EXCLUDED.SUGGESTOR,
                                         RAW_MODS   = EXCLUDED.RAW_MODS,
                                         INFO       = EXCLUDED.INFO
                    -- 注意：ADD_TS 只会保留第一次创建记录时的值，后续不会被更新""",
                ),
                params=beatmap,
            )
        else:
            raise ValueError(_("no changes made"))
        s.commit()


def online_playlist_action_logger(bid: int | str, mods: str, action: int, old_mods: Optional[str] = None) -> None:
    bid = int(bid)
    mods = str(mods)
    verb_mapping = {
        0: "added",
        1: "deleted",
        2: "updated",
    }
    msg = "%s (%d %s)" % (verb_mapping[action], bid, mods)
    if old_mods is not None:
        msg += " from %s" % old_mods
    logger.get_logger(st.session_state.username).info(msg)
    st.toast(msg)


if st.session_state.perm >= 1:
    st.markdown(_("## Online Playlist Creator"))
    available_pools = conn.query(
        """SELECT DISTINCT POOL
           FROM BEATMAP
           ORDER BY POOL""",
    )["POOL"].to_list()

    with st.form(_("Add beatmap")):
        col1, col2 = st.columns(2)
        with col1:
            urls_input = st.text_input(_("Beatmap URLs or IDs, split by spaces"))
            slot_input = st.text_input(_("Slot")).upper()
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
            # 处理 BID
            # SLOTS 自动大写
            specs_input: list[tuple[int, list, str, str, str, int, str, str, float]] = []
            urls_input_split = urls_input.split()
            raw_mods_input = generate_mods_from_lines(slot_input, mod_settings_input)
            for url_input in urls_input_split:
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
                    (
                        bid_input,
                        raw_mods_input,
                        slot_input,
                        st.session_state.gen_form_pool,
                        notes_input,
                        0,
                        "",
                        st.session_state.username,
                        time.time(),
                    ),
                )
            playlist_beatmaps_input = create_tmp_playlist(uid, specs_input)
            for beatmap_to_insert in playlist_beatmaps_input:
                update_beatmap(beatmap_to_insert)
                online_playlist_action_logger(beatmap_to_insert["BID"], beatmap_to_insert["MODS"], 0)

    with st.container(border=True):
        filter_col1, filter_col2, filter_col3 = st.columns([1, 1, 3])
        with filter_col1:
            memorized_selectbox(_("Pool"), "gen_filter_pool", ["-"] + available_pools, "-")
        with filter_col2:
            memorized_selectbox(_("Status"), "gen_filter_status", [-1, 0, 1, 2], -1)
        with filter_col3:
            st.text_input(_("Search"), key="gen_filter_search")

    # 查询重复的 BID 与 SID，用于后期查询结果表格渲染。BID 与 SID 要分开表示，重复的 BID 行要标记为红色，重复的 SID 行要标记为黄色
    duplicate_bids = conn.query(
        """SELECT BID
           FROM BEATMAP
           GROUP BY BID
           HAVING COUNT(*) > 1""",
    )["BID"].tolist()
    duplicate_sids = conn.query(
        """SELECT SID
           FROM BEATMAP
           GROUP BY SID
           HAVING COUNT(*) > 1""",
    )["SID"].tolist()

    # pool 和 status 的查询使用 SQL 完成
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
    df = conn.query(filter_query, params=filter_params)

    # keywords 的筛选用 pandas 完成，从 bid、sid、info、slot、mods、notes 中查找包含输入内容的条目
    if st.session_state.gen_filter_search:
        keyword_lower = st.session_state.gen_filter_search.lower()
        target_cols = ["BID", "SID", "INFO", "SKILL_SLOT", "MODS", "NOTES"]
        mask = df[target_cols].apply(lambda x: x.str.lower().str.contains(keyword_lower, na=False).any(), axis=1)
        df = df[mask]

    # 新增 JS 辅助列
    df["_is_dup_bid"] = df["BID"].isin(duplicate_bids)
    df["_is_dup_sid"] = df["SID"].isin(duplicate_sids)

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
        "_is_dup_bid",
        "_is_dup_sid",
    ]
    df = df[desired_col_order]

    gb = GridOptionsBuilder.from_dataframe(df)
    gb.configure_selection(selection_mode="multiple", use_checkbox=True, suppressRowClickSelection=True)
    gb.configure_grid_options(
        **dict(
            domLayout="normal",
            rowHeight=32,
            getRowStyle=row_style_js,
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
    # 共可可修改列: SKILL_SLOT, STATUS, COMMENTS, POOL, NOTES, RAW_MODS,
    cell_rules = {"clickable-cell-style": "true"}  # 这里的 'true' 是 JS 表达式，表示始终应用
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
            "white-space": "pre-wrap",  # 这一步关键：保留空格和换行，否则会被挤成一行
        },
        cellEditor="agLargeTextCellEditor",
        cellEditorPopup=True,
    )
    gb.configure_column("STATUS", header_name="Status", editable=True, cellEditor="agSelectCellEditor", cellEditorParams={"values": [0, 1, 2]}, width=25)

    # 隐藏列 _is_dup_bid、_is_dup_sid、ADD_TS
    gb.configure_column("_is_dup_bid", hide=True)
    gb.configure_column("_is_dup_sid", hide=True)
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
        allow_unsafe_jscode=True,
        key="gen_playlist_grid",
    )
    try:
        edited_df = pd.DataFrame(grid_response["data"])
    except ValueError:
        refresh(1)

    selected_rows = grid_response["selected_rows"]

    col_save_n_refresh, col_blank, col_del = st.columns(spec=[0.5, 0.15, 0.35], gap="large")
    with col_save_n_refresh:
        with st.container(border=False, horizontal=True):
            if st.button(_("Modify"), use_container_width=True):
                if edited_df.empty:
                    st.toast(_("no changes made"))
                else:
                    specs_recalculate: list[tuple[int, list, str, str, str, int, str, str, float]] = []
                    olds_to_drop: list[tuple[str, bool]] = []  # (old MODS, RAW_MODS changed)
                    for index, row in edited_df.iterrows():
                        edited_bid, edited_mods = row["BID"], row["MODS"]
                        # 由于 MODS 在这里尚未更改，因此还是可以根据 BID + MODS 的组合定位原始表格中的对应行
                        original_row = df.loc[(df["BID"] == edited_bid) & (df["MODS"] == edited_mods)]
                        if original_row.empty:
                            st.toast(_("(%d %s) not found, skipped") % (edited_bid, edited_mods))
                            continue
                        original_row = original_row.iloc[0]
                        # 在可修改列中如果有任意一项被修改了，那么就添加到重算列表中
                        new_primary = False
                        for editable_col in ["RAW_MODS", "SKILL_SLOT", "POOL", "NOTES", "STATUS", "COMMENTS"]:
                            if pd.isna(row[editable_col]) and pd.isna(original_row[editable_col]):
                                continue
                            if row[editable_col] != original_row[editable_col]:
                                # 如果 RAW_MODS 修改了，那么就认为需要先删除原始记录，然后再添加新记录
                                if editable_col == "RAW_MODS":
                                    new_primary = True
                                break
                        else:
                            continue
                        specs_recalculate.append((edited_bid, orjson.loads(row["RAW_MODS"]), row["SKILL_SLOT"], row["POOL"], row["NOTES"], row["STATUS"], row["COMMENTS"], original_row["SUGGESTOR"], original_row["ADD_TS"]))
                        olds_to_drop.append((original_row["MODS"], new_primary))
                    playlist_beatmaps_recalculate = create_tmp_playlist(uid, specs_recalculate)
                    for old_to_drop, beatmap_to_upsert in zip(olds_to_drop, playlist_beatmaps_recalculate):
                        if old_to_drop[1]:
                            update_beatmap(beatmap_to_upsert, old_mods=old_to_drop[0])
                            online_playlist_action_logger(beatmap_to_upsert["BID"], beatmap_to_upsert["MODS"], 2, old_to_drop[0])
                        else:
                            update_beatmap(beatmap_to_upsert)
                            online_playlist_action_logger(beatmap_to_upsert["BID"], beatmap_to_upsert["MODS"], 2)
                refresh(1.5)
            if st.button(_("Refresh"), use_container_width=True):
                refresh()

    with col_del:
        with st.container(border=False, horizontal_alignment="right"):
            if st.button(_("Delete"), type="primary", use_container_width=True):
                if len(selected_rows) == 0:
                    st.toast(_("no beatmaps selected"))
                else:
                    for index, row in selected_rows.iterrows():
                        selected_bid, selected_mods = row["BID"], row["MODS"]
                        update_beatmap(old_bid=selected_bid, old_mods=selected_mods)
                        online_playlist_action_logger(selected_bid, selected_mods, 1)
                refresh(1.5)

st.divider()

st.markdown(_("## Generate from a File"))
uploaded_file = st.file_uploader(_("choose a file"), type=["properties"])
if uploaded_file is not None:
    playlist_name = os.path.splitext(uploaded_file.name)[0]
    session_path = os.path.join(C.UPLOADED_DIRECTORY.value, uid)
    if not os.path.exists(session_path):
        os.mkdir(session_path)
    playlist_filename = str(os.path.join(session_path, "%s.properties" % playlist_name))
    html_filename = str(os.path.join(session_path, "%s.html" % playlist_name))
    covers_dir = str(os.path.join(session_path, "%s.covers" % playlist_name))
    csv_filename = str(os.path.join(session_path, "%s.csv" % playlist_name))
    css_filename = str(os.path.join(session_path, "style.css"))
    zip_filename = str(os.path.join(C.UPLOADED_DIRECTORY.value, "%s.zip" % uid))
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
