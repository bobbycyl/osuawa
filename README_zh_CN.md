# osuawa

## 简介

我用到的和 osu! 相关的工具（支持 Lazer 数据！）

现已上线工具：查成分、做课题和看记录

## 软件要求

Python 3.12, Rust ([rosu-pp-py](https://github.com/MaxOhn/rosu-pp-py) 需要)
以及 .NET 8.0 SDK ([osu-tools](https://github.com/ppy/osu-tools) 需要)

## 快速开始

1. 获取 osu-tools。 `git clone https://github.com/ppy/osu-tools.git`

2. 克隆本仓库。 `git clone https://github.com/bobbycyl/osuawa.git`

3. 创建并激活虚拟环境。

   ```shell
   cd osuawa
   python -m venv ./venv  # 如果必要，将 python 替换为 python3 或 py
   source ./venv/bin/activate  # Windows平台须使用 .\venv\Scripts\activate
   ```

4. 安装依赖。

   ```shell
   # 使用 pip 可以安装绝大多数依赖
   python -m pip install -r requirements.txt
   # 但 fontfallback 需要手动安装
   git clone https://github.com/TrueMyst/PillowFontFallback.git
   cp -r ./PillowFontFallback/fontfallback ./venv/lib/python3.12/site-packages/
   rm -r PillowFontFallback
   ```

5. 配置。

   1. 从[这里](https://osu.ppy.sh/home/account/edit)获取你的开放授权客户端。
      端口号须和 `./.streamlit/config.toml` 中的 `server.port` 一致。

   2. 在一个你喜欢的地方创建 `osu.properties` 文件，并添加以下内容：

      ```properties
      client_id=<客户端 ID>
      client_secret=<客户端密钥>
      redirect_url=<应用回调链接>
      ```

   3. 编辑 `./.streamlit/secrets.toml`。

   4. 如果不需要HTTPS，删除 `./.streamlit/config.toml` 中的SSL相关设置。

6. 运行程序。 `python run.py`
