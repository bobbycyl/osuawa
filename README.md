# osuawa

[**简体中文**](README_zh_CN.md)

## Introduction

just some useful tools for osu! (Lazer data supported!)

## Requirements

Python 3.12, Rust (for [rosu-pp-py](https://github.com/MaxOhn/rosu-pp-py))
and .NET 8.0 SDK (for [osu-tools](https://github.com/ppy/osu-tools))

## Getting Started

1. Get osu-tools. `git clone https://github.com/ppy/osu-tools.git`

2. Clone the repository. `git clone https://github.com/bobbycyl/osuawa.git`

3. Create and activate a virtual environment.

   ```shell
   cd osuawa
   python -m venv ./venv  # replace python with python3 or py if necessary
   source ./venv/bin/activate  # replace with .\venv\Scripts\activate on Windows
   ```

4. Install dependencies.

   ```shell
   # use pip to install most of the dependencies
   python -m pip install -r requirements.txt
   # manually install fontfallback for Pillow
   git clone https://github.com/TrueMyst/PillowFontFallback.git
   cp -r ./PillowFontFallback/fontfallback ./venv/lib/python3.12/site-packages/
   rm -r PillowFontFallback
   ```

5. Configure the settings.

   1. Acquire your osu! OAuth client from [here](https://osu.ppy.sh/home/account/edit).
      The port should match which set in `./.streamlit/config.toml`.

   2. Create a file called `osu.properties` somewhere. The file should like this:

      ```properties
      client_id=<Client ID>
      client_secret=<Client Secret>
      redirect_url=<Application Callback URLs>
      ```

   3. Edit `./.streamlit/secrets.toml`.

   4. If you do not need HTTPS, delete SSL related settings in `./.streamlit/config.toml`.

6. Run the app. `python run.py`
