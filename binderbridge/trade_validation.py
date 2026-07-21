"""Trade proposal validation.

The app facade injects shared helpers/constants into this module at import time.
"""

def validate_trade_sides(user, offered, requested):
    if not offered and not requested:
        raise ValueError("Choose at least one card for the trade.")
    if not offered or not requested:
        policy = one_way_trade_policy()
        if user_can_propose_one_way_trade(user):
            return
        if policy == "disabled":
            raise ValueError("One-directional trades are disabled by site policy. Select cards on both sides.")
        if policy == "admins":
            raise ValueError("One-directional trades can only be proposed by admins.")
        if policy == "anyone":
            raise ValueError("One-directional trades can only be proposed by active users.")
        raise ValueError("One-directional trades can only be proposed by trusted users.")

__all__ = ["validate_trade_sides"]
