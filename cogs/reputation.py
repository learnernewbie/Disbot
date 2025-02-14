import discord
from discord import app_commands
from discord.ext import commands
import json
import os
from datetime import datetime, timedelta
from typing import Dict, Optional, Union, List, Any

class ReputationCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.reputation: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self.cooldowns: Dict[str, datetime] = {}
        self.locks: Dict[str, bool] = {}  # For preventing race conditions
        os.makedirs('data', exist_ok=True)
        self.load_data()

    async def check_permissions(self, guild: discord.Guild) -> bool:
        """Check if bot has required permissions for reputation operations"""
        if not guild or not guild.me:
            return False

        required_permissions = [
            "view_channel",
            "send_messages",
            "embed_links"
        ]

        missing_permissions = []
        for perm in required_permissions:
            if not getattr(guild.me.guild_permissions, perm, False):
                missing_permissions.append(perm)

        if missing_permissions:
            print(f"Missing reputation permissions in {guild.name}: {', '.join(missing_permissions)}")
            return False
        return True

    def load_data(self):
        try:
            with open('data/reputation.json', 'r') as f:
                data = json.load(f)
                if isinstance(data, dict):
                    self.reputation = data
                else:
                    print("Warning: reputation.json contained invalid data, resetting")
                    self.reputation = {}
                    self.save_data()
        except FileNotFoundError:
            self.reputation = {}
            self.save_data()
        except json.JSONDecodeError:
            print("Error: reputation.json is corrupted, creating new file")
            self.reputation = {}
            self.save_data()

    def save_data(self):
        """Save reputation data with atomic writes and validation"""
        temp_file = 'data/reputation_temp.json'  
        try:
            # Prepare data for saving
            with open(temp_file, 'w') as f:
                json.dump(self.reputation, f, indent=4)

            # Atomic replace to prevent corruption
            os.replace(temp_file, 'data/reputation.json')
        except Exception as e:
            print(f"Error saving reputation data: {e}")
        finally:
            # Clean up temp file in case of errors
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception as e:
                    print(f"Error cleaning up temp file: {e}")

    def get_user_rep(self, guild_id: int, user_id: int) -> Dict:
        """Get or create user reputation data with validation"""
        guild_key = str(guild_id)
        user_key = str(user_id)

        try:
            if guild_key not in self.reputation:
                self.reputation[guild_key] = {}

            if user_key not in self.reputation[guild_key]:
                self.reputation[guild_key][user_key] = {
                    "points": 0,
                    "level": 1,
                    "last_daily": None,
                    "history": []
                }
                self.save_data()

            # Validate data structure
            user_data = self.reputation[guild_key][user_key]
            if not isinstance(user_data, dict):
                print(f"Invalid user data structure for {user_key} in guild {guild_key}, resetting")
                self.reputation[guild_key][user_key] = {
                    "points": 0,
                    "level": 1,
                    "last_daily": None,
                    "history": []
                }
                self.save_data()

            return self.reputation[guild_key][user_key]
        except Exception as e:
            print(f"Error in get_user_rep: {e}")
            return {
                "points": 0,
                "level": 1,
                "last_daily": None,
                "history": []
            }

    async def initialize_guild(self, guild: discord.Guild):
        """Initialize reputation data for a new guild with validation"""
        try:
            if not guild:
                return False

            if not await self.check_permissions(guild):
                return False

            guild_id = str(guild.id)
            if guild_id not in self.reputation:
                self.reputation[guild_id] = {}
                self.save_data()
                print(f"Initialized reputation system for guild: {guild.name} ({guild_id})")
            return True
        except Exception as e:
            print(f"Error initializing guild {guild.id if guild else 'Unknown'}: {str(e)}")
            return False

    def calculate_level(self, points: int) -> int:
        """Calculate level based on points with validation"""
        try:
            return max(1, int((points / 100) ** 0.5) + 1)
        except Exception as e:
            print(f"Error calculating level for {points} points: {e}")
            return 1

    async def update_points(self, guild_id: int, user_id: int, points: int, reason: str):
        """Update user's reputation points with improved error handling and race condition prevention"""
        lock_key = f"{guild_id}_{user_id}"
        if self.locks.get(lock_key):
            raise ValueError("Another reputation update is in progress")

        try:
            self.locks[lock_key] = True
            data = self.get_user_rep(guild_id, user_id)
            previous_points = data["points"]
            data["points"] = max(0, data["points"] + points)
            new_level = self.calculate_level(data["points"])

            # Validate history structure
            if not isinstance(data.get("history", []), list):
                data["history"] = []

            data["history"].append({
                "change": points,
                "previous_points": previous_points,
                "new_points": data["points"],
                "reason": reason,
                "timestamp": datetime.utcnow().isoformat()
            })

            # Keep only last 30 days of history
            cutoff = datetime.utcnow() - timedelta(days=30)
            data["history"] = [
                h for h in data["history"]
                if datetime.fromisoformat(h["timestamp"]) > cutoff
            ]

            # Level up notification
            level_changed = new_level != data["level"]
            data["level"] = new_level

            self.save_data()
            return level_changed
        except Exception as e:
            print(f"Error updating points for user {user_id} in guild {guild_id}: {e}")
            return False
        finally:
            self.locks[lock_key] = False

    @app_commands.command(name="rep", description="View reputation points and level for yourself or another member")
    @app_commands.describe(
        member="The server member whose reputation you want to check (leave empty to check your own)"
    )
    async def rep(self, interaction: discord.Interaction, member: Optional[discord.Member] = None):
        """View your or another member's reputation"""
        try:
            if not interaction.guild:
                raise ValueError("This command can only be used in a server")

            if not await self.check_permissions(interaction.guild):
                raise discord.Forbidden("I don't have the required permissions")

            target = member or interaction.user
            if isinstance(target, discord.User):  # If the target is not in the guild
                await interaction.response.send_message(
                    "That user is not a member of this server.",
                    ephemeral=True
                )
                return

            if not await self.initialize_guild(interaction.guild):
                await interaction.response.send_message(
                    "Failed to initialize reputation system.",
                    ephemeral=True
                )
                return

            data = self.get_user_rep(interaction.guild.id, target.id)

            embed = discord.Embed(
                title=f"Reputation for {target.display_name}",
                color=discord.Color.blue()
            )

            embed.add_field(name="Level", value=str(data["level"]))
            embed.add_field(name="Points", value=str(data["points"]))

            # Progress to next level
            current_level_points = (data["level"] - 1) ** 2 * 100
            next_level_points = data["level"] ** 2 * 100
            progress = (data["points"] - current_level_points) / (next_level_points - current_level_points) * 100

            embed.add_field(
                name="Progress to Next Level",
                value=f"{progress:.1f}%",
                inline=False
            )

            # Recent history
            recent_history = sorted(
                data["history"],
                key=lambda x: datetime.fromisoformat(x["timestamp"]),
                reverse=True
            )[:5]

            if recent_history:
                history_text = "\n".join(
                    f"{h['change']:+d} points - {h['reason']} "
                    f"(<t:{int(datetime.fromisoformat(h['timestamp']).timestamp())}:R>)"
                    for h in recent_history
                )
                embed.add_field(
                    name="Recent Changes",
                    value=history_text,
                    inline=False
                )

            await interaction.response.send_message(embed=embed)
        except Exception as e:
            error_msg = str(e) if isinstance(e, (ValueError, discord.Forbidden)) else "An error occurred while fetching reputation data"
            await interaction.response.send_message(
                error_msg,
                ephemeral=True
            )

    @app_commands.command(name="giverep", description="Give reputation points to another member for being helpful")
    @app_commands.describe(
        member="The server member you want to give reputation points to",
        reason="Why you're giving reputation points (minimum 3 characters)"
    )
    async def giverep(self, interaction: discord.Interaction, member: discord.Member, reason: str):
        """Give reputation points to a member"""
        try:
            if not interaction.guild:
                raise ValueError("This command can only be used in a server")

            if not await self.check_permissions(interaction.guild):
                raise discord.Forbidden("I don't have the required permissions")

            if member == interaction.user:
                raise ValueError("You cannot give reputation to yourself!")

            if member.bot:
                raise ValueError("You cannot give reputation to bots!")

            if not reason or len(reason.strip()) < 3:
                raise ValueError("Please provide a valid reason (at least 3 characters)")

            # Check cooldown with guild-specific tracking
            cooldown_key = f"{interaction.guild.id}_{interaction.user.id}_{member.id}"
            if cooldown_key in self.cooldowns:
                remaining = timedelta(hours=12) - (datetime.utcnow() - self.cooldowns[cooldown_key])
                if remaining.total_seconds() > 0:
                    raise ValueError(
                        f"You can give reputation to this user again in {int(remaining.total_seconds() / 60)} minutes"
                    )

            if not await self.initialize_guild(interaction.guild):
                raise ValueError("Failed to initialize reputation system")

            self.cooldowns[cooldown_key] = datetime.utcnow()
            level_up = await self.update_points(
                interaction.guild.id,
                member.id,
                10,  # Base reputation gain
                f"Received from {interaction.user.name}: {reason}"
            )

            await interaction.response.send_message(
                f"Gave +10 reputation to {member.mention} for: {reason}"
            )

            if level_up:
                data = self.get_user_rep(interaction.guild.id, member.id)
                await interaction.channel.send(
                    f"🎉 Congratulations {member.mention}! "
                    f"You've reached reputation level {data['level']}!"
                )
        except Exception as e:
            error_msg = str(e) if isinstance(e, (ValueError, discord.Forbidden)) else "An error occurred while giving reputation"
            await interaction.response.send_message(
                error_msg,
                ephemeral=True
            )

    @app_commands.command(name="toprep", description="View the server's reputation leaderboard showing top members")
    async def toprep(self, interaction: discord.Interaction):
        """View the server's reputation leaderboard"""
        try:
            if not interaction.guild:
                raise ValueError("This command can only be used in a server")

            if not await self.check_permissions(interaction.guild):
                raise discord.Forbidden("I don't have the required permissions")

            if not await self.initialize_guild(interaction.guild):
                raise ValueError("Failed to initialize reputation system")

            guild_id = str(interaction.guild.id)
            if guild_id not in self.reputation:
                await interaction.response.send_message(
                    "No reputation data for this server yet!",
                    ephemeral=True
                )
                return

            # Sort users by points
            leaderboard = []
            for user_id, data in self.reputation[guild_id].items():
                try:
                    member = interaction.guild.get_member(int(user_id))
                    if member and not member.bot:  # Only include non-bot users still in the server
                        leaderboard.append((member, data["points"], data["level"]))
                except ValueError:
                    continue  # Skip invalid user IDs

            if not leaderboard:
                await interaction.response.send_message(
                    "No reputation data to display yet!",
                    ephemeral=True
                )
                return

            leaderboard.sort(key=lambda x: x[1], reverse=True)

            embed = discord.Embed(
                title="🏆 Reputation Leaderboard",
                color=discord.Color.gold()
            )

            # Show top 10
            for i, (member, points, level) in enumerate(leaderboard[:10], 1):
                embed.add_field(
                    name=f"{i}. {member.display_name}",
                    value=f"Level {level} ({points} points)",
                    inline=False
                )

            await interaction.response.send_message(embed=embed)
        except Exception as e:
            error_msg = str(e) if isinstance(e, (ValueError, discord.Forbidden)) else "An error occurred while fetching the leaderboard"
            await interaction.response.send_message(
                error_msg,
                ephemeral=True
            )

    async def handle_violation(self, member: discord.Member, violation_type: str, severity: int):
        """Handle reputation loss from violations"""
        try:
            if not member or not member.guild:
                return

            if not await self.initialize_guild(member.guild):
                return

            # Calculate reputation loss based on severity
            points_loss = -10 * severity

            await self.update_points(
                member.guild.id,
                member.id,
                points_loss,
                f"Violation: {violation_type}"
            )
        except Exception as e:
            print(f"Error handling violation for {member.id}: {e}")

async def setup(bot):
    try:
        await bot.add_cog(ReputationCog(bot))
        print("Successfully loaded ReputationCog")
    except Exception as e:
        print(f"Error loading ReputationCog: {str(e)}")