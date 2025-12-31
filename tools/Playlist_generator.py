import asyncio
import os.path
import shutil
import time
from functools import partial
from typing import Literal, Optional
from uuid import UUID

import orjson
import pandas as pd
import streamlit as st
from clayutil.futil import Properties, compress_as_zip
from clayutil.validator import validate_type
from sqlalchemy import text
from st_aggrid import AgGrid, GridOptionsBuilder, JsCode
from streamlit import logger
from streamlit.runtime.scriptrunner import get_script_run_ctx

from osuawa import C, OsuPlaylist
from osuawa.components import init_page_layout, memorized_selectbox
from osuawa.utils import generate_mods_from_lines, to_readable_mods

validate_restricted_identifier = partial(validate_type, type_=str, min_value=1, max_value=16, predicate=str.isidentifier)

init_page_layout(_("Playlist generator") + " - osuawa")
with st.sidebar:
    st.toggle(_("new style"), key="new_style", value=True)

conn = st.connection("osuawa", type="sql")
uid = UUID(get_script_run_ctx().session_id).hex
row_style_js = JsCode(
    """
        function(params) {
            if (params.data._is_dup_bid) return { backgroundColor: 'crimson' };
            if (params.data._is_dup_sid) return { backgroundColor: 'lemonchiffon' };
            if (params.data.STATUS == 1) return { backgroundColor: 'darkseagreen' };
            if (params.data.STATUS == 2) return { backgroundColor: 'powderblue' };
            return {};
        }
    """,
)
slot_cell_style_js = JsCode(
    f"""
function(params) {{
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
    """
function(event) {
    let target = event.event.target;

    // 向上查找真正的单元格容器
    while (target && !target.classList.contains('ag-cell')) {
        target = target.parentElement;
    }

    if (target && event.value !== undefined && event.value !== null) {

        navigator.clipboard.writeText(String(event.value))
            .then(() => {
                console.log('Copied:', event.value);

                // --- 关键修改 1: 强制开启背景色过渡 ---
                // !important 确保覆盖默认 CSS
                target.style.transition = "background-color 0.3s ease";

                // --- 关键修改 2: 变色 ---
                target.style.backgroundColor = '#90EE90'; 

                // --- 关键修改 3: 恢复 ---
                setTimeout(() => {
                    // 设为透明，而不是空字符串！
                    // 这样能透出你在 getRowStyle 里定义的颜色
                    target.style.backgroundColor = 'transparent';

                    // 0.3s 后移除 transition 属性，避免影响表格滚动性能或 hover 效果
                    setTimeout(() => {
                        target.style.removeProperty('transition');
                    }, 300);

                }, 300); // 保持高亮 600ms，让用户看得更清楚
            })
            .catch(err => {
                console.error('Failed to copy:', err);
                target.style.transition = "background-color 0.3s ease";
                target.style.backgroundColor = '#FFB6C1';
                setTimeout(() => {
                    target.style.backgroundColor = 'transparent';
                    setTimeout(() => target.style.removeProperty('transition'), 300);
                }, 300);
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


@st.cache_data(show_spinner=False)
def convert_df(data: pd.DataFrame, filename: str):
    data.to_csv(filename, encoding="utf-8", index=False)


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


def insert_beatmap(beatmap: dict) -> tuple[int, str]:
    with conn.session as s:
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
        s.commit()
    return beatmap["BID"], beatmap["MODS"]


def delete_beatmap(bid: int, mods: str) -> tuple[int, str]:
    bid = int(bid)
    mods = str(mods)
    with conn.session as s:
        s.execute(
            text(
                """DELETE
                   FROM BEATMAP
                   WHERE BID = :bid
                     AND MODS = :mods""",
            ),
            {"bid": bid, "mods": mods},
        )
        s.commit()
    return bid, mods


def online_playlist_action_logger(bid: int | str, mods: str, action: Literal["submit", "update", "delete"]) -> None:
    bid = int(bid)
    mods = str(mods)
    logger.get_logger(st.session_state.username).info("%sed (%d %s)" % (action.rstrip("e"), bid, mods))


if st.session_state.perm >= 1:
    st.markdown(_("## Online Playlist Creator"))
    with st.form(_("Add beatmap")):
        col1, col2 = st.columns(2)
        with col1:
            urls_input = st.text_input(_("Beatmap URLs or IDs, split by spaces"))
            slot_input = st.text_input(_("Slot")).upper()
            notes_input = st.text_input(_("Notes"))
        with col2:
            pool_input = st.text_input(_("Pool"), value="_DEFAULT_POOL")
            mod_settings_input = st.text_area(_("Mod Settings"), height="stretch")
            # status_input = st.slider(_("Status"), 0, 2, 0)
        submitted = st.form_submit_button(_("Add"), use_container_width=True)
        if submitted:
            # 处理 BID
            # SLOTS 和 MODS 均自动大写
            specs_input: list[tuple[int, list, str, str, str, int, str, str, float]] = []
            urls_input_split = urls_input.split()
            raw_mods_input = generate_mods_from_lines(slot_input, mod_settings_input)
            for url_input in urls_input_split:
                bid_input = int(url_input.rsplit("/", 1)[-1])
                # 这里要提前转化 raw_mods 为 "; ".join(mods_ready)，一方面检验是否能序列化，另一方面查重并终止
                try:
                    mods_ready_input = to_readable_mods(raw_mods_input)
                except (orjson.JSONDecodeError, ValueError, KeyError):
                    st.error(_("Invalid mods: %s" % raw_mods_input))
                    continue
                mods_input = "; ".join(mods_ready_input)
                if check_beatmap_exists(bid_input, mods_input):
                    st.warning(_("(%d %s) already exists, skipped" % (bid_input, mods_input)))
                    continue
                validate_restricted_identifier(pool_input)
                specs_input.append(
                    (
                        bid_input,
                        raw_mods_input,
                        slot_input,
                        pool_input,
                        notes_input,
                        0,
                        "",
                        st.session_state.username,
                        time.time(),
                    ),
                )
            playlist_beatmaps_recalculate = create_tmp_playlist(uid, specs_input)
            for beatmap_to_insert in playlist_beatmaps_recalculate:
                updated_bid, updated_mods = insert_beatmap(beatmap_to_insert)
                online_playlist_action_logger(updated_bid, updated_mods, "submit")

    filter_col1, filter_col2, filter_col3 = st.columns([1, 1, 3])
    with filter_col1:
        available_pools = conn.query(
            """SELECT DISTINCT POOL
               FROM BEATMAP
               ORDER BY POOL""",
        )["POOL"].to_list()
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

    # 以下是弃用的 DataFrame Styler
    # def playlist_df_style(row) -> list[str]:
    #     # BID 重复: crimson
    #     # SID 重复: lemonchiffon
    #     # STATUS 1: darkseagreen
    #     # STATUS 2: powderblue
    #     if row["BID"] in duplicate_bids:
    #         return ["crimson"] * len(row)
    #     if row["SID"] in duplicate_sids:
    #         return ["lemonchiffon"] * len(row)
    #     if row["STATUS"] == 1:
    #         return ["darkseagreen"] * len(row)
    #     if row["STATUS"] == 2:
    #         return ["powderblue"] * len(row)
    #     return [""] * len(row)

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
        ),
    )
    gb.configure_column(
        field="LINK",
        header_name="Cover",
        cellRenderer=image_link_renderer,
        width=30,
        pinned="left",
        editable=False,
    )
    # 由于 MODS 列使用的是 readable_mods 的表示，是不可变的
    # 因此用户可以更改的是与 mods_input 对应的 RAW_MODS 列
    # 共可可修改列: SKILL_SLOT, STATUS, COMMENTS, POOL, NOTES, RAW_MODS,
    gb.configure_column("BID", width=100, onCellClicked=copy_on_click_js)
    gb.configure_column("SKILL_SLOT", header_name="Slot", editable=True, cellStyle=slot_cell_style_js, width=50)
    gb.configure_column("MODS", header_name="Mods", width=30)
    gb.configure_column("INFO", header_name="Beatmap", wrapText=True, width=250)
    gb.configure_column("SR", width=65)
    gb.configure_column("BPM", width=50)
    gb.configure_column("HIT_LENGTH", header_name="Drain", width=45)
    gb.configure_column("MAX_COMBO", header_name="Combo", width=50)
    gb.configure_column("CS", width=20)
    gb.configure_column("AR", width=20)
    gb.configure_column("OD", width=20)
    gb.configure_column("POOL", header_name="Pool", editable=True, width=55)
    gb.configure_column("SUGGESTOR", header_name="Suggestor", editable=True, width=35)
    gb.configure_column("NOTES", header_name="Notes", editable=True, width=50)
    gb.configure_column("ADD_DATETIME", width=80)
    gb.configure_column("COMMENTS", editable=True, wrapText=True, width=120)
    gb.configure_column(
        "RAW_MODS",
        editable=True,
        wrapText=True,
        width=250,
        cellStyle={
            "font-family": "monospace",
            "white-space": "pre-wrap",  # 这一步关键：保留空格和换行，否则会被挤成一行
        },
    )
    gb.configure_column("STATUS", editable=True, cellEditor="agSelectCellEditor", cellEditorParams={"values": [0, 1, 2]}, width=20)

    # 隐藏列 _is_dup_bid、_is_dup_sid、ADD_TS
    gb.configure_column("_is_dup_bid", hide=True)
    gb.configure_column("_is_dup_sid", hide=True)
    gb.configure_column("SID", hide=True)
    gb.configure_column("ADD_TS", hide=True)

    grid_options = gb.build()

    grid_response = AgGrid(
        df,
        gridOptions=grid_options,
        update_on=[
            "cellValueChanged",  # 单元格值改变时触发 (编辑)
            "selectionChanged",  # 选择行改变时触发 (删除)
        ],
        height=1000,
        allow_unsafe_jscode=True,
        fit_columns_on_grid_load=False,
        key="gen_playlist_grid",
    )
    edited_df = pd.DataFrame(grid_response["data"])

    selected_rows = grid_response["selected_rows"]

    col_save_n_refresh, col_blank, col_del = st.columns(spec=[0.5, 0.15, 0.35], gap="large")
    with col_save_n_refresh:
        col_save, col_refresh = st.columns(2, gap="small")
        with col_save:
            if st.button(_("Modify"), use_container_width=True):
                if edited_df.empty:
                    st.toast(_("no changes made"))
                else:
                    affected_original_beatmaps: set[tuple[int, str]] = set()
                    specs_recalculate: list[tuple[int, list, str, str, str, int, str, str, float]] = []
                    for index, row in edited_df.iterrows():
                        edited_bid, edited_raw_mods = row["BID"], orjson.loads(row["RAW_MODS"])
                        # 由于 MODS 在这里尚未更改，因此还是可以根据 BID + MODS 的组合定位原始表格中的对应行
                        original_row = df.loc[(df["BID"] == edited_bid) & (df["MODS"] == row["MODS"])]
                        if original_row.empty:
                            st.toast(_("(%d %s) not found, skipped") % (edited_bid, edited_raw_mods))
                            continue
                        original_row = original_row.iloc[0]
                        # 在可修改列中如果有任意一项被修改了，那么就添加到重算列表中
                        if any([row[editable_col] != original_row[editable_col] for editable_col in ["RAW_MODS", "SKILL_SLOT", "POOL", "NOTES", "STATUS", "COMMENTS"]]):
                            specs_recalculate.append((edited_bid, edited_raw_mods, row["SKILL_SLOT"], row["POOL"], row["NOTES"], row["STATUS"], row["COMMENTS"], original_row["SUGGESTOR"], original_row["ADD_TS"]))
                            # 由于 BID 不可变，RAW_MODS 修改后可能会导致 MODS 改变，从而使得这次不是单纯的原地更新，而是新增了一条记录
                            # 因此要保存原始的 BID 与 MODS，在 insert_beatmap 后进行比对
                            # 若 BID + MODS 的组合变化了，那么原始记录理应被丢弃
                            affected_original_beatmaps.add((original_row["BID"], original_row["MODS"]))
                    playlist_beatmaps_recalculate = create_tmp_playlist(uid, specs_recalculate)
                    for beatmap_to_upsert in playlist_beatmaps_recalculate:
                        upserted_bid, upserted_mods = insert_beatmap(beatmap_to_upsert)
                        # 查找 upserted_bid + upserted_mods 在 affected_original_beatmaps 中是否存在
                        # 如果存在，说明是原地更新，那 insert_beatmap 已设置 ON CONFLICT DO NOTHING，无需删除，logger 记为 update
                        # 如果不存在，说明本次更新更新了 MODS，属于新增记录，需要删除原始记录，logger 记为 submit 与 delete
                        if (upserted_bid, upserted_mods) in affected_original_beatmaps:
                            online_playlist_action_logger(upserted_bid, upserted_mods, "update")
                            affected_original_beatmaps.remove((upserted_bid, upserted_mods))
                        else:
                            online_playlist_action_logger(upserted_bid, upserted_mods, "submit")
                    for orphan_bid, orphan_mods in affected_original_beatmaps:
                        deleted_bid, deleted_mods = delete_beatmap(orphan_bid, orphan_mods)
                        online_playlist_action_logger(deleted_bid, deleted_mods, "delete")
        with col_refresh:
            if st.button(_("Refresh"), use_container_width=True):
                conn.reset()
                st.cache_data.clear()
                st.rerun()
    with col_del:
        if st.button(_("Delete"), type="primary", width=200):
            if len(selected_rows) == 0:
                st.toast(_("no beatmaps selected"))
            else:
                for index, row in selected_rows.iterrows():
                    selected_bid, selected_mods = row["BID"], row["MODS"]
                    deleted_bid, deleted_mods = delete_beatmap(selected_bid, selected_mods)
                    online_playlist_action_logger(deleted_bid, deleted_mods, "delete")

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
    convert_df(table, csv_filename)
    if os.path.exists("./playlists/style.css"):
        shutil.copy("./playlists/style.css", css_filename)
    st.dataframe(table, hide_index=True)
    compress_as_zip(session_path, zip_filename)
    with open(zip_filename, "rb") as zfi:
        st.download_button(label=_("download the resources"), file_name="%s.zip" % uid, data=zfi)
