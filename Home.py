from collections.abc import Generator
from time import sleep
from typing import Any, TYPE_CHECKING, cast

import streamlit as st
from clayutil.cmdparse import (
    CommandError,
)
from streamlit import logger
from streamlit.components.v1 import html
from streamlit.errors import Error
from zai.core import StreamResponse
from zai.types.chat import ChatCompletionChunk, ChoiceDeltaToolCall

from osuawa import LANGUAGES
from osuawa.components import get_session_id, init_page, memorized_selectbox,tail_log, cat
from zai import ZhipuAiClient
import json
import pandas as pd

if TYPE_CHECKING:

    def _(text: str) -> str: ...


init_page(_("Homepage") + " - osuawa")


def run(g: Generator[Any, Any, int]):
    while True:
        try:
            st.write(next(g))
        except CommandError as e:
            st.error(e)
            continue
        except StopIteration as e:
            st.success(_("%d sub-tasks done") % (e.value or 0))
            break
        except (Error, NotImplementedError) as e:
            logger.get_logger("streamlit").exception(e)
            # st.session_state.clear()
            break
        except Exception as e:
            st.exception(e)
            logger.get_logger("streamlit").exception(e)
            break


def submit():
    logger.get_logger(st.session_state.username).info(st.session_state["input"])
    run(st.session_state.cmdparser.parse_command(st.session_state["input"]))
    st.session_state["delete_line"] = True
    st.session_state["counter"] += 1


with st.sidebar:
    memorized_selectbox(":material/language: lang", "uni_lang", LANGUAGES, None)

with st.spinner(_("Preparing for the next operation...")):
    sleep(1.5)
if "delete_line" not in st.session_state:
    st.session_state["delete_line"] = True
if "counter" not in st.session_state:
    st.success(_("Welcome!"))
    st.session_state["counter"] = 0
if st.session_state["delete_line"]:
    st.session_state["input"] = ""
    st.session_state["delete_line"] = False

y = st.text_input("> ", key="input", on_change=submit, placeholder=_('Type "help" to get started.'), label_visibility="collapsed")


# 函数映射
FUNCTION_MAP = {
    "tail_log": tail_log,
    "get_user_info": st.session_state.awa.get_user_info,
    "get_user_beatmap_scores": st.session_state.awa.get_user_beatmap_scores,
    "cat": cat
}

# 工具定义
tools = [
    {
        "type": "function",
        "function": {
            "name": "tail_log",
            "description": "类似于 tail 配合 grep，拿到日志文件末尾 n 行中包含 keyword 的行并返回。用于快速查看和过滤日志内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "n": {
                        "type": "integer",
                        "description": "从日志文件末尾获取的行数，默认为 100 行",
                        "default": 100
                    },
                    "keyword": {
                        "type": "string",
                        "description": "用于过滤日志行的关键词，仅返回包含该关键词的行。如果不指定则返回所有行",
                        "default": None
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_user_info",
            "description": "查询 osu! 游戏玩家的个人信息。通过用户名查询该玩家的 PP、排名、准确率、游玩次数等信息。返回的数据包含 user_id（玩家ID），可用于其他需要 user_id 的工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "username": {
                        "type": "string",
                        "description": "osu! 游戏玩家用户名（username）"
                    }
                },
                "required": ["username"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_user_beatmap_scores",
            "description": "查询 osu! 玩家在指定谱面（beatmap）上的游玩记录和分数。⚠️ 重要：user 参数需要玩家ID（user_id，整数），如果你只有用户名，请先调用 get_user_info 获取 user_id。",
            "parameters": {
                "type": "object",
                "properties": {
                    "beatmap": {
                        "type": "integer",
                        "description": "osu! 谱面 ID（beatmap id/BID），必须是整数"
                    },
                    "user": {
                        "type": "integer",
                        "description": "osu! 玩家 ID（user_id，整数）。如果只知道用户名，请先调用 get_user_info 获取。不指定则查询当前登录用户。",
                        "default": None
                    }
                },
                "required": ["beatmap"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "cat",
            "description": "获取指定 osu! 玩家的所有本地保存的游玩数据。⚠️ 重要：user 参数需要玩家ID（user_id，整数），如果你只有用户名，请先调用 get_user_info 获取 user_id。",
            "parameters": {
                "type": "object",
                "properties": {
                    "user": {
                        "type": "integer",
                        "description": "osu! 玩家ID（user_id，整数）。如果只知道用户名，请先调用 get_user_info 获取。"
                    }
                },
                "required": ["user"]
            }
        }
    }
]

# client 初始化
client = ZhipuAiClient(api_key=st.secrets.args.api_key)

if "openai_model" not in st.session_state:
    st.session_state["openai_model"] = "glm-4-flash"

if "messages" not in st.session_state:
    st.session_state.messages = cast(list[dict[str, Any]], [
    {
        "role": "system",
        "content": """你是一个 osu! 游戏助手。当用户询问玩家和谱面相关信息时，请使用可用的工具进行查询。

重要的使用规则：
1. 当用户提到某个玩家名并询问 PP、排名、数据等信息时，立即使用 get_user_info 工具查询；
2. 不需要等待用户明确说"用户名为xxx"或"玩家xxx"，只要看到疑似游戏玩家名字就主动查询；
3. osu! 用户名中间可以包含空格，首尾可能有"-"、"["、"]"等特殊字符，这些也是用户名的一部分，不要擅自 strip 或 trim；

示例：
1. 当用户询问某位玩家在指定谱面上的成绩时，先调用 get_user_info 工具获取 user_id，再调用 get_user_beatmap_scores 工具查询成绩。"""
    }
]
                                     )


def execute_tool_call(tool_call: dict[str, Any]) -> str:
    """执行工具调用"""
    function_name = tool_call["function"]["name"]
    function_args = json.loads(tool_call["function"]["arguments"])

    if function_name in FUNCTION_MAP:
        if hasattr(FUNCTION_MAP[function_name], "__func__") and hasattr(FUNCTION_MAP[function_name], "__self__"):  # method
            result = FUNCTION_MAP[function_name].__func__(FUNCTION_MAP[function_name].__self__, **function_args)  # type: ignore
        else:
            result = FUNCTION_MAP[function_name](**function_args)
        # 如果返回 DataFrame，转换为字符串
        if isinstance(result, pd.DataFrame):
            return result.to_markdown() or "无数据"
        return str(result)
    else:
        return f"错误：未知函数 {function_name}"


def process_streaming_with_tools():
    """处理带工具调用的流式响应"""
    messages: list[dict[str, Any]] = st.session_state.messages.copy()
    iteration = 0
    max_iterations = 5  # 防止无限循环

    while iteration < max_iterations:
        iteration += 1

        # 创建流式响应
        stream = cast(StreamResponse[ChatCompletionChunk], client.chat.completions.create(
            model=st.session_state["openai_model"],
            messages=messages,
            tools=tools,
            stream=True
        ))

        # 处理流式响应
        with st.chat_message("assistant"):
            response_placeholder = st.empty()
            content = ""
            tool_calls: list[dict[str, Any]] = []

            # 遍历流式块
            for chunk in stream:
                delta = chunk.choices[0].delta

                # 显示文本内容（实时更新）
                if hasattr(delta, "content") and delta.content:
                    content += delta.content
                    response_placeholder.markdown(content + "▌")

                # 收集工具调用
                if hasattr(delta, "tool_calls") and delta.tool_calls:
                    tool_call_chunk: ChoiceDeltaToolCall
                    for tool_call_chunk in delta.tool_calls:
                        idx = tool_call_chunk.index

                        # 确保列表有足够的空间
                        while len(tool_calls) <= idx:
                            tool_calls.append(
                                {
                                    "id": None,
                                    "type": "function",
                                    "function": {"name": None, "arguments": ""}
                                }
                            )

                        # 拼接工具调用信息
                        if tool_call_chunk.id:
                            tool_calls[idx]["id"] = tool_call_chunk.id
                        if tool_call_chunk.function is not None:
                            if tool_call_chunk.function.name:
                                tool_calls[idx]["function"]["name"] = tool_call_chunk.function.name
                            if tool_call_chunk.function.arguments:
                                tool_calls[idx]["function"]["arguments"] += tool_call_chunk.function.arguments
                        else:
                            raise ValueError(f"工具调用 {idx} 缺少函数信息")

            # 清除光标，显示最终内容
            if content:
                response_placeholder.markdown(content)

        # 检查是否有工具调用
        if not tool_calls or tool_calls[0]["id"] is None:
            # 无工具调用，保存最终消息并退出
            if content:
                st.session_state.messages.append(
                    {
                        "role": "assistant",
                        "content": content
                    }
                )
            return

        # 有工具调用
        st.info(f"🔧 正在执行 {len(tool_calls)} 个工具调用...")

        # 将助手消息加入历史（用于下一轮调用）
        messages.append(
            {
                "role": "assistant",
                "content": content or None,
                "tool_calls": tool_calls
            }
        )

        # 执行所有工具调用
        with st.expander("📋 工具调用详情", expanded=True):
            for i, tool_call in enumerate(tool_calls, 1):
                func_name = tool_call["function"]["name"]
                func_args = tool_call["function"]["arguments"]

                st.code(f"{func_name}({func_args})", language="python")

                try:
                    result = execute_tool_call(tool_call)
                    st.success(f"✅ 工具调用 {i} 执行成功")
                    st.text(result[:100] + "..." if len(result) > 100 else result)
                except Exception as e:
                    result = f"工具调用 {i} 执行失败: {str(e)}"
                    st.error(f"❌ {result}")

                # 将结果加入消息历史
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "name": func_name,
                        "content": result
                    }
                )

        # 继续循环，让模型基于工具结果生成最终回复


# 显示历史消息
for message in st.session_state.messages:
    if message["role"] == "system":
        continue
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# 用户输入
if prompt := st.chat_input(_("How can I help you?")):
    # 添加用户消息
    st.session_state.messages.append({"role": "user", "content": prompt})

    with st.chat_message("user"):
        st.markdown(prompt)

    process_streaming_with_tools()

html(
    """<script>
    var input = window.parent.document.querySelectorAll("input[type=text]");
    for (var i = 0; i < input.length; ++i) {
        input[i].focus();
    }
</script>
""",
    height=0,
)

if y:
    st.text(y)

#################################
### DEBUGGING COMPONENTS AREA ###
#################################
if st.session_state._debugging_mode:
    from osuawa.components import memorized_selectbox, memorized_multiselect

    memorized_selectbox("Memorized Selectbox Test", "test_memorized_selectbox", list("abcde"), "c")
    memorized_multiselect("Memorized Multiselect Test", "test_memorized_multiselect", list("abcde"), ["c", "e"])

st.text(_("Session: %s") % get_session_id())
