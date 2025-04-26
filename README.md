# osuawa

[**简体中文**](README_zh_CN.md)

## Introduction

just some useful tools for osu! (Lazer data supported!)

current tools: Score Visualizer, Playlist Generator and Recorder

## Requirements

Python 3.12, Rust (for [rosu-pp-py](https://github.com/MaxOhn/rosu-pp-py))

## Getting Started

1. Clone the repository. `git clone https://github.com/bobbycyl/osuawa.git`

2. Create and activate a virtual environment.

   ```shell
   cd osuawa
   python -m venv ./.venv  # replace python with python3 or py if necessary
   source ./.venv/bin/activate  # replace with .\.venv\Scripts\activate on Windows
   ```

3. Install dependencies.

   ```shell
   # use pip to install most of the dependencies
   python -m pip install -r requirements.txt
   # manually install fontfallback for Pillow
   git clone https://github.com/TrueMyst/PillowFontFallback.git
   cp -r ./PillowFontFallback/fontfallback ./.venv/lib/python3.12/site-packages/  # replace with .\.venv\Lib\site-packages\ on Windows
   rm -r PillowFontFallback
   ```

4. Configure the settings.

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

5. Run the app. `python run.py`
