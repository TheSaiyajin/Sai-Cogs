from io import BytesIO
from typing import Dict

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover
    Image = None
    ImageDraw = None
    ImageFont = None


def _owner_color(owner: int) -> tuple:
    if owner == 1:
        return (44, 122, 255)
    if owner == 2:
        return (227, 87, 65)
    return (145, 145, 145)


def render_map_png(map_def: Dict[str, object], territory_state: Dict[str, Dict[str, object]], battles: Dict[str, Dict[str, object]]) -> bytes:
    if Image is None:
        raise RuntimeError("Pillow is required for map rendering.")

    image = Image.new("RGB", (1400, 900), color=(22, 25, 31))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()

    territories = map_def["territories"]

    for left, right in map_def["edges"]:
        l = territories[left]
        r = territories[right]
        draw.line((l.x, l.y, r.x, r.y), fill=(85, 89, 98), width=3)

    for territory in territories.values():
        state = territory_state.get(territory.key, {})
        owner = int(state.get("owner", 0))

        color = _owner_color(owner)
        radius = 22 if territory.is_capital else 18
        draw.ellipse(
            (territory.x - radius, territory.y - radius, territory.x + radius, territory.y + radius),
            fill=color,
            outline=(240, 240, 240),
            width=2,
        )

        label = territory.name
        draw.text((territory.x + 24, territory.y - 8), label, fill=(235, 235, 235), font=font)
        if territory.is_capital:
            draw.text((territory.x - 10, territory.y - 36), "CAP", fill=(255, 234, 114), font=font)

    y = 15
    draw.text((15, y), f"Map: {map_def['name']}", fill=(230, 230, 230), font=font)
    y += 18
    draw.text((15, y), "Blue = Alliance 1 | Red = Alliance 2 | Gray = Neutral", fill=(220, 220, 220), font=font)
    y += 18
    active_count = sum(1 for battle in battles.values() if battle.get("status") == "active")
    draw.text((15, y), f"Active battles: {active_count}", fill=(220, 220, 220), font=font)

    y += 20
    for battle_id, battle in sorted(battles.items()):
        if battle.get("status") != "active":
            continue
        source = battle.get("source")
        target = battle.get("target")
        line = (
            f"{battle_id}: {source}->{target} "
            f"A{battle.get('attacker_alliance')} {battle.get('attacker_power')} "
            f"vs D{battle.get('defender_alliance')} {battle.get('defender_power')}"
        )
        draw.text((15, y), line, fill=(255, 221, 177), font=font)
        y += 16

    output = BytesIO()
    image.save(output, format="PNG")
    output.seek(0)
    return output.getvalue()
