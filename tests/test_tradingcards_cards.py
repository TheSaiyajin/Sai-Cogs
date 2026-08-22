import random

from tradingcards import cards


def test_rare_odds_total_exactly_100():
    assert cards.validate_rare_odds_total(cards.RARE_SLOT_ODDS) is True
    assert sum(weight for _tier, weight in cards.RARE_SLOT_ODDS) == 100


def test_nearest_tier_fallback_when_requested_pool_missing():
    pools = {
        "regular_or_holo": [{}],
        "double_rare": [],
        "illustration_rare": [],
        "ultra_rare": [{"id": "x"}],
        "special_illustration_rare": [],
        "hyper_rare": [],
    }
    assert cards.nearest_available_tier("double_rare", pools) == "regular_or_holo"
    assert cards.nearest_available_tier("special_illustration_rare", pools) == "ultra_rare"


def test_trade_confirmation_resets():
    trade = {
        "offers": {
            "1": {"cards": ["A"], "credits": 10, "confirmed": True},
            "2": {"cards": [], "credits": 0, "confirmed": True},
        }
    }
    cards.reset_trade_confirmations(trade)
    assert trade["offers"]["1"]["confirmed"] is False
    assert trade["offers"]["2"]["confirmed"] is False


def test_locking_and_unlocking_cards():
    lock_data = {}
    assert cards.lock_card(lock_data, "TCG-1", 1, "trade", "TRD-1") is True
    assert cards.lock_card(lock_data, "TCG-1", 1, "trade", "TRD-1") is False
    assert cards.is_card_locked(lock_data, "TCG-1") is True
    assert cards.unlock_card(lock_data, "TCG-1", lock_type="trade", ref_id="TRD-1") is True
    assert cards.is_card_locked(lock_data, "TCG-1") is False


def test_market_listing_validation_and_favorites():
    card = {"instance_id": "TCG-1", "current_owner_id": 10}
    ok, _reason = cards.market_listing_is_valid_for_member_card(
        card=card,
        member_id=10,
        favorites=[],
        lock_data={},
        listed_card_ids=[],
    )
    assert ok is True
    ok, reason = cards.market_listing_is_valid_for_member_card(
        card=card,
        member_id=10,
        favorites=["TCG-1"],
        lock_data={},
        listed_card_ids=[],
    )
    assert ok is False
    assert "Favorited" in reason


def test_market_tax_calculation():
    tax, payout = cards.calculate_market_tax(1250, 10.0)
    assert tax == 125
    assert payout == 1125


def test_ownership_transfer():
    card = {"instance_id": "TCG-999", "current_owner_id": 1}
    moved = cards.move_card_instance(card, 2)
    assert moved["current_owner_id"] == 2
    assert card["current_owner_id"] == 1


def test_insufficient_balance_validation():
    assert cards.has_sufficient_balance(1000, 999) is True
    assert cards.has_sufficient_balance(1000, 1001) is False


def test_trade_expired_detection():
    trade = {"expires_at": 1000}
    assert cards.trade_is_expired(trade, now=1000) is True
    assert cards.trade_is_expired(trade, now=999.999) is False


def test_failed_credit_exchange_rolls_back():
    success, balances = cards.simulate_atomic_credit_exchange(
        1000,
        1000,
        200,
        100,
        fail_after_withdrawals=True,
    )
    assert success is False
    assert balances == (1000, 1000)


def test_success_credit_exchange():
    success, balances = cards.simulate_atomic_credit_exchange(1000, 1000, 200, 100)
    assert success is True
    assert balances == (900, 1100)


def test_weighted_roll_returns_known_tier():
    rng = random.Random(42)
    rolled = cards.roll_weighted_rare_tier(rng)
    assert rolled in {tier for tier, _weight in cards.RARE_SLOT_ODDS}
