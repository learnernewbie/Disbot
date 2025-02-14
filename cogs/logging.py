import discord
from discord import app_commands
from discord.ext import commands, tasks
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Union
import os

class LoggingCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.log_channels: Dict[str, Dict[str, int]] = {}
        self.log_settings: Dict[str, Dict[str, Union[List[str], int, bool]]] = {}
        self.audit_cache: Dict[str, Dict] = {}
        self.active_logs: Dict[str, Dict[int, Dict]] = {}
        self.command_cooldowns: Dict[str, datetime] = {}
        self.load_data()
        print("LoggingCog initialized")
        self.update_logs.start()
        self.cleanup_old_logs.start()  # Start cleanup task

    def cog_unload(self):
        self.update_logs.cancel()
        self.cleanup_old_logs.cancel()

    @tasks.loop(hours=1)  # Run cleanup every hour
    async def cleanup_old_logs(self):
        """Cleanup old logs based on retention settings"""
        try:
            now = datetime.utcnow()
            for guild_id, settings in self.log_settings.items():
                retention_days = settings.get("retention_days", 30)
                cutoff_date = now - timedelta(days=retention_days)

                # Clean up active logs
                if guild_id in self.active_logs:
                    self.active_logs[guild_id] = {
                        log_id: log_data
                        for log_id, log_data in self.active_logs[guild_id].items()
                        if datetime.fromisoformat(log_data["timestamp"]) > cutoff_date
                    }

                # Clean up audit cache
                if guild_id in self.audit_cache:
                    self.audit_cache[guild_id] = {
                        event_id: event_data
                        for event_id, event_data in self.audit_cache[guild_id].items()
                        if datetime.fromisoformat(event_data["timestamp"]) > cutoff_date
                    }

            print(f"Log cleanup completed at {now.isoformat()}")
        except Exception as e:
            print(f"Error during log cleanup: {str(e)}")

    def load_data(self):
        try:
            os.makedirs('data', exist_ok=True)
            with open('data/log_channels.json', 'r') as f:
                self.log_channels = json.load(f)
            with open('data/log_settings.json', 'r') as f:
                self.log_settings = json.load(f)
            print("Successfully loaded logging configuration")
        except FileNotFoundError:
            print("No existing logging configuration found, initializing empty")
            self.log_channels = {}
            self.log_settings = {}
            self.save_data()
        except json.JSONDecodeError as e:
            print(f"Error decoding logging configuration: {str(e)}")
            self.log_channels = {}
            self.log_settings = {}
            self.save_data()

    def save_data(self):
        try:
            os.makedirs('data', exist_ok=True)
            with open('data/log_channels.json', 'w') as f:
                json.dump(self.log_channels, f, indent=4)
            with open('data/log_settings.json', 'w') as f:
                json.dump(self.log_settings, f, indent=4)
            print("Successfully saved logging configuration")
        except Exception as e:
            print(f"Error saving logging configuration: {str(e)}")

    async def ensure_guild_initialized(self, guild: discord.Guild) -> None:
        """Ensure guild data structures are initialized"""
        guild_id = str(guild.id)
        if guild_id not in self.log_channels:
            self.log_channels[guild_id] = {}
        if guild_id not in self.log_settings:
            self.log_settings[guild_id] = {
                "enabled_events": ["all"],
                "retention_days": 30,
                "include_audit": True,
                "max_logs_per_channel": 5000
            }

    async def get_log_channel(self, guild: discord.Guild, log_type: str = "all") -> Optional[discord.TextChannel]:
        """Get the appropriate logging channel based on log type"""
        try:
            guild_id = str(guild.id)
            if guild_id not in self.log_channels:
                return None

            channels = self.log_channels[guild_id]
            channel_id = channels.get(log_type) or channels.get("all")

            if channel_id:
                channel = self.bot.get_channel(channel_id)
                if channel and channel.permissions_for(guild.me).send_messages:
                    return channel
                else:
                    print(f"Missing permissions for log channel {channel_id} in guild {guild.id}")
            return None
        except Exception as e:
            print(f"Error getting log channel: {str(e)}")
            return None

    async def log_event(self, guild: discord.Guild, embed: discord.Embed, log_type: str = "all"):
        """Enhanced log event with size limits"""
        try:
            if not guild:
                print("Cannot log event: Guild is None")
                return

            await self.ensure_guild_initialized(guild)
            channel = await self.get_log_channel(guild, log_type)
            if not channel:
                return

            # Add timestamp if not present
            if not embed.timestamp:
                embed.timestamp = datetime.utcnow()

            # Enforce size limits on embed fields
            if len(embed.fields) > 25:  # Discord's limit
                embed.fields = embed.fields[:25]

            for field in embed.fields:
                if len(field.value) > 1024:
                    field.value = field.value[:1021] + "..."

            if embed.description and len(embed.description) > 4096:
                embed.description = embed.description[:4093] + "..."

            message = await channel.send(embed=embed)
            print(f"Successfully logged {log_type} event in channel {channel.name}")
            return message

        except discord.Forbidden as e:
            print(f"Forbidden error logging to channel: {str(e)}")
        except Exception as e:
            print(f"Error logging event: {str(e)}")

    @tasks.loop(seconds=30)
    async def update_logs(self):
        """Update active log messages periodically"""
        current_time = datetime.utcnow()
        for guild_id, logs in self.active_logs.copy().items():
            for log_id, log_data in logs.copy().items():
                try:
                    # Remove logs older than 1 hour
                    if current_time - log_data["timestamp"] > timedelta(hours=1):
                        del self.active_logs[guild_id][log_id]
                        if not self.active_logs[guild_id]:
                            del self.active_logs[guild_id]
                        continue

                    # Update the embed with latest information
                    channel = self.bot.get_channel(log_data["channel_id"])
                    if not channel:
                        continue

                    try:
                        message = await channel.fetch_message(log_id)
                        if not message:
                            continue

                        # Update based on log type
                        if log_data["type"] == "mod":
                            embed = await self.create_mod_log_embed(log_data["data"])
                        elif log_data["type"] == "member":
                            embed = await self.create_member_log_embed(log_data["data"])
                        else:
                            continue

                        await message.edit(embed=embed)
                    except discord.NotFound:
                        # Message was deleted, remove from tracking
                        del self.active_logs[guild_id][log_id]

                except Exception as e:
                    print(f"Error updating log {log_id}: {e}")

    async def create_mod_log_embed(self, data: Dict) -> discord.Embed:
        """Create or update a moderation log embed"""
        embed = discord.Embed(
            title=data["title"],
            color=data["color"],
            timestamp=datetime.fromisoformat(data["timestamp"])
        )

        for field in data["fields"]:
            embed.add_field(
                name=field["name"],
                value=field["value"],
                inline=field.get("inline", True)
            )

        if "footer" in data:
            embed.set_footer(text=data["footer"])

        return embed

    async def create_member_log_embed(self, data: Dict) -> discord.Embed:
        """Create or update a member log embed"""
        embed = discord.Embed(
            title=data["title"],
            color=data["color"],
            timestamp=datetime.fromisoformat(data["timestamp"])
        )

        for field in data["fields"]:
            embed.add_field(
                name=field["name"],
                value=field["value"],
                inline=field.get("inline", True)
            )

        if "thumbnail_url" in data:
            embed.set_thumbnail(url=data["thumbnail_url"])

        return embed
    @app_commands.command(name="setlog")
    @app_commands.default_permissions(manage_guild=True)
    async def setlog(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        log_type: str = "all"
    ):
        """Set up logging channels for different types of logs"""
        try:
            if not interaction.guild:
                raise ValueError("This command can only be used in a server")

            await self.ensure_guild_initialized(interaction.guild)
            guild_id = str(interaction.guild.id)

            # Verify bot permissions in the target channel
            if not channel.permissions_for(interaction.guild.me).send_messages:
                raise discord.Forbidden("I need 'Send Messages' permission in the specified channel")

            self.log_channels[guild_id][log_type] = channel.id
            self.save_data()

            # Send test log
            test_embed = discord.Embed(
                title="📝 Logging Channel Set",
                description=f"This channel will now receive {log_type} logs",
                color=discord.Color.green(),
                timestamp=datetime.utcnow()
            )
            test_embed.add_field(name="Log Type", value=log_type)
            test_embed.add_field(name="Set By", value=interaction.user.mention)
            await channel.send(embed=test_embed)

            # Send confirmation
            confirm_embed = discord.Embed(
                title="✅ Logging Channel Set",
                description=f"Successfully set {log_type} logging channel to {channel.mention}",
                color=discord.Color.green()
            )
            await interaction.response.send_message(embed=confirm_embed)

        except Exception as e:
            error_embed = discord.Embed(
                title="❌ Error",
                description=str(e),
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=error_embed, ephemeral=True)

    @app_commands.command(name="logsettings", description="Configure server logging settings and controls")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        retention_days="How many days to keep logs (1-30 days). Older logs are automatically deleted",
        include_audit="Enable/disable detailed audit log tracking for better insights into server changes",
        log_type="Type of logs to configure: message (chat), member (joins/leaves), server (settings)",
        enabled="Turn the selected log type on (True) or off (False)"
    )
    async def logsettings(
        self,
        interaction: discord.Interaction,
        retention_days: Optional[app_commands.Range[int, 1, 30]] = None,
        include_audit: Optional[bool] = None,
        log_type: Optional[str] = None,
        enabled: Optional[bool] = None
    ):
        """Configure logging settings with enhanced controls

        Parameters
        ----------
        retention_days : Optional[int]
            Number of days to keep logs (1-30). After this period, old logs are automatically deleted
        include_audit : Optional[bool]
            Enable or disable detailed audit log tracking for better insights
        log_type : Optional[str]
            Type of logs to configure: 'message' (chat), 'member' (joins/leaves), or 'server' (settings)
        enabled : Optional[bool]
            Turn the selected log type on or off
        """
        try:
            if not interaction.guild:
                raise ValueError("This command can only be used in a server")

            guild_id = str(interaction.guild.id)
            if guild_id not in self.log_settings:
                self.log_settings[guild_id] = {
                    "enabled_events": ["all"],
                    "retention_days": 30,
                    "include_audit": True,
                    "max_logs_per_channel": 5000
                }

            settings = self.log_settings[guild_id]

            # Update settings based on parameters
            if retention_days is not None:
                settings["retention_days"] = max(1, min(30, retention_days))

            if include_audit is not None:
                settings["include_audit"] = include_audit

            if log_type and enabled is not None:
                if enabled and log_type not in settings["enabled_events"]:
                    settings["enabled_events"].append(log_type)
                elif not enabled and log_type in settings["enabled_events"]:
                    settings["enabled_events"].remove(log_type)

            self.save_data()

            # Create response embed
            embed = discord.Embed(
                title="📝 Logging Settings",
                color=discord.Color.blue()
            )

            # Add fields for all settings
            embed.add_field(
                name="Retention Period",
                value=f"{settings['retention_days']} days"
            )
            embed.add_field(
                name="Audit Log Integration",
                value="✅ Enabled" if settings["include_audit"] else "❌ Disabled"
            )
            embed.add_field(
                name="Max Logs per Channel",
                value=str(settings.get("max_logs_per_channel", 5000))
            )

            # Show enabled event types
            enabled_events = settings["enabled_events"]
            embed.add_field(
                name="Enabled Event Types",
                value="\n".join(f"• {event}" for event in enabled_events) or "None",
                inline=False
            )

            # Show configured channels
            channels = self.log_channels.get(guild_id, {})
            if channels:
                channel_text = "\n".join(
                    f"{log_type}: {self.bot.get_channel(channel_id).mention}"
                    for log_type, channel_id in channels.items()
                    if self.bot.get_channel(channel_id)
                )
                embed.add_field(
                    name="Logging Channels",
                    value=channel_text,
                    inline=False
                )

            await interaction.response.send_message(embed=embed)

        except Exception as e:
            error_embed = discord.Embed(
                title="❌ Error",
                description=str(e),
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=error_embed, ephemeral=True)

    async def _check_cooldown(self, user_id: int, command: str, cooldown: int = 5) -> bool:
        """Check if a command is on cooldown"""
        key = f"{user_id}_{command}"
        if key in self.command_cooldowns:
            if datetime.utcnow() - self.command_cooldowns[key] < timedelta(seconds=cooldown):
                return False
        self.command_cooldowns[key] = datetime.utcnow()
        return True

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        embed = discord.Embed(
            title="🔨 Member Banned",
            description=f"{user.mention} was banned from the server",
            color=discord.Color.red(),
            timestamp=datetime.utcnow()
        )

        if self.log_settings.get(str(guild.id), {}).get("include_audit", True):
            async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.ban):
                if entry.target.id == user.id:
                    embed.add_field(
                        name="Banned By",
                        value=entry.user.mention
                    )
                    if entry.reason:
                        embed.add_field(
                            name="Reason",
                            value=entry.reason
                        )
                    break

        await self.log_event(guild, embed, "mod")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        embed = discord.Embed(
            title="👋 Member Joined",
            color=discord.Color.green(),
            timestamp=datetime.utcnow()
        )

        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(
            name="Member",
            value=f"{member.mention} (`{member.id}`)"
        )
        embed.add_field(
            name="Account Created",
            value=f"<t:{int(member.created_at.timestamp())}:R>"
        )

        await self.log_event(member.guild, embed, "member")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        embed = discord.Embed(
            title="👋 Member Left",
            color=discord.Color.orange(),
            timestamp=datetime.utcnow()
        )

        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(
            name="Member",
            value=f"{member.mention} (`{member.id}`)"
        )
        embed.add_field(
            name="Joined Server",
            value=f"<t:{int(member.joined_at.timestamp())}:R>"
        )

        roles = [role.mention for role in member.roles[1:]]
        if roles:
            embed.add_field(
                name="Roles",
                value=" ".join(roles),
                inline=False
            )

        await self.log_event(member.guild, embed, "member")

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.author.bot:
            return

        embed = discord.Embed(
            title="🗑️ Message Deleted",
            color=discord.Color.red(),
            timestamp=datetime.utcnow()
        )

        embed.add_field(
            name="Author",
            value=f"{message.author.mention} (`{message.author.id}`)"
        )
        embed.add_field(
            name="Channel",
            value=message.channel.mention
        )

        if message.content:
            if len(message.content) > 1024:
                embed.add_field(
                    name="Content",
                    value=message.content[:1021] + "...",
                    inline=False
                )
            else:
                embed.add_field(
                    name="Content",
                    value=message.content,
                    inline=False
                )

        if message.attachments:
            attachment_list = "\n".join(
                f"[{a.filename}]({a.url})" for a in message.attachments
            )
            embed.add_field(
                name="Attachments",
                value=attachment_list,
                inline=False
            )

        await self.log_event(message.guild, embed, "msg")

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if before.author.bot or before.content == after.content:
            return

        embed = discord.Embed(
            title="✏️ Message Edited",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )

        embed.add_field(
            name="Author",
            value=f"{before.author.mention} (`{before.author.id}`)"
        )
        embed.add_field(
            name="Channel",
            value=before.channel.mention
        )
        embed.add_field(
            name="Jump to Message",
            value=f"[Click Here]({after.jump_url})"
        )

        if len(before.content) > 1024:
            embed.add_field(
                name="Before",
                value=before.content[:1021] + "...",
                inline=False
            )
        else:
            embed.add_field(
                name="Before",
                value=before.content or "Empty",
                inline=False
            )

        if len(after.content) > 1024:
            embed.add_field(
                name="After",
                value=after.content[:1021] + "...",
                inline=False
            )
        else:
            embed.add_field(
                name="After",
                value=after.content or "Empty",
                inline=False
            )

        await self.log_event(before.guild, embed, "msg")

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        embed = discord.Embed(
            title="📝 Channel Created",
            color=discord.Color.green(),
            timestamp=datetime.utcnow()
        )

        embed.add_field(
            name="Name",
            value=f"{channel.name} (`{channel.id}`)"
        )
        embed.add_field(
            name="Type",
            value=str(channel.type)
        )

        if isinstance(channel, discord.TextChannel):
            embed.add_field(
                name="Category",
                value=channel.category.name if channel.category else "None"
            )

        if self.log_settings.get(str(channel.guild.id), {}).get("include_audit", True):
            async for entry in channel.guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_create):
                if entry.target.id == channel.id:
                    embed.add_field(
                        name="Created By",
                        value=entry.user.mention
                    )
                    break

        await self.log_event(channel.guild, embed, "server")

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        embed = discord.Embed(
            title="🗑️ Channel Deleted",
            color=discord.Color.red(),
            timestamp=datetime.utcnow()
        )

        embed.add_field(
            name="Name",
            value=f"{channel.name} (`{channel.id}`)"
        )
        embed.add_field(
            name="Type",
            value=str(channel.type)
        )

        if self.log_settings.get(str(channel.guild.id), {}).get("include_audit", True):
            async for entry in channel.guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_delete):
                if entry.target.id == channel.id:
                    embed.add_field(
                        name="Deleted By",
                        value=entry.user.mention
                    )
                    break

        await self.log_event(channel.guild, embed, "server")

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.roles != after.roles:
            # Role changes
            added_roles = set(after.roles) - set(before.roles)
            removed_roles = set(before.roles) - set(after.roles)

            if added_roles or removed_roles:
                embed = discord.Embed(
                    title="👥 Member Roles Updated",
                    color=discord.Color.blue(),
                    timestamp=datetime.utcnow()
                )

                embed.add_field(
                    name="Member",
                    value=f"{after.mention} (`{after.id}`)"
                )

                if added_roles:
                    embed.add_field(
                        name="Added Roles",
                        value=" ".join(role.mention for role in added_roles),
                        inline=False
                    )

                if removed_roles:
                    embed.add_field(
                        name="Removed Roles",
                        value=" ".join(role.mention for role in removed_roles),
                        inline=False
                    )

                if self.log_settings.get(str(after.guild.id), {}).get("include_audit", True):
                    async for entry in after.guild.audit_logs(limit=1, action=discord.AuditLogAction.member_role_update):
                        if entry.target.id == after.id:
                            embed.add_field(
                                name="Updated By",
                                value=entry.user.mention
                            )
                            break

                await self.log_event(after.guild, embed, "member")

        # Nickname changes
        if before.nick != after.nick:
            embed = discord.Embed(
                title="📝 Nickname Changed",
                color=discord.Color.blue(),
                timestamp=datetime.utcnow()
            )

            embed.add_field(
                name="Member",
                value=f"{after.mention} (`{after.id}`)"
            )
            embed.add_field(
                name="Before",
                value=before.nick or "None"
            )
            embed.add_field(
                name="After",
                value=after.nick or "None"
            )

            if self.log_settings.get(str(after.guild.id), {}).get("include_audit", True):
                async for entry in after.guild.audit_logs(limit=1, action=discord.AuditLogAction.member_update):
                    if entry.target.id == after.id:
                        embed.add_field(
                            name="Changed By",
                            value=entry.user.mention
                        )
                        break

            await self.log_event(after.guild, embed, "member")

    @commands.Cog.listener()
    async def on_member_kick(self, guild: discord.Guild, user: discord.User):
        """Event handler for member kicks"""
        if not guild:
            return

        embed = discord.Embed(
            title="👢 Member Kicked",
            description=f"{user.mention} was kicked from the server",
            color=discord.Color.orange(),
            timestamp=datetime.utcnow()
        )

        if self.log_settings.get(str(guild.id), {}).get("include_audit", True):
            try:
                async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.kick):
                    if entry.target.id == user.id:
                        embed.add_field(
                            name="Kicked By",
                            value=entry.user.mention,
                            inline=True
                        )
                        if entry.reason:
                            embed.add_field(
                                name="Reason",
                                value=entry.reason,
                                inline=True
                            )
                        break
            except discord.Forbidden:
                embed.add_field(
                    name="Note",
                    value="Could not fetch audit log details (Missing Permissions)",
                    inline=False
                )

        # Send log message
        try:
            await self.log_event(guild, embed, "mod")
            print(f"Successfully logged kick event for user {user.id} in guild {guild.id}")
        except Exception as e:
            print(f"Error logging kick event: {str(e)}")



async def setup(bot):
    try:
        await bot.add_cog(LoggingCog(bot))
        print("Successfully loaded LoggingCog")
    except Exception as e:
        print(f"Error loading LoggingCog: {str(e)}")