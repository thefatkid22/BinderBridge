"""Configuration helpers for BinderBridge.

Values can come from a local INI file, with environment variables taking
precedence for container and hosted deployments.
"""

import configparser
import os
from pathlib import Path


CONFIG_ENV_VAR = "BINDERBRIDGE_CONFIG"
TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}

_CONFIG = None
_CONFIG_PATH = None


def _project_root():
    return Path(__file__).resolve().parent.parent


def configured_path():
    value = os.environ.get(CONFIG_ENV_VAR)
    return Path(value).expanduser() if value else None


def candidate_paths():
    configured = configured_path()
    if configured:
        return [configured]
    root = _project_root()
    return [
        root / "binderbridge.ini",
        root / "config.ini",
        root / "data" / "binderbridge.ini",
    ]


def load_config():
    global _CONFIG, _CONFIG_PATH
    if _CONFIG is not None:
        return _CONFIG
    parser = configparser.ConfigParser()
    found = None
    for path in candidate_paths():
        if path.exists():
            parser.read(path, encoding="utf-8")
            found = path
            break
    _CONFIG = parser
    _CONFIG_PATH = found
    return parser


def active_config_path():
    load_config()
    return _CONFIG_PATH


def reset_config_cache():
    global _CONFIG, _CONFIG_PATH
    _CONFIG = None
    _CONFIG_PATH = None


def _env_value(names):
    for name in names:
        value = os.environ.get(name)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return None


def _file_value(section=None, key=None):
    if not section or not key:
        return None
    parser = load_config()
    if parser.has_option(section, key):
        value = parser.get(section, key)
        if str(value).strip() != "":
            return str(value).strip()
    return None


def config_str(*names, default="", section=None, key=None):
    value = _env_value(names)
    if value is not None:
        return value
    value = _file_value(section, key)
    if value is not None:
        return value
    return default


def config_int(*names, default=0, section=None, key=None):
    value = config_str(*names, default=str(default), section=section, key=key)
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def config_float(*names, default=0.0, section=None, key=None):
    value = config_str(*names, default=str(default), section=section, key=key)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def config_bool(*names, default=False, section=None, key=None):
    value = config_str(*names, default="", section=section, key=key)
    if value == "":
        return default
    text = str(value).strip().lower()
    if text in TRUE_VALUES:
        return True
    if text in FALSE_VALUES:
        return False
    return default

