"""Bounded Discord rendering of documented public game data."""

from datetime import datetime, timezone
import math
from typing import Any

import discord

from .api import APIError


def number(value: Any) -> str:
    if (isinstance(value, (int, float)) and not isinstance(value, bool)
            and (not isinstance(value, float) or math.isfinite(value))):
        return f"{value:,.0f}"
    return "Unavailable"


def text(value: Any, limit: int = 180) -> str:
    if value is None:
        return "Unavailable"
    return discord.utils.escape_markdown(discord.utils.escape_mentions(str(value)))[:limit]


def timestamp(value: Any) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value < 253402300800:
        return f"<t:{int(value)}:f>"
    return "Unavailable"


def mapping(data: dict, field: str) -> dict:
    value = data.get(field)
    if not isinstance(value, dict):
        raise APIError(200, "schema")
    return value


def roster(data: dict) -> list:
    members = data.get("members")
    if not isinstance(members, list) or not all(isinstance(m, dict) for m in members):
        raise APIError(200, "schema")
    return members


def records(data: Any, required: set, depth: int = 0) -> list:
    """Docs omit leaderboard/matchup envelope names; match records, not guessed wrappers."""
    if depth > 8:
        raise APIError(200, "schema")
    if isinstance(data, dict):
        if required.issubset(data):
            return [data]
        result = []
        for value in data.values():
            if isinstance(value, (dict, list)):
                result.extend(records(value, required, depth + 1))
        return result
    if isinstance(data, list):
        result = []
        for value in data:
            result.extend(records(value, required, depth + 1))
        return result
    return []


def calendar(data: dict) -> list:
    categories = data.get("categories")
    if not isinstance(categories, list):
        raise APIError(200, "schema")
    result = []
    for category in categories:
        if not isinstance(category, dict) or not isinstance(category.get("events"), list):
            raise APIError(200, "schema")
        for event in category["events"]:
            if not isinstance(event, dict):
                raise APIError(200, "schema")
            result.append(event)
    return result


def embed(title: str, data=None, description=None) -> discord.Embed:
    result = discord.Embed(title=title[:256], description=description,
                           color=discord.Color.gold())
    freshness = "MightPulse | Data may be up to 60 minutes old"
    if data:
        if data.get("cached_at") is not None:
            freshness += " | Cached: " + text(data["cached_at"], 80)
        if data.get("age_seconds") is not None:
            freshness += " | Age (seconds): " + number(data["age_seconds"])
        if data.get("fresh") is False:
            freshness += " | Upstream refresh timed out; stored data"
    result.set_footer(text=freshness[:2048])
    return result


def image(result: discord.Embed, url: Any):
    if isinstance(url, str) and url.startswith("https://") and len(url) <= 2000:
        result.set_thumbnail(url=url)


def fields(result: discord.Embed, data: dict, specs):
    for label, key, numeric in specs:
        result.add_field(name=label, value=number(data.get(key)) if numeric else text(data.get(key)))


def pages(title: str, lines: list, *, data=None) -> list:
    chunks = []
    current = ""
    for line in lines:
        line = line[:1000] + "\n"
        if len(current) + len(line) > 3500:
            chunks.append(current)
            current = ""
        current += line
    chunks.append(current or "No records available.")
    return [embed(f"{title} ({i + 1}/{len(chunks)})", data, chunk)
            for i, chunk in enumerate(chunks)]


def comparison_pages(first: dict, second: dict, stats) -> list:
    left, right = mapping(first, "player"), mapping(second, "player")
    title = f"{text(left.get('nick_name'), 90)} vs {text(right.get('nick_name'), 90)}"
    summary = embed(title)
    for label, key, _ in stats:
        if key == "power":
            label = "Total power (player profile)"
        summary.add_field(name=label, value=f"{number(left.get(key))} / {number(right.get(key))}",
                          inline=False)
    lines = [
        f"Left: {text(left.get('nick_name'))} ({text(left.get('governor_id'))}), "
        f"K{number(left.get('kid'))}",
        f"Right: {text(right.get('nick_name'))} ({text(right.get('governor_id'))}), "
        f"K{number(right.get('kid'))}",
        "Ranks are within each player's own kingdom, not global standings.",
    ]
    rankings = []
    for data in (first, second):
        ranks = data.get("ranks")
        if ranks is None:
            ranks = {}
        if not isinstance(ranks, dict):
            raise APIError(200, "schema")
        boards = ranks.get("leaderboards", [])
        if not isinstance(boards, list):
            raise APIError(200, "schema")
        indexed = {}
        for board in boards:
            if (not isinstance(board, dict) or not isinstance(board.get("name"), str)
                    or not board["name"].strip() or board["name"] in indexed):
                raise APIError(200, "schema")
            indexed[board["name"]] = board
        rankings.append((ranks, indexed))
    for label, value_key, rank_key in (
        ("Personal power", "power", "power_rank"),
        ("Kills", "kills", "kills_rank"),
        ("Town Center", "town_center_level", "town_center_rank"),
        ("Migrant score", "migrant_score", "migrant_rank"),
        ("Mystic Trial stage", "mystic_trial", "mystic_rank"),
    ):
        values = [f"{number(ranks.get(value_key))} (kingdom rank: {number(ranks.get(rank_key))})"
                  for ranks, _ in rankings]
        lines.append(f"**{label}**\nLeft: {values[0]}\nRight: {values[1]}")
    names = dict.fromkeys(list(rankings[0][1]) + list(rankings[1][1]))
    # Keep requested categories visible even when the API omits their leaderboard entries.
    normalized_names = {name.casefold().replace("_", " ") for name in names}
    for label in ("Hero Power", "Hero Total", "Master Power", "Island Prosperity"):
        if label.casefold() not in normalized_names:
            names[label] = None
    for name in names:
        values = []
        for _, boards in rankings:
            board = boards.get(name, {})
            values.append(f"{number(board.get('value'))} "
                          f"(kingdom rank: {number(board.get('kingdom_rank'))})")
        lines.append(f"**{text(name)}**\nLeft: {values[0]}\nRight: {values[1]}")
    output = [summary] + pages("Ranking comparison", lines)
    freshness = (f"MightPulse | Ages (seconds), left / right: "
                 f"{number(first.get('age_seconds'))} / {number(second.get('age_seconds'))}; "
                 "not necessarily simultaneous")
    if first.get("fresh") is False or second.get("fresh") is False:
        freshness += " | Stored upstream data; refresh timed out"
    for page in output:
        page.set_footer(text=freshness)
    return output


SCOUT_BOARDS = (
    ("alliance_power", "Strongest alliances", "Power"),
    ("personal_power", "Top 10 players", "Power"),
    ("town_center", "Top Town Center levels", "TC"),
    ("mystic_trial", "Top Mystic Trial", "Stage"),
)


def scout_rows(data: dict, board: str) -> list:
    required = {"aid", "score"} if board == "alliance_power" else {"uid", "score"}
    rows = records(data, required)
    if not rows or len(rows) > 100:
        raise APIError(200, "schema")
    identity = "aid" if board == "alliance_power" else "uid"
    ids = set()
    for row in rows:
        uid = row[identity]
        if (not isinstance(uid, int) or isinstance(uid, bool) or uid <= 0 or uid in ids
                or number(row["score"]) == "Unavailable" or row["score"] < 0):
            raise APIError(200, "schema")
        ids.add(uid)
    return sorted(rows, key=lambda row: row["score"], reverse=True)


def scout_embeds(kids: tuple, kingdoms: list, boards: list, errors: list) -> list:
    summary = embed(f"Kingdom scout: K{kids[0]} vs K{kids[1]}",
                    description="Kingdom statistics, not preparation scores or castle battle results.\n"
                    "Higher numeric value: left / right / tied; not a prediction of a KvK winner.")
    metrics = [
        ("Total power", "power"), ("Personal power total", "gov_power"),
        ("Hero power", "hero_power"), ("Hero total", "hero_total"), ("Master power", "master_power"),
        ("Troop power", "troop_power"),
        ("Pet power", "pet_power"), ("Building power", "building_power"),
        ("Research power", "research_power"), ("Governor gear power", "governor_gear_power"),
        ("Governor charm power", "governor_charm_power"), ("Kills", "kills"),
        ("Mystic Trial total (not an individual stage)", "mystic_trial"),
        ("Island prosperity", "island_prosperity"), ("Migrant score", "migrant_score"),
        ("Power gain, 7 days", "power_gain_7d"),
        ("Players on map (not recent activity)", "located"),
    ]
    for label, key in metrics:
        values = [kingdom.get(key) for kingdom in kingdoms]
        formatted = [number(value) for value in values]
        standing = "Unavailable"
        if "Unavailable" not in formatted:
            standing = "Left higher" if values[0] > values[1] else (
                "Right higher" if values[0] < values[1] else "Tied")
        summary.add_field(name=label, value=f"K{kids[0]}: {formatted[0]}\n"
                          f"K{kids[1]}: {formatted[1]}\n{standing}", inline=True)
    for count in (10, 100):
        values = []
        for indexed in boards:
            rows = indexed.get("personal_power", [])
            values.append(number(sum(row["score"] for row in rows[:count]))
                          if len(rows) >= count else f"Unavailable (need {count} ranked players)")
        summary.add_field(name=f"Top-{count} player power sum",
                          value=f"K{kids[0]}: {values[0]}\nK{kids[1]}: {values[1]}", inline=False)
    summary.add_field(name="API health label (not a verified activity assessment)",
                      value=f"K{kids[0]}: {text(kingdoms[0].get('health'))}\n"
                      f"K{kids[1]}: {text(kingdoms[1].get('health'))}", inline=False)
    output = [summary]
    for board, title, label in SCOUT_BOARDS:
        result = embed(title)
        for index, kid in enumerate(kids):
            rows = boards[index].get(board)
            if rows is None:
                value = errors[index].get(board, "Leaderboard unavailable.")
            else:
                lines = []
                for rank, row in enumerate(rows[:10], 1):
                    if board == "alliance_power":
                        name = f"[{text(row.get('abbr'), 20)}] {text(row.get('name'), 35)}"
                    else:
                        name = text(row.get("nick_name"), 50)
                    lines.append(f"{rank}. {name} - {label} {number(row['score'])}"[:95])
                value = "\n".join(lines)
            result.add_field(name=f"K{kid}", value=value[:1024], inline=True)
        output.append(result)
    for result in output:
        result.set_footer(text="MightPulse | Cached data up to 1 hour old; independent requests | "
                          "Top lists sampled from up to 100 entries per kingdom and board")
    return output


def kvk_embed(kid: int, matchup: dict, scores: dict = None, unavailable: str = None,
              updated: float = None) -> discord.Embed:
    kingdom = mapping(matchup, "kingdom")
    opponent = matchup.get("opponent")
    if kingdom.get("kid") != kid:
        raise APIError(200, "schema")
    opponent_id = opponent.get("kid") if isinstance(opponent, dict) else None
    title = f"Kingdom {kid} vs " + (str(opponent_id) if opponent_id else "unpublished opponent")
    if matchup.get("bye") is True:
        title = f"Kingdom {kid} - Bye"
    result = embed(title, description="Preparation scores only; not castle battle results.")
    result.add_field(name="Season", value=text(matchup.get("season")))
    result.add_field(name="Stage", value=text(matchup.get("stage")))
    if matchup.get("bye") is True:
        result.add_field(name="Matchup", value="No opponent this season.", inline=False)
    elif scores is None:
        result.add_field(name="Preparation scores", value=unavailable or "Not requested.", inline=False)
    else:
        if (scores.get("kingdom") != kid or scores.get("opponent") != opponent_id
                or scores.get("season") != matchup.get("season")
                or scores.get("stage") != matchup.get("stage")):
            raise APIError(200, "schema")
        ours, theirs = scores.get("score"), scores.get("opponent_score")
        result.add_field(name=f"K{kid} preparation", value=number(ours))
        result.add_field(name=f"K{opponent_id} preparation", value=number(theirs))
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (ours, theirs)):
            difference = ours - theirs
            label = "Winning" if difference > 0 else "Losing" if difference < 0 else "Tied"
            result.add_field(name="Preparation standing", value=f"{label}: {difference:+,.0f}")
        else:
            result.add_field(name="Preparation standing", value="Scores unpublished.")
        days = scores.get("days", [])
        if not isinstance(days, list) or len(days) > 5:
            raise APIError(200, "schema")
        for day in days:
            if not isinstance(day, dict):
                raise APIError(200, "schema")
            value = (f"{text(day.get('status'))}\nK{kid}: {number(day.get('kingdom_score'))}"
                     f"\nK{opponent_id}: {number(day.get('opponent_score'))}"
                     f"\nStarts {timestamp(day.get('begin_ts'))}")
            result.add_field(name=f"Prep day {text(day.get('name', day.get('day')))}",
                             value=value, inline=False)
    if updated is not None:
        result.add_field(name="Last successful API check", value=timestamp(updated), inline=False)
        result.timestamp = datetime.fromtimestamp(updated, timezone.utc)
    result.set_footer(text="MightPulse | Preparation scores refresh upstream every 5 minutes")
    return result
