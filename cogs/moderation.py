import discord
from discord import app_commands
from discord.ext import commands, tasks
import json
import os
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Optional, Union, List, Any
from utils.helpers import parse_time, format_duration

class ModActionButtons(discord.ui.View):
    def __init__(self, mod_cog, target: discord.Member):
        super().__init__(timeout=60)
        self.mod_cog = mod_cog
        self.target = target
        self.locks: Dict[str, asyncio.Lock] = {}
        self.cooldowns: Dict[str, datetime] = {}

    async def get_lock(self, key: str) -> asyncio.Lock:
        """Get or create a lock for the given key"""
        if key not in self.locks:
            self.locks[key] = asyncio.Lock()
        return self.locks[key]

    async def check_cooldown(self, key: str) -> bool:
        """Check if an action is on cooldown"""
        now = datetime.utcnow()
        if key in self.cooldowns:
            if now - self.cooldowns[key] < timedelta(seconds=3):
                return False
        self.cooldowns[key] = now
        return True

    async def handle_button_error(self, interaction: discord.Interaction, error: Exception):
        """Handle errors in button interactions with improved feedback"""
        error_embed = discord.Embed(
            title="❌ Error",
            color=discord.Color.red()
        )

        if isinstance(error, discord.Forbidden):
            error_embed.description = "I don't have the required permissions to perform this action."
        elif isinstance(error, discord.NotFound):
            error_embed.description = "The target member could not be found. They may have left the server."
        elif isinstance(error, discord.HTTPException):
            error_embed.description = "Discord API error occurred. Please try again later."
        else:
            error_embed.description = f"An error occurred: {str(error)}"
            print(f"Button interaction error: {str(error)}")

        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=error_embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=error_embed, ephemeral=True)
        except Exception as e:
            print(f"Error sending error message: {str(e)}")

    @discord.ui.button(label="Warn", style=discord.ButtonStyle.secondary)
    async def warn_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            # Rate limit check
            if not await self.check_cooldown(f"warn_{interaction.user.id}"):
                await interaction.response.send_message("Please wait before using this button again.", ephemeral=True)
                return

            if not interaction.user.guild_permissions.manage_messages:
                raise discord.Forbidden("You don't have permission to warn members")

            if not interaction.guild.me.guild_permissions.manage_messages:
                raise discord.Forbidden("I don't have permission to warn members")

            async with await self.get_lock(f"warn_{self.target.id}"):
                await self.mod_cog.handle_violation(self.target, "Warning", severity=1)

                embed = discord.Embed(
                    title="Warning Issued",
                    description=f"Warning issued to {self.target.mention}",
                    color=discord.Color.yellow()
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            await self.handle_button_error(interaction, e)

    @discord.ui.button(label="Timeout (30m)", style=discord.ButtonStyle.primary)
    async def timeout_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            # Rate limit check
            if not await self.check_cooldown(f"timeout_{interaction.user.id}"):
                await interaction.response.send_message("Please wait before using this button again.", ephemeral=True)
                return

            if not interaction.user.guild_permissions.moderate_members:
                raise discord.Forbidden("You don't have permission to timeout members")

            if not interaction.guild.me.guild_permissions.moderate_members:
                raise discord.Forbidden("I don't have permission to timeout members")

            if self.target.top_role >= interaction.guild.me.top_role:
                raise ValueError("Cannot timeout a member with a higher role than me")

            if self.target.top_role >= interaction.user.top_role:
                raise ValueError("Cannot timeout a member with a higher role than you")

            if self.target.guild_permissions.administrator:
                raise ValueError("Cannot timeout an administrator")

            async with await self.get_lock(f"timeout_{self.target.id}"):
                duration = timedelta(minutes=30)
                await self.target.timeout(duration, reason=f"Timeout via moderation button by {interaction.user}")

                embed = discord.Embed(
                    title="Member Timed Out",
                    description=f"Timed out {self.target.mention} for 30 minutes",
                    color=discord.Color.blue()
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            await self.handle_button_error(interaction, e)

    @discord.ui.button(label="Kick", style=discord.ButtonStyle.danger)
    async def kick_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            # Rate limit check
            if not await self.check_cooldown(f"kick_{interaction.user.id}"):
                await interaction.response.send_message("Please wait before using this button again.", ephemeral=True)
                return

            if not interaction.user.guild_permissions.kick_members:
                raise discord.Forbidden("You don't have permission to kick members")

            if not interaction.guild.me.guild_permissions.kick_members:
                raise discord.Forbidden("I don't have permission to kick members")

            if self.target.top_role >= interaction.guild.me.top_role:
                raise ValueError("Cannot kick a member with a higher role than me")

            if self.target.top_role >= interaction.user.top_role:
                raise ValueError("Cannot kick a member with a higher role than you")

            if self.target.guild_permissions.administrator:
                raise ValueError("Cannot kick an administrator")

            async with await self.get_lock(f"kick_{self.target.id}"):
                await self.target.kick(reason=f"Kicked via moderation button by {interaction.user}")

                embed = discord.Embed(
                    title="Member Kicked",
                    description=f"Kicked {self.target.mention}",
                    color=discord.Color.red()
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            await self.handle_button_error(interaction, e)

class ModerationCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.warnings: Dict[str, Dict[str, List[Dict[str, Any]]]] = {} 
        self.temp_roles: Dict[str, Dict[str, Any]] = {}
        self.appeals: Dict[str, Dict[str, Any]] = {}
        self.violation_tracker: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
        self.command_cooldowns: Dict[str, datetime] = {}
        self.punishment_tiers = {
            1: {"action": "warn", "duration": None},
            2: {"action": "timeout", "duration": timedelta(minutes=30)},
            3: {"action": "timeout", "duration": timedelta(hours=2)},
            4: {"action": "timeout", "duration": timedelta(days=1)},
            5: {"action": "ban", "duration": None}
        }
        os.makedirs('data', exist_ok=True)
        self.load_data()
        self.check_temp_roles.start()

    def cog_unload(self):
        self.check_temp_roles.stop()

    async def ensure_guild_initialized(self, guild: discord.Guild) -> None:
        """Ensure guild data structures are initialized"""
        guild_id = str(guild.id)
        if guild_id not in self.violation_tracker:
            self.violation_tracker[guild_id] = {}
        if guild_id not in self.warnings:
            self.warnings[guild_id] = {}
        if guild_id not in self.appeals:
            self.appeals[guild_id] = {}
        self.save_data()

    async def check_guild_permissions(self, interaction: discord.Interaction, action: str) -> bool:
        """Check if the bot has required permissions in the guild"""
        guild = interaction.guild
        if not guild:
            raise ValueError("This command can only be used in a server")

        bot_member = guild.me
        required_permissions = {
            "kick": bot_member.guild_permissions.kick_members,
            "ban": bot_member.guild_permissions.ban_members,
            "timeout": bot_member.guild_permissions.moderate_members,
            "manage_roles": bot_member.guild_permissions.manage_roles,
            "manage_messages": bot_member.guild_permissions.manage_messages
        }

        if action not in required_permissions:
            return True

        if not required_permissions[action]:
            permission_name = action.replace("_", " ").title()
            raise discord.Forbidden(f"I need the '{permission_name}' permission to perform this action")

        return True

    async def handle_command_error(self, interaction: discord.Interaction, error: Exception):
        """Enhanced error handler for moderation commands"""
        error_embed = discord.Embed(
            title="❌ Moderation Error",
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
        """Load moderation data from JSON files"""
        try:
            with open('data/warnings.json', 'r') as f:
                self.warnings = json.load(f)
            with open('data/temp_roles.json', 'r') as f:
                self.temp_roles = json.load(f)
            with open('data/appeals.json', 'r') as f:
                self.appeals = json.load(f)
            with open('data/violations.json', 'r') as f:
                self.violation_tracker = json.load(f)
        except FileNotFoundError:
            # Create empty files if they don't exist
            self.warnings = {}
            self.temp_roles = {}
            self.appeals = {}
            self.violation_tracker = {}
            self.save_data()
        except json.JSONDecodeError as e:
            print(f"Error decoding JSON data: {str(e)}")
            # Initialize with empty data
            self.warnings = {}
            self.temp_roles = {}
            self.appeals = {}
            self.violation_tracker = {}
            self.save_data()

    def save_data(self):
        """Save moderation data to JSON files"""
        try:
            os.makedirs('data', exist_ok=True)
            for filename, data in [
                ('warnings.json', self.warnings),
                ('temp_roles.json', self.temp_roles),
                ('appeals.json', self.appeals),
                ('violations.json', self.violation_tracker)
            ]:
                with open(f'data/{filename}', 'w') as f:
                    json.dump(data, f, indent=4)
        except Exception as e:
            print(f"Error saving data: {str(e)}")

    async def initialize_guild(self, guild: discord.Guild):
        """Initialize moderation data for a new guild"""
        guild_id = str(guild.id)
        # Initialize all necessary dictionaries for the guild
        if guild_id not in self.violation_tracker:
            self.violation_tracker[guild_id] = {}
        if guild_id not in self.warnings:
            self.warnings[guild_id] = {}
        if guild_id not in self.appeals:
            self.appeals[guild_id] = {}
        self.save_data()

    async def handle_violation(self, member: discord.Member, violation_type: str, severity: int = 1):
        """Handle a rule violation with automatic punishment escalation"""
        try:
            if not member or not member.guild:
                raise ValueError("Invalid member object")

            if not member.guild.me.guild_permissions.manage_roles:
                raise discord.Forbidden("Bot lacks required permissions")

            guild_id = str(member.guild.id)
            user_id = str(member.id)

            await self.ensure_guild_initialized(member.guild)

            if guild_id not in self.violation_tracker:
                self.violation_tracker[guild_id] = {}
            if user_id not in self.violation_tracker[guild_id]:
                self.violation_tracker[guild_id][user_id] = []

            # Add new violation with additional validation
            if not isinstance(severity, int) or severity < 1 or severity > 5:
                severity = 1  # Default to lowest severity if invalid

            self.violation_tracker[guild_id][user_id].append({
                "type": violation_type,
                "severity": severity,
                "timestamp": datetime.utcnow().isoformat()
            })

            # Calculate tier based on recent violations
            recent_violations = [
                v for v in self.violation_tracker[guild_id][user_id]
                if datetime.utcnow() - datetime.fromisoformat(v["timestamp"]) < timedelta(days=30)
            ]

            tier = min(5, len(recent_violations))  # Cap at tier 5
            punishment = self.punishment_tiers[tier]

            reason = f"Auto-escalation: {violation_type} (Violation tier {tier})"

            # Execute punishment with enhanced error handling
            try:
                if punishment["action"] == "warn":
                    if guild_id not in self.warnings:
                        self.warnings[guild_id] = {}
                    if user_id not in self.warnings[guild_id]:
                        self.warnings[guild_id][user_id] = []
                    self.warnings[guild_id][user_id].append({
                        "reason": reason,
                        "moderator": self.bot.user.id,
                        "timestamp": datetime.utcnow().isoformat()
                    })

                elif punishment["action"] == "timeout":
                    if not member.guild.me.guild_permissions.moderate_members:
                        raise discord.Forbidden("Bot lacks timeout permissions")
                    await member.timeout(punishment["duration"], reason=reason)

                elif punishment["action"] == "ban":
                    if not member.guild.me.guild_permissions.ban_members:
                        raise discord.Forbidden("Bot lacks ban permissions")
                    await member.ban(reason=reason)

                self.save_data()

                # Update reputation if available
                reputation_cog = self.bot.get_cog("ReputationCog")
                if reputation_cog:
                    try:
                        await reputation_cog.handle_violation(member, violation_type, severity)
                    except Exception as e:
                        print(f"Error updating reputation: {str(e)}")

                # Log the punishment with enhanced error handling
                try:
                    log_cog = self.bot.get_cog("LoggingCog")
                    if log_cog:
                        embed = discord.Embed(
                            title="🛡️ Auto-Moderation Action",
                            description=f"Action taken against {member.mention}",
                            color=discord.Color.red(),
                            timestamp=datetime.utcnow()
                        )
                        embed.add_field(name="Violation", value=violation_type)
                        embed.add_field(name="Action", value=punishment["action"].title())
                        if punishment["duration"]:
                            embed.add_field(name="Duration", value=str(punishment["duration"]))
                        embed.add_field(name="Tier", value=str(tier))
                        embed.add_field(name="Total Recent Violations", value=str(len(recent_violations)))
                        embed.set_footer(text=f"User ID: {member.id}")

                        await log_cog.log_event(member.guild, embed, "mod")
                except Exception as e:
                    print(f"Error logging moderation action: {str(e)}")

                return tier, punishment["action"]

            except discord.Forbidden as e:
                print(f"Permission error executing punishment: {str(e)}")
                raise
            except Exception as e:
                print(f"Error executing punishment: {str(e)}")
                raise

        except Exception as e:
            print(f"Error in handle_violation: {str(e)}")
            raise

    @app_commands.command(name="violations", description="View a member's violation history")
    @app_commands.describe(
        member="The member whose violations to check (Example: @username)"
    )
    @app_commands.default_permissions(manage_messages=True)
    async def violations(self, interaction: discord.Interaction, member: discord.Member):
        """View a member's violation history"""
        guild_id = str(interaction.guild.id)
        user_id = str(member.id)

        await self.ensure_guild_initialized(interaction.guild)

        if (guild_id not in self.violation_tracker or 
            user_id not in self.violation_tracker[guild_id] or 
            not self.violation_tracker[guild_id][user_id]):
            await interaction.response.send_message(f"{member.mention} has no violations.")
            return

        violations = self.violation_tracker[guild_id][user_id]
        recent_violations = [
            v for v in violations
            if datetime.utcnow() - datetime.fromisoformat(v["timestamp"]) < timedelta(days=30)
        ]

        embed = discord.Embed(
            title=f"Violation History for {member.name}",
            color=discord.Color.orange()
        )

        for i, v in enumerate(recent_violations, 1):
            timestamp = datetime.fromisoformat(v["timestamp"])
            embed.add_field(
                name=f"Violation #{i}",
                value=f"Type: {v['type']}\n"
                      f"Severity: {v['severity']}\n"
                      f"When: <t:{int(timestamp.timestamp())}:R>",
                inline=False
            )

        embed.set_footer(text=f"Total violations in last 30 days: {len(recent_violations)}")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="clearviolations", description="Clear all violations from a member's history")
    @app_commands.describe(
        member="The member whose violations to clear (Example: @username)"
    )
    @app_commands.default_permissions(administrator=True)
    async def clearviolations(self, interaction: discord.Interaction, member: discord.Member):
        """Clear a member's violation history"""
        guild_id = str(interaction.guild.id)
        user_id = str(member.id)

        await self.ensure_guild_initialized(interaction.guild)

        if guild_id in self.violation_tracker and user_id in self.violation_tracker[guild_id]:
            del self.violation_tracker[guild_id][user_id]
            self.save_data()
            await interaction.response.send_message(f"Cleared violation history for {member.mention}")
        else:
            await interaction.response.send_message(f"{member.mention} has no violations to clear.")

    @app_commands.command(name="ban", description="Ban a member from the server with optional duration")
    @app_commands.describe(
        member="The member to ban (Example: @username)",
        reason="Reason for the ban (Example: 'Severe rule violation')",
        duration="Optional ban duration (Example: '7d' for 7 days, leave empty for permanent)"
    )
    @app_commands.default_permissions(ban_members=True)
    async def ban(self, interaction: discord.Interaction, member: discord.Member, 
                 reason: str = None, duration: str = None):
        """Ban a member, optionally with a duration"""
        try:
            await self.ensure_guild_initialized(interaction.guild)
            await self.check_guild_permissions(interaction, "ban")

            if member.top_role >= interaction.user.top_role:
                raise ValueError("You cannot ban this user!")

            if duration:
                try:
                    ban_duration = parse_time(duration)
                    unban_time = datetime.utcnow() + ban_duration
                    self.temp_roles[str(member.id)] = {
                        "action": "ban",
                        "guild_id": interaction.guild.id,
                        "expires": unban_time.isoformat(),
                        "reason": reason
                    }
                    self.save_data()
                    await interaction.response.send_message(
                        f"Banned {member.mention} for {format_duration(ban_duration)}. Reason: {reason}"
                    )
                except ValueError:
                    raise ValueError("Invalid duration format!")
            else:
                await member.ban(reason=reason)
                await interaction.response.send_message(
                    f"Banned {member.mention} permanently. Reason: {reason}"
                )
        except Exception as e:
            await self.handle_command_error(interaction, e)

    @app_commands.command(name="temprole", description="Assign a role to a member for a specific duration")
    @app_commands.describe(
        member="The member to give the role to (Example: @username)",
        role="The role to assign (Example: @Muted)",
        duration="How long to keep the role (Example: '2h' for 2 hours)"
    )
    @app_commands.default_permissions(manage_roles=True)
    async def temprole(self, interaction: discord.Interaction, member: discord.Member,
                      role: discord.Role, duration: str):
        """Assign a temporary role to a member"""
        try:
            await self.ensure_guild_initialized(interaction.guild)
            await self.check_guild_permissions(interaction, "manage_roles")

            role_duration = parse_time(duration)
            if role >= interaction.user.top_role:
                raise ValueError("You cannot assign this role!")

            await member.add_roles(role)
            expire_time = datetime.utcnow() + role_duration

            self.temp_roles[f"{member.id}_{role.id}"] = {
                "action": "role",
                "guild_id": interaction.guild.id,
                "role_id": role.id,
                "expires": expire_time.isoformat()
            }
            self.save_data()

            await interaction.response.send_message(
                f"Gave {member.mention} the role {role.name} for {format_duration(role_duration)}"
            )
        except ValueError as e:
            await self.handle_command_error(interaction, e)

    @app_commands.command(name="appeal", description="Submit an appeal if you've been punished")
    @app_commands.describe(
        reason="Explain why your punishment should be reconsidered (Example: 'It was a misunderstanding because...')"
    )
    async def appeal(self, interaction: discord.Interaction, reason: str):
        """Submit an appeal for a punishment"""
        try:
            await self.ensure_guild_initialized(interaction.guild)
            user_id = str(interaction.user.id)
            guild_id = str(interaction.guild.id)
            if guild_id not in self.appeals or user_id not in self.appeals[guild_id]:
                if user_id in self.appeals:
                    raise ValueError("You already have a pending appeal!")

                self.appeals[guild_id][user_id] = {
                    "reason": reason,
                    "timestamp": datetime.utcnow().isoformat(),
                    "status": "pending"
                }
                self.save_data()

                # Send appeal to mod channel
                mod_channel = discord.utils.get(
                    interaction.guild.channels,
                    name="mod-logs"
                )
                if mod_channel:
                    embed = discord.Embed(
                        title="New Appeal",
                        description=f"User: {interaction.user.mention}\nReason: {reason}",
                        color=discord.Color.blue()
                    )
                    message = await mod_channel.send(embed=embed)
                    await message.add_reaction("✅")
                    await message.add_reaction("❌")

                await interaction.response.send_message(
                    "Your appeal has been submitted and will be reviewed by moderators.",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message("You already have a pending appeal!")
        except Exception as e:
            await self.handle_command_error(interaction, e)

    @app_commands.command(name="restrict", description="Prevent a member from sending messages in all channels")
    @app_commands.describe(
        member="The member to restrict (Example: @username)"
    )
    @app_commands.default_permissions(manage_roles=True)
    async def restrict(self, interaction: discord.Interaction, member: discord.Member):
        """Restrict a user from sending messages in all channels"""
        try:
            await self.ensure_guild_initialized(interaction.guild)
            await self.check_guild_permissions(interaction, "manage_roles")
            for channel in interaction.guild.channels:
                if isinstance(channel, discord.TextChannel):
                    await channel.set_permissions(member, send_messages=False)

            await interaction.response.send_message(f"Restricted {member.mention} from sending messages.")
        except Exception as e:
            await self.handle_command_error(interaction, e)

    @tasks.loop(minutes=1)
    async def check_temp_roles(self):
        """Check and remove expired temporary roles"""
        current_time = datetime.utcnow()
        for key, data in list(self.temp_roles.items()):
            try:
                expire_time = datetime.fromisoformat(data["expires"])

                if current_time >= expire_time:
                    guild = self.bot.get_guild(data["guild_id"])
                    if not guild:
                        continue

                    if data["action"] == "ban":
                        try:
                            await guild.unban(discord.Object(id=int(key)))
                        except Exception as e:
                            print(f"Error unbanning user: {str(e)}")
                    elif data["action"] == "role":
                        try:
                            member_id, role_id = map(int, key.split("_"))
                            member = guild.get_member(member_id)
                            role = guild.get_role(role_id)

                            if member and role:
                                await member.remove_roles(role)
                        except Exception as e:
                            print(f"Error removing temporary role: {str(e)}")

                    del self.temp_roles[key]
                    self.save_data()
            except Exception as e:
                print(f"Error processing temporary role {key}: {str(e)}")
                continue

    @check_temp_roles.before_loop
    async def before_check_temp_roles(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="warn", description="Give a warning to a server member")
    @app_commands.describe(
        member="The member to warn (Example: @username)",
        reason="Reason for the warning (Example: 'Spamming in chat')"
    )
    @app_commands.default_permissions(manage_messages=True)
    async def warn(self, interaction: discord.Interaction, member: discord.Member, reason: str = None):
        """Warn a member"""
        try:
            await self.ensure_guild_initialized(interaction.guild)
            await self.check_guild_permissions(interaction, "manage_messages")

            if not interaction.user.guild_permissions.manage_messages:
                raise discord.Forbidden("You don't have permission to warn members")

            if member.bot:
                raise ValueError("Cannot warn bot accounts")

            if member.top_role >= interaction.user.top_role:
                raise ValueError("Cannot warn a member with a higher role than you")

            if len(reason or "") > 1000:
                raise ValueError("Warning reason cannot exceed 1000 characters")

            guild_id = str(interaction.guild.id)
            user_id = str(member.id)

            # Add warning
            if guild_id not in self.warnings:
                self.warnings[guild_id] = {}
            if user_id not in self.warnings[guild_id]:
                self.warnings[guild_id][user_id] = []

            self.warnings[guild_id][user_id].append({
                "reason": reason or "No reason provided",
                "moderator": interaction.user.id,
                "timestamp": datetime.utcnow().isoformat()
            })

            self.save_data()

            # Handle violation
            try:
                await self.handle_violation(member, "Warning", severity=1)
            except Exception as e:
                print(f"Error handling violation: {str(e)}")

            # Create warning embed
            embed = discord.Embed(
                title="⚠️ Warning Issued",
                color=discord.Color.yellow(),
                timestamp=datetime.utcnow()
            )
            embed.add_field(name="Member", value=f"{member.mention} ({member.id})")
            embed.add_field(name="Moderator", value=interaction.user.mention)
            embed.add_field(
                name="Reason",
                value=reason or "No reason provided",
                inline=False
            )
            embed.add_field(
                name="Total Warnings",
                value=str(len(self.warnings[guild_id][user_id])),
                inline=False
            )

            await interaction.response.send_message(embed=embed)

            # Try to DM the warned user
            try:
                dm_embed = discord.Embed(
                    title=f"You received a warning in {interaction.guild.name}",
                    color=discord.Color.yellow()
                )
                dm_embed.add_field(name="Reason", value=reason or "No reason provided")
                await member.send(embed=dm_embed)
            except:
                pass  # Don't raise an error if DM fails

        except Exception as e:
            await self.handle_command_error(interaction, e)

    @app_commands.command(name="warnings", description="View warnings for a member")
    @app_commands.describe(
        member="The member whose warnings to view (Example: @username)"
    )
    async def warnings(self, interaction: discord.Interaction, member: discord.Member):
        """View warnings for a member"""
        try:
            await self.ensure_guild_initialized(interaction.guild)
            guild_id = str(interaction.guild.id)
            user_id = str(member.id)

            if (guild_id not in self.warnings or 
                user_id not in self.warnings[guild_id] or 
                not self.warnings[guild_id][user_id]):
                await interaction.response.send_message(f"{member.mention} has no warnings.")
                return

            embed = discord.Embed(title=f"Warnings for {member.name}")
            for i, warning in enumerate(self.warnings[guild_id][user_id], 1):
                moderator = self.bot.get_user(warning["moderator"])
                embed.add_field(
                    name=f"Warning {i}",
                    value=f"Reason: {warning['reason']}\n" \
                          f"Moderator: {moderator.mention if moderator else 'Unknown'}\n" \
                          f"Date: {warning['timestamp']}",
                    inline=False
                )

            await interaction.response.send_message(embed=embed)
        except Exception as e:
            await self.handle_command_error(interaction, e)

    @app_commands.command(name="kick", description="Remove a member from the server (they can rejoin with a new invite)")
    @app_commands.describe(
        member="The member to kick (Example: @username)",
        reason="Reason for kicking the member (Example: 'Multiple rule violations')"
    )
    @app_commands.default_permissions(kick_members=True)
    async def kick(self, interaction: discord.Interaction, member: discord.Member, reason: str = None):
        """Kick a member from the server"""
        try:
            if not interaction.guild:
                raise ValueError("This command can only be used in a server")

            # Initialize and check permissions
            await self.ensure_guild_initialized(interaction.guild)
            await self.check_guild_permissions(interaction, "kick")

            # Additional permission checks
            if not interaction.user.guild_permissions.kick_members:
                raise discord.Forbidden("You don't have permission to kick members")

            # Validate member hierarchy
            if member.top_role >= interaction.guild.me.top_role:
                raise ValueError("I cannot kick this member as their highest role is above mine")

            if member.top_role >= interaction.user.top_role:
                raise ValueError("You cannot kick this member as their highest role is above yours")

            if member.id == interaction.guild.owner_id:
                raise ValueError("Cannot kick the server owner")

            if member.id == self.bot.user.id:
                raise ValueError("I cannot kick myself")

            # Perform the kick with audit log reason
            kick_reason = f"Kicked by {interaction.user} ({interaction.user.id})"
            if reason:
                kick_reason += f" - {reason}"

            try:
                await member.kick(reason=kick_reason)
            except discord.Forbidden:
                raise discord.Forbidden("I don't have permission to kick this member")
            except discord.HTTPException:
                raise ValueError("Failed to kick the member. Please try again")

            # Create and send confirmation embed
            embed = discord.Embed(
                title="Member Kicked",
                description=f"Successfully kicked {member.mention}",
                color=discord.Color.red(),
                timestamp=datetime.utcnow()
            )
            embed.add_field(name="Member", value=f"{member} ({member.id})")
            embed.add_field(name="Moderator", value=interaction.user.mention)
            if reason:
                embed.add_field(name="Reason", value=reason, inline=False)

            # Send confirmation to command user
            await interaction.response.send_message(embed=embed)

            # Log the kick
            try:
                log_cog = self.bot.get_cog("LoggingCog")
                if log_cog:
                    log_embed = discord.Embed(
                        title="👢 Member Kicked",
                        description=f"{member.mention} has been kicked from the server",
                        color=discord.Color.red(),
                        timestamp=datetime.utcnow()
                    )
                    log_embed.add_field(name="Member", value=f"{member} ({member.id})")
                    log_embed.add_field(name="Moderator", value=f"{interaction.user.mention}")
                    if reason:
                        log_embed.add_field(name="Reason", value=reason, inline=False)
                    log_embed.set_footer(text=f"User ID: {member.id}")

                    await log_cog.log_event(interaction.guild, log_embed, "mod")
            except Exception as e:
                print(f"Error logging kick: {str(e)}")
                # Don't raise the error since the kick was successful

            # Trigger the kick event
            try:
                self.bot.dispatch('member_kick', interaction.guild, member)
            except Exception as e:
                print(f"Error dispatching kick event: {str(e)}")

        except Exception as e:
            await self.handle_command_error(interaction, e)

    @app_commands.command(name="moderate", description="Open a menu to moderate a member")
    @app_commands.describe(
        member="The member to moderate (Example: @username)"
    )
    @app_commands.default_permissions(manage_messages=True)
    async def moderate(self, interaction: discord.Interaction, member: discord.Member):
        """Open moderation action menu for a member"""
        try:
            await self.ensure_guild_initialized(interaction.guild)
            # Check cooldown
            if not self._check_cooldown(interaction.user.id, "moderate", 5):
                remaining = self._get_cooldown_remaining(interaction.user.id, "moderate")
                raise app_commands.CommandOnCooldown(cooldown=5, retry_after=remaining)

            embed = discord.Embed(
                title=f"Moderate {member.name}",
                description="Select a moderation action:",
                color=discord.Color.blue()
            )
            embed.add_field(name="User ID", value=member.id)
            embed.add_field(name="Joined At", value=member.joined_at.strftime("%Y-%m-%d %H:%M:%S"))
            embed.set_thumbnail(url=member.display_avatar.url)

            view = ModActionButtons(self, member)
            await interaction.response.send_message(embed=embed, view=view)
        except Exception as e:
            await self.handle_command_error(interaction, e)

    def _check_cooldown(self, user_id: int, command: str, cooldown: int = 5) -> bool:
        """Check if a command is on cooldown"""
        key = f"{user_id}_{command}"
        if key in self.command_cooldowns:
            if datetime.utcnow() - self.command_cooldowns[key] < timedelta(seconds=cooldown):
                return False
        self.command_cooldowns[key] = datetime.utcnow()
        return True

    def _get_cooldown_remaining(self, user_id: int, command: str) -> float:
        """Get remaining cooldown time in seconds"""
        key = f"{user_id}_{command}"
        if key in self.command_cooldowns:
            elapsed = datetime.utcnow() - self.command_cooldowns[key]
            remaining = timedelta(seconds=5)- elapsed
            if remaining.total_seconds() > 0:
                return remaining.total_seconds()
        return 0

async def setup(bot):
    try:
        await bot.add_cog(ModerationCog(bot))
        print("Successfully loaded ModerationCog")
    except Exception as e:
        print(f"Error loading ModerationCog: {str(e)}")