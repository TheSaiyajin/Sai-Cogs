import discord
import re
from redbot.core import Config, commands
from redbot.core.utils.chat_formatting import pagify


class SaiReply(commands.Cog):
    """Reply when specific words appear in selected channels."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=621739484158, force_registration=True)
        self.config.register_guild(channels={})

    @staticmethod
    def _message_contains_keyword(content: str, keyword: str) -> bool:
        escaped_keyword = re.escape(keyword)
        return re.search(rf"(?<!\w){escaped_keyword}(?!\w)", content) is not None

    @commands.Cog.listener()
    async def on_message(self, message):
        if not message.guild or message.author.bot:
            return

        channels = await self.config.guild(message.guild).channels()
        triggers = channels.get(str(message.channel.id))
        if not triggers:
            return

        content = message.content.lower()

        for keyword, response in triggers.items():
            if self._message_contains_keyword(content, keyword):
                await message.reply(response, mention_author=False)

    @commands.group(name="saireply", aliases=["keywordreply", "kr", "sai"])
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def saireply_group(self, ctx):
        """Manage keyword reply channels and trigger words."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @saireply_group.group(name="channel")
    async def saireply_channel_group(self, ctx):
        """Manage channels where keyword replies are enabled."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @saireply_channel_group.command(name="add")
    async def saireply_channel_add(self, ctx, channel: discord.TextChannel):
        """Enable keyword replies in a channel."""
        channel_key = str(channel.id)

        async with self.config.guild(ctx.guild).channels() as channels:
            if channel_key in channels:
                await ctx.send(f"Keyword replies are already enabled in {channel.mention}.")
                return
            channels[channel_key] = {}

        await ctx.send(f"Enabled keyword replies in {channel.mention}.")

    @saireply_channel_group.command(name="remove")
    async def saireply_channel_remove(self, ctx, channel: discord.TextChannel):
        """Disable keyword replies in a channel."""
        channel_key = str(channel.id)

        async with self.config.guild(ctx.guild).channels() as channels:
            if channel_key not in channels:
                await ctx.send(f"Keyword replies are not enabled in {channel.mention}.")
                return
            del channels[channel_key]

        await ctx.send(f"Disabled keyword replies in {channel.mention}.")

    @saireply_channel_group.command(name="list")
    async def saireply_channel_list(self, ctx):
        """List channels where keyword replies are enabled."""
        channels = await self.config.guild(ctx.guild).channels()
        if not channels:
            await ctx.send("No channels are enabled yet.")
            return

        lines = []
        for channel_id in sorted(channels.keys(), key=int):
            channel_obj = ctx.guild.get_channel(int(channel_id))
            channel_name = channel_obj.mention if channel_obj else f"`{channel_id}` (not found)"
            keyword_count = len(channels[channel_id])
            lines.append(f"- {channel_name}: {keyword_count} keyword(s)")

        await ctx.send("Enabled channels:\n" + "\n".join(lines))

    @saireply_group.group(name="trigger", aliases=["word", "keyword"])
    async def saireply_trigger_group(self, ctx):
        """Manage trigger words and replies for a channel."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @saireply_trigger_group.command(name="add")
    async def saireply_trigger_add(
        self,
        ctx,
        channel: discord.TextChannel,
        keyword: str,
        *,
        response: str,
    ):
        """Add or update a trigger word and reply for a channel."""
        normalized_keyword = keyword.strip().lower()
        if not normalized_keyword:
            await ctx.send("Keyword cannot be empty.")
            return

        channel_key = str(channel.id)

        async with self.config.guild(ctx.guild).channels() as channels:
            if channel_key not in channels:
                channels[channel_key] = {}

            existed = normalized_keyword in channels[channel_key]
            channels[channel_key][normalized_keyword] = response

        if existed:
            await ctx.send(f"Updated `{normalized_keyword}` in {channel.mention}.")
        else:
            await ctx.send(f"Added `{normalized_keyword}` in {channel.mention}.")

    @saireply_trigger_group.command(name="remove", aliases=["delete", "del"])
    async def saireply_trigger_remove(self, ctx, channel: discord.TextChannel, keyword: str):
        """Remove a trigger word from a channel."""
        normalized_keyword = keyword.strip().lower()
        if not normalized_keyword:
            await ctx.send("Keyword cannot be empty.")
            return

        channel_key = str(channel.id)

        async with self.config.guild(ctx.guild).channels() as channels:
            if channel_key not in channels:
                await ctx.send(f"Keyword replies are not enabled in {channel.mention}.")
                return

            if normalized_keyword not in channels[channel_key]:
                await ctx.send(f"`{normalized_keyword}` was not found in {channel.mention}.")
                return

            del channels[channel_key][normalized_keyword]
            if not channels[channel_key]:
                del channels[channel_key]
                await ctx.send(
                    f"Removed `{normalized_keyword}` from {channel.mention}. "
                    "No keywords left, so the channel was disabled."
                )
                return

        await ctx.send(f"Removed `{normalized_keyword}` from {channel.mention}.")

    @saireply_trigger_group.command(name="list")
    async def saireply_trigger_list(self, ctx, channel: discord.TextChannel):
        """List trigger words configured for a channel."""
        channels = await self.config.guild(ctx.guild).channels()
        channel_key = str(channel.id)
        triggers = channels.get(channel_key)

        if not triggers:
            await ctx.send(f"No trigger words configured for {channel.mention}.")
            return

        lines = [f"- `{keyword}` -> {response}" for keyword, response in sorted(triggers.items())]
        await ctx.send(f"Triggers for {channel.mention}:\n" + "\n".join(lines))

    @saireply_trigger_group.command(name="all", aliases=["listall"])
    async def saireply_trigger_all(self, ctx):
        """List all trigger words and replies across enabled channels."""
        channels = await self.config.guild(ctx.guild).channels()
        if not channels:
            await ctx.send("No channels are enabled yet.")
            return

        lines = []
        for channel_id in sorted(channels.keys(), key=int):
            channel_obj = ctx.guild.get_channel(int(channel_id))
            channel_name = channel_obj.mention if channel_obj else f"`{channel_id}` (not found)"
            triggers = channels[channel_id]

            if not triggers:
                lines.append(f"{channel_name}\n- (no triggers)")
                continue

            lines.append(channel_name)
            for keyword, response in sorted(triggers.items()):
                lines.append(f"- `{keyword}` -> {response}")

        output = "All configured triggers:\n" + "\n".join(lines)
        for page in pagify(output, delims=["\n"], page_length=1900):
            await ctx.send(page)


async def setup(bot):
    await bot.add_cog(SaiReply(bot))
