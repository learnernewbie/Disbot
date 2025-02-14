import discord
from discord import app_commands
from discord.ext import commands, tasks
import json
from datetime import datetime, timedelta
from utils.helpers import parse_time
from typing import Dict, Optional
import os
import re

class UtilityCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.afk_users = {}
        self.sticky_messages = {}
        self.reminders = {}
        self.invite_tracking = {}
        self.command_cooldowns = {}
        self.custom_triggers = {}
        self.trigger_patterns = {}  # Cache for compiled regex patterns
        self.trigger_cooldowns = {}  # Rate limiting for triggers
        self.load_data()
        self.trigger_cleanup_task.start()

    async def ensure_guild_initialized(self, guild: discord.Guild) -> None:
        """Ensure guild data structures are initialized with proper error handling"""
        try:
            if not guild:
                raise ValueError("Invalid guild object")

            guild_id = str(guild.id)
            if guild_id not in self.afk_users:
                self.afk_users[guild_id] = {}
            if guild_id not in self.sticky_messages:
                self.sticky_messages[guild_id] = {}
            if guild_id not in self.invite_tracking:
                self.invite_tracking[guild_id] = {}
            if guild_id not in self.command_cooldowns:
                self.command_cooldowns[guild_id] = {}
            if guild_id not in self.custom_triggers:
                self.custom_triggers[guild_id] = {}


            print(f"Successfully initialized data for guild: {guild.name} ({guild_id})")
        except Exception as e:
            print(f"Error initializing guild {guild.id if guild else 'Unknown'}: {str(e)}")
            raise

    async def check_guild_permissions(self, interaction: discord.Interaction, action: str) -> bool:
        """Enhanced permission checking with proper error messages"""
        if not interaction.guild:
            raise ValueError("This command can only be used in a server")

        bot_member = interaction.guild.me
        required_permissions = {
            "manage_messages": bot_member.guild_permissions.manage_messages,
            "manage_channels": bot_member.guild_permissions.manage_channels,
            "manage_roles": bot_member.guild_permissions.manage_roles,
            "view_audit_log": bot_member.guild_permissions.view_audit_log,
            "moderate_members": bot_member.guild_permissions.moderate_members
        }

        if action not in required_permissions:
            return True

        if not required_permissions[action]:
            permission_name = action.replace("_", " ").title()
            raise discord.Forbidden(f"I need the '{permission_name}' permission to perform this action")

        return True

    async def handle_command_error(self, interaction: discord.Interaction, error: Exception):
        """Enhanced error handler for utility commands"""
        error_embed = discord.Embed(
            title="❌ Error",
            color=discord.Color.red()
        )

        if isinstance(error, ValueError) and "can only be used in a server" in str(error):
            error_embed.description = "This command can only be used in a server"
        elif isinstance(error, app_commands.MissingPermissions):
            error_embed.description = "You don't have the required permissions to use this command"
        elif isinstance(error, discord.Forbidden):
            error_embed.description = str(error)
        elif isinstance(error, ValueError):
            error_embed.description = str(error)
        else:
            error_embed.description = f"An unexpected error occurred: {str(error)}"
            print(f"Error in {interaction.command.name if interaction.command else 'unknown command'}: {str(error)}")

        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=error_embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=error_embed, ephemeral=True)
        except Exception as e:
            print(f"Error sending error message: {str(e)}")

    def load_data(self):
        """Load utility data from JSON files"""
        try:
            with open('data/sticky_messages.json', 'r') as f:
                self.sticky_messages = json.load(f)
            with open('data/reminders.json', 'r') as f:
                self.reminders = json.load(f)
            with open('data/invite_tracking.json', 'r') as f:
                self.invite_tracking = json.load(f)
            with open('data/custom_triggers.json', 'r') as f:
                self.custom_triggers = json.load(f)

        except FileNotFoundError:
            # Initialize with empty data
            self.sticky_messages = {}
            self.reminders = {}
            self.invite_tracking = {}
            self.custom_triggers = {}
            self.save_data()
        except json.JSONDecodeError as e:
            print(f"Error decoding JSON data: {str(e)}")
            # Initialize with empty data
            self.sticky_messages = {}
            self.reminders = {}
            self.invite_tracking = {}
            self.custom_triggers = {}
            self.save_data()
        except Exception as e:
            print(f"Error loading data: {str(e)}")
            # Initialize with empty data
            self.sticky_messages = {}
            self.reminders = {}
            self.invite_tracking = {}
            self.custom_triggers = {}

    def save_data(self):
        """Save utility data to JSON files"""
        try:
            os.makedirs('data', exist_ok=True)
            with open('data/sticky_messages.json', 'w') as f:
                json.dump(self.sticky_messages, f, indent=4)
            with open('data/reminders.json', 'w') as f:
                json.dump(self.reminders, f, indent=4)
            with open('data/invite_tracking.json', 'w') as f:
                json.dump(self.invite_tracking, f, indent=4)
            with open('data/custom_triggers.json', 'w') as f:
                json.dump(self.custom_triggers, f, indent=4)
        except Exception as e:
            print(f"Error saving data: {str(e)}")

    def _check_cooldown(self, guild_id: int, user_id: int, command: str, cooldown: int) -> bool:
        """Check if a command is on cooldown for a specific user in a guild"""
        guild_key = str(guild_id)
        user_key = str(user_id)
        if guild_key not in self.command_cooldowns:
            self.command_cooldowns[guild_key] = {}
        if user_key not in self.command_cooldowns[guild_key]:
            self.command_cooldowns[guild_key][user_key] = {}

        command_cooldowns = self.command_cooldowns[guild_key][user_key]
        if command in command_cooldowns:
            if datetime.utcnow() - datetime.fromisoformat(command_cooldowns[command]) < timedelta(seconds=cooldown):
                return False
        command_cooldowns[command] = datetime.utcnow().isoformat()
        return True

    @app_commands.command(name="botinvite")
    async def botinvite(self, interaction: discord.Interaction):
        """Get a link to invite the bot to your server"""
        try:
            permissions = discord.Permissions(
                manage_guild=True,
                manage_messages=True,
                kick_members=True,
                ban_members=True,
                moderate_members=True,
                view_audit_log=True,
                manage_roles=True,
                view_guild_insights=True,
                manage_events=True,
                read_messages=True,
                send_messages=True,
                embed_links=True,
                attach_files=True,
                read_message_history=True,
                add_reactions=True,
                use_external_emojis=True,
                manage_webhooks=True,
                create_instant_invite=True,
                manage_channels=True,
                view_channel=True,
                mention_everyone=True,
                change_nickname=True,
                manage_nicknames=True
            )

            scopes = [
                'bot',
                'applications.commands'
            ]

            invite_link = discord.utils.oauth_url(
                self.bot.user.id,
                permissions=permissions,
                scopes=scopes
            )

            embed = discord.Embed(
                title="🤖 Invite Bot to Server",
                description="Click the link below to add the bot to your server with all required permissions.",
                color=discord.Color.blue()
            )
            embed.add_field(
                name="Required Permissions",
                value="• Manage Messages & Roles\n• Moderate Members\n• View Audit Log\n• Read & Send Messages",
                inline=False
            )
            embed.add_field(name="🔗 Invite Link", value=invite_link, inline=False)
            embed.set_footer(text="Note: You need 'Manage Server' permission to add the bot")

            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            await self.handle_command_error(interaction, e)

    @app_commands.command(name="afk")
    @app_commands.describe(message="The AFK message to display (max 100 characters)")
    async def afk(self, interaction: discord.Interaction, message: str = "AFK"):
        """Set your AFK status with enhanced validation"""
        try:
            if not interaction.guild:
                raise ValueError("This command can only be used in a server")

            # Validate message length and content
            message = message.strip()
            if len(message) > 100:
                raise ValueError("AFK message cannot be longer than 100 characters")

            if not message:
                message = "AFK"

            # Initialize guild data
            await self.ensure_guild_initialized(interaction.guild)

            guild_id = str(interaction.guild.id)
            user_id = str(interaction.user.id)

            # Set AFK status
            self.afk_users[guild_id][user_id] = {
                "message": message,
                "timestamp": datetime.utcnow().isoformat()
            }

            embed = discord.Embed(
                title="AFK Status Set",
                description=f"Set your AFK status in {interaction.guild.name}",
                color=discord.Color.blue()
            )
            embed.add_field(name="Message", value=message)
            embed.set_footer(text="You'll be marked as back when you send a message")

            await interaction.response.send_message(embed=embed)
        except Exception as e:
            await self.handle_command_error(interaction, e)

    @app_commands.command(name="sticky")
    @app_commands.describe(message="The message to make sticky")
    @app_commands.default_permissions(manage_messages=True)
    async def sticky(self, interaction: discord.Interaction, message: str):
        """Create a sticky message with enhanced error handling"""
        try:
            if not interaction.guild:
                raise ValueError("This command can only be used in a server")

            # Check permissions
            await self.ensure_guild_initialized(interaction.guild)
            await self.check_guild_permissions(interaction, "manage_messages")

            # Validate message length and content
            if not message.strip():
                raise ValueError("Sticky message cannot be empty")

            if len(message) > 2000:
                raise ValueError("Sticky message cannot be longer than 2000 characters")

            guild_id = str(interaction.guild.id)
            channel_id = str(interaction.channel.id)

            # Delete previous sticky if it exists
            if channel_id in self.sticky_messages.get(guild_id, {}):
                try:
                    old_message_id = self.sticky_messages[guild_id][channel_id]["message_id"]
                    old_message = await interaction.channel.fetch_message(old_message_id)
                    await old_message.delete()
                except Exception as e:
                    print(f"Error deleting old sticky message: {str(e)}")

            # Create new sticky message
            try:
                sticky_message = await interaction.channel.send(f"📌 {message}")
                if guild_id not in self.sticky_messages:
                    self.sticky_messages[guild_id] = {}
                self.sticky_messages[guild_id][channel_id] = {
                    "content": message,
                    "message_id": sticky_message.id
                }
                self.save_data()

                embed = discord.Embed(
                    title="Sticky Message Created",
                    description="Message has been pinned to this channel",
                    color=discord.Color.green()
                )
                embed.add_field(name="Content", value=message[:1024])
                await interaction.response.send_message(embed=embed, ephemeral=True)
            except Exception as e:
                raise discord.Forbidden(f"Failed to create sticky message: {str(e)}")

        except Exception as e:
            await self.handle_command_error(interaction, e)

    @app_commands.command(name="unsticky")
    @app_commands.default_permissions(manage_messages=True)
    async def unsticky(self, interaction: discord.Interaction):
        """Remove the sticky message from the current channel"""
        try:
            await self.ensure_guild_initialized(interaction.guild)
            await self.check_guild_permissions(interaction, "manage_messages")

            guild_id = str(interaction.guild.id)
            channel_id = str(interaction.channel.id)

            if guild_id not in self.sticky_messages or channel_id not in self.sticky_messages[guild_id]:
                raise ValueError("No sticky message found in this channel")

            # Delete the sticky message
            try:
                message_id = self.sticky_messages[guild_id][channel_id]["message_id"]
                message = await interaction.channel.fetch_message(message_id)
                await message.delete()
            except:
                pass  # Message might already be deleted

            # Remove from tracking
            del self.sticky_messages[guild_id][channel_id]
            self.save_data()

            embed = discord.Embed(
                title="Sticky Message Removed",
                description="The sticky message has been removed from this channel",
                color=discord.Color.green()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            await self.handle_command_error(interaction, e)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Handle AFK, sticky message, and custom trigger updates"""
        if message.author.bot or not message.guild:
            return

        # Run custom triggers in the background
        self.bot.loop.create_task(self.handle_custom_triggers(message))

        guild_id = str(message.guild.id)

        # AFK System
        if guild_id in self.afk_users:
            # Check mentioned users
            for user in message.mentions:
                user_id = str(user.id)
                if user_id in self.afk_users[guild_id]:
                    afk_data = self.afk_users[guild_id][user_id]
                    try:
                        await message.channel.send(
                            f"{user.name} is AFK: {afk_data['message']} "
                            f"(Since: <t:{int(datetime.fromisoformat(afk_data['timestamp']).timestamp())}:R>)"
                        )
                    except Exception as e:
                        print(f"Error sending AFK message: {str(e)}")

            # Return from AFK
            author_id = str(message.author.id)
            if author_id in self.afk_users[guild_id]:
                try:
                    del self.afk_users[guild_id][author_id]
                    await message.channel.send(f"Welcome back {message.author.name}! I've removed your AFK status.")
                except Exception as e:
                    print(f"Error removing AFK status: {str(e)}")

        # Sticky Messages
        if guild_id in self.sticky_messages:
            channel_id = str(message.channel.id)
            if channel_id in self.sticky_messages[guild_id]:
                sticky_data = self.sticky_messages[guild_id][channel_id]
                try:
                    # Delete old sticky message
                    old_message = await message.channel.fetch_message(sticky_data["message_id"])
                    await old_message.delete()
                except Exception as e:
                    print(f"Error deleting old sticky message: {str(e)}")

                try:
                    # Send new sticky message
                    new_sticky = await message.channel.send(f"📌 {sticky_data['content']}")
                    sticky_data["message_id"] = new_sticky.id
                    self.save_data()
                except Exception as e:
                    print(f"Error creating new sticky message: {str(e)}")

    @tasks.loop(minutes=5)  # Reduced from 30 to 5 minutes for more frequent cleanup
    async def trigger_cleanup_task(self):
        """Run cleanup task periodically"""
        await self.cleanup_trigger_caches()

    async def cleanup_trigger_caches(self):
        """Periodic cleanup of trigger caches to prevent memory leaks"""
        try:
            # Cleanup trigger cooldowns
            now = datetime.utcnow()
            cleanup_threshold = now - timedelta(minutes=2)  # Reduced from 5 to 2 minutes
            self.trigger_cooldowns = {
                key: timestamp 
                for key, timestamp in self.trigger_cooldowns.items()
                if timestamp > cleanup_threshold
            }

            # Cleanup unused patterns
            active_patterns = set()
            for guild_data in self.custom_triggers.values():
                for trigger_data in guild_data.values():
                    active_patterns.add(trigger_data["pattern"])

            # Remove cached patterns that are no longer in use
            self.trigger_patterns = {
                pattern: compiled 
                for pattern, compiled in self.trigger_patterns.items()
                if pattern in active_patterns
            }

            # Limit pattern cache size to prevent memory issues
            if len(self.trigger_patterns) > 1000:  # Maximum cache size
                oldest_patterns = sorted(self.trigger_patterns.keys())[:-1000]
                for pattern in oldest_patterns:
                    del self.trigger_patterns[pattern]

            print(f"Cleanup completed: {len(self.trigger_patterns)} patterns cached")
        except Exception as e:
            print(f"Error cleaning up trigger caches: {str(e)}")

    async def handle_custom_triggers(self, message: discord.Message):
        """Optimized custom trigger handling with improved caching and rate limiting"""
        if not message.guild or message.author.bot:
            return

        guild_id = str(message.guild.id)
        if guild_id not in self.custom_triggers:
            return

        # Check rate limit with shorter cooldown
        cooldown_key = f"{guild_id}_{message.channel.id}"
        now = datetime.utcnow()
        if cooldown_key in self.trigger_cooldowns:
            if (now - self.trigger_cooldowns[cooldown_key]) < timedelta(seconds=1):  # Reduced from 2 to 1 second
                return
        self.trigger_cooldowns[cooldown_key] = now

        content = message.content.lower()

        # Process triggers in batches for better performance
        batch_size = 5
        triggers = list(self.custom_triggers[guild_id].items())

        for i in range(0, len(triggers), batch_size):
            batch = triggers[i:i + batch_size]
            for trigger_name, trigger_data in batch:
                pattern = trigger_data["pattern"]

                # Use cached pattern or compile and cache it
                if pattern not in self.trigger_patterns:
                    try:
                        self.trigger_patterns[pattern] = re.compile(pattern, re.IGNORECASE)
                    except re.error:
                        continue

                # Check if message matches pattern
                if self.trigger_patterns[pattern].search(content):
                    try:
                        if trigger_data["type"] == "message":
                            await message.channel.send(trigger_data["response"])
                        elif trigger_data["type"] == "reaction":
                            await message.add_reaction(trigger_data["emoji"])
                    except discord.Forbidden:
                        print(f"Missing permissions for trigger response in {message.guild.name}")
                    except Exception as e:
                        print(f"Error handling trigger {trigger_name} in {message.guild.name}: {e}")
                    return  # Exit after first match to prevent multiple triggers

    @app_commands.command(name="serverinfo")
    async def serverinfo(self, interaction: discord.Interaction):
        """Display server information and bot status"""
        try:
            await self.ensure_guild_initialized(interaction.guild)
            guild = interaction.guild
            embed = discord.Embed(
                title=f"{guild.name} Server Information",
                color=discord.Color.blue()
            )

            # General Info
            embed.add_field(name="Owner", value=guild.owner.mention)
            embed.add_field(name="Created", value=guild.created_at.strftime("%Y-%m-%d"))
            embed.add_field(name="Region", value=str(guild.preferred_locale))

            # Member Stats
            total_members = guild.member_count
            humans = len([m for m in guild.members if not m.bot])
            bots = total_members - humans
            embed.add_field(name="Total Members", value=f"👥 {total_members}")
            embed.add_field(name="Humans", value=f"👤 {humans}")
            embed.add_field(name="Bots", value=f"🤖 {bots}")

            # Bot Permissions
            bot_member = guild.me
            permissions = []
            if bot_member.guild_permissions.administrator:
                permissions.append("✅ Administrator")
            if bot_member.guild_permissions.manage_guild:
                permissions.append("✅ Manage Server")
            if bot_member.guild_permissions.manage_messages:
                permissions.append("✅ Manage Messages")
            if bot_member.guild_permissions.kick_members:
                permissions.append("✅ Kick Members")
            if bot_member.guild_permissions.ban_members:
                permissions.append("✅ Ban Members")

            embed.add_field(
                name="Bot Permissions",
                value="\n".join(permissions) or "No special permissions",
                inline=False
            )

            # Data Status
            data_files = {
                "Reputation": os.path.exists('data/reputation.json'),
                "Tickets": os.path.exists('data/tickets.json'),
                "Warnings": os.path.exists('data/warnings.json'),
                "Violations": os.path.exists('data/violations.json')
            }

            status = []
            for system, exists in data_files.items():
                status.append(f"{'✅' if exists else '❌'} {system}")

            embed.add_field(
                name="System Status",
                value="\n".join(status),
                inline=False
            )

            await interaction.response.send_message(embed=embed)
        except Exception as e:
            await self.handle_command_error(interaction, e)

    @app_commands.command(name="userinfo")
    async def userinfo(self, interaction: discord.Interaction, member: discord.Member = None):
        """Display user information"""
        try:
            await self.ensure_guild_initialized(interaction.guild)
            member = member or interaction.user
            embed = discord.Embed(title=f"User Information for {member.name}")
            embed.set_thumbnail(url=member.display_avatar.url)

            embed.add_field(name="Joined Server", value=member.joined_at.strftime("%Y-%m-%d"))
            embed.add_field(name="Account Created", value=member.created_at.strftime("%Y-%m-%d"))

            # Role Information
            roles = [role.mention for role in reversed(member.roles[1:])]
            embed.add_field(name=f"Roles [{len(roles)}]", value=" ".join(roles) if roles else "None", inline=False)

            # Permissions
            key_permissions = []
            if member.guild_permissions.administrator:
                key_permissions.append("Administrator")
            if member.guild_permissions.manage_guild:
                key_permissions.append("Manage Server")
            if member.guild_permissions.manage_roles:
                key_permissions.append("Manage Roles")
            if member.guild_permissions.manage_channels:
                key_permissions.append("Manage Channels")
            if member.guild_permissions.manage_messages:
                key_permissions.append("Manage Messages")

            embed.add_field(name="Key Permissions", value=", ".join(key_permissions) if key_permissions else "None", inline=False)

            await interaction.response.send_message(embed=embed)
        except Exception as e:
            await self.handle_command_error(interaction, e)

    @app_commands.command(name="remindme")
    async def remindme(self, interaction: discord.Interaction, reminder: str, time: str):
        """Set a reminder"""
        try:
            await self.ensure_guild_initialized(interaction.guild)
            duration = parse_time(time)
            reminder_time = datetime.utcnow() + duration

            self.reminders[f"{interaction.user.id}_{int(reminder_time.timestamp())}"] = {
                "user_id": interaction.user.id,
                "channel_id": interaction.channel.id,
                "reminder": reminder,
                "time": reminder_time.isoformat()
            }
            self.save_data()

            await interaction.response.send_message(
                f"I'll remind you about: {reminder} in {time} "
                f"(<t:{int(reminder_time.timestamp())}:R>)"
            )
        except ValueError:
            await interaction.response.send_message("Invalid time format! Use format like 1h, 30m, 1d")
        except Exception as e:
            await self.handle_command_error(interaction, e)

    @app_commands.command(name="calc")
    @app_commands.describe(expression="The mathematical expression to calculate")
    async def calc(self, interaction: discord.Interaction, expression: str):
        """Calculate a mathematical expression with enhanced security"""
        try:
            # Strict input validation using regex
            cleaned_expr = expression.replace(" ", "")
            if not re.match(r'^[\d\+\-\*\/\(\)\.]+$', cleaned_expr):
                raise ValueError("Expression can only contain numbers and basic operators (+, -, *, /, parentheses)")

            # Additional security checks
            if ".." in cleaned_expr or "//" in cleaned_expr:
                raise ValueError("Invalid expression")

            # Evaluate with restricted environment
            allowed_names = {"__builtins__": {}}
            result = eval(cleaned_expr, allowed_names, {})

            # Format result
            if isinstance(result, (int, float)):
                if result == float('inf') or result == float('-inf'):
                    raise ValueError("Result is too large")
                formatted_result = f"{result:,.2f}" if isinstance(result, float) else f"{result:,}"
                await interaction.response.send_message(f"`{expression} = {formatted_result}`")
            else:
                raise ValueError("Invalid result type")
        except Exception as e:
            error_msg = "Invalid expression" if isinstance(e, (ValueError, SyntaxError)) else "Error calculating result"
            await interaction.response.send_message(f"❌ {error_msg}", ephemeral=True)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        """Track successful bot invites"""
        try:
            await self.ensure_guild_initialized(guild)
            async for entry in guild.audit_logs(action=discord.AuditLogAction.bot_add, limit=1):
                if entry.target.id == self.bot.user.id:
                    inviter_id = str(entry.user.id)
                    if inviter_id in self.invite_tracking:
                        self.invite_tracking[inviter_id]["successful_invites"] += 1
                        self.save_data()
        except Exception as e:
            print(f"Error tracking guild join: {str(e)}")

    def cog_unload(self):
        """Cleanup when cog is unloaded"""
        self.trigger_cleanup_task.cancel()


async def setup(bot):
    try:
        await bot.add_cog(UtilityCog(bot))
        print("Successfully loaded UtilityCog")
    except Exception as e:
        print(f"Error loading UtilityCog: {str(e)}")