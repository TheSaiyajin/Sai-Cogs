from dataclasses import dataclass
from typing import Dict, List, Set


@dataclass(frozen=True)
class TerritoryDef:
    key: str
    name: str
    x: int
    y: int
    is_capital: bool = False
    starter_owner: int = 0


ALPHA_MAP_ID = "alpha21"


def build_alpha_map() -> Dict[str, object]:
    territories: Dict[str, TerritoryDef] = {
        "a_cap": TerritoryDef("a_cap", "A Capital", 140, 440, is_capital=True, starter_owner=1),
        "a_north": TerritoryDef("a_north", "A North", 260, 300, starter_owner=1),
        "a_mid": TerritoryDef("a_mid", "A Mid", 280, 440, starter_owner=1),
        "a_south": TerritoryDef("a_south", "A South", 260, 580, starter_owner=1),
        "n1": TerritoryDef("n1", "North Gate", 420, 250),
        "n2": TerritoryDef("n2", "North Ford", 560, 220),
        "n3": TerritoryDef("n3", "Ridge", 720, 250),
        "n4": TerritoryDef("n4", "North Pass", 880, 280),
        "n5": TerritoryDef("n5", "North Watch", 1040, 300),
        "m1": TerritoryDef("m1", "Mid Gate", 420, 440),
        "m2": TerritoryDef("m2", "Crossroads", 580, 430),
        "m3": TerritoryDef("m3", "Central Keep", 740, 430),
        "m4": TerritoryDef("m4", "Bridgefield", 900, 430),
        "m5": TerritoryDef("m5", "East Gate", 1040, 440),
        "s1": TerritoryDef("s1", "South Gate", 420, 630),
        "s2": TerritoryDef("s2", "Low Ford", 560, 660),
        "s3": TerritoryDef("s3", "Wetlands", 720, 640),
        "s4": TerritoryDef("s4", "South Pass", 880, 610),
        "s5": TerritoryDef("s5", "South Watch", 1040, 580),
        "b_north": TerritoryDef("b_north", "B North", 1160, 300, starter_owner=2),
        "b_cap": TerritoryDef("b_cap", "B Capital", 1260, 440, is_capital=True, starter_owner=2),
    }

    edges: List[tuple] = [
        ("a_cap", "a_mid"),
        ("a_cap", "a_south"),
        ("a_cap", "a_north"),
        ("a_north", "n1"),
        ("n1", "n2"),
        ("n2", "n3"),
        ("n3", "n4"),
        ("n4", "n5"),
        ("n5", "b_north"),
        ("b_north", "b_cap"),
        ("a_mid", "m1"),
        ("m1", "m2"),
        ("m2", "m3"),
        ("m3", "m4"),
        ("m4", "m5"),
        ("m5", "b_cap"),
        ("a_south", "s1"),
        ("s1", "s2"),
        ("s2", "s3"),
        ("s3", "s4"),
        ("s4", "s5"),
        ("s5", "b_cap"),
        ("n2", "m2"),
        ("m2", "s2"),
        ("n3", "m3"),
        ("m3", "s3"),
        ("n4", "m4"),
        ("m4", "s4"),
        ("a_north", "a_mid"),
        ("a_mid", "a_south"),
    ]

    adjacency: Dict[str, Set[str]] = {key: set() for key in territories}
    for left, right in edges:
        adjacency[left].add(right)
        adjacency[right].add(left)

    return {
        "map_id": ALPHA_MAP_ID,
        "name": "Alpha Frontlines",
        "territories": territories,
        "adjacency": adjacency,
        "edges": edges,
    }


def normalize_territory_key(raw: str) -> str:
    return str(raw).strip().lower().replace("-", "_").replace(" ", "_")


def is_connected(map_def: Dict[str, object], source_key: str, target_key: str) -> bool:
    adjacency = map_def["adjacency"]
    return target_key in adjacency.get(source_key, set())
