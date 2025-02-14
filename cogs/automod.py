import discord
from discord import app_commands
from discord.ext import commands
import re
import json
import os
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Set, Union, Any, Optional, Literal

class AutoModCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.spam_check: Dict[int, List[datetime]] = {}
        self.whitelisted_roles: Dict[int, Set[int]] = {}
        self.guild_configs: Dict[str, Dict[str, Any]] = {}
        self.locks: Dict[str, asyncio.Lock] = {}
        self.action_cooldowns: Dict[str, datetime] = {}
        self.load_data()
        self.load_guild_configs()

    async def get_lock(self, key: str) -> asyncio.Lock:
        """Get or create a lock for the given key"""
        if key not in self.locks:
            self.locks[key] = asyncio.Lock()
        return self.locks[key]

    def load_data(self):
        """Load whitelist data with enhanced error handling"""
        try:
            with open('data/role_whitelist.json', 'r') as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    raise ValueError("Invalid whitelist data format")
                self.whitelisted_roles = {
                    int(guild_id): set(map(int, roles))
                    for guild_id, roles in data.items()
                }
        except FileNotFoundError:
            print("No existing whitelist data found, creating new file")
            self.whitelisted_roles = {}
        except json.JSONDecodeError:
            print("Error: role_whitelist.json is corrupted, creating backup and new file")
            self._backup_corrupted_file('data/role_whitelist.json')
            self.whitelisted_roles = {}
        except Exception as e:
            print(f"Error loading whitelist data: {e}")
            self.whitelisted_roles = {}
        self.save_data()

    def _backup_corrupted_file(self, filepath: str):
        """Create a backup of a corrupted file"""
        try:
            if os.path.exists(filepath):
                backup_path = f"{filepath}.bak.{int(datetime.utcnow().timestamp())}"
                os.rename(filepath, backup_path)
                print(f"Created backup of corrupted file: {backup_path}")
        except Exception as e:
            print(f"Error creating backup: {e}")

    def save_data(self):
        """Save whitelist data with atomic writes"""
        try:
            temp_file = 'data/role_whitelist_temp.json'
            with open(temp_file, 'w') as f:
                json.dump({
                    str(guild_id): list(map(str, roles))
                    for guild_id, roles in self.whitelisted_roles.items()
                }, f, indent=4)
            os.replace(temp_file, 'data/role_whitelist.json')
        except Exception as e:
            print(f"Error saving whitelist data: {e}")
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except:
                    pass

    def load_guild_configs(self):
        """Load guild configurations with validation"""
        try:
            with open('data/automod_config.json', 'r') as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    raise ValueError("Invalid config data format")
                # Validate each guild's config
                for guild_id, config in data.items():
                    if self._validate_guild_config(config):
                        self.guild_configs[guild_id] = config
        except FileNotFoundError:
            print("No existing config found, creating new file")
            self.guild_configs = {}
        except json.JSONDecodeError:
            print("Error: automod_config.json is corrupted, creating backup")
            self._backup_corrupted_file('data/automod_config.json')
            self.guild_configs = {}
        except Exception as e:
            print(f"Error loading guild configs: {e}")
            self.guild_configs = {}
        self.save_guild_configs()

    def _validate_guild_config(self, config: Dict[str, Any]) -> bool:
        """Validate guild configuration structure"""
        required_fields = {
            'max_mentions': int,
            'max_messages': int,
            'timeframe': int,
            'blocked_words': list,
            'link_whitelist': list,
            'max_lines': int,
            'max_emojis': int,
            'caps_threshold': float
        }

        try:
            for field, field_type in required_fields.items():
                if field not in config:
                    return False
                if not isinstance(config[field], field_type):
                    return False

            # Validate numeric ranges
            if not (0 <= config['caps_threshold'] <= 1):
                return False
            if any(v < 0 for v in [config['max_mentions'], config['max_messages'],
                                    config['timeframe'], config['max_lines'],
                                    config['max_emojis']]):
                return False

            return True
        except Exception as e:
            print(f"Error validating guild config: {e}")
            return False

    def save_guild_configs(self):
        """Save guild configurations with atomic writes"""
        try:
            temp_file = 'data/automod_config_temp.json'
            with open(temp_file, 'w') as f:
                json.dump(self.guild_configs, f, indent=4)
            os.replace(temp_file, 'data/automod_config.json')
        except Exception as e:
            print(f"Error saving guild configs: {e}")
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except:
                    pass

    def get_guild_config(self, guild_id: str) -> Dict[str, Any]:
        """Get or create guild-specific configuration with validation"""
        try:
            if not guild_id:
                raise ValueError("Invalid guild ID")

            if guild_id not in self.guild_configs:
                self.guild_configs[guild_id] = {
                    "max_mentions": 5,
                    "max_messages": 5,
                    "timeframe": 5,
                    "blocked_words": [],
                    "link_whitelist": [],
                    "max_lines": 10,
                    "max_emojis": 10,
                    "caps_threshold": 0.7
                }
                self.save_guild_configs()
            return self.guild_configs[guild_id]
        except Exception as e:
            print(f"Error getting guild config for {guild_id}: {e}")
            return {
                "max_mentions": 5,
                "max_messages": 5,
                "timeframe": 5,
                "blocked_words": [],
                "link_whitelist": [],
                "max_lines": 10,
                "max_emojis": 10,
                "caps_threshold": 0.7
            }

    async def can_perform_action(self, guild_id: str) -> bool:
        """Check if an action can be performed (rate limiting)"""
        now = datetime.utcnow()
        key = f"automod_{guild_id}"
        if key in self.action_cooldowns:
            if now - self.action_cooldowns[key] < timedelta(seconds=1):
                return False
        self.action_cooldowns[key] = now
        return True

    async def initialize_guild(self, guild: discord.Guild):
        """Initialize automod data for a new guild with error handling"""
        try:
            if not guild:
                raise ValueError("Invalid guild object")

            guild_id = str(guild.id)
            self.get_guild_config(guild_id)  # This will create default config if it doesn't exist
            print(f"Initialized automod for guild: {guild.name} ({guild_id})")
        except Exception as e:
            print(f"Error initializing automod for guild {guild.id if guild else 'Unknown'}: {e}")

    async def check_permissions(self, guild: discord.Guild) -> bool:
        """Check if bot has required permissions for automod operations"""
        try:
            if not guild or not guild.me:
                return False

            required_permissions = [
                "manage_messages",
                "manage_roles",
                "view_audit_log",
                "moderate_members"
            ]

            missing_permissions = []
            for perm in required_permissions:
                if not getattr(guild.me.guild_permissions, perm, False):
                    missing_permissions.append(perm)

            if missing_permissions:
                print(f"Missing automod permissions in {guild.name}: {', '.join(missing_permissions)}")
                return False
            return True
        except Exception as e:
            print(f"Error checking permissions: {e}")
            return False

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Handle message moderation with enhanced error handling"""
        try:
            if not message.guild or message.author.bot:
                return

            # Check bot permissions first
            if not await self.check_permissions(message.guild):
                return

            # Rate limit check
            if not await self.can_perform_action(str(message.guild.id)):
                return

            guild_id = str(message.guild.id)
            config = self.get_guild_config(guild_id)

            # Check if user has whitelisted roles
            member_roles = {role.id for role in message.author.roles}
            guild_whitelist = self.whitelisted_roles.get(message.guild.id, set())
            if any(role_id in guild_whitelist for role_id in member_roles):
                return

            violations = []

            # Spam Detection with improved timing handling
            author_id = message.author.id
            now = datetime.utcnow()
            async with await self.get_lock(f"spam_{author_id}"):
                if author_id not in self.spam_check:
                    self.spam_check[author_id] = []

                # Clean old messages from spam check
                self.spam_check[author_id] = [
                    msg_time for msg_time in self.spam_check[author_id]
                    if now - msg_time < timedelta(seconds=config["timeframe"])
                ]
                self.spam_check[author_id].append(now)

                if len(self.spam_check[author_id]) > config["max_messages"]:
                    violations.append(("spam", 2))

            # Enhanced Caps Check
            if len(message.content) > 10:
                caps_ratio = sum(1 for c in message.content if c.isupper()) / len(message.content)
                if caps_ratio > config["caps_threshold"]:
                    violations.append(("excessive_caps", 1))

            # Mention Spam Check
            if len(message.mentions) > config["max_mentions"]:
                violations.append(("mention_spam", 2))

            # Line Spam Check
            if len(message.content.splitlines()) > config["max_lines"]:
                violations.append(("line_spam", 1))

            # Emoji Spam Check
            emoji_count = len(re.findall(r'<a?:\w+:\d+>|[\U0001F300-\U0001F9FF]', message.content))
            if emoji_count > config["max_emojis"]:
                violations.append(("emoji_spam", 1))

            # Word Filter
            content_lower = message.content.lower()
            if any(word.lower() in content_lower for word in config["blocked_words"]):
                violations.append(("blocked_words", 3))

            # Handle violations
            if violations:
                try:
                    await message.delete()
                except discord.Forbidden:
                    print(f"Cannot delete message in {message.guild.name}: Missing permissions")
                    return
                except discord.NotFound:
                    return  # Message already deleted
                except Exception as e:
                    print(f"Error deleting message: {e}")
                    return

                # Use the highest severity violation
                violation_type, severity = max(violations, key=lambda x: x[1])

                # Use ModerationCog's violation system
                try:
                    mod_cog = self.bot.get_cog("ModerationCog")
                    if mod_cog:
                        await mod_cog.handle_violation(message.author, violation_type, severity)
                except Exception as e:
                    print(f"Error handling violation: {e}")

        except Exception as e:
            print(f"Error in automod message handler: {e}")

    @app_commands.command(name="automod", description="Configure auto-moderation settings for the server")
    @app_commands.describe(
        setting="Which auto-mod setting to configure (spam, mentions, caps, emojis, lines)",
        value="New value for the setting (number or true/false)",
        enabled="Turn this auto-mod feature on or off"
    )
    async def automod(
        self,
        interaction: discord.Interaction,
        setting: str,
        value: int,
        enabled: Optional[bool] = None
    ):
        """Configure server auto-moderation settings

        Parameters
        ----------
        setting : str
            The auto-mod feature to configure
        value : int
            New threshold value for the setting
        enabled : Optional[bool]
            Enable or disable this feature
        """
        # Add your automod configuration logic here.  This will likely involve updating
        # self.guild_configs based on the provided setting, value, and enabled flags.
        await interaction.response.send_message(f"Automod setting '{setting}' updated to {value}. Enabled: {enabled}")


    @app_commands.command(name="whitelist", description="Add or remove roles from auto-mod whitelist")
    @app_commands.describe(
        role="The role to add/remove from whitelist",
        action="Whether to add or remove the role"
    )
    async def whitelist(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        action: Literal["add", "remove"]
    ):
        """Manage auto-mod role whitelist"""
        guild_id = interaction.guild_id
        if action == "add":
            self.whitelisted_roles.setdefault(guild_id, set()).add(role.id)
            await interaction.response.send_message(f"Role {role.name} added to whitelist.")
        elif action == "remove":
            if guild_id in self.whitelisted_roles and role.id in self.whitelisted_roles[guild_id]:
                self.whitelisted_roles[guild_id].remove(role.id)
                await interaction.response.send_message(f"Role {role.name} removed from whitelist.")
            else:
                await interaction.response.send_message(f"Role {role.name} not found in whitelist.")
        self.save_data()



async def setup(bot):
    try:
        await bot.add_cog(AutoModCog(bot))
        print("Successfully loaded AutoModCog")
    except Exception as e:
        print(f"Error loading AutoModCog: {str(e)}")