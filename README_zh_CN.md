# osuawa

## 简介

我用到的和 osu! 相关的工具（支持 Lazer 数据！）

现已上线工具：查成分、做课题和看记录

## 软件要求

Python 3.12, Rust ([rosu-pp-py](https://github.com/MaxOhn/rosu-pp-py) 需要)

## 快速开始

### 克隆仓库

```shell
git clone https://github.com/bobbycyl/osuawa.git
```

### 创建并激活虚拟环境

```shell
# 切换目录
cd osuawa
# 创建虚拟环境
python -m venv ./.venv  # 如有必要，将 python 替换为 python3 或 py
# 激活虚拟环境
source ./.venv/bin/activate  # Windows 没有 source 命令，须替换为 .\.venv\Scripts\activate
```

每当你重新打开终端并想要运行程序，都需要先激活虚拟环境。

### 安装依赖

1. pip 可以帮你安装绝大多数依赖。

   ```shell
   python -m pip install -r requirements.txt
   ```

2. 但是 `fontfallback` 需要手动安装.

   1. 假设你已经按照上述操作进入 `osuawa` 目录, 克隆这个项目的仓库。

      ```shell
      git clone https://github.com/TrueMyst/PillowFontFallback.git
      ```

   2. 将 `fontfallback` 文件夹复制到虚拟环境的 `site-packages` 目录中。

      ```shell
      cp -r ./PillowFontFallback/fontfallback/ ./.venv/lib/python3.12/site-packages/  # 在 Windows 上该目录为 .\.venv\Lib\site-packages\
      ```

   3. 如果你没有别的需求，可以删除 `PillowFontFallback` 目录。

      ```shell
      rm -r ./PillowFontFallback/
      ```

### 配置设置

1. 从 [这里](https://osu.ppy.sh/home/account/edit) 获取你的 osu! 开放授权客户端。
   端口设置须与 `./.streamlit/config.toml` 中的保持一致。

2. 在一个你喜欢的地方按下述格式创建 `osu.properties` 文件，注意文件末尾须留空行。

   ```properties
   client_id=<客户端 ID>
   client_secret=<客户端密钥>
   redirect_url=<应用回调链接>

   ```

3. 编辑 `./.streamlit/secrets.toml`.

   ```toml
   [args]
   oauth_filename = "<之前创建的 osu.properties 目录（可以使用相对目录表示）>"
   admins = []  # 匹配的用户将无需传递一次性令牌即可获得所有功能的使用权限
   ```

4. 如果你用不到 SSL，或者使用反向代理实现了这个功能，在 `./.streamlit/config.toml` 中删除与 SSL 相关的配置即可。

### 开始使用吧

```shell
# 可以直接调用 run.py
python run.py
# 或者可以用 streamlit run app.py 以应用更多启动设置
streamlit run --server.enableCORS=false --server.enableXsrfProtection=false app.py
```
