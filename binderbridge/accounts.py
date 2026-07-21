"""Compatibility facade for account-domain services.

Focused modules own each responsibility while this facade preserves the public
symbols historically imported from binderbridge.accounts and app.
"""

from binderbridge import account_admin as _account_admin
from binderbridge import account_profile as _account_profile
from binderbridge import password_recovery as _password_recovery
from binderbridge import registration_invites as _registration_invites
from binderbridge import trade_validation as _trade_validation

__binderbridge_feature_modules__ = (
    _account_profile,
    _password_recovery,
    _account_admin,
    _registration_invites,
    _trade_validation,
)

from binderbridge.account_admin import *
from binderbridge.account_profile import *
from binderbridge.password_recovery import *
from binderbridge.registration_invites import *
from binderbridge.trade_validation import *

__all__ = [
    *_account_profile.__all__,
    *_password_recovery.__all__,
    *_account_admin.__all__,
    *_registration_invites.__all__,
    *_trade_validation.__all__,
]
