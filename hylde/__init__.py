import sys
from loguru import logger as lolg
from dynaconf import Dynaconf  # type:ignore

# get settings
settings = Dynaconf(
    envvar_prefix="HYLDE",
    settings_files=["config.toml", "config.dev.toml"],
)

# configure Loguru
lolg.remove()
lolg.add(
    sys.stdout,
    level="DEBUG",
    colorize=True,
)
lolg.add(
    settings.logfile,
    rotation="1 MB",
    retention="7 days",
    level=settings.loglevel,
)
lolg.info(f"Writing {settings.loglevel} log to: {settings.logfile}")
