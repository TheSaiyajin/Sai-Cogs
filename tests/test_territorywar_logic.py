from territorywar.battle import apply_tick, process_battle_tick
from territorywar.mapdata import build_alpha_map, is_connected
from territorywar.minigames import compute_power_gain


def test_apply_tick_never_negative():
    atk, deff = apply_tick(50, 80, 100)
    assert atk == 0
    assert deff == 0


def test_battle_tie_defender_wins():
    battle = {
        "status": "active",
        "attacker_power": 100,
        "defender_power": 100,
    }
    out = process_battle_tick(battle, damage=100, now_ts=1000.0)
    assert out["status"] == "resolved"
    assert out["winner_side"] == "defender"
    assert out["surviving_power"] == 0


def test_adjacency_validation():
    map_def = build_alpha_map()
    assert is_connected(map_def, "a_mid", "m1") is True
    assert is_connected(map_def, "a_cap", "b_cap") is False


def test_personal_best_power_gain_delta_only():
    best, gain = compute_power_gain(0, 64)
    assert best == 64 and gain == 64

    best, gain = compute_power_gain(best, 50)
    assert best == 64 and gain == 0

    best, gain = compute_power_gain(best, 78)
    assert best == 78 and gain == 14
