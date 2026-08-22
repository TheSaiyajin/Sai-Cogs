from __future__ import annotations

import random
import re
import statistics
import time
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

SUPPORTED_SETS: Dict[str, str] = {
    "sv3pt5": "Scarlet & Violet—151",
    "sv4pt5": "Paldean Fates",
    "sv6": "Twilight Masquerade",
    "sv8": "Surging Sparks",
}

SET_ALIASES: Dict[str, str] = {
    "151": "sv3pt5",
    "sv151": "sv3pt5",
    "scarlet151": "sv3pt5",
    "scarletviolet151": "sv3pt5",
    "paldean": "sv4pt5",
    "paldeanfates": "sv4pt5",
    "twilight": "sv6",
    "twilightmasquerade": "sv6",
    "surging": "sv8",
    "surgingsparks": "sv8",
}

DEFAULT_PACK_PRICES: Dict[str, int] = {
    "sv3pt5": 1200,
    "sv4pt5": 1300,
    "sv6": 1100,
    "sv8": 1250,
}

DEFAULT_ENABLED_SETS: Dict[str, bool] = {set_id: True for set_id in SUPPORTED_SETS}

DEFAULT_MAX_BUY = 20
DEFAULT_MAX_OPEN = 20
DEFAULT_TRADE_EXPIRY_MINUTES = 15
DEFAULT_MARKET_TAX_PERCENT = 0.0

MAX_HISTORY_TRADES = 400
MAX_HISTORY_SALES = 1200

RARE_TIER_ORDER: List[str] = [
    "regular_or_holo",
    "double_rare",
    "illustration_rare",
    "ultra_rare",
    "special_illustration_rare",
    "hyper_rare",
]

# Simulated odds for the rare-or-better slot. These are intentionally configurable constants.
RARE_SLOT_ODDS: List[Tuple[str, int]] = [
    ("regular_or_holo", 70),
    ("double_rare", 16),
    ("illustration_rare", 7),
    ("ultra_rare", 4),
    ("special_illustration_rare", 2),
    ("hyper_rare", 1),
]

# Rarity normalization into pull tiers. Keep this mapping explicit for future set-specific tuning.
RARITY_TO_RARE_TIER: Dict[str, str] = {
    "rare": "regular_or_holo",
    "holo rare": "regular_or_holo",
    "rare holo": "regular_or_holo",
    "classic collection": "regular_or_holo",
    "rare ace": "regular_or_holo",
    "double rare": "double_rare",
    "shiny rare": "double_rare",
    "illustration rare": "illustration_rare",
    "ultra rare": "ultra_rare",
    "shiny ultra rare": "ultra_rare",
    "special illustration rare": "special_illustration_rare",
    "hyper rare": "hyper_rare",
}

BASIC_ENERGY_LABEL = "Basic Energy"


def normalize_set_token(token: str) -> Optional[str]:
    normalized = re.sub(r"[^a-z0-9]+", "", str(token).strip().lower())
    if not normalized:
        return None
    if normalized in SUPPORTED_SETS:
        return normalized
    return SET_ALIASES.get(normalized)


def set_display_name(set_id: str) -> str:
    return SUPPORTED_SETS.get(set_id, set_id)


def sanitize_search_text(text: str, *, max_len: int = 80) -> str:
    cleaned = re.sub(r"\s+", " ", str(text).strip())
    cleaned = re.sub(r"[^\w\s\-:#']", "", cleaned)
    return cleaned[:max_len]


def now_ts() -> float:
    return time.time()


def validate_rare_odds_total(odds: Sequence[Tuple[str, int]]) -> bool:
    return sum(weight for _tier, weight in odds) == 100


def rarity_key(rarity: Optional[str]) -> str:
    return str(rarity or "").strip().lower()


def rarity_for_display(rarity: Optional[str]) -> str:
    raw = str(rarity or "").strip()
    return raw if raw else "Unknown"


def map_rarity_to_rare_tier(rarity: Optional[str]) -> Optional[str]:
    return RARITY_TO_RARE_TIER.get(rarity_key(rarity))


def is_common_rarity(rarity: Optional[str]) -> bool:
    return rarity_key(rarity) == "common"


def is_uncommon_rarity(rarity: Optional[str]) -> bool:
    return rarity_key(rarity) == "uncommon"


def is_reverse_eligible_rarity(rarity: Optional[str]) -> bool:
    key = rarity_key(rarity)
    if key in {"common", "uncommon", "rare", "holo rare", "rare holo"}:
        return True
    return False


def build_card_snapshot(api_card: Dict) -> Optional[Dict]:
    card_id = str(api_card.get("id", "")).strip()
    card_name = str(api_card.get("name", "")).strip()
    set_data = api_card.get("set") or {}
    set_id = str(set_data.get("id", "")).strip()
    set_name = str(set_data.get("name", "")).strip()
    if not card_id or not card_name or not set_id:
        return None

    images = api_card.get("images") or {}
    snapshot = {
        "card_id": card_id,
        "name": card_name,
        "set_id": set_id,
        "set_name": set_name or set_display_name(set_id),
        "set_number": str(set_data.get("printedTotal", "")),
        "printed_number": str(api_card.get("number", "")).strip(),
        "rarity": rarity_for_display(api_card.get("rarity")),
        "artist": str(api_card.get("artist", "")).strip(),
        "small_image": str(images.get("small", "")).strip(),
        "large_image": str(images.get("large", "")).strip(),
        "api_url": f"https://api.pokemontcg.io/v2/cards/{card_id}",
    }
    return snapshot


def build_set_cache(cards: Sequence[Dict], set_id: str, set_name: str) -> Dict:
    snapshots: List[Dict] = []
    for api_card in cards:
        snapshot = build_card_snapshot(api_card)
        if snapshot is None:
            continue
        snapshots.append(snapshot)

    snapshots.sort(key=lambda c: (c.get("printed_number", ""), c.get("name", "")))
    pools = build_card_pools(snapshots)
    artwork_url = ""
    for card in snapshots:
        if card.get("large_image"):
            artwork_url = card["large_image"]
            break
    return {
        "set_id": set_id,
        "set_name": set_name,
        "updated_at": now_ts(),
        "cards": snapshots,
        "artwork_url": artwork_url,
        "counts": {
            "total": len(snapshots),
            "common": len(pools["common"]),
            "uncommon": len(pools["uncommon"]),
            "reverse_eligible": len(pools["reverse_eligible"]),
            "regular_or_holo": len(pools["rare_tiers"]["regular_or_holo"]),
            "double_rare": len(pools["rare_tiers"]["double_rare"]),
            "illustration_rare": len(pools["rare_tiers"]["illustration_rare"]),
            "ultra_rare": len(pools["rare_tiers"]["ultra_rare"]),
            "special_illustration_rare": len(pools["rare_tiers"]["special_illustration_rare"]),
            "hyper_rare": len(pools["rare_tiers"]["hyper_rare"]),
        },
    }


def build_card_pools(cards: Sequence[Dict]) -> Dict:
    common: List[Dict] = []
    uncommon: List[Dict] = []
    reverse_eligible: List[Dict] = []
    rare_tiers: Dict[str, List[Dict]] = {tier: [] for tier in RARE_TIER_ORDER}
    for card in cards:
        rarity = card.get("rarity")
        if is_common_rarity(rarity):
            common.append(card)
        if is_uncommon_rarity(rarity):
            uncommon.append(card)
        if is_reverse_eligible_rarity(rarity):
            reverse_eligible.append(card)
        rare_tier = map_rarity_to_rare_tier(rarity)
        if rare_tier:
            rare_tiers[rare_tier].append(card)
    return {
        "common": common,
        "uncommon": uncommon,
        "reverse_eligible": reverse_eligible,
        "rare_tiers": rare_tiers,
    }


def roll_weighted_rare_tier(rng: random.Random) -> str:
    roll = rng.randint(1, 100)
    position = 0
    for tier, weight in RARE_SLOT_ODDS:
        position += weight
        if roll <= position:
            return tier
    return RARE_SLOT_ODDS[-1][0]


def nearest_available_tier(requested_tier: str, pools: Dict[str, List[Dict]]) -> Optional[str]:
    if pools.get(requested_tier):
        return requested_tier
    if requested_tier not in RARE_TIER_ORDER:
        return None
    idx = RARE_TIER_ORDER.index(requested_tier)
    best: Optional[Tuple[int, int, str]] = None
    for pool_idx, tier in enumerate(RARE_TIER_ORDER):
        if not pools.get(tier):
            continue
        distance = abs(pool_idx - idx)
        candidate = (distance, pool_idx, tier)
        if best is None or candidate < best:
            best = candidate
    return best[2] if best else None


def choose_card_from_pool(rng: random.Random, pool: Sequence[Dict]) -> Optional[Dict]:
    if not pool:
        return None
    return dict(rng.choice(list(pool)))


def choose_multiple_cards(rng: random.Random, pool: Sequence[Dict], amount: int) -> List[Dict]:
    if amount <= 0 or not pool:
        return []
    available = list(pool)
    if len(available) >= amount:
        return [dict(card) for card in rng.sample(available, amount)]
    picks = [dict(card) for card in available]
    while len(picks) < amount:
        picks.append(dict(rng.choice(available)))
    return picks


def pull_finish_for_rarity(rarity: Optional[str]) -> str:
    key = rarity_key(rarity)
    if key in {"holo rare", "rare holo"}:
        return "Holo"
    return "Normal"


def format_card_line(card_instance: Dict) -> str:
    finish = card_instance.get("finish", "Normal")
    number = card_instance.get("printed_number", "?")
    rarity = rarity_for_display(card_instance.get("rarity"))
    return (
        f"`{card_instance.get('instance_id', 'N/A')}` - {card_instance.get('name', 'Unknown')} "
        f"({number}) • {rarity} • {finish}"
    )


def new_instance_id(counter: int) -> str:
    return f"TCG-{counter:010d}"


def append_bounded(history: List[Dict], item: Dict, max_items: int) -> None:
    history.append(item)
    overflow = len(history) - max_items
    if overflow > 0:
        del history[:overflow]


def trade_is_expired(trade: Dict, now: Optional[float] = None) -> bool:
    now_val = now if now is not None else now_ts()
    return now_val >= float(trade.get("expires_at", 0))


def reset_trade_confirmations(trade: Dict) -> None:
    offers = trade.get("offers", {})
    for member_key, offer in offers.items():
        if isinstance(offer, dict):
            offer["confirmed"] = False
            offers[member_key] = offer
    trade["offers"] = offers


def card_lock_owner(lock_data: Dict, instance_id: str) -> Optional[int]:
    lock = lock_data.get(instance_id)
    if not lock:
        return None
    owner_id = lock.get("owner_id")
    if owner_id is None:
        return None
    return int(owner_id)


def is_card_locked(lock_data: Dict, instance_id: str) -> bool:
    return instance_id in lock_data


def lock_card(lock_data: Dict, instance_id: str, owner_id: int, lock_type: str, ref_id: str) -> bool:
    existing = lock_data.get(instance_id)
    if existing:
        return False
    lock_data[instance_id] = {
        "owner_id": int(owner_id),
        "lock_type": str(lock_type),
        "ref_id": str(ref_id),
        "locked_at": now_ts(),
    }
    return True


def unlock_card(lock_data: Dict, instance_id: str, *, lock_type: Optional[str] = None, ref_id: Optional[str] = None) -> bool:
    existing = lock_data.get(instance_id)
    if not existing:
        return False
    if lock_type is not None and str(existing.get("lock_type")) != str(lock_type):
        return False
    if ref_id is not None and str(existing.get("ref_id")) != str(ref_id):
        return False
    del lock_data[instance_id]
    return True


def card_matches_search(card: Dict, query: str) -> bool:
    if not query:
        return True
    hay = " ".join(
        [
            str(card.get("name", "")),
            str(card.get("set_name", "")),
            str(card.get("rarity", "")),
            str(card.get("set_id", "")),
        ]
    ).lower()
    return query.lower() in hay


def summarize_sales(prices: Iterable[int]) -> Optional[Dict[str, int]]:
    values = [int(p) for p in prices if int(p) > 0]
    if not values:
        return None
    return {
        "latest": values[-1],
        "lowest": min(values),
        "highest": max(values),
        "median": int(statistics.median(values)),
    }


def calculate_market_tax(price: int, tax_percent: float) -> Tuple[int, int]:
    gross = max(0, int(price))
    tax_rate = max(0.0, min(100.0, float(tax_percent))) / 100.0
    tax = int(round(gross * tax_rate))
    payout = gross - tax
    if payout < 0:
        payout = 0
    return tax, payout


def completion_percent(unique_owned: int, set_total: int) -> float:
    if set_total <= 0:
        return 0.0
    return round((max(0, unique_owned) / set_total) * 100.0, 2)


def owned_unique_counts_by_set(card_instances: Iterable[Dict]) -> Dict[str, int]:
    per_set: Dict[str, set] = {}
    for instance in card_instances:
        set_id = str(instance.get("set_id", "")).strip()
        card_id = str(instance.get("api_card_id", "")).strip()
        if not set_id or not card_id:
            continue
        per_set.setdefault(set_id, set()).add(card_id)
    return {sid: len(values) for sid, values in per_set.items()}


def high_rarity_pull_count(card_instances: Iterable[Dict]) -> int:
    count = 0
    for instance in card_instances:
        tier = map_rarity_to_rare_tier(instance.get("rarity"))
        if tier and tier != "regular_or_holo":
            count += 1
    return count


def market_listing_is_valid_for_member_card(
    *,
    card: Optional[Dict],
    member_id: int,
    favorites: Sequence[str],
    lock_data: Dict,
    listed_card_ids: Sequence[str],
) -> Tuple[bool, str]:
    if not card:
        return False, "You do not own that card instance."
    if int(card.get("current_owner_id", 0)) != int(member_id):
        return False, "You no longer own that card."
    instance_id = str(card.get("instance_id"))
    if instance_id in set(favorites):
        return False, "Favorited cards cannot be listed. Unfavorite it first."
    if instance_id in set(listed_card_ids):
        return False, "That card is already listed."
    if is_card_locked(lock_data, instance_id):
        return False, "That card is currently locked by another transaction."
    return True, ""


def move_card_instance(card: Dict, new_owner_id: int) -> Dict:
    moved = dict(card)
    moved["current_owner_id"] = int(new_owner_id)
    return moved


def has_sufficient_balance(balance: int, offered_credits: int) -> bool:
    return int(balance) >= max(0, int(offered_credits))


def simulate_atomic_credit_exchange(
    balance_a: int,
    balance_b: int,
    credits_a_to_b: int,
    credits_b_to_a: int,
    *,
    fail_after_withdrawals: bool = False,
) -> Tuple[bool, Tuple[int, int]]:
    """
    Simulate atomic trade-credit transfer and rollback behavior.
    Returns (success, (final_balance_a, final_balance_b)).
    """
    bal_a = int(balance_a)
    bal_b = int(balance_b)
    c_a = max(0, int(credits_a_to_b))
    c_b = max(0, int(credits_b_to_a))
    if bal_a < c_a or bal_b < c_b:
        return False, (bal_a, bal_b)
    original = (bal_a, bal_b)
    bal_a -= c_a
    bal_b -= c_b
    if fail_after_withdrawals:
        return False, original
    bal_b += c_a
    bal_a += c_b
    return True, (bal_a, bal_b)
