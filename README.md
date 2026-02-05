# osuawa

[**简体中文**](README_zh_CN.md)

## Introduction

just some useful tools for osu! standard (Lazer data supported!)

current tools: Score Visualizer, Playlist Generator and Recorder

## Requirements

Python 3.12, .NET 8.0

## Getting Started

### Clone this repository

```shell
git clone https://github.com/bobbycyl/osuawa.git
```

### Create and activate the virtual environment

```shell
# change to the directory
cd osuawa
# create the virtual environment
python -m venv ./.venv  # replace python with python3 or py if necessary
# activate the virtual environment
source ./.venv/bin/activate  # replace with .\.venv\Scripts\activate on Windows
```

Everytime you want to run the app, you need to activate the virtual environment first.

### Install some dependencies

Firstly, install python packages.

```shell
python -m pip install -r requirements.txt
```

Secondly, download and build `osu-tools`.

```shell
# ensure you are in the directory of osuawa
git clone https://github.com/ppy/osu.git
git clone https://github.com/ppy/osu-tools.git
git clone https://github.com/bobbycyl/osu-patch.git
cd osu
git checkout 2025.1007.0
git apply ../osu-patch/strain_timeline.patch
cd ../osu-tools
./UseLocalOsu.sh  # replace with .\UseLocalOsu.ps1 on Windows
cd PerformanceCalculator
dotnet build -c Release
```

### Configure the settings

1. Acquire your osu! OAuth client from [the official site](https://osu.ppy.sh/home/account/edit).
   The port should match which set in `./.streamlit/config.toml`.

2. Create a file named `./.streamlit/secrets.toml` and edit it.
   You can find [an example here](./.streamlit/secrets.example.toml)

3. If you do not need HTTPS, delete SSL related settings in `./.streamlit/config.toml`.

### Run the app

```shell
python run.py
# If automatic bootstrapping is not needed, use the following command instead
streamlit run --server.enableCORS=false --server.enableXsrfProtection=false app.py
```
