from typing import Dict, List, Tuple


def apply_tick(attacker_power: int, defender_power: int, damage: int) -> Tuple[int, int]:
    dmg = max(0, int(damage))
    atk = max(0, int(attacker_power))
    deff = max(0, int(defender_power))
    atk -= min(atk, dmg)
    deff -= min(deff, dmg)
    return atk, deff


def resolve_from_powers(attacker_power: int, defender_power: int) -> Tuple[str, int]:
    atk = max(0, int(attacker_power))
    deff = max(0, int(defender_power))
    if atk > deff:
        return "attacker", atk
    return "defender", deff


def process_battle_tick(battle: Dict[str, object], damage: int, now_ts: float) -> Dict[str, object]:
    if battle.get("status") != "active":
        return battle

    atk, deff = apply_tick(battle.get("attacker_power", 0), battle.get("defender_power", 0), damage)
    battle["attacker_power"] = atk
    battle["defender_power"] = deff

    if atk <= 0 or deff <= 0:
        winner_side, survivor = resolve_from_powers(atk, deff)
        battle["status"] = "resolved"
        battle["winner_side"] = winner_side
        battle["surviving_power"] = survivor
        battle["resolved_ts"] = float(now_ts)
    return battle


def count_active_offensives(battles: Dict[str, Dict[str, object]], alliance_id: int) -> int:
    total = 0
    for battle in battles.values():
        if battle.get("status") != "active":
            continue
        if int(battle.get("attacker_alliance", 0)) == int(alliance_id):
            total += 1
    return total


def reinforcement_log_entry(alliance_id: int, amount: int, actor_id: int, now_ts: float) -> Dict[str, object]:
    return {
        "ts": float(now_ts),
        "alliance_id": int(alliance_id),
        "amount": int(amount),
        "actor_id": int(actor_id),
    }


def territory_in_active_battle(battles: Dict[str, Dict[str, object]], territory_key: str) -> bool:
    for battle in battles.values():
        if battle.get("status") != "active":
            continue
        if battle.get("source") == territory_key or battle.get("target") == territory_key:
            return True
    return False


def battle_public_summary_lines(battles: Dict[str, Dict[str, object]]) -> List[str]:
    lines: List[str] = []
    for battle_id, battle in sorted(battles.items()):
        if battle.get("status") != "active":
            continue
        lines.append(
            f"{battle_id}: {battle.get('source')} -> {battle.get('target')} | "
            f"A{battle.get('attacker_alliance')} {battle.get('attacker_power')} vs "
            f"D{battle.get('defender_alliance')} {battle.get('defender_power')}"
        )
    return lines
