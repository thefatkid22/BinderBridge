"""BinderBridge release metadata."""

APP_NAME = "BinderBridge"
APP_VERSION = "0.2.0-alpha.2"
RELEASE_TAG = f"v{APP_VERSION}"
DEFAULT_SOURCE_URL = "https://github.com/thefatkid22/BinderBridge"
DEFAULT_USER_AGENT = f"{APP_NAME}/{APP_VERSION} self-hosted collection manager"
__version__ = APP_VERSION

__all__ = [
    "APP_NAME",
    "APP_VERSION",
    "RELEASE_TAG",
    "DEFAULT_SOURCE_URL",
    "DEFAULT_USER_AGENT",
    "__version__",
]
