# osuawa

[**简体中文**](README_zh_CN.md)

## Introduction

just some useful tools for osu! (Lazer data supported!)

current tools: Score Visualizer, Playlist Generator and Recorder

## Requirements

Python 3.12, Rust (for [rosu-pp-py](https://github.com/MaxOhn/rosu-pp-py))

## Getting Started

### Clone the repository

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

### Install dependencies

1. Use pip to install most of the dependencies.

   ```shell
   python -m pip install -r requirements.txt
   ```

2. Install `fontfallback` for Pillow.

   1. Assuming you have entered the `osuawa` directory as described above, clone the `fontfallback` repository.

      ```shell
      git clone https://github.com/TrueMyst/PillowFontFallback.git
      ```

   2. Copy the `fontfallback` folder to the `site-packages` directory of the virtual environment.

      ```shell
      cp -r ./PillowFontFallback/fontfallback/ ./.venv/lib/python3.12/site-packages/  # replace with .\.venv\Lib\site-packages\ on Windows
      ```

   3. Remove the `PillowFontFallback` folder if you do not need it anymore.

      ```shell
      rm -r ./PillowFontFallback/
      ```

### Configure the settings

1. Acquire your osu! OAuth client from [here](https://osu.ppy.sh/home/account/edit).
   The port should match which set in `./.streamlit/config.toml`.

2. Create a file called `osu.properties` somewhere. The file should like as follows.

   ```properties
   client_id=<Client ID>
   client_secret=<Client Secret>
   redirect_url=<Application Callback URLs>

   ```

3. Edit `./.streamlit/secrets.toml`.

   ```toml
   [args]
   oauth_filename = "/path/to/osu.properties"
   admins = []  # user who match the id will auto gain the highest cmdparser permission without any need to pass the token
   ```

4. If you do not need HTTPS, delete SSL related settings in `./.streamlit/config.toml`.

### Run the app

```shell
python run.py
```
