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


PLAYER_COMPARISON_STATS = (
    ("personal_power", "Personal Power"),
    ("mystic_trial", "Mystic Trial Stage"),
    ("hero_total", "Hero Power"),
    ("pet_power", "Pet Power"),
    ("master_power", "Masters Power"),
)
MISSING = "\N{EM DASH}"


def compact_number(value: Any, *, stage: bool = False) -> str:
    if number(value) == "Unavailable" or value < 0:
        return MISSING
    if stage:
        return str(int(value)) if value == int(value) else MISSING
    for divisor, suffix in ((10**9, "B"), (10**6, "M"), (10**3, "K")):
        if value >= divisor:
            scaled = round(value / divisor, 2)
            if scaled >= 1000 and suffix in ("K", "M"):
                scaled /= 1000
                suffix = "M" if suffix == "K" else "B"
            return f"{scaled:.2f}".rstrip("0").rstrip(".") + suffix
    return number(value)


def comparison_text(value: Any) -> str:
    if value is None or value == "":
        return MISSING
    return discord.utils.escape_markdown(discord.utils.escape_mentions(str(value)))


def ranked_name(row: dict, *, alliance: bool = False) -> str:
    membership = row.get("alliance")
    tag = row.get("abbr")
    if tag is None and isinstance(membership, dict):
        tag = membership.get("abbr")
    name = row.get("name") if alliance else row.get("nick_name")
    return f"[{comparison_text(tag)}] {comparison_text(name)}"


def side_by_side(title: str, headings: list, columns: list) -> list:
    """Keep complete ranking lines, continuing both columns when a field fills."""
    chunks = []
    for lines in columns:
        parts, current = [], ""
        for line in lines:
            if current and len(current) + len(line) + 1 > 1024:
                parts.append(current)
                current = ""
            while len(line) > 1024:
                parts.append(line[:1024])
                line = line[1024:]
            current += ("\n" if current else "") + line
        if current:
            parts.append(current)
        chunks.append(parts or [MISSING])
    count = max(len(column) for column in chunks)
    output = []
    for index in range(count):
        result = embed(title + (f" ({index + 1}/{count})" if count > 1 else ""))
        for heading, column in zip(headings, chunks):
            result.add_field(name=heading, value=column[index] if index < len(column) else MISSING,
                             inline=True)
        output.append(result)
    return output


def player_comparison_values(data: dict) -> dict:
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
        if not isinstance(board, dict) or not isinstance(board.get("name"), str):
            raise APIError(200, "schema")
        name = " ".join(board["name"].casefold().replace("_", " ").split())
        if not name or name in indexed:
            raise APIError(200, "schema")
        indexed[name] = board.get("value")
    power = ranks.get("power")
    if power is None:
        power = mapping(data, "player").get("power")
    trial = ranks.get("mystic_trial")
    if trial is None:
        trial = indexed.get("mystic trial")
    values = {"personal_power": power, "mystic_trial": trial}
    for slug, names in (
        ("hero_total", ("hero total", "hero power")),
        ("pet_power", ("pet power",)),
        ("master_power", ("master power", "masters power")),
    ):
        values[slug] = next((indexed[name] for name in names if indexed.get(name) is not None), None)
    return values


def higher_label(values: list, index: int, *, stage: bool = False) -> str:
    if any(compact_number(value, stage=stage) == MISSING for value in values):
        return ""
    if values[0] == values[1]:
        return " (Tied)"
    return " (Higher)" if values[index] > values[1 - index] else ""


def comparison_pages(first: dict, second: dict) -> list:
    players = [mapping(data, "player") for data in (first, second)]
    for player in players:
        kid = player.get("kid")
        if kid is not None and (not isinstance(kid, int) or isinstance(kid, bool)
                                or not 1 <= kid <= 2**63 - 1):
            raise APIError(200, "schema")
    values = [player_comparison_values(data) for data in (first, second)]
    columns = []
    for index, player in enumerate(players):
        lines = [f"**{ranked_name(player)}**",
                 f"Governor ID: {comparison_text(player.get('governor_id'))}"]
        for slug, label in PLAYER_COMPARISON_STATS:
            pair = [value.get(slug) for value in values]
            stage = slug == "mystic_trial"
            lines.append(f"**{label}:** {compact_number(pair[index], stage=stage)}"
                         + higher_label(pair, index, stage=stage))
        columns.append(lines)
    headings = [f"Player {index + 1} | Kingdom #{comparison_text(player.get('kid'))}"
                for index, player in enumerate(players)]
    output = side_by_side("Player Comparison", headings, columns)
    freshness = (f"MightPulse | Ages (seconds), left / right: "
                 f"{number(first.get('age_seconds'))} / {number(second.get('age_seconds'))}; "
                 "not necessarily simultaneous")
    if first.get("fresh") is False or second.get("fresh") is False:
        freshness += " | Stored upstream data; refresh timed out"
    for page in output:
        page.set_footer(text=freshness)
    return output


SCOUT_BOARDS = (
    ("personal_power", "Top 10 Players - Personal Power", "Power"),
    ("alliance_power", "Top 10 Alliances - Alliance Power", "Power"),
    ("mystic_trial", "Top 10 Mystic Trial Stages", "Stage"),
    ("hero_total", "Top 10 Hero Power", "Power"),
    ("pet_power", "Top 10 Pet Power", "Power"),
    ("master_power", "Top 10 Masters Power", "Power"),
)


def scout_rows(data: dict, board: str) -> list:
    identity = "aid" if board == "alliance_power" else "uid"
    rows = records(data, {identity})
    if not rows:
        raise APIError(200, "schema")
    if any(identity not in row for row in records(data, {"score"})):
        raise APIError(200, "schema")
    indexed = {}
    for row in rows:
        uid = row[identity]
        score = row.get("score")
        if not isinstance(uid, int) or isinstance(uid, bool) or uid <= 0:
            raise APIError(200, "schema")
        if score is not None and (
            number(score) == "Unavailable" or score < 0
            or (board == "mystic_trial" and score != int(score))
        ):
            raise APIError(200, "schema")
        if uid in indexed:
            if row != indexed[uid]:
                raise APIError(200, "schema")
        else:
            indexed[uid] = row
            if len(indexed) > 100:
                raise APIError(200, "schema")
    return sorted(indexed.values(),
                  key=lambda row: row["score"] if row.get("score") is not None else -1,
                  reverse=True)


def scout_embeds(kids: tuple, boards: list, errors: list) -> list:
    headings = [f"Kingdom #{kid}" for kid in kids]
    output = []
    for board, title, label in SCOUT_BOARDS:
        columns = []
        for index, kid in enumerate(kids):
            rows = boards[index].get(board)
            if rows is None:
                lines = [MISSING + " " + errors[index].get(board, "Leaderboard unavailable.")]
            else:
                lines = []
                for rank, row in enumerate(rows[:10], 1):
                    if board == "alliance_power":
                        name = ranked_name(row, alliance=True)
                    else:
                        name = ranked_name(row)
                    value = compact_number(row.get("score"), stage=board == "mystic_trial")
                    position = f"{rank}." if value != MISSING else MISSING
                    note = " (rank unavailable)" if value == MISSING else ""
                    lines.append(f"{position} {name} {MISSING} {label} {value}{note}")
                if any(row.get("score") is None for row in rows):
                    lines.append("Missing scores: ranking uses available values only.")
            columns.append(lines)
        output.extend(side_by_side(title, headings, columns))
    columns = [[], []]
    for board, label in PLAYER_COMPARISON_STATS:
        values = [indexed[board][0].get("score") if indexed.get(board) else None for indexed in boards]
        stage = board == "mystic_trial"
        for index in range(2):
            columns[index].append(f"**{label} - Top Player:** "
                                  f"{compact_number(values[index], stage=stage)}"
                                  + higher_label(values, index, stage=stage))
    output.extend(side_by_side("Kingdom Statistics - Top Player (not totals)", headings, columns))
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
