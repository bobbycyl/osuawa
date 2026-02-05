# osuawa

## 简介

我用到的和 osu! 主模式相关的工具（支持 Lazer 数据！）

现已上线工具：查成分、做课题和看记录

## 软件要求

Python 3.12、.NET 8.0

## 快速开始

### 克隆所需仓库

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
source ./.venv/bin/activate  # Windows 没有 source 命令，直接使用 .\.venv\Scripts\activate 即可
```

每当你重新打开终端并想要运行程序，都需要先激活虚拟环境。

### 安装依赖

首先，安装 Python 包。

```shell
python -m pip install -r requirements.txt
```

然后，下载并编译 `osu-tools`。

```shell
# 确保位于 osuawa 文件夹内
git clone https://github.com/ppy/osu.git
git clone https://github.com/ppy/osu-tools.git
git clone https://github.com/bobbycyl/osu-patch.git
cd osu
git checkout 2025.1007.0
git apply ../osu-patch/strain_timeline.patch
cd ../osu-tools
./UseLocalOsu.sh  # Windows 系统下请使用 .\UseLocalOsu.ps1
cd PerformanceCalculator
dotnet build -c Release
```

### 配置设置

1. 从 [官网](https://osu.ppy.sh/home/account/edit) 获取你的 osu! 开放授权客户端。
   端口设置须与 `./.streamlit/config.toml` 中的保持一致。

2. 创建并编辑 `./.streamlit/secrets.toml`，可参考 [示例文件](./.streamlit/secrets.example.toml)

3. 如果你用不到 SSL，或者使用反向代理实现了这个功能，在 `./.streamlit/config.toml` 中删除与 SSL 相关的配置即可。

### 开始使用吧

```shell
# 第一次使用建议调用 run.py 以自动补全所需资源
python run.py
# 如果是经验丰富的老手，可以用 streamlit run app.py 以应用更多启动设置
streamlit run --server.enableCORS=false --server.enableXsrfProtection=false app.py
```
