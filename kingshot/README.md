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
For the kingdom scouting update before merge, append
`feature/kingshot-kingdom-scout` to `repo add` when adding a new repository.
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

## Account linking and server nickname/roles

Self-service linking is optional. Statistics remain public; nobody needs to link
to use `ks player`, `ks alliance`, ranks or other statistics commands.

If your prefix is `!!`, use:

```text
!!link 12345678
!!sync
!!unlink
```

`link <governor_id>` takes the **in-game governor ID**, not a Discord user ID or
internal game UID. It looks up that governor, saves your account association in
this Discord server, changes your **server nickname** to the recorded in-game name
(up to Discord's 32-character limit), and applies the administrator-configured
existing roles. It does not change your global Discord username. No API key,
game password or verification code is requested from members.

**Links are UNVERIFIED.** MightPulse player lookup does not prove who owns an
account. Anyone who knows an ID can claim it. Links and assigned roles are not
identity verification or authorization to staff/privileged access. Do not
configure access to sensitive private channels based on these roles. One governor
ID can be linked to one Discord member per server; an administrator can clear a
disputed link. This uniqueness rule is not ownership verification.

### Administrator setup (existing server roles only)

Create or choose suitable **non-privileged** roles in Discord yourself, then:

```text
[p]ks linkset role @LinkedPlayer
[p]ks linkset alliance @VILMember
[p]ks linkset nickname true
[p]ks linkset status
```

- `role [role]`: assign this role to linked accounts; omit the role to disable.
- `alliance [role]`: assign this role only when the recorded player belongs to
  the saved kingdom **and** case-sensitive alliance tag (570 / VIL initially);
  omit the role to disable. It is not a mapping of every alliance in the game.
- `nickname <true|false>`: enable/disable nickname synchronization; enabled by default.
- `status`: show linking settings.
- `clear <Discord member>`: clear a disputed link and attempt role/nickname cleanup,
  e.g. `[p]ks linkset clear @Member`. Administrators other than the server owner
  may only clear members below their highest role.

Roles are disabled by default until an administrator selects them. The cog never
creates roles, guesses role names or applies an arbitrary role just because its
name resembles an alliance tag. Managed roles, `@everyone`, roles above/equal to
the bot/administrator and roles with staff/moderation/management permissions are
rejected. Permission checks are repeated on sync; formerly assigned roles that
become unsafe are removed when manageable, or a cleanup warning is displayed.

The bot needs **Manage Nicknames** and **Manage Roles**, with its highest role
above the roles it assigns and above the members whose nickname it edits. Discord
does not allow changing the server owner's nickname. Missing permissions or
hierarchy failures are reported; account linking can persist even when a
nickname/role update fails. Fix permissions and run `sync` again.
Each link/sync/unlink fetches current Discord member state rather than trusting
gateway-cache updates, so quick successive commands can safely reconcile changes.

`sync` updates your nickname/roles from the recorded account. It removes old
cog-assigned alliance roles if you leave the configured alliance and applies
new role settings. It respects the existing **one-hour player cache**, so an
in-game rename or alliance transfer may not appear immediately. There is **no
scheduled member sync**: changes apply only on `link` or `sync`. Role/kingdom/tag
configuration changes do not modify every member immediately.

`unlink` removes the association and roles this cog assigned, while preserving
roles you already had before linking. It restores the previous server nickname
only if your current nickname is one set/pending from this cog; a manually changed
nickname is not overwritten. Failed/ambiguous operations retain cleanup markers
for retry with `unlink` (or `sync` while still linked). Relinking another governor
keeps cleanup tracking for the original nickname and previous cog-assigned roles.

Member associations, link timestamps, assigned-role IDs and original/applied/
pending nicknames persist across cog reloads. Red's data-deletion hook removes
that member's linking records in every guild. Data deletion does not make Discord
role/nickname changes; use `unlink` before requesting deletion if cleanup is desired.
Links are per Discord server and have no automatic activity or ownership checks.

## Statistics commands

| Command | Output |
|---|---|
| `[p]ks player <governor_id>` | Name, governor ID, power, Town Center, kills, VIP, alliance, kingdom, avatar |
| `[p]ks heroes <governor_id>` | Recorded **arena defence team**, levels, stars, power, equipped upgrades/mastery and widget |
| `[p]ks gear <governor_id>` | Governor gear, quality, tier, stars, strength upgrades, score and combat; hidden gear is identified |
| `[p]ks compare <player1> <player2>` | Profile statistics plus all available player ranking values and kingdom positions, including personal power, kills, Mystic Trial stage and returned pet/hero/other boards; paginated with independent data ages |
| `[p]ks scout <kingdom1> <kingdom2>` | Kingdom metric comparison and top alliances, power players, TC levels and Mystic Trial stages; alias `comparekingdoms` |
| `[p]ks ranks [board] [kingdom_id]` | Up to 100 leaderboard entries, paginated; default `personal_power` |
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

### Comparing kingdoms (KvK scouting)

```text
[p]ks scout 2312 2316
[p]ks comparekingdoms 2312 2316
```

The command shows five navigable cards, similar to a scouting report:

1. Kingdom totals: power, hero/troop/pet/building/research/gear/charm power,
   kills, Mystic Trial aggregate, island prosperity, migrant score, published
   7-day power gain and on-map player count. Each metric shows both kingdoms and
   whether the left or right numeric value is higher (or tied).
2. Ten strongest alliances from each kingdom's `alliance_power` leaderboard.
3. Top ten players by `personal_power`.
4. Top ten Town Center levels from `town_center`.
5. Top ten individual Mystic Trial stages from `mystic_trial`.

The kingdom summary also includes top-10/top-100 player power sums, **only** when
the power board supplies at least 10/100 unique entries with valid numeric scores.
Short/partial boards do not produce fabricated full top-list totals. Kingdom
`mystic_trial` is labelled as a total, never an individual's stage; the player
Mystic leaderboard is shown separately.

The API health label is presented as an upstream label, not proof of recent
activity. Stale player activity / derived active-7d and active-30d counts from the
example screenshot are intentionally not presented as verified activity. A growth
percentage is not invented when a verified historical denominator is unavailable.
These are numeric comparisons, **not** a prediction of a KvK winner, preparation
scores or castle battle results.

Two distinct positive kingdom IDs are required. There are up to **10 counted
requests on a cold cache** (two kingdom summaries and four boards per kingdom),
using the shared one-hour cache and existing rate limiter. No individual player
or alliance profile requests are made for the top lists. All users who can use
statistics may scout; linking is not required. Saved settings and automation
remain unchanged.

Missing/malformed individual boards are explicitly labelled on the relevant
card. Authentication, subscription, rate-limit and server/network failures
abort the report with the normal error message rather than masquerading as
successful empty data. Leaderboard envelope parsing uses the documented entry
fields, as with `ks ranks`; live authenticated structures remain owner-verifiable.
Top-list samples can be independently cached and are not necessarily simultaneous.

### Comparing players and rankings

`[p]ks compare <player1> <player2>` accepts two in-game governor IDs. The first
page retains power, TC, kills, VIP and kingdom. Further pages compare documented
`ranks` values and positions for personal power, kills, Town Center, migrant
score and Mystic Trial stage, plus **every named entry returned in each player's
`ranks.leaderboards`** (for example pet power, hero power or island prosperity).

Boards are matched by their API-provided name, not their list position. A board
present for only one player still appears, with the other value shown as
`Unavailable`. Missing ranks are never zero. Only returned player data is compared;
the cog does not fetch every kingdom leaderboard or invent missing pet/stage data.
The API may not return every board listed under `ks ranks` for a particular player.

Values are shown as left player / right player with their governor IDs and
kingdoms labelled. Rank positions belong to each player's **own kingdom**, not
global standings, so positions across different kingdoms are not directly
equivalent. All pages show the independent data ages; the records may not be
simultaneous. Comparisons use the existing cached player requests, with no extra
paid leaderboard requests.

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

Offline validation on 2026-11-01: **223 Kingshot tests / 239 full repository tests
passed**, with Python 3.11, Red 3.5.24 and discord.py 2.7.1. Real Red cog injection,
unload/reload and temporary on-disk JSON Config persistence passed without login.
Cross-kingdom command parsing, explicit API targets, invalid arguments and
unchanged per-guild settings are covered.
Linking tests cover nickname/role updates, permission/hierarchy failures,
unverified role restrictions, guild-local duplicate claims, cleanup, retry markers,
member Config persistence, data deletion and real top-level command registration.
Ranking comparison tests cover asymmetric/reordered boards, missing values,
malformed responses, long-list pagination and the existing two-request budget.
Kingdom scout tests cover exact request targets/budget, real command parsing,
top-list sums and minimum counts, missing boards, fatal API errors and embed limits.
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
7. Configure non-privileged linking roles, run `link` / `sync` / `unlink` with a test
   member below the bot, and verify nickname, role assignment and restoration.
8. Run `ks scout <kingdom1> <kingdom2>` and check all five cards against actual
   kingdom/board data, particularly Mystic Trial totals versus individual stages.

No real Discord login or authenticated MightPulse requests are made in automated
tests. Installation in your running bot and live subscription behavior cannot be
certified by offline tests alone.
