import sys
import logging
from loguru import logger as lolg
from dynaconf import Dynaconf  # type:ignore

# get settings
settings = Dynaconf(
    envvar_prefix="HYLDE",
    settings_files=[
        "config.toml",
        "config.dev.toml",
        "/config/config.toml",
    ],
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


# bridge gallery-dl stdlib logging -> loguru
class _InterceptHandler(logging.Handler):
    def emit(self, record):
        if "gallery_dl" not in record.pathname:
            return
        try:
            lolg.opt(depth=6, exception=record.exc_info).log(
                record.levelname, record.getMessage()
            )
        except Exception:
            self.handleError(record)


_intercept = _InterceptHandler()
_intercept.setLevel(logging.WARNING)
logging.getLogger().addHandler(_intercept)
