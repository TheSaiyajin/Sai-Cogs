import random


PROFILE_NAMES = (
    "stable",
    "wild",
    "uptrend",
    "downtrend",
    "swing",
    "bullrun",
    "crash",
    "recovery",
    "flat",
)


PROFILE_DATA = {
    "stable": {
        "volatility": 0.0045,
        "risk": 0.85,
        "momentum": 0.45,
        "reversal_accel": 0.16,
        "drift": 0.00002,
        "bull_bias": 0.001,
    },
    "wild": {
        "volatility": 0.018,
        "risk": 1.25,
        "momentum": 0.48,
        "reversal_accel": 0.10,
        "drift": 0.0,
        "bull_bias": 0.0,
    },
    "uptrend": {
        "volatility": 0.007,
        "risk": 0.95,
        "momentum": 0.56,
        "reversal_accel": 0.09,
        "drift": 0.00018,
        "bull_bias": 0.018,
    },
    "downtrend": {
        "volatility": 0.007,
        "risk": 0.95,
        "momentum": 0.56,
        "reversal_accel": 0.09,
        "drift": -0.00018,
        "bull_bias": -0.018,
    },
    "swing": {
        "volatility": 0.010,
        "risk": 1.05,
        "momentum": 0.42,
        "reversal_accel": 0.22,
        "drift": 0.0,
        "bull_bias": 0.0,
    },
    "bullrun": {
        "volatility": 0.012,
        "risk": 1.10,
        "momentum": 0.62,
        "reversal_accel": 0.06,
        "drift": 0.00035,
        "bull_bias": 0.035,
    },
    "crash": {
        "volatility": 0.016,
        "risk": 1.20,
        "momentum": 0.45,
        "reversal_accel": 0.03,
        "drift": -0.00120,
        "bull_bias": -0.070,
    },
    "recovery": {
        "volatility": 0.008,
        "risk": 0.95,
        "momentum": 0.55,
        "reversal_accel": 0.11,
        "drift": 0.00025,
        "bull_bias": 0.020,
    },
    "flat": {
        "volatility": 0.0025,
        "risk": 0.70,
        "momentum": 0.35,
        "reversal_accel": 0.24,
        "drift": 0.0,
        "bull_bias": 0.0,
    },
}


PROFILE_TRANSITIONS = {
    "stable": [("stable", 40), ("uptrend", 22), ("downtrend", 20), ("swing", 10), ("flat", 4), ("bullrun", 2), ("crash", 2)],
    "uptrend": [("uptrend", 42), ("stable", 28), ("swing", 10), ("downtrend", 8), ("bullrun", 8), ("flat", 4)],
    "downtrend": [("downtrend", 42), ("stable", 28), ("swing", 10), ("uptrend", 8), ("crash", 8), ("flat", 4)],
    "swing": [("swing", 40), ("stable", 20), ("uptrend", 15), ("downtrend", 15), ("wild", 7), ("flat", 3)],
    "wild": [("wild", 35), ("swing", 25), ("stable", 15), ("uptrend", 10), ("downtrend", 10), ("bullrun", 3), ("crash", 2)],
    "bullrun": [("uptrend", 65), ("stable", 20), ("swing", 10), ("wild", 5)],
    "crash": [("recovery", 70), ("stable", 15), ("downtrend", 10), ("flat", 5)],
    "recovery": [("stable", 45), ("uptrend", 35), ("swing", 10), ("flat", 10)],
    "flat": [("stable", 50), ("uptrend", 15), ("downtrend", 15), ("swing", 15), ("wild", 5)],
}


def behavior_profile(profile: str):
    return PROFILE_DATA.get(profile)


def profile_transition_window_seconds():
    return random.randint(2 * 3600, 8 * 3600)


def next_profile(current_profile: str):
    options = PROFILE_TRANSITIONS.get(current_profile, PROFILE_TRANSITIONS["stable"])
    roll = random.uniform(0.0, sum(weight for _, weight in options))
    running = 0.0
    for name, weight in options:
        running += weight
        if roll <= running:
            return name
    return options[-1][0]


def detect_asset_profile(asset: dict) -> str:
    explicit_profile = str(asset.get("profile", "")).strip().lower()
    if explicit_profile in set(PROFILE_NAMES) | {"custom"}:
        return explicit_profile

    asset_volatility = round(float(asset.get("volatility", 0.0)), 4)
    asset_risk = round(float(asset.get("risk", 1.0)), 2)
    asset_momentum = round(float(asset.get("momentum", 0.6)), 4)
    asset_reversal_accel = round(float(asset.get("reversal_accel", 0.08)), 4)
    asset_drift = round(float(asset.get("drift", 0.0)), 5)
    asset_bull_bias = round(float(asset.get("bull_bias", 0.05)), 4)

    for profile_name in PROFILE_NAMES:
        profile_data = behavior_profile(profile_name)
        if profile_data is None:
            continue
        if (
            asset_volatility == round(float(profile_data.get("volatility", 0.0)), 4)
            and asset_risk == round(float(profile_data.get("risk", 1.0)), 2)
            and asset_momentum == round(float(profile_data.get("momentum", 0.6)), 4)
            and asset_reversal_accel == round(float(profile_data.get("reversal_accel", 0.08)), 4)
            and asset_drift == round(float(profile_data.get("drift", 0.0)), 5)
            and asset_bull_bias == round(float(profile_data.get("bull_bias", 0.05)), 4)
        ):
            return profile_name

    return "custom"
