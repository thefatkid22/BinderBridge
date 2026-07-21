"""Compatibility facade for account and authentication route handlers."""

from binderbridge import account_auth_routes as _account_auth_routes
from binderbridge import account_settings_routes as _account_settings_routes

__binderbridge_feature_modules__ = (
    _account_auth_routes,
    _account_settings_routes,
)

from binderbridge.account_auth_routes import *
from binderbridge.account_settings_routes import *

ACCOUNT_ROUTE_METHODS = (
    *ACCOUNT_AUTH_ROUTE_METHODS,
    *ACCOUNT_SETTINGS_ROUTE_METHODS,
)

__all__ = ["ACCOUNT_ROUTE_METHODS", *ACCOUNT_ROUTE_METHODS]
