# Kingshot

A Red-DiscordBot V3 cog for MightPulse player statistics, alliance snapshots,
kingdom leaderboards, Mighty KvK preparation dashboards and kingdom event reminders.
No additional paid Discord bot is needed. Events, KvK and matchups require your
existing **Mighty** API subscription.

## Install

Requires Red **3.5.24 or newer** and its supported Python version. `aiohttp` is
declared for Downloader; Red already uses it. Install from the main branch:

```text
[p]repo add sai-cogs https://github.com/TheSaiyajin/Sai-Cogs
[p]cog install sai-cogs kingshot
[p]load kingshot
```

If `sai-cogs` is already added, use that repository name and its configured branch.
For the cross-kingdom lookup update before merge, append
`feature/kingshot-cross-kingdom` to `repo add` when adding a new repository.
`[p]` means your bot's command prefix, not literal text.

### Secure configuration (bot owner)

```text
[p]ksset apikey
[p]ksset kingdom 570
[p]ksset alliance VIL
[p]ksset channel #kingshot
[p]ksset interval 10
[p]ksset test
[p]ksset status
```

`ksset apikey` takes **no key argument**. It sends Red's owner-only token button
by DM. Enter the key only in the modal. The service is `mightpulse` and the token
name is `api_key`. The modal expects a pair such as `api_key YOUR_KEY`; its
placeholder supplies the expected token name. Never post a key in a channel,
issue, log, command argument or repository. If DMs are blocked, enable them or use
Red's `[p]set api mightpulse` in a DM and enter the same token name in its modal.

Red's shared token storage is bot-wide (not a secrets vault or per-server key).
Only bot owners can configure it. **All servers share that key and API allowance**;
each server has independent kingdom, alliance, channels, history, message ID and
reminder settings. Defaults are kingdom `570`, case-sensitive tag `VIL` and
dashboard interval `10` minutes. Configure the kingdom/alliance to register the
server for scheduled snapshots. Changing either resets its roster history;
changing kingdom also resets reminder markers. No credentials appear in status.

`ksset test` checks authentication and Mighty status via `/usage` without consuming
the counted allowance. It does not call every paid endpoint.

## Commands

All `ks` commands require a server. Normal members may read statistics.

| Command | Output |
|---|---|
| `[p]ks player <governor_id>` | Name, governor ID, power, Town Center, kills, VIP, alliance, kingdom, avatar |
| `[p]ks heroes <governor_id>` | Recorded **arena defence team**, levels, stars, power, equipped upgrades/mastery and widget |
| `[p]ks gear <governor_id>` | Governor gear, quality, tier, stars, strength upgrades, score and combat; hidden gear is identified |
| `[p]ks compare <player1> <player2>` | Recorded power, TC, kills, VIP and kingdom, with independent data ages |
| `[p]ks ranks [board] [kingdom_id]` | Up to 100 leaderboard entries, paginated; defaults to `personal_power`. Choose another board such as `kills`, `mystic_trial`, `town_center` or any supported slug below. |
| `[p]ks alliance [kingdom_id tag]` | Alliance overview; omit both arguments for configured alliance |
| `[p]ks members [kingdom_id tag]` | Paginated roster, IDs, alliance ranks, power and TC |
| `[p]ks top [kingdom_id tag]` | Ten strongest members with available power |
| `[p]ks inactive` | Explanation only: activity reporting is disabled |
| `[p]ks progress` | Member power differences between the latest two hourly snapshots |
| `[p]ks changes` | New/departed members between those snapshots |
| `[p]ks kingdom [kingdom_id]` | Kingdom statistics, including on-map player count (not recent activity) |
| `[p]ks kvk [kingdom_id]` | Current matchup, season, stage and bye status |
| `[p]ks score [kingdom_id]` | Preparation totals, difference, winning/losing/tied, published days |
| `[p]ks kvkdays [kingdom_id]` | Same score card, including every available preparation day I-V and its status |
| `[p]ks matchups` | Available current-season matchup records |
| `[p]ks events [kingdom_id]` | Kingdom calendar with start/end Discord timestamps |
| `[p]ks usage` | **Bot owner only:** Mighty status and minute/day allowance |

### Look up other kingdoms without changing settings

The optional ID is an **in-game kingdom/server ID**, not a Discord server ID.
Anyone who can use the lookup commands can query another kingdom:

```text
[p]ks kingdom 571
[p]ks alliance 571 ABC
[p]ks members 571 ABC
[p]ks top 571 ABC
[p]ks ranks personal_power 571
[p]ks kvk 571
[p]ks score 571
[p]ks kvkdays 571
[p]ks events 571
```

Replace `571` and `ABC` with the desired kingdom and actual alliance **tag**.
The documented alliance endpoint takes a case-sensitive tag (`abbr`), not a full
alliance name; there is no full-name search endpoint in this cog. Alliance queries
require both ID and tag when overriding, avoiding an accidental lookup of your
saved tag in another kingdom. Leaderboards keep the existing board-first syntax.
Omitting arguments preserves existing configured defaults (570/VIL initially).

These are read-only, one-off queries: they do not change your dashboard, reminders,
saved kingdom/alliance or snapshot history. `ks progress` and `ks changes` still
report only the configured alliance's stored snapshots. Player commands already
look up governor IDs independently of kingdom. All kingdoms share the existing
API key, cache and subscription allowance; events/KvK still require Mighty.

### Choosing a leaderboard

`personal_power` is only the default, not the only available leaderboard.
Replace `[board]` with a supported slug to view kills, Mystic Trial, Town Center,
hero power, alliance power and other rankings:

```text
[p]ks ranks
[p]ks ranks kills
[p]ks ranks mystic_trial
[p]ks ranks town_center
[p]ks ranks hero_total
[p]ks ranks alliance_power
[p]ks ranks kills 571
[p]ks ranks mystic_trial 571
```

Omitting the board shows personal power in your configured kingdom. Supplying
only a board uses the configured kingdom; adding a kingdom ID queries that
kingdom instead. Use the exact slug `mystic_trial` for Mystic Trial, not `mystic`.
Changing the board is a one-off query and does not change the default.

Supported leaderboard slugs:

```text
alliance_power alliance_kills personal_power kills town_center rebel_conquest
single_hero hero_total troop_power building_power research_power
hero_no_equip hero_equip gov_gear gov_charm pet_power island_prosperity
migrant_score mystic_trial coliseum forest_of_life crystal_cave
knowledge_nexus molten_fort radiant_spire master_power
```

Governor IDs are in-game IDs, **not internal UIDs**. Positive integer identifiers
are validated. Tags preserve case and accept 1-12 ASCII letters, digits, `_`, `-`;
this intentionally conservative input policy prevents path injection. An actual
game tag outside this policy requires an explicit code change and validation.

### Automatic dashboard (Red administrators / Administrator permission)

```text
[p]ks auto channel #kvk
[p]ks auto interval 10
[p]ks auto enable
[p]ks auto refresh
[p]ks auto disable
```

Alternatively, `ksset channel` supplies a fallback channel. Refresh intervals are
**5-1440 minutes**. There is one scheduler per cog instance; enable never starts a
second scheduler. Refreshes in a guild are serialized. The dashboard edits its
existing message and persists the message ID. Deleted messages are replaced;
permission failures do not trigger duplicate posts. After a crash between sending
and saving the ID, a guild-specific footer marker can recover the message from
the latest 50 channel messages. Do not run multiple bot processes against the same
Red data directory. Switching channels leaves the old dashboard untouched.

The card shows the configured kingdom (570 by default), opponent, season/stage,
preparation totals and difference, available days and last successful API check.
Byes, unpublished opponents and `404 scores_unavailable` are explained.
Authentication, subscription and network failures preserve the previous card and
last success; the error appears in `ksset status`. A successful check reporting
unpublished scores is still a successful API check. **Preparation scores are not
castle battle outcomes.** Manual refresh respects the shared API cache.

Required channel permissions: View Channel, Send Messages, Embed Links and Read
Message History. Paginated command responses use Red's standard menu controls.

### Event reminders (administrators)

```text
[p]ks remind channel #events
[p]ks remind enable
[p]ks remind list
[p]ks remind offsets 24 1
[p]ks remind disable
```

Reminders default to **24 hours and 1 hour** before documented `begin_ts` Unix
timestamps. Offsets accept 1-8 distinct values, each 1-168 hours. The scheduler
checks enabled reminder guilds every five minutes. Notifications are delivered
only within ten minutes of the due time and before the event begins. Extended
downtime does not flood old reminders on startup.

Delivery markers persist across restarts and are pruned seven days after the event.
Notifications use **at-most-once** reservation before sending. A process crash
between reservation and send, or an ambiguous Discord HTTP failure, can miss a
notification rather than duplicate it. Definitive Forbidden/Not Found errors
release the reservation for retry within the delivery window. Reminders contain
no role/everyone mentions.

The API calendar is kingdom-wide. It does **not document personal alliance events
such as Bear Trap**. Administrators can add explicitly labelled, one-off manual
events using UTC Unix seconds:

```text
[p]ks remind add <start_unix_seconds> Bear Trap
[p]ks remind remove <start_unix_seconds> Bear Trap
```

The start must be in the future and within one year. Names are at most 100
characters; at most 50 future manual events are stored per server. There is no
automatic recurrence or assumed alliance event API. Reminder delivery still
requires an accessible Mighty calendar; enable verifies access first.

## Data and request policy

Official documentation: <https://api.mightpulse.com/> (inspected 2026-10-31).
Only these authenticated endpoints are used:

```text
GET /v1/players/{id}?include=base,heroes,ranks,gov_gear
GET /v1/alliances/{kid}/{tag}?include=info,roster
GET /v1/kingdoms/{kid}
GET /v1/kingdoms/{kid}/ranks?board={documented_slug}&limit=100
GET /v1/kingdoms/{kid}/events
GET /v1/kingdoms/{kid}/kvk
GET /v1/kingdoms/{kid}/kvk/scores
GET /v1/kvk/matchups
GET /v1/usage
```

Requests use `Authorization: Bearer`, one shared `aiohttp` session, a 105-second
total / 15-second connection timeout, no redirects and no environment proxies.
The upstream can wait 90 seconds for fresh player/alliance sections. Requests
are serialized and spaced at least 1.05 seconds, conservatively below the standard
60/minute limit even when multiple guilds share a Mighty key.

Player/alliance/kingdom/ranks cache for one hour; KvK/scores/matchups for five
minutes; events for at most fifteen minutes, expiring at midnight UTC; usage for
15 seconds. The cache is shared between guilds, capped at 256 entries, and cleared
when the key changes. Hourly scheduled alliance snapshots store only two observations,
not unlimited history; identical upstream `cached_at` is not stored twice. Partial
rosters with mismatched published counts are rejected to avoid false departures.
Commands do not independently create snapshots. Idle guilds with only snapshots
cost at most ~24 requests/day; enabled dashboards add up to ~288/day at the default
ten-minute interval, and reminder calendars up to ~96/day, before cache sharing.
Startup/failures/retries can add requests; `/usage` is not counted by MightPulse.

429, connection/timeout and 5xx failures have at most three attempts with bounded
exponential backoff. `Retry-After` seconds/date and rate-limit reset headers are
respected. Delays longer than 120 seconds return an error rather than hanging a
command; 429 blocks other uncached requests until the advertised reset. Successful
responses exhausting an allowance also block further uncached requests. Non-retryable
400/401/403/404 and unexpected formats have explicit, credential-free messages.
Cache entries and the uncounted `/usage` endpoint remain usable during rate-limit
blocking. Server `Retry-After` also delays other counted requests until that time.

Missing values are not converted to zero. Upstream caching/age appears on profile
and alliance cards where supplied. `online`, `last_active_at`, `last_login` are
documented stale remnants and are **never** used for activity reporting. Snapshot
progress is observed cached power, not a real-time growth measurement. Kingdom
activity-derived metrics are not presented as verified recent activity.

## Verification and limitations

The documentation lists player/alliance/kingdom fields and contains literal
events, KvK matchup, score and usage JSON examples. Leaderboard entry fields are
documented but their envelope is not; `/kvk/matchups` has no complete JSON response
example. The parser traverses bounded containers to find matching record fields
rather than hard-code invented envelopes. Unknown structures produce an explicit
schema error, never fabricated standings. Offline tests use synthetic envelopes
for those two endpoints. **Their authenticated live structures remain unverified.**

Tests mock all HTTP traffic and Discord operations, use real Redbot and discord.py
command classes, and cover errors, rate limits, data rendering, snapshots, dashboard
editing, deletion, restart persistence, reminders and lifecycle cleanup. Run in an
environment containing Redbot and pytest:

```text
python -m pytest tests/test_kingshot.py -q
python -m compileall -q kingshot
```

Offline validation on 2026-10-31: **153 Kingshot tests / 169 full repository tests
passed**, with Python 3.11, Red 3.5.24 and discord.py 2.7.1. Real Red cog injection,
unload/reload and temporary on-disk JSON Config persistence passed without login.
Cross-kingdom command parsing, explicit API targets, invalid arguments and
unchanged per-guild settings are covered.
Syntax compilation and `git diff --check` also passed. Two pytest deprecation
warnings originate in Red's `aiohttp-json-rpc` dependency, not this cog.

The bot owner must perform live acceptance after installation:

1. Configure the key privately and run `ksset test` / `ks usage`.
2. Exercise each statistics command with valid governor IDs, especially `ks ranks`
   and `ks matchups`; confirm their real response structure.
3. Enable a dashboard, refresh twice and verify the message ID stays unchanged.
4. Reload the cog and verify the same dashboard is edited.
5. Verify a manual event reminder in a test channel and both default offsets.
6. Wait for two distinct hourly snapshots, then verify progress and membership changes.

No real Discord login or authenticated MightPulse requests are made in automated
tests. Installation in your running bot and live subscription behavior cannot be
certified by offline tests alone.
