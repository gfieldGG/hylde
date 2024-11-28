from pathlib import Path
from loguru import logger as lolg
from dynaconf import Dynaconf  # type:ignore

# get settings
settings = Dynaconf(envvar_prefix="", settings_files=[Path("./config.toml").resolve()])

# configure Loguru
lolg.add(settings.logfile, rotation="1 MB", retention="7 days", level="DEBUG")
lolg.info(f"Writing log to: {settings.logfile}")
