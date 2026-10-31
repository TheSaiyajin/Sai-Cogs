"""Documented MightPulse endpoints, safe failures and shared request budgeting."""

import asyncio
from collections import OrderedDict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import re
import time
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import quote

import aiohttp

BASE_URL = "https://api.mightpulse.com/v1"
BOARDS = frozenset(
    "alliance_power alliance_kills personal_power kills town_center rebel_conquest "
    "single_hero hero_total troop_power building_power research_power hero_no_equip "
    "hero_equip gov_gear gov_charm pet_power island_prosperity migrant_score mystic_trial "
    "coliseum forest_of_life crystal_cave knowledge_nexus molten_fort radiant_spire "
    "master_power".split()
)


class APIError(Exception):
    """Never contains response bodies, URLs, request headers or credentials."""

    def __init__(self, status: int, code: str):
        self.status = status
        self.code = code
        messages = {
            "no_key": "No MightPulse key configured. The bot owner should use ksset apikey.",
            "auth": "MightPulse rejected the key. Ask the bot owner to update it.",
            "forbidden": "Access denied. Events and KvK require an active Mighty subscription.",
            "missing": "No record found, or preparation scores have not been published.",
            "scores_unavailable": "Preparation scores have not been published yet.",
            "rate_limit": "MightPulse rate limit reached. Try again after the allowance resets.",
            "invalid": "MightPulse rejected the requested identifier or parameters.",
            "server": "MightPulse is temporarily unavailable. Try again later.",
            "network": "MightPulse could not be reached within the request timeout.",
            "schema": "MightPulse returned an unexpected response format; no data was inferred.",
        }
        super().__init__(messages[code])


def identifier(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 2**63 - 1:
        raise ValueError("Identifiers must be positive integers below 2^63.")
    return value


def alliance_tag(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,12}", value):
        raise ValueError("Use a 1-12 character alliance tag (letters, digits, _ or -). Case matters.")
    return value


def retry_delay(headers: Any, now: float) -> float:
    value = headers.get("Retry-After")
    if value:
        try:
            seconds = float(value)
            if math.isfinite(seconds):
                return max(0.0, seconds)
        except (ValueError, TypeError):
            try:
                return max(0.0, parsedate_to_datetime(value).timestamp() - now)
            except (ValueError, TypeError, OverflowError):
                pass
    resets = []
    for bucket in ("Minute", "Day"):
        if headers.get(f"X-RateLimit-Remaining-{bucket}") == "0":
            try:
                reset = float(headers[f"X-RateLimit-Reset-{bucket}"]) - now
                if math.isfinite(reset):
                    resets.append(reset)
            except (ValueError, KeyError, TypeError):
                pass
    return max([0.0] + resets) if resets else 60.0


class MightyAPI:
    def __init__(self, token_provider: Callable[[], Awaitable[Optional[str]]]):
        self.token_provider = token_provider
        self.session: Optional[aiohttp.ClientSession] = None
        self._lock = asyncio.Lock()
        self._cache: OrderedDict = OrderedDict()
        self._next_request = 0.0
        self._blocked_until = 0.0
        self._blocked_error = APIError(429, "rate_limit")
        self._token: Optional[str] = None

    async def start(self):
        if self.session is None or self.session.closed:
            # The upstream can wait 90 seconds for a fresh player section.
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=105, connect=15),
                trust_env=False,
            )

    async def close(self):
        if self.session is not None:
            await self.session.close()
        self._cache.clear()
        self._token = None

    async def get(self, path: str, *, params=None, ttl: int = 300, force: bool = False):
        key = (path, tuple(sorted((params or {}).items())))
        async with self._lock:
            token = await self.token_provider()
            if not token:
                raise APIError(401, "no_key")
            if token != self._token:
                self._cache.clear()
                self._blocked_until = 0.0
                self._token = token
            cached = self._cache.get(key)
            if not force and cached and cached[0] > time.monotonic():
                self._cache.move_to_end(key)
                return cached[1]
            if path != "/usage" and time.time() < self._blocked_until:
                raise self._blocked_error
            await self.start()
            assert self.session is not None
            for attempt in range(3):
                await asyncio.sleep(max(0.0, self._next_request - time.monotonic()))
                self._next_request = time.monotonic() + 1.05
                delay = 2**attempt
                try:
                    async with self.session.get(
                        BASE_URL + path, params=params,
                        headers={"Authorization": f"Bearer {token}"},
                        allow_redirects=False,
                    ) as response:
                        status = response.status
                        if status == 429:
                            delay = retry_delay(response.headers, time.time())
                            self._blocked_until = time.time() + delay
                            error = APIError(status, "rate_limit")
                            self._blocked_error = error
                        elif status >= 500:
                            error = APIError(status, "server")
                            if response.headers.get("Retry-After"):
                                delay = retry_delay(response.headers, time.time())
                                self._blocked_until = time.time() + delay
                                self._blocked_error = error
                        elif status != 200:
                            code = {400: "invalid", 401: "auth", 403: "forbidden",
                                    404: "missing"}.get(status, "schema")
                            if status == 404:
                                try:
                                    body = await response.json()
                                except (ValueError, aiohttp.ContentTypeError):
                                    body = None
                                if isinstance(body, dict) and (
                                    body.get("error") == "scores_unavailable"
                                    or body.get("code") == "scores_unavailable"
                                ):
                                    code = "scores_unavailable"
                            raise APIError(status, code)
                        else:
                            try:
                                data = await response.json()
                            except (ValueError, aiohttp.ContentTypeError):
                                raise APIError(status, "schema") from None
                            if not isinstance(data, dict) or data.get("ok") is not True:
                                raise APIError(status, "schema")
                            if any(response.headers.get(f"X-RateLimit-Remaining-{bucket}") == "0"
                                   for bucket in ("Minute", "Day")):
                                self._blocked_until = time.time() + retry_delay(
                                    response.headers, time.time()
                                )
                                self._blocked_error = APIError(429, "rate_limit")
                            self._cache[key] = (time.monotonic() + ttl, data)
                            self._cache.move_to_end(key)
                            while len(self._cache) > 256:
                                self._cache.popitem(last=False)
                            return data
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    error = APIError(0, "network")
                if attempt == 2 or delay > 120:
                    raise error from None
                await asyncio.sleep(delay)
            raise APIError(0, "network")

    async def player(self, governor_id: int):
        return await self.get(f"/players/{identifier(governor_id)}",
                              params={"include": "base,heroes,ranks,gov_gear"}, ttl=3600)

    async def alliance(self, kid: int, tag: str):
        return await self.get(f"/alliances/{identifier(kid)}/{quote(alliance_tag(tag), safe='')}",
                              params={"include": "info,roster"}, ttl=3600)

    async def kingdom(self, kid: int):
        return await self.get(f"/kingdoms/{identifier(kid)}", ttl=3600)

    async def ranks(self, kid: int, board: str):
        if board not in BOARDS:
            raise ValueError("Unknown board. Supported boards: " + ", ".join(sorted(BOARDS)))
        return await self.get(f"/kingdoms/{identifier(kid)}/ranks",
                              params={"board": board, "limit": 100}, ttl=3600)

    async def kvk(self, kid: int):
        return await self.get(f"/kingdoms/{identifier(kid)}/kvk")

    async def scores(self, kid: int):
        return await self.get(f"/kingdoms/{identifier(kid)}/kvk/scores")

    async def matchups(self):
        return await self.get("/kvk/matchups")

    async def events(self, kid: int):
        now = datetime.now(timezone.utc)
        midnight = datetime.combine(now.date(), datetime.min.time(), timezone.utc).timestamp() + 86400
        return await self.get(f"/kingdoms/{identifier(kid)}/events",
                              ttl=max(1, min(900, int(midnight - now.timestamp()))))

    async def usage(self):
        return await self.get("/usage", ttl=15)
