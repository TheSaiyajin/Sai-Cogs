"""Offline tests using real Red/discord command classes and mocked external services."""

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from email.utils import format_datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import discord
import pytest

from kingshot import setup
from kingshot import api as api_module
from kingshot.api import APIError, BOARDS, MightyAPI, alliance_tag, identifier, retry_delay
from kingshot.kingshot import GUILD_DEFAULTS, MEMBER_DEFAULTS, PLAYER_STATS, Kingshot
from kingshot.views import (
    calendar, comparison_pages, kvk_embed, number, pages, records, roster,
    SCOUT_BOARDS, scout_embeds, scout_rows,
)


PLAYER = {
    "ok": True, "uid": 10, "governor_id": 1234, "id_type": "governor_id",
    "include": ["base", "heroes", "ranks", "gov_gear"], "fresh": False,
    "cached_at": "2026-10-30T12:00:00Z", "age_seconds": 3900,
    "player": {"uid": 10, "governor_id": 1234, "nick_name": "Governor", "kid": 570,
               "power": 12000000, "town_center_level": 30, "kills": 2000000, "vip": 9,
               "avatar_url": "https://example.com/avatar.png", "alliance": {"abbr": "VIL"}},
    "heroes": [{"id": 1, "name": "Hero", "level": 80, "stars": 5, "star_label": "5 stars",
                "power": 100000, "gear": [{"name": "Helmet", "enhancement_level": 50,
                                         "refine_level": 4, "quality_label": "Legendary"}],
                "exclusive_gear": {"name": "Widget", "level": 3}}],
    "gov_gear": {"hidden": False, "items": [{"name": "Coat", "slot": 1, "quality": 5,
                                           "tier": 2, "star": 3, "strength_level": 4,
                                           "score": 500, "combat": 20}]},
}
ALLIANCE = {
    "ok": True, "cached_at": "2026-10-30T12:00:00Z",
    "alliance": {"aid": 100, "name": "Vanguard", "abbr": "VIL", "kid": 570,
                 "power": 22000000, "count": 2, "leader_name": "Governor", "power_rank": 3},
    "members": [{"uid": 10, "governor_id": 1234, "nick_name": "Governor", "power": 12000000,
                 "town_center_level": 30, "alliance_rank_label": "R5"},
                {"uid": 20, "governor_id": 5678, "nick_name": "Member", "power": 10000000}],
}
MATCHUP = {"ok": True, "season": 22, "stage": "armament", "bye": False,
           "kingdom": {"kid": 570, "name": None}, "opponent": {"kid": 39, "name": None},
           "previous_season": 21}
SCORES = {"ok": True, "season": 22, "stage": "armament", "kingdom": 570, "opponent": 39,
          "score": 166483680, "opponent_score": 60111200,
          "days": [{"day": 1, "name": "I", "status": "In Progress",
                    "kingdom_score": 166483680, "opponent_score": 60111200,
                    "begin_ts": 1791158400, "end_ts": 1791244800}]}
EVENTS = {"ok": True, "kid": 570, "date": "2026-10-30", "count": 1,
          "categories": [{"name": "Events", "events": [
              {"name": "Hero Rally", "category": "Events", "when": "date line",
               "begin_ts": 1793500000, "end_ts": 1793600000}]}]}
USAGE = {"ok": True, "mighty": True, "minute": {"limit": 120, "used": 2, "remaining": 118,
                                               "resets_at": "2026-10-31T00:00:00Z"},
         "daily": {"limit": 10000, "used": 100, "remaining": 9900,
                   "resets_at": "2026-11-01T00:00:00Z"}}


class Response:
    def __init__(self, status=200, body=None, headers=None):
        self.status = status
        self.body = body if body is not None else {"ok": True}
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self):
        if isinstance(self.body, Exception):
            raise self.body
        return deepcopy(self.body)


class Session:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls = []
        self.closed = False

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        assert self.responses, "Unexpected API request: tests must supply a mocked response"
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    # Every Kingshot HTTP session is replaced; no test can contact MightPulse.
    monkeypatch.setattr(api_module.aiohttp, "ClientSession", lambda **kwargs: Session())


def api(responses):
    client = MightyAPI(AsyncMock(return_value="TEST-ONLY-KEY"))
    client.session = Session(responses)
    return client


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize("method,args,path,params,body", [
    ("player", (1234,), "/players/1234", {"include": "base,heroes,ranks,gov_gear"}, PLAYER),
    ("alliance", (570, "VIL"), "/alliances/570/VIL", {"include": "info,roster"}, ALLIANCE),
    ("kingdom", (570,), "/kingdoms/570", None, {"ok": True, "kingdom": {"kid": 570}}),
    ("ranks", (570, "personal_power"), "/kingdoms/570/ranks",
     {"board": "personal_power", "limit": 100}, {"ok": True, "rows": [{"uid": 10, "score": 100}]}),
    ("kvk", (570,), "/kingdoms/570/kvk", None, MATCHUP),
    ("scores", (570,), "/kingdoms/570/kvk/scores", None, SCORES),
    ("matchups", (), "/kvk/matchups", None, {"ok": True, "season": 22, "pairs": [MATCHUP]}),
    ("events", (570,), "/kingdoms/570/events", None, EVENTS),
    ("usage", (), "/usage", None, USAGE),
])
def test_documented_requests(method, args, path, params, body):
    client = api([Response(body=body)])
    result = run(getattr(client, method)(*args))
    assert result == body
    url, kwargs = client.session.calls[0]
    assert url == api_module.BASE_URL + path
    assert kwargs["params"] == params
    assert kwargs["headers"] == {"Authorization": "Bearer TEST-ONLY-KEY"}
    assert kwargs["allow_redirects"] is False


@pytest.mark.parametrize("status,code", [(400, "invalid"), (401, "auth"),
                                        (403, "forbidden"), (404, "missing"), (302, "schema")])
def test_safe_http_errors(status, code):
    client = api([Response(status, {"error": "TEST-ONLY-KEY"})])
    with pytest.raises(APIError) as error:
        run(client.player(1234))
    assert error.value.code == code
    assert "TEST-ONLY-KEY" not in str(error.value)
    assert len(client.session.calls) == 1


def test_scores_unavailable():
    client = api([Response(404, {"error": "scores_unavailable"})])
    with pytest.raises(APIError, match="not been published") as error:
        run(client.scores(570))
    assert error.value.code == "scores_unavailable"


def test_no_key():
    client = api([])
    client.token_provider = AsyncMock(return_value=None)
    with pytest.raises(APIError, match="No MightPulse key"):
        run(client.player(1234))
    assert client.session.calls == []


def test_cache_rotation_and_coalescing(monkeypatch):
    monkeypatch.setattr(api_module.asyncio, "sleep", AsyncMock())
    client = api([Response(body=PLAYER), Response(body=PLAYER)])

    async def scenario():
        results = await asyncio.gather(client.player(1234), client.player(1234))
        assert results[0] == results[1] == PLAYER
        assert len(client.session.calls) == 1
        client.token_provider = AsyncMock(return_value="ROTATED-TEST-KEY")
        await client.player(1234)
        assert len(client.session.calls) == 2
        await client.close()
        assert client.session.closed and not client._cache
    run(scenario())


def test_rate_limit_retry_and_minimum_spacing(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr(api_module.asyncio, "sleep", sleep)
    client = api([Response(429, headers={"Retry-After": "2"}), Response(body=PLAYER)])
    assert run(client.player(1234)) == PLAYER
    assert len(client.session.calls) == 2
    assert any(call.args[0] == 2 for call in sleep.call_args_list)
    assert any(call.args[0] > 1 for call in sleep.call_args_list)


def test_daily_limit_blocks_other_paths(monkeypatch):
    monkeypatch.setattr(api_module.asyncio, "sleep", AsyncMock())
    client = api([Response(429, headers={"Retry-After": "86400"})])

    async def scenario():
        for lookup in (lambda: client.player(1234), lambda: client.kingdom(570)):
            with pytest.raises(APIError) as error:
                await lookup()
            assert error.value.code == "rate_limit"
        assert len(client.session.calls) == 1
    run(scenario())


def test_usage_remains_available_when_limited(monkeypatch):
    monkeypatch.setattr(api_module.asyncio, "sleep", AsyncMock())
    client = api([Response(429, headers={"Retry-After": "86400"}), Response(body=USAGE)])

    async def scenario():
        with pytest.raises(APIError):
            await client.player(1234)
        assert await client.usage() == USAGE
    run(scenario())


def test_server_retry_after_blocks_other_requests(monkeypatch):
    monkeypatch.setattr(api_module.asyncio, "sleep", AsyncMock())
    client = api([Response(503, headers={"Retry-After": "300"})])

    async def scenario():
        for lookup in (lambda: client.player(1234), lambda: client.kingdom(570)):
            with pytest.raises(APIError) as error:
                await lookup()
            assert error.value.code == "server"
        assert len(client.session.calls) == 1
    run(scenario())


@pytest.mark.parametrize("failure", [
    Response(500), Response(503), aiohttp.ClientConnectionError("TEST-ONLY-KEY"),
    asyncio.TimeoutError(),
])
def test_bounded_retries(monkeypatch, failure):
    monkeypatch.setattr(api_module.asyncio, "sleep", AsyncMock())
    client = api([failure, failure, failure])
    with pytest.raises(APIError) as error:
        run(client.player(1234))
    assert error.value.code in ("server", "network")
    assert len(client.session.calls) == 3
    assert "TEST-ONLY-KEY" not in str(error.value)


@pytest.mark.parametrize("body", [[], {"ok": False}, {"player": {}}, ValueError("secret")])
def test_unexpected_json(body):
    with pytest.raises(APIError, match="unexpected response"):
        run(api([Response(body=body)]).player(1234))


def test_retry_after_formats():
    assert retry_delay({"Retry-After": "6"}, 100) == 6
    date = format_datetime(datetime.fromtimestamp(180, timezone.utc), usegmt=True)
    assert retry_delay({"Retry-After": date}, 100) == 80
    assert retry_delay({"X-RateLimit-Remaining-Day": "0",
                        "X-RateLimit-Reset-Day": "1000"}, 100) == 900
    assert retry_delay({"Retry-After": "invalid"}, 100) == 60
    assert retry_delay({"Retry-After": "nan"}, 100) == 60
    assert retry_delay({"Retry-After": "inf"}, 100) == 60
    assert retry_delay({"X-RateLimit-Remaining-Minute": "0",
                        "X-RateLimit-Reset-Minute": "120"}, 100) == 20


def test_cache_expiry_and_bound(monkeypatch):
    monkeypatch.setattr(api_module.asyncio, "sleep", AsyncMock())
    client = api([Response() for _ in range(258)])

    async def scenario():
        for index in range(257):
            await client.get(f"/kingdoms/{index + 1}", ttl=0)
        assert len(client._cache) == 256
        assert ("/kingdoms/1", ()) not in client._cache
        await client.get("/kingdoms/257")
        assert len(client.session.calls) == 258
    run(scenario())


def test_success_exhausting_allowance_blocks_followups(monkeypatch):
    monkeypatch.setattr(api_module.asyncio, "sleep", AsyncMock())
    client = api([Response(body=PLAYER, headers={
        "X-RateLimit-Remaining-Day": "0", "X-RateLimit-Reset-Day": "9999999999"})])

    async def scenario():
        assert await client.player(1234) == PLAYER
        assert await client.player(1234) == PLAYER  # Cached reads are still allowed.
        with pytest.raises(APIError) as error:
            await client.kingdom(570)
        assert error.value.code == "rate_limit"
        assert len(client.session.calls) == 1
    run(scenario())


@pytest.mark.parametrize("bad", [0, -1, 2**63, True, "123"])
def test_identifiers(bad):
    with pytest.raises(ValueError):
        identifier(bad)


@pytest.mark.parametrize("bad", ["a/b", "../x", "bad tag", "", "x" * 13, "tag?include=x"])
def test_tags(bad):
    with pytest.raises(ValueError):
        alliance_tag(bad)


def test_roster_calendar_and_envelope_independent_boards():
    assert len(roster(ALLIANCE)) == 2
    assert calendar(EVENTS)[0]["begin_ts"] == 1793500000
    row = {"uid": 10, "governor_id": 1234, "nick_name": "Governor", "score": 12000000}
    assert records({"ok": True, "boards": [{"entries": [row]}]}, {"uid", "score"}) == [row]
    assert records({"ok": True, "matchups": [MATCHUP]}, {"kingdom", "opponent"}) == [MATCHUP]
    assert "personal_power" in BOARDS and "pet_power" in BOARDS
    for parser, body in ((roster, {}), (calendar, {}), (roster, {"members": [None]})):
        with pytest.raises(APIError):
            parser(body)


@pytest.mark.parametrize("ours,theirs,label", [(1000, 500, "Winning: +500"),
                                              (500, 1000, "Losing: -500"), (500, 500, "Tied: +0")])
def test_scores_and_days(ours, theirs, label):
    scores = dict(SCORES, score=ours, opponent_score=theirs)
    result = kvk_embed(570, MATCHUP, scores, updated=1793400000).to_dict()
    values = "\n".join(field["value"] for field in result["fields"])
    assert label in values
    assert "166,483,680" in values
    assert "castle battle" in result["description"]
    assert any("day I" in field["name"] for field in result["fields"])


def test_missing_bye_and_mismatched_scores():
    result = kvk_embed(570, dict(MATCHUP, bye=True, opponent=None)).to_dict()
    assert "Bye" in result["title"]
    result = kvk_embed(570, MATCHUP, unavailable="Unpublished").to_dict()
    assert any(field["value"] == "Unpublished" for field in result["fields"])
    scores = dict(SCORES, score=None)
    result = kvk_embed(570, MATCHUP, scores).to_dict()
    assert any(field["value"] == "Scores unpublished." for field in result["fields"])
    with pytest.raises(APIError):
        kvk_embed(570, MATCHUP, dict(SCORES, season=21))
    assert number(None) == "Unavailable" and number(1234567) == "1,234,567"


def test_embed_pagination_limits():
    output = pages("Title", ["@everyone " + "x" * 900] * 100)
    assert len(output) > 1
    assert all(len(page.description) <= 3500 and len(page) < 6000 for page in output)


class Value:
    def __init__(self, storage, key):
        self.storage, self.key = storage, key

    async def __call__(self):
        return deepcopy(self.storage[self.key])

    async def set(self, value):
        self.storage[self.key] = deepcopy(value)


class Group:
    def __init__(self, storage, defaults=GUILD_DEFAULTS):
        self.storage = storage
        self.defaults = defaults

    async def all(self):
        return deepcopy(self.storage)

    def get_attr(self, key):
        assert key in self.defaults, f"Unregistered Config field {key}"
        return Value(self.storage, key)

    def __getattr__(self, key):
        return self.get_attr(key)

    async def clear(self):
        self.storage.clear()
        self.storage.update(deepcopy(self.defaults))


class MemoryConfig:
    def __init__(self):
        self.guilds = {}
        self.members = {}

    def register_guild(self, **kwargs):
        assert kwargs == GUILD_DEFAULTS

    def register_member(self, **kwargs):
        assert kwargs == MEMBER_DEFAULTS

    def member(self, member):
        return self.member_from_ids(member.guild.id, member.id)

    def member_from_ids(self, guild_id, member_id):
        storage = self.members.setdefault(guild_id, {}).setdefault(member_id, deepcopy(MEMBER_DEFAULTS))
        return Group(storage, MEMBER_DEFAULTS)

    async def all_members(self, guild=None):
        return deepcopy(self.members if guild is None else self.members.get(guild.id, {}))

    def guild(self, guild):
        return self.guild_from_id(guild.id)

    def guild_from_id(self, guild_id):
        return Group(self.guilds.setdefault(guild_id, deepcopy(GUILD_DEFAULTS)))

    async def all_guilds(self):
        return deepcopy(self.guilds)


@pytest.fixture
def cog_context(monkeypatch):
    config = MemoryConfig()
    monkeypatch.setattr("kingshot.kingshot.Config.get_conf", lambda *a, **kw: config)
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 99
    channel.permissions_for.return_value = SimpleNamespace(
        view_channel=True, send_messages=True, embed_links=True, read_message_history=True)
    message = SimpleNamespace(id=555, author=SimpleNamespace(id=42), edit=AsyncMock())
    channel.send = AsyncMock(return_value=message)
    channel.fetch_message = AsyncMock(return_value=message)
    guild = SimpleNamespace(id=1, me=SimpleNamespace(id=42), get_channel=lambda cid: channel)
    bot = SimpleNamespace(user=SimpleNamespace(id=42),
                          get_shared_api_tokens=AsyncMock(return_value={"api_key": "TEST-ONLY-KEY"}),
                          wait_until_red_ready=AsyncMock(),
                          cog_disabled_in_guild=AsyncMock(return_value=False),
                          get_guild=lambda gid: guild)
    cog = Kingshot(bot)
    cog.api.kvk = AsyncMock(return_value=deepcopy(MATCHUP))
    cog.api.scores = AsyncMock(return_value=deepcopy(SCORES))
    cog.api.alliance = AsyncMock(return_value=deepcopy(ALLIANCE))
    cog.api.events = AsyncMock(return_value=deepcopy(EVENTS))
    cog.api.player = AsyncMock(return_value=deepcopy(PLAYER))
    cog.api.usage = AsyncMock(return_value=deepcopy(USAGE))
    ctx = SimpleNamespace(guild=guild, bot=bot, author=SimpleNamespace(id=7, send=AsyncMock()),
                          send=AsyncMock(), tick=AsyncMock(), send_help=AsyncMock())
    config.guild(guild).storage["channel"] = channel.id
    return cog, ctx, config, channel, message


def invoke(cog, ctx, command, *args, **kwargs):
    return run(getattr(Kingshot, command).callback(cog, ctx, *args, **kwargs))


@pytest.mark.parametrize("command,args", [
    ("player", (1234,)), ("heroes", (1234,)), ("gear", (1234,)),
    ("alliance", ()), ("members", ()), ("top", ()),
    ("inactive", ()), ("kvk", ()), ("score", ()), ("kvkdays", ()),
    ("events", ()), ("usage", ()), ("status", ()), ("test_api", ()), ("remind_list", ()),
    ("progress", ()), ("changes", ()),
])
def test_command_outputs(cog_context, command, args):
    cog, ctx, _, _, _ = cog_context
    invoke(cog, ctx, command, *args)
    assert ctx.send.called
    assert "TEST-ONLY-KEY" not in str(ctx.send.call_args_list)


def test_hidden_gear_and_unavailable_heroes(cog_context):
    cog, ctx, _, _, _ = cog_context
    data = deepcopy(PLAYER)
    data["gov_gear"] = {"hidden": True, "message": "Governor hides equipment", "items": []}
    cog.api.player.return_value = data
    invoke(cog, ctx, "gear", 1234)
    assert "hidden" in ctx.send.call_args.kwargs["embed"].title
    data["heroes"] = None
    with pytest.raises(APIError):
        invoke(cog, ctx, "heroes", 1234)


def test_compare_all_rankings_and_missing_values(cog_context, monkeypatch):
    cog, ctx, _, _, _ = cog_context
    first, second = deepcopy(PLAYER), deepcopy(PLAYER)
    first["ranks"] = {"power": 12000000, "power_rank": 5, "kills": 2000000,
                      "kills_rank": 3, "mystic_trial": 800, "mystic_rank": 9,
                      "leaderboards": [
                          {"name": "Pet Power", "value": 120000, "kingdom_rank": 7},
                          {"name": "Hero Power", "value": 40000, "kingdom_rank": 2}]}
    second["ranks"] = {"mystic_trial": 700, "mystic_rank": 10, "leaderboards": [
        {"name": "Hero Power", "value": 30000, "kingdom_rank": 8},
        {"name": "Island Prosperity", "value": 5000, "kingdom_rank": 6}]}
    second["player"].update(kid=571, governor_id=5678)
    second["age_seconds"] = 100
    cog.api.player.side_effect = [first, second]
    mock_menu = AsyncMock()
    monkeypatch.setattr("kingshot.kingshot.menu", mock_menu)
    invoke(cog, ctx, "compare", 1234, 5678)
    output = mock_menu.call_args.args[1]
    description = "\n".join(page.description or "" for page in output)
    assert "Personal power" in description and "12,000,000 (kingdom rank: 5)" in description
    assert "Mystic Trial stage" in description and "800 (kingdom rank: 9)" in description
    assert "**Pet Power**\nLeft: 120,000 (kingdom rank: 7)\nRight: Unavailable" in description
    assert "**Hero Power**\nLeft: 40,000 (kingdom rank: 2)\nRight: 30,000 (kingdom rank: 8)" in description
    assert "Island Prosperity" in description and "K571" in description
    assert all("3,900 / 100" in page.footer.text for page in output)
    assert cog.api.player.await_count == 2


def test_compare_missing_rank_sections():
    output = comparison_pages(PLAYER, PLAYER, PLAYER_STATS)
    assert len(output) == 2 and len(output[0].fields) == len(PLAYER_STATS)
    assert "Unavailable" in output[1].description
    assert "own kingdom" in output[1].description


def scout_fixture_rows(board, count=100):
    if board == "alliance_power":
        return [{"aid": i + 1, "abbr": f"A{i}", "name": f"Alliance {i}", "score": i + 1}
                for i in range(count)]
    return [{"uid": i + 1, "governor_id": i + 1000, "nick_name": f"Governor {i}", "score": i + 1}
            for i in range(count)]


def test_scout_command_targets_and_request_budget(cog_context, monkeypatch):
    cog, ctx, config, _, _ = cog_context
    before = deepcopy(config.guilds)
    cog.api.kingdom = AsyncMock(side_effect=lambda kid: {
        "ok": True, "kingdom": {"kid": kid, "power": kid * 1000, "pet_power": 100,
                               "mystic_trial": 3000, "power_gain_7d": 50, "health": "F"}})
    cog.api.ranks = AsyncMock(side_effect=lambda kid, board: {
        "ok": True, "entries": scout_fixture_rows(board)})
    mock_menu = AsyncMock()
    monkeypatch.setattr("kingshot.kingshot.menu", mock_menu)
    invoke(cog, ctx, "scout", 2312, 2316)
    assert not mock_menu.called
    assert ctx.send.await_count == 5
    output = [call.kwargs["embed"] for call in ctx.send.call_args_list]
    assert len(output) == 5
    assert "2312" in output[0].title and "2316" in output[0].title
    assert any(field.name == "Pet power" for field in output[0].fields)
    assert any(field.name.startswith("Mystic Trial total") for field in output[0].fields)
    assert any("5,050" in field.value for field in output[0].fields if "Top-100" in field.name)
    assert any("955" in field.value for field in output[0].fields if "Top-10" in field.name)
    assert "Alliance 99" in output[1].fields[0].value
    assert "Governor 99" in output[2].fields[0].value
    assert "TC 100" in output[3].fields[0].value
    assert "Stage 100" in output[4].fields[0].value
    assert cog.api.kingdom.await_count == 2 and cog.api.ranks.await_count == 8
    assert config.guilds == before
    assert all(len(item) <= 6000 for item in output)
    assert all(len(field.value) <= 1024 for item in output for field in item.fields)
    assert not any("Active 7d" in field.name for item in output for field in item.fields)


def test_scout_missing_board_is_visible_without_fabricated_totals(cog_context, monkeypatch):
    cog, ctx, _, _, _ = cog_context
    cog.api.kingdom = AsyncMock(side_effect=lambda kid: {"ok": True, "kingdom": {"kid": kid}})

    async def ranks(kid, board):
        if kid == 571 and board == "mystic_trial":
            raise APIError(404, "missing")
        return {"ok": True, "rows": scout_fixture_rows(board, count=5)}
    cog.api.ranks = AsyncMock(side_effect=ranks)
    mock_menu = AsyncMock()
    monkeypatch.setattr("kingshot.kingshot.menu", mock_menu)
    invoke(cog, ctx, "scout", 570, 571)
    assert not mock_menu.called
    output = [call.kwargs["embed"] for call in ctx.send.call_args_list]
    assert "No record found" in output[4].fields[1].value
    assert all("Unavailable" in f.value for f in output[0].fields if f.name.startswith("Top-"))
    assert "Unavailable" in output[0].fields[0].value


def test_safe_board_diagnostics_exclude_response_values(cog_context):
    cog, ctx, _, _, _ = cog_context
    cog.api.ranks = AsyncMock(return_value={"ok": True, "rows": [
        {"uid": 10, "score": "123", "nick_name": "SECRET-VALUE"},
        {"uid": 10, "score": 99},
        {"governor_id": 1234, "score": None}]})
    invoke(cog, ctx, "boardtest", 570, "mystic_trial")
    message = ctx.send.call_args.args[0]
    assert "Records containing score: 3" in message
    assert "uid types: int=2, missing=1" in message
    assert "Duplicate positive integer uid records: 1" in message
    assert "Invalid or negative scores: 2" in message
    assert "Scout parser: rejected" in message
    assert "SECRET-VALUE" not in message and "1234" not in message


def test_safe_board_diagnostics_accept_valid_data(cog_context):
    cog, ctx, _, _, _ = cog_context
    cog.api.ranks = AsyncMock(return_value={"ok": True, "rows": scout_fixture_rows("mystic_trial")})
    invoke(cog, ctx, "boardtest", 570)
    assert "Scout parser: accepted 100 rows." in ctx.send.call_args.args[0]


def test_scout_schema_error_shows_owner_diagnostic(cog_context):
    cog, ctx, _, _, _ = cog_context
    cog.api.kingdom = AsyncMock(side_effect=lambda kid: {"ok": True, "kingdom": {"kid": kid}})
    cog.api.ranks = AsyncMock(return_value={"ok": True, "rows": []})
    invoke(cog, ctx, "scout", 570, 463)
    assert ctx.send.await_count == 5
    assert "ks boardtest 570 mystic_trial" in ctx.send.call_args.kwargs["embed"].fields[0].value


@pytest.mark.parametrize("status,code", [(401, "auth"), (403, "forbidden"),
                                        (429, "rate_limit"), (503, "server")])
def test_scout_fatal_api_error_does_not_look_successful(cog_context, monkeypatch, status, code):
    cog, ctx, _, _, _ = cog_context
    cog.api.kingdom = AsyncMock(side_effect=lambda kid: {"ok": True, "kingdom": {"kid": kid}})
    cog.api.ranks = AsyncMock(side_effect=APIError(status, code))
    mock_menu = AsyncMock()
    monkeypatch.setattr("kingshot.kingshot.menu", mock_menu)
    with pytest.raises(APIError):
        invoke(cog, ctx, "scout", 570, 571)
    assert not mock_menu.called


@pytest.mark.parametrize("kids", [(0, 571), (570, 0), (570, 570)])
def test_scout_invalid_ids_make_no_requests(cog_context, kids):
    cog, ctx, _, _, _ = cog_context
    cog.api.kingdom = AsyncMock()
    with pytest.raises(ValueError):
        invoke(cog, ctx, "scout", *kids)
    assert not cog.api.kingdom.called


def test_scout_rejects_wrong_kingdom_response(cog_context):
    cog, ctx, _, _, _ = cog_context
    cog.api.kingdom = AsyncMock(return_value={"ok": True, "kingdom": {"kid": 999}})
    with pytest.raises(APIError):
        invoke(cog, ctx, "scout", 570, 571)


def test_scout_real_red_argument_parsing_and_alias(cog_context, monkeypatch):
    from discord.ext.commands.view import StringView
    from redbot.core import commands

    cog, _, _, _, _ = cog_context
    cmd = Kingshot.scout
    monkeypatch.setattr(cmd, "cog", cog)
    message = MagicMock()
    message.attachments = []
    parsed = commands.Context(message=message, bot=cog.bot,
                              view=StringView("2312 2316"), command=cmd)
    run(cmd._parse_arguments(parsed))
    assert parsed.args[2:] == [2312, 2316]
    assert Kingshot.ks.get_command("comparekingdoms") is cmd


@pytest.mark.parametrize("rows", [[], [{"uid": 1, "score": None}], [{"uid": 1, "score": -1}],
                                  [{"uid": 1, "score": float("nan")}],
                                  [{"uid": 1, "score": 1}, {"uid": 1, "score": 2}]])
def test_scout_rejects_invalid_rank_samples(rows):
    with pytest.raises(APIError):
        scout_rows({"ok": True, "rows": rows}, "personal_power")


def test_scout_long_names_all_ten_rows_fit_and_higher_labels():
    boards = {board: scout_rows({"rows": scout_fixture_rows(board)}, board)
              for board, _, _ in SCOUT_BOARDS}
    for values in boards.values():
        for row in values:
            row.update(nick_name="@everyone " * 20, name="Long name " * 20)
    output = scout_embeds((570, 571), [{"power": 100}, {"power": 50}], [boards, boards], [{}, {}])
    assert "Left higher" in output[0].fields[0].value
    for card in output[1:]:
        assert all("10. " in field.value and len(field.value) <= 1024 for field in card.fields)


def test_compare_large_ranking_list_is_paginated_without_lost_records():
    first = deepcopy(PLAYER)
    first["ranks"] = {"leaderboards": [
        {"name": f"Board {i}", "value": i, "kingdom_rank": i + 1} for i in range(100)]}
    output = comparison_pages(first, PLAYER, PLAYER_STATS)
    assert len(output) > 2
    descriptions = "\n".join(page.description or "" for page in output)
    assert all(f"**Board {i}**" in descriptions for i in range(100))
    assert all(len(page) <= 6000 for page in output)
    assert all(len(page.description or "") <= 3500 for page in output)


@pytest.mark.parametrize("ranks", [[], {"leaderboards": None}, {"leaderboards": [None]},
                                   {"leaderboards": [{"value": 1}]},
                                   {"leaderboards": [{"name": "X"}, {"name": "X"}]}])
def test_compare_malformed_rankings_are_explicit_errors(ranks):
    first = dict(PLAYER, ranks=ranks)
    with pytest.raises(APIError):
        comparison_pages(first, PLAYER, PLAYER_STATS)


def test_ranks_kingdom_and_matchup_commands(cog_context):
    cog, ctx, _, _, _ = cog_context
    cog.api.ranks = AsyncMock(return_value={"ok": True, "boards": [{"rows": [
        {"uid": 10, "governor_id": 1234, "nick_name": "Governor", "score": 12000000}]}]})
    invoke(cog, ctx, "ranks", "personal_power")
    assert "12,000,000" in ctx.send.call_args.kwargs["embed"].description
    cog.api.kingdom = AsyncMock(return_value={"ok": True, "kingdom": {"kid": 570, "power": 900000000}})
    invoke(cog, ctx, "kingdom")
    assert any(f.value == "900,000,000" for f in ctx.send.call_args.kwargs["embed"].fields)
    cog.api.matchups = AsyncMock(return_value={"ok": True, "season": 22, "matchups": [MATCHUP]})
    invoke(cog, ctx, "matchups")
    assert "K570 vs K39" in ctx.send.call_args.kwargs["embed"].description


@pytest.mark.parametrize("command", ["alliance", "members", "top"])
@pytest.mark.parametrize("override", [False, True])
def test_alliance_lookup_target_and_unchanged_settings(cog_context, command, override):
    cog, ctx, config, _, _ = cog_context
    before = deepcopy(config.guilds)
    args = (571, "AbC") if override else ()
    data = deepcopy(ALLIANCE)
    data["alliance"].update(kid=571 if override else 570, abbr="AbC" if override else "VIL")
    cog.api.alliance.return_value = data
    invoke(cog, ctx, command, *args)
    cog.api.alliance.assert_awaited_once_with(571 if override else 570, "AbC" if override else "VIL")
    assert ctx.send.called and config.guilds == before
    if command != "alliance":
        title = ctx.send.call_args.kwargs["embed"].title
        assert str(data["alliance"]["kid"]) in title and data["alliance"]["abbr"] in title


@pytest.mark.parametrize("command", ["kingdom", "kvk", "score", "kvkdays", "events", "ranks"])
@pytest.mark.parametrize("override", [False, True])
def test_kingdom_lookup_target_and_unchanged_settings(cog_context, command, override):
    cog, ctx, config, _, _ = cog_context
    before = deepcopy(config.guilds)
    kid = 571 if override else 570
    matchup = deepcopy(MATCHUP)
    matchup["kingdom"]["kid"] = kid
    cog.api.kvk.return_value = matchup
    cog.api.scores.return_value = dict(SCORES, kingdom=kid)
    cog.api.kingdom = AsyncMock(return_value={"ok": True, "kingdom": {"kid": kid}})
    cog.api.ranks = AsyncMock(return_value={"ok": True, "rows": [
        {"uid": 10, "nick_name": "Governor", "score": 1000}]})
    args = (kid,) if override else ()
    if command == "ranks":
        args = ("personal_power", kid) if override else ()
    invoke(cog, ctx, command, *args)
    if command in ("kvk", "score", "kvkdays"):
        cog.api.kvk.assert_awaited_once_with(kid)
        if command != "kvk":
            cog.api.scores.assert_awaited_once_with(kid)
    elif command == "ranks":
        cog.api.ranks.assert_awaited_once_with(kid, "personal_power")
    else:
        getattr(cog.api, command).assert_awaited_once_with(kid)
    assert str(kid) in ctx.send.call_args.kwargs["embed"].title
    assert config.guilds == before


@pytest.mark.parametrize("command", ["alliance", "members", "top"])
@pytest.mark.parametrize("args", [(571,), (None, "ABC"), (0, "ABC"), (571, "../ABC"),
                                   (571, "Alliance Full Name")])
def test_invalid_alliance_override_never_requests_or_changes_settings(cog_context, command, args):
    cog, ctx, config, _, _ = cog_context
    before = deepcopy(config.guilds)
    with pytest.raises(ValueError):
        invoke(cog, ctx, command, *args)
    assert not cog.api.alliance.called and not ctx.send.called
    assert config.guilds == before


@pytest.mark.parametrize("command", ["kingdom", "kvk", "score", "kvkdays", "events", "ranks"])
def test_invalid_kingdom_override_never_requests(cog_context, command):
    cog, ctx, config, _, _ = cog_context
    before = deepcopy(config.guilds)
    args = ("personal_power", 0) if command == "ranks" else (0,)
    with pytest.raises(ValueError):
        invoke(cog, ctx, command, *args)
    assert not cog.api.kvk.called and not cog.api.scores.called and not cog.api.events.called
    assert not ctx.send.called and config.guilds == before


@pytest.mark.parametrize("command,arguments,expected", [
    ("kingdom", "571", [571]), ("kingdom", "", [None]),
    ("alliance", "571 AbC", [571, "AbC"]), ("alliance", "", [None, None]),
    ("members", "571 AbC", [571, "AbC"]), ("top", "571 AbC", [571, "AbC"]),
    ("ranks", "personal_power 571", ["personal_power", 571]),
    ("ranks", "kills", ["kills", None]), ("ranks", "", ["personal_power", None]),
    ("kvk", "571", [571]), ("score", "571", [571]), ("kvkdays", "571", [571]),
    ("events", "571", [571]),
])
def test_red_parses_cross_kingdom_command_arguments(cog_context, monkeypatch,
                                                  command, arguments, expected):
    from discord.ext.commands.view import StringView
    from redbot.core import commands

    cog, ctx, _, _, _ = cog_context
    cmd = getattr(Kingshot, command)
    monkeypatch.setattr(cmd, "cog", cog)
    message = MagicMock()
    message.attachments = []
    parsed = commands.Context(message=message, bot=cog.bot,
                              view=StringView(arguments), command=cmd)
    run(cmd._parse_arguments(parsed))
    assert parsed.args[2:] == expected


@pytest.mark.parametrize("command,arguments", [
    ("kingdom", "not-an-id"), ("kingdom", "571 extra"), ("kvk", "ABC"),
    ("score", "ABC"), ("kvkdays", "ABC"), ("events", "ABC"),
    ("ranks", "personal_power not-an-id"), ("alliance", "571 ABC extra"),
])
def test_red_rejects_invalid_or_extra_arguments(cog_context, monkeypatch, command, arguments):
    from discord.ext.commands.view import StringView
    from redbot.core import commands

    cog, _, _, _, _ = cog_context
    cmd = getattr(Kingshot, command)
    monkeypatch.setattr(cmd, "cog", cog)
    message = MagicMock()
    message.attachments = []
    parsed = commands.Context(message=message, bot=cog.bot,
                              view=StringView(arguments), command=cmd)
    with pytest.raises(commands.UserInputError):
        run(cmd._parse_arguments(parsed))


def test_dashboard_updates_and_persists(cog_context):
    cog, ctx, config, channel, message = cog_context

    async def scenario():
        await cog._refresh(ctx.guild)
        assert channel.send.await_count == 1
        assert config.guilds[1]["message_id"] == 555
        assert config.guilds[1]["auto_success"] > 0
        reloaded = Kingshot(cog.bot)
        reloaded.api.kvk, reloaded.api.scores = cog.api.kvk, cog.api.scores
        await asyncio.gather(reloaded._refresh(ctx.guild), reloaded._refresh(ctx.guild))
        assert channel.send.await_count == 1
        assert message.edit.await_count == 2
        assert config.guilds[1]["message_id"] == 555
    run(scenario())


def test_dashboard_recovers_unpersisted_message(cog_context):
    cog, ctx, config, channel, message = cog_context
    recovered_embed = kvk_embed(570, MATCHUP, SCORES)
    recovered_embed.set_footer(text="MightPulse | Kingshot dashboard | Guild 1")
    message.embeds = [recovered_embed]

    async def history(**kwargs):
        yield message
    channel.history = history
    run(cog._refresh(ctx.guild))
    assert not channel.send.called
    assert message.edit.await_count == 1
    assert config.guilds[1]["message_id"] == 555


def not_found():
    return discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "deleted")


def forbidden():
    return discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "missing permissions")


@pytest.mark.parametrize("during", ["fetch", "edit"])
def test_dashboard_deleted_message(cog_context, during):
    cog, ctx, config, channel, message = cog_context
    config.guilds[1]["message_id"] = 555
    if during == "fetch":
        channel.fetch_message.side_effect = not_found()
    else:
        message.edit.side_effect = not_found()
    run(cog._refresh(ctx.guild))
    assert channel.send.await_count == 1
    assert config.guilds[1]["message_id"] == 555


def test_dashboard_forbidden_does_not_duplicate(cog_context):
    cog, ctx, config, channel, _ = cog_context
    config.guilds[1]["message_id"] = 555
    channel.fetch_message.side_effect = forbidden()
    with pytest.raises(discord.Forbidden):
        run(cog._refresh(ctx.guild))
    assert not channel.send.called
    assert config.guilds[1]["auto_error"] == "Discord permissions are missing."
    assert config.guilds[1]["auto_success"] == 0


def test_scores_unavailable_and_bye_no_score_request(cog_context):
    cog, ctx, config, _, _ = cog_context
    cog.api.scores.side_effect = APIError(404, "scores_unavailable")
    run(cog._refresh(ctx.guild))
    assert config.guilds[1]["auto_error"] is None
    cog.api.kvk.return_value = dict(MATCHUP, bye=True, opponent=None)
    cog.api.scores.reset_mock()
    invoke(cog, ctx, "score")
    assert not cog.api.scores.called


def test_snapshots_progress_changes_and_partial_guard(cog_context):
    cog, ctx, config, _, _ = cog_context

    async def scenario():
        settings = await config.guild(ctx.guild).all()
        await cog._snapshot(ctx.guild, settings)
        next_data = deepcopy(ALLIANCE)
        next_data["cached_at"] = "2026-10-30T13:00:00Z"
        next_data["members"][0]["power"] += 500
        next_data["members"][1].update(uid=30, nick_name="Newcomer")
        cog.api.alliance.return_value = next_data
        await cog._snapshot(ctx.guild, await config.guild(ctx.guild).all())
        await Kingshot.progress.callback(cog, ctx)
        assert "+500" in ctx.send.call_args.kwargs["embed"].description
        await Kingshot.changes.callback(cog, ctx)
        description = ctx.send.call_args.kwargs["embed"].description
        assert "Joined: Newcomer" in description and "Departed: Member" in description
        await cog._snapshot(ctx.guild, await config.guild(ctx.guild).all())
        assert len(config.guilds[1]["snapshots"]) == 2
        next_data["members"] = []
        with pytest.raises(APIError):
            await cog._snapshot(ctx.guild, await config.guild(ctx.guild).all())
    run(scenario())


def test_guild_settings_and_minimum_interval(cog_context):
    cog, ctx, config, _, _ = cog_context
    invoke(cog, ctx, "set_kingdom", 571)
    invoke(cog, ctx, "set_alliance", "Vil")
    assert config.guilds[1]["kingdom"] == 571 and config.guilds[1]["alliance"] == "Vil"
    assert run(config.guild_from_id(2).kingdom()) == 570
    for command in ("set_interval", "auto_interval"):
        with pytest.raises(ValueError):
            invoke(cog, ctx, command, 4)
        invoke(cog, ctx, command, 5)
        assert config.guilds[1]["interval"] == 5


def test_administrator_and_channel_commands(cog_context):
    cog, ctx, config, channel, _ = cog_context
    for command, key in (("set_channel", "channel"), ("auto_channel", "auto_channel"),
                         ("remind_channel", "remind_channel")):
        invoke(cog, ctx, command, channel)
        assert config.guilds[1][key] == 99
    invoke(cog, ctx, "auto_enable")
    assert config.guilds[1]["auto_enabled"] is True
    invoke(cog, ctx, "auto_refresh")
    invoke(cog, ctx, "auto_disable")
    assert config.guilds[1]["auto_enabled"] is False
    invoke(cog, ctx, "remind_enable")
    assert config.guilds[1]["remind_enabled"] is True
    invoke(cog, ctx, "remind_disable")
    assert config.guilds[1]["remind_enabled"] is False


def test_enable_failure_never_reports_success(cog_context):
    cog, ctx, config, _, _ = cog_context
    cog.api.kvk.side_effect = APIError(403, "forbidden")
    with pytest.raises(APIError):
        invoke(cog, ctx, "auto_enable")
    assert config.guilds[1]["auto_enabled"] is False and not ctx.tick.called
    cog.api.events.side_effect = APIError(403, "forbidden")
    with pytest.raises(APIError):
        invoke(cog, ctx, "remind_enable")
    assert config.guilds[1]["remind_enabled"] is False and not ctx.tick.called


def test_missing_channel_permissions(cog_context):
    cog, ctx, _, channel, _ = cog_context
    channel.permissions_for.return_value.embed_links = False
    with pytest.raises(ValueError, match="permissions"):
        invoke(cog, ctx, "auto_channel", channel)
    assert not ctx.tick.called


def test_manual_schedule_and_offsets(cog_context):
    cog, ctx, config, _, _ = cog_context
    begin = int(api_module.time.time()) + 86400
    invoke(cog, ctx, "remind_add", begin, name="Bear Trap")
    assert config.guilds[1]["manual_events"][0]["category"] == "Manual alliance event"
    invoke(cog, ctx, "remind_offsets", 24, 1, 1)
    assert config.guilds[1]["remind_offsets"] == [24, 1]
    invoke(cog, ctx, "remind_remove", begin, name="Bear Trap")
    assert config.guilds[1]["manual_events"] == []
    with pytest.raises(ValueError):
        invoke(cog, ctx, "remind_remove", begin, name="Bear Trap")
    with pytest.raises(ValueError):
        invoke(cog, ctx, "remind_add", 1, name="Past event")
    with pytest.raises(ValueError):
        invoke(cog, ctx, "remind_offsets", 0)
    with pytest.raises(ValueError):
        invoke(cog, ctx, "remind_offsets")


def test_command_error_messages_are_safe(cog_context):
    from redbot.core import commands

    cog, ctx, _, _, _ = cog_context
    run(cog.cog_command_error(ctx, APIError(401, "auth")))
    assert "rejected the key" in ctx.send.call_args.args[0]
    run(cog.cog_command_error(ctx, RuntimeError("TEST-ONLY-KEY")))
    assert "TEST-ONLY-KEY" not in str(ctx.send.call_args_list)
    run(cog.cog_command_error(ctx, commands.TooManyArguments()))
    assert "extra argument" in ctx.send.call_args.args[0]


def test_secure_modal_no_secret_argument(cog_context, monkeypatch):
    cog, ctx, _, _, _ = cog_context
    view = MagicMock()
    monkeypatch.setattr("kingshot.kingshot.SetApiView", lambda **kwargs: view)
    invoke(cog, ctx, "apikey")
    assert ctx.author.send.call_args.kwargs["view"] is view
    assert not cog.api.player.called
    assert list(Kingshot.apikey.clean_params) == []


def test_real_secure_view_is_stopped_on_unload(cog_context):
    cog, ctx, _, _, _ = cog_context

    async def scenario():
        await Kingshot.apikey.callback(cog, ctx)
        view = ctx.author.send.call_args.kwargs["view"]
        assert view.default_service == "mightpulse" and view.default_keys == {"api_key": ""}
        await cog.cog_unload()
        assert view.is_finished() and not cog._token_views
    run(scenario())


def test_reminders_restart_dedup_and_manual_events(cog_context):
    cog, ctx, config, channel, _ = cog_context
    begin = EVENTS["categories"][0]["events"][0]["begin_ts"]

    async def scenario():
        now = begin - 3600 + 5
        settings = await config.guild(ctx.guild).all()
        await cog._reminders(ctx.guild, settings, now)
        assert channel.send.await_count == 1
        assert len(config.guilds[1]["reminder_sent"]) == 1
        reloaded = Kingshot(cog.bot)
        reloaded.api.events = cog.api.events
        await reloaded._reminders(ctx.guild, await config.guild(ctx.guild).all(), now + 60)
        assert channel.send.await_count == 1
        settings = await config.guild(ctx.guild).all()
        settings["manual_events"] = [{"name": "Bear Trap", "category": "Manual alliance event",
                                      "begin_ts": begin}]
        await reloaded._reminders(ctx.guild, settings, now + 61)
        assert channel.send.await_count == 2
        assert "Manual alliance event" in channel.send.call_args.kwargs["embed"].description
    run(scenario())


def test_reminders_no_catchup_and_failed_reservation(cog_context):
    cog, ctx, config, channel, _ = cog_context
    begin = EVENTS["categories"][0]["events"][0]["begin_ts"]

    async def scenario():
        settings = await config.guild(ctx.guild).all()
        await cog._reminders(ctx.guild, settings, begin - 3600 + 601)
        assert not channel.send.called
        channel.send.side_effect = forbidden()
        with pytest.raises(discord.Forbidden):
            await cog._reminders(ctx.guild, settings, begin - 3600 + 5)
        assert not config.guilds[1]["reminder_sent"]
    run(scenario())


def test_ambiguous_reminder_delivery_keeps_reservation(cog_context):
    cog, ctx, config, channel, _ = cog_context
    begin = EVENTS["categories"][0]["events"][0]["begin_ts"]
    channel.send.side_effect = discord.HTTPException(
        SimpleNamespace(status=500, reason="Server Error"), "unknown delivery state")

    async def scenario():
        with pytest.raises(discord.HTTPException):
            await cog._reminders(ctx.guild, await config.guild(ctx.guild).all(), begin - 3595)
        assert len(config.guilds[1]["reminder_sent"]) == 1
        await cog._reminders(ctx.guild, await config.guild(ctx.guild).all(), begin - 3500)
        assert channel.send.await_count == 1
    run(scenario())


def test_background_cadence_failure_and_disable(cog_context):
    cog, ctx, config, _, _ = cog_context
    settings = config.guilds[1]
    settings.update(auto_enabled=True, remind_enabled=True)

    async def scenario():
        now = 1793400000
        cog.api.alliance.side_effect = APIError(401, "auth")
        await cog._tick_guild(ctx.guild, now)
        assert settings["snapshot_error"] is not None
        assert settings["auto_success"] > 0
        counts = (cog.api.alliance.await_count, cog.api.kvk.await_count, cog.api.events.await_count)
        await cog._tick_guild(ctx.guild, now + 60)
        assert counts == (cog.api.alliance.await_count, cog.api.kvk.await_count, cog.api.events.await_count)
        settings["auto_enabled"] = settings["remind_enabled"] = False
        await cog._tick_guild(ctx.guild, now + 600)
        assert counts == (cog.api.alliance.await_count, cog.api.kvk.await_count, cog.api.events.await_count)
    run(scenario())


def test_scheduler_respects_red_guild_disable(cog_context, monkeypatch):
    cog, _, _, _, _ = cog_context
    cog.bot.cog_disabled_in_guild.return_value = True
    cog._tick_guild = AsyncMock()
    monkeypatch.setattr(api_module.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        run(cog._run())
    assert cog.bot.wait_until_red_ready.await_count == 1
    assert not cog._tick_guild.called


def test_setup_load_reload_and_cleanup(cog_context):
    cog, ctx, config, _, _ = cog_context
    bot = cog.bot
    config.guilds.clear()
    loaded = []

    async def add_cog(instance):
        # Red calls cog_load through discord.py's cog injection.
        await instance.cog_load()
        loaded.append(instance)
    bot.add_cog = add_cog

    async def scenario():
        await setup(bot)
        instance = loaded[-1]
        worker = instance._worker
        await asyncio.sleep(0)
        assert worker is not None and not worker.done()
        await instance.cog_load()
        assert instance._worker is worker
        await instance.cog_unload()
        assert worker.done() and instance._worker is None and instance.api.session.closed
        await setup(bot)
        assert loaded[-1] is not instance and loaded[-1]._worker is not worker
        await loaded[-1].cog_unload()
        assert loaded[-1].api.session.closed
    run(scenario())


def test_setup_failure_closes_session(cog_context):
    cog, _, _, _, _ = cog_context
    captured = []

    async def failure(instance):
        captured.append(instance)
        await instance.cog_load()
        raise RuntimeError("load failed")
    cog.bot.add_cog = failure
    with pytest.raises(RuntimeError, match="load failed"):
        run(setup(cog.bot))
    assert captured[0]._worker is None
    assert captured[0].api.session.closed


class LinkRole:
    def __init__(self, role_id, position, permissions=None, managed=False, default=False):
        self.id, self.position = role_id, position
        self.permissions = permissions or discord.Permissions.none()
        self.managed, self.default = managed, default

    def __ge__(self, other):
        return self.position >= other.position

    def is_default(self):
        return self.default


@pytest.fixture
def linking_context(cog_context):
    cog, ctx, config, _, _ = cog_context
    guild = ctx.guild
    linked = LinkRole(101, 1, discord.Permissions(view_channel=True, send_messages=True))
    alliance = LinkRole(102, 2)
    ordinary = LinkRole(103, 1)
    guild_roles = {r.id: r for r in (linked, alliance, ordinary)}
    guild.get_role = guild_roles.get
    guild.owner_id = 999
    guild.me.guild_permissions = discord.Permissions(manage_nicknames=True, manage_roles=True)
    guild.me.top_role = LinkRole(200, 10)
    member = SimpleNamespace(id=7, guild=guild, nick="Previous nickname", roles=[ordinary],
                             top_role=LinkRole(150, 5), send=AsyncMock())

    async def add_roles(role, **kwargs):
        if role not in member.roles:
            member.roles.append(role)

    async def remove_roles(role, **kwargs):
        member.roles = [r for r in member.roles if r.id != role.id]

    async def edit(**kwargs):
        member.nick = kwargs["nick"]
    member.add_roles = AsyncMock(side_effect=add_roles)
    member.remove_roles = AsyncMock(side_effect=remove_roles)
    member.edit = AsyncMock(side_effect=edit)
    guild.fetch_member = AsyncMock(return_value=member)
    ctx.author = member
    config.guilds[1].update(linked_role=101, alliance_role=102)
    return cog, ctx, config, guild_roles


def test_link_sync_and_unlink(linking_context):
    cog, ctx, config, _ = linking_context
    invoke(cog, ctx, "link", 1234)
    state = run(config.member(ctx.author).all())
    assert state["governor_id"] == 1234 and state["linked_at"] > 0
    assert state["assigned_roles"] == [101, 102]
    assert state["original_nick"] == "Previous nickname" and state["applied_nick"] == "Governor"
    assert ctx.author.nick == "Governor" and {r.id for r in ctx.author.roles} == {101, 102, 103}
    assert "UNVERIFIED" in ctx.send.call_args.kwargs["embed"].title
    assert "does not verify" in ctx.send.call_args.kwargs["embed"].description
    cog.api.player.return_value["player"]["nick_name"] = "New game name"
    invoke(cog, ctx, "sync")
    assert ctx.author.nick == "New game name"
    assert ctx.author.add_roles.await_count == 2
    invoke(cog, ctx, "unlink")
    assert ctx.author.nick == "Previous nickname" and [r.id for r in ctx.author.roles] == [103]
    assert run(config.member(ctx.author).all()) == MEMBER_DEFAULTS


@pytest.mark.parametrize("kid,tag,expected", [(570, "VIL", {101, 102, 103}),
                                            (571, "VIL", {101, 103}),
                                            (570, "Vil", {101, 103}), (570, None, {101, 103})])
def test_link_alliance_roles_match_exact_kingdom_and_tag(linking_context, kid, tag, expected):
    cog, ctx, _, _ = linking_context
    cog.api.player.return_value["player"].update(kid=kid, alliance={"abbr": tag} if tag else None)
    invoke(cog, ctx, "link", 1234)
    assert {r.id for r in ctx.author.roles} == expected


def test_sync_removes_departed_alliance_role(linking_context):
    cog, ctx, config, _ = linking_context
    invoke(cog, ctx, "link", 1234)
    cog.api.player.return_value["player"]["alliance"] = None
    invoke(cog, ctx, "sync")
    assert {r.id for r in ctx.author.roles} == {101, 103}
    assert run(config.member(ctx.author).assigned_roles()) == [101]


def test_preexisting_roles_and_manual_nickname_are_preserved(linking_context):
    cog, ctx, config, roles = linking_context
    ctx.author.roles.append(roles[101])
    invoke(cog, ctx, "link", 1234)
    assert run(config.member(ctx.author).assigned_roles()) == [102]
    ctx.author.nick = "Moderator-changed nickname"
    invoke(cog, ctx, "unlink")
    assert ctx.author.nick == "Moderator-changed nickname"
    assert {r.id for r in ctx.author.roles} == {101, 103}


def test_disabled_nickname_and_roles(linking_context):
    cog, ctx, config, _ = linking_context
    config.guilds[1].update(linked_role=None, alliance_role=None, link_nickname=False)
    invoke(cog, ctx, "link", 1234)
    assert ctx.author.nick == "Previous nickname"
    assert not ctx.author.edit.called and not ctx.author.add_roles.called
    assert run(config.member(ctx.author).governor_id()) == 1234


def test_disable_nickname_restores_on_sync(linking_context):
    cog, ctx, config, _ = linking_context
    invoke(cog, ctx, "link", 1234)
    invoke(cog, ctx, "linkset_nickname", False)
    invoke(cog, ctx, "sync")
    assert ctx.author.nick == "Previous nickname"
    assert run(config.member(ctx.author).applied_nick()) is None


@pytest.mark.parametrize("failure", [APIError(401, "auth"), APIError(404, "missing")])
def test_failed_lookup_does_not_link_or_change_member(linking_context, failure):
    cog, ctx, config, _ = linking_context
    cog.api.player.side_effect = failure
    with pytest.raises(APIError):
        invoke(cog, ctx, "link", 1234)
    assert run(config.member(ctx.author).all()) == MEMBER_DEFAULTS
    assert not ctx.author.edit.called and not ctx.author.add_roles.called


@pytest.mark.parametrize("change", [{"governor_id": 9999}, {"nick_name": None}, {"nick_name": " "}])
def test_invalid_player_cannot_link(linking_context, change):
    cog, ctx, config, _ = linking_context
    cog.api.player.return_value["player"].update(change)
    with pytest.raises(APIError):
        invoke(cog, ctx, "link", 1234)
    assert run(config.member(ctx.author).governor_id()) is None


def test_missing_alliance_data_keeps_current_roles(linking_context):
    cog, ctx, config, _ = linking_context
    invoke(cog, ctx, "link", 1234)
    del cog.api.player.return_value["player"]["alliance"]
    before = run(config.member(ctx.author).all())
    with pytest.raises(APIError):
        invoke(cog, ctx, "sync")
    assert run(config.member(ctx.author).all()) == before
    assert not ctx.author.remove_roles.called


@pytest.mark.parametrize("change", [{"alliance": []}, {"alliance": {}}, {"kid": None}])
def test_malformed_alliance_membership_cannot_assign_or_remove_roles(linking_context, change):
    cog, ctx, config, _ = linking_context
    invoke(cog, ctx, "link", 1234)
    cog.api.player.return_value["player"].update(change)
    with pytest.raises((ValueError, APIError)):
        invoke(cog, ctx, "sync")
    assert run(config.member(ctx.author).assigned_roles()) == [101, 102]
    assert not ctx.author.remove_roles.called


def test_failed_nickname_sync_keeps_last_applied_name_for_unlink(linking_context):
    cog, ctx, config, _ = linking_context
    invoke(cog, ctx, "link", 1234)
    cog.api.player.return_value["player"]["nick_name"] = "Changed name"
    saved_edit = ctx.author.edit.side_effect
    ctx.author.edit.side_effect = forbidden()
    invoke(cog, ctx, "sync")
    state = run(config.member(ctx.author).all())
    assert state["applied_nick"] == "Governor" and state["pending_nick"] == "Changed name"
    ctx.author.edit.side_effect = saved_edit
    invoke(cog, ctx, "unlink")
    assert ctx.author.nick == "Previous nickname"
    assert run(config.member(ctx.author).all()) == MEMBER_DEFAULTS


def test_interrupted_nickname_change_can_be_reconciled_after_reload(linking_context):
    cog, ctx, config, _ = linking_context

    async def ambiguous_edit(**kwargs):
        ctx.author.nick = kwargs["nick"]
        raise discord.HTTPException(SimpleNamespace(status=500, reason="Server Error"), "ambiguous")

    ctx.author.edit.side_effect = ambiguous_edit
    invoke(cog, ctx, "link", 1234)
    assert run(config.member(ctx.author).pending_nick()) == "Governor"
    reloaded = Kingshot(cog.bot)
    reloaded.api.player = cog.api.player
    invoke(reloaded, ctx, "sync")
    assert run(config.member(ctx.author).applied_nick()) == "Governor"
    assert run(config.member(ctx.author).pending_nick()) is None
    assert run(config.member(ctx.author).original_nick()) == "Previous nickname"


def test_link_concurrency_reserves_governor_id(linking_context):
    cog, ctx, config, _ = linking_context
    second_ctx = SimpleNamespace(guild=ctx.guild, author=SimpleNamespace(id=8, guild=ctx.guild),
                                 send=AsyncMock())
    ctx.guild.fetch_member.side_effect = lambda uid: ctx.author if uid == 7 else second_ctx.author

    async def scenario():
        results = await asyncio.gather(cog._link_account(ctx, 1234),
                                       cog._link_account(second_ctx, 1234), return_exceptions=True)
        assert results[0] is None and isinstance(results[1], ValueError)
        assert await config.member_from_ids(1, 7).governor_id() == 1234
        assert await config.member_from_ids(1, 8).governor_id() is None
        assert cog.api.player.await_count == 1
    run(scenario())


def test_link_cleanup_uses_fresh_members_not_stale_gateway_cache(linking_context):
    cog, ctx, config, roles = linking_context
    server = {"nick": "Previous nickname", "roles": [roles[103]]}

    async def fetch_member(uid):
        fresh = SimpleNamespace(id=uid, guild=ctx.guild, nick=server["nick"],
                                roles=list(server["roles"]), top_role=ctx.author.top_role)

        async def edit(**kwargs):
            server["nick"] = kwargs["nick"]  # Member.edit returns a new member, not a cache mutation.

        async def add_roles(role, **kwargs):
            server["roles"].append(role)

        async def remove_roles(role, **kwargs):
            server["roles"] = [r for r in server["roles"] if r.id != role.id]
        fresh.edit = edit
        fresh.add_roles = add_roles
        fresh.remove_roles = remove_roles
        return fresh
    ctx.guild.fetch_member.side_effect = fetch_member
    invoke(cog, ctx, "link", 1234)
    assert ctx.author.nick == "Previous nickname"  # Gateway cache has not caught up.
    assert server["nick"] == "Governor"
    invoke(cog, ctx, "unlink")
    assert server["nick"] == "Previous nickname"
    assert [r.id for r in server["roles"]] == [103]
    assert run(config.member(ctx.author).all()) == MEMBER_DEFAULTS


def test_nickname_length_and_missing_permission(linking_context):
    cog, ctx, config, _ = linking_context
    cog.api.player.return_value["player"]["nick_name"] = "x" * 100
    ctx.guild.me.guild_permissions.manage_nicknames = False
    invoke(cog, ctx, "link", 1234)
    assert ctx.author.nick == "Previous nickname"
    assert "Manage Nicknames" in ctx.send.call_args.kwargs["embed"].fields[-1].value
    ctx.guild.me.guild_permissions.manage_nicknames = True
    invoke(cog, ctx, "sync")
    assert ctx.author.nick == "x" * 32
    assert run(config.member(ctx.author).original_nick()) == "Previous nickname"


def test_discord_failures_are_explicit_and_retryable(linking_context):
    cog, ctx, config, _ = linking_context
    saved_add, saved_edit = ctx.author.add_roles.side_effect, ctx.author.edit.side_effect
    ctx.author.add_roles.side_effect = forbidden()
    ctx.author.edit.side_effect = forbidden()
    invoke(cog, ctx, "link", 1234)
    assert run(config.member(ctx.author).governor_id()) == 1234
    output = ctx.send.call_args.kwargs["embed"].fields[-1].value
    assert "could not assign" in output and "could not change" in output
    ctx.author.add_roles.side_effect = saved_add
    ctx.author.edit.side_effect = saved_edit
    invoke(cog, ctx, "sync")
    assert ctx.author.nick == "Governor" and {r.id for r in ctx.author.roles} == {101, 102, 103}
    ctx.author.remove_roles.side_effect = forbidden()
    invoke(cog, ctx, "unlink")
    assert run(config.member(ctx.author).governor_id()) is None
    assert "retry sync/unlink" in ctx.send.call_args.args[0]
    assert run(config.member(ctx.author).assigned_roles()) == [101, 102]


def test_duplicate_links_are_guild_local_and_do_not_prove_ownership(linking_context):
    cog, ctx, config, _ = linking_context
    run(config.member_from_ids(2, 8).governor_id.set(1234))
    invoke(cog, ctx, "link", 1234)
    run(config.member(ctx.author).clear())
    run(config.member_from_ids(1, 8).governor_id.set(1234))
    cog.api.player.reset_mock()
    with pytest.raises(ValueError, match="already linked"):
        invoke(cog, ctx, "link", 1234)
    assert not cog.api.player.called
    assert run(config.member(ctx.author).governor_id()) is None


def test_sync_requires_link(linking_context):
    cog, ctx, _, _ = linking_context
    with pytest.raises(ValueError, match="Link an account first"):
        invoke(cog, ctx, "sync")
    assert not cog.api.player.called


@pytest.mark.parametrize("permission", ["administrator", "manage_roles", "manage_guild",
                                      "manage_messages", "ban_members", "kick_members",
                                      "manage_nicknames", "mention_everyone"])
def test_privileged_roles_cannot_be_configured(linking_context, permission):
    cog, ctx, _, roles = linking_context
    roles[101].permissions = discord.Permissions(**{permission: True})
    with pytest.raises(ValueError, match="privileged"):
        invoke(cog, ctx, "linkset_role", roles[101])
    with pytest.raises(ValueError, match="privileged"):
        invoke(cog, ctx, "linkset_alliance", roles[101])


@pytest.mark.parametrize("kind", ["managed", "default", "bot_hierarchy", "admin_hierarchy",
                                 "missing_permission"])
def test_unmanageable_roles_cannot_be_configured(linking_context, kind):
    cog, ctx, _, roles = linking_context
    role = roles[101]
    if kind == "managed":
        role.managed = True
    elif kind == "default":
        role.default = True
    elif kind == "bot_hierarchy":
        role.position = 10
    elif kind == "admin_hierarchy":
        role.position = 5
    else:
        ctx.guild.me.guild_permissions.manage_roles = False
    with pytest.raises(ValueError):
        invoke(cog, ctx, "linkset_role", role)


def test_role_configuration_and_revalidation(linking_context):
    cog, ctx, config, roles = linking_context
    invoke(cog, ctx, "linkset_role", roles[101])
    invoke(cog, ctx, "linkset_alliance", roles[102])
    invoke(cog, ctx, "linkset_status")
    assert "UNVERIFIED" in ctx.send.call_args.args[0]
    invoke(cog, ctx, "link", 1234)
    roles[101].permissions = discord.Permissions(administrator=True)
    invoke(cog, ctx, "sync")
    assert 101 not in {r.id for r in ctx.author.roles}
    assert "privileged" in ctx.send.call_args.kwargs["embed"].fields[-1].value
    invoke(cog, ctx, "linkset_alliance")
    invoke(cog, ctx, "sync")
    assert 102 not in {r.id for r in ctx.author.roles}
    assert config.guilds[1]["alliance_role"] is None


def test_deleted_role_cleanup(linking_context):
    cog, ctx, config, roles = linking_context
    invoke(cog, ctx, "link", 1234)
    del roles[102]
    invoke(cog, ctx, "unlink")
    assert run(config.member(ctx.author).all()) == MEMBER_DEFAULTS


def test_admin_clear_and_data_deletion(linking_context):
    cog, ctx, config, _ = linking_context
    invoke(cog, ctx, "link", 1234)
    member = ctx.author
    admin = SimpleNamespace(id=12, top_role=LinkRole(201, 6))
    ctx.author = admin
    invoke(cog, ctx, "linkset_clear", member)
    assert run(config.member(member).governor_id()) is None and member.nick == "Previous nickname"
    ctx.author = member
    invoke(cog, ctx, "link", 1234)
    run(config.member_from_ids(2, 7).governor_id.set(5678))
    run(config.member_from_ids(2, 8).governor_id.set(1000))
    run(cog.red_delete_data_for_user(requester="discord_deleted_user", user_id=7))
    assert run(config.member_from_ids(1, 7).all()) == MEMBER_DEFAULTS
    assert run(config.member_from_ids(2, 7).all()) == MEMBER_DEFAULTS
    assert run(config.member_from_ids(2, 8).governor_id()) == 1000


def test_admin_cannot_clear_equal_hierarchy(linking_context):
    cog, ctx, _, _ = linking_context
    with pytest.raises(ValueError, match="highest role"):
        invoke(cog, ctx, "linkset_clear", ctx.author)


def test_real_red_command_registration():
    command = Kingshot.ks
    expected = {"player", "heroes", "gear", "compare", "ranks", "alliance", "members", "top",
                "inactive", "progress", "changes", "kingdom", "kvk", "score", "kvkdays",
                "matchups", "events", "usage", "auto", "remind", "linkset", "scout",
                "comparekingdoms", "boardtest"}
    assert set(command.all_commands) == expected
    from redbot.core.commands.requires import PrivilegeLevel
    assert Kingshot.ksset.requires.privilege_level == PrivilegeLevel.BOT_OWNER
    assert Kingshot.usage.requires.privilege_level == PrivilegeLevel.BOT_OWNER
    assert Kingshot.boardtest.requires.privilege_level == PrivilegeLevel.BOT_OWNER
    assert Kingshot.auto.requires.privilege_level == PrivilegeLevel.ADMIN
    assert Kingshot.remind.requires.privilege_level == PrivilegeLevel.ADMIN
    assert Kingshot.linkset.requires.privilege_level == PrivilegeLevel.ADMIN
    assert set(Kingshot.linkset.all_commands) == {"role", "alliance", "nickname", "status", "clear"}
    assert set(Kingshot.link.clean_params) == {"governor_id"}


@pytest.mark.parametrize("command,arguments,expected", [("link", "1234", [1234]),
                                                       ("sync", "", []), ("unlink", "", [])])
def test_real_red_link_command_parsing(linking_context, monkeypatch, command, arguments, expected):
    from discord.ext.commands.view import StringView
    from redbot.core import commands

    cog, _, _, _ = linking_context
    cmd = getattr(Kingshot, command)
    monkeypatch.setattr(cmd, "cog", cog)
    message = MagicMock()
    message.attachments = []
    parsed = commands.Context(message=message, bot=cog.bot, view=StringView(arguments), command=cmd)
    run(cmd._parse_arguments(parsed))
    assert parsed.args[2:] == expected


def test_real_red_load_unload_and_persistent_config(monkeypatch, tmp_path):
    """Exercise actual Red.add_cog and on-disk Config, without Discord login."""
    from redbot.core import data_manager
    from redbot.core._cli import parse_cli_flags
    from redbot.core.bot import Red

    basic = deepcopy(data_manager.basic_config_default)
    basic["DATA_PATH"] = str(tmp_path)
    basic["STORAGE_TYPE"] = "JSON"
    monkeypatch.setattr(data_manager, "basic_config", basic)

    async def scenario():
        bot = Red(cli_flags=parse_cli_flags(["test-instance"]), owner_ids={1},
                  intents=discord.Intents.none())
        monkeypatch.setattr(bot, "wait_until_red_ready", AsyncMock())
        monkeypatch.setattr(bot, "get_shared_api_tokens", AsyncMock(return_value={}))
        async with bot:
            await setup(bot)
            first = bot.get_cog("Kingshot")
            worker = first._worker
            assert bot.get_command("ks player") is not None
            assert bot.get_command("link") is not None
            assert bot.get_command("sync") is not None
            assert bot.get_command("unlink") is not None
            await first.config.member_from_ids(1, 7).governor_id.set(1234)
            await first.config.guild_from_id(1).message_id.set(555)
            await first.config.guild_from_id(1).kingdom.set(571)
            await bot.remove_cog("Kingshot")
            assert worker.done() and first.api.session.closed
            await setup(bot)
            second = bot.get_cog("Kingshot")
            assert second is not first
            assert await second.config.guild_from_id(1).message_id() == 555
            assert await second.config.guild_from_id(1).kingdom() == 571
            assert await second.config.guild_from_id(2).kingdom() == 570
            assert await second.config.member_from_ids(1, 7).governor_id() == 1234
            await second.red_delete_data_for_user(requester="user", user_id=7)
            assert await second.config.member_from_ids(1, 7).governor_id() is None
            await bot.remove_cog("Kingshot")
            assert second._worker is None and second.api.session.closed
    run(scenario())
