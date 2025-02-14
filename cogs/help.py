import discord
from discord import app_commands
from discord.ext import commands
from typing import List, Dict, Optional
from discord.ui import View, Button
import math
import asyncio
from datetime import datetime, timedelta

class HelpView(View):
    def __init__(self, help_cog, timeout=180):
        super().__init__(timeout=timeout)
        self.help_cog = help_cog
        self.current_page = 0
        self.current_category = None
        self.items_per_page = 4
        self.locks: Dict[str, asyncio.Lock] = {}
        self.rate_limits: Dict[str, datetime] = {}
        self._message: Optional[discord.Message] = None  # Private message attribute
        self.setup_buttons()

    @property
    def message(self) -> Optional[discord.Message]:
        """Safe access to message property"""
        return self._message

    @message.setter
    def message(self, value: Optional[discord.Message]):
        """Safe way to set message property"""
        self._message = value

    def setup_buttons(self):
        """Dynamically create category buttons based on current page"""
        try:
            self.clear_items()

            categories = self.help_cog.get_categories()
            total_pages = math.ceil(len(categories) / self.items_per_page)
            start_idx = self.current_page * self.items_per_page
            end_idx = start_idx + self.items_per_page

            for category in categories[start_idx:end_idx]:
                style = discord.ButtonStyle.primary
                if category == "Fun":
                    style = discord.ButtonStyle.success
                elif category == "Utility":
                    style = discord.ButtonStyle.secondary
                elif category == "Moderation":
                    style = discord.ButtonStyle.danger

                button = Button(
                    label=f"{self.help_cog.get_category_emoji(category)} {category}",
                    style=style,
                    custom_id=f"category_{category}",
                    row=0
                )
                button.callback = self.create_category_callback(category)
                self.add_item(button)

            nav_row = 1
            if total_pages > 1:
                if self.current_page > 0:
                    prev_button = Button(
                        label="◀️",
                        style=discord.ButtonStyle.secondary,
                        custom_id="prev_page",
                        row=nav_row
                    )
                    prev_button.callback = self.prev_page_callback
                    self.add_item(prev_button)

                page_indicator = Button(
                    label=f"Page {self.current_page + 1}/{total_pages}",
                    style=discord.ButtonStyle.secondary,
                    disabled=True,
                    row=nav_row,
                    custom_id="page_indicator"
                )
                self.add_item(page_indicator)

                if self.current_page < total_pages - 1:
                    next_button = Button(
                        label="▶️",
                        style=discord.ButtonStyle.secondary,
                        custom_id="next_page",
                        row=nav_row
                    )
                    next_button.callback = self.next_page_callback
                    self.add_item(next_button)

            if self.current_category:
                home_button = Button(
                    label="🏠 Back to Menu",
                    style=discord.ButtonStyle.danger,
                    custom_id="home",
                    row=2
                )
                home_button.callback = self.home_callback
                self.add_item(home_button)
        except Exception as e:
            print(f"Error in setup_buttons: {e}")

    def create_category_callback(self, category: str):
        """Create category button callback"""
        async def callback(interaction: discord.Interaction):
            try:
                if not interaction.guild:
                    await interaction.response.send_message(
                        "This command can only be used in a server.",
                        ephemeral=True
                    )
                    return

                if not await self.check_rate_limit(str(interaction.user.id)):
                    await interaction.response.send_message(
                        "Please wait before using buttons again.",
                        ephemeral=True
                    )
                    return

                if not await self.help_cog.can_access_category(interaction.user, category, interaction.guild):
                    await interaction.response.send_message(
                        "You don't have permission to view these commands.",
                        ephemeral=True
                    )
                    return

                async with await self.get_lock(f"view_{interaction.guild.id}_{interaction.user.id}"):
                    self.current_category = category
                    self.setup_buttons()
                    await interaction.response.edit_message(
                        embed=self.help_cog.get_commands_page(category, interaction.guild),
                        view=self
                    )
            except Exception as e:
                print(f"Error in category callback: {e}")
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "An error occurred while showing the commands.",
                        ephemeral=True
                    )
        return callback

    async def prev_page_callback(self, interaction: discord.Interaction):
        """Handle previous page navigation"""
        try:
            if not await self.check_rate_limit(str(interaction.user.id)):
                await interaction.response.send_message(
                    "Please wait before using buttons again.",
                    ephemeral=True
                )
                return

            async with await self.get_lock(f"view_{interaction.guild.id}_{interaction.user.id}"):
                self.current_page = max(0, self.current_page - 1)
                self.setup_buttons()
                await interaction.response.edit_message(
                    embed=self.help_cog.get_categories_page(interaction.guild),
                    view=self
                )
        except Exception as e:
            print(f"Error in prev_page callback: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "An error occurred while navigating pages.",
                    ephemeral=True
                )

    async def next_page_callback(self, interaction: discord.Interaction):
        """Handle next page navigation"""
        try:
            if not await self.check_rate_limit(str(interaction.user.id)):
                await interaction.response.send_message(
                    "Please wait before using buttons again.",
                    ephemeral=True
                )
                return

            async with await self.get_lock(f"view_{interaction.guild.id}_{interaction.user.id}"):
                categories = self.help_cog.get_categories()
                max_pages = math.ceil(len(categories) / self.items_per_page)
                self.current_page = min(self.current_page + 1, max_pages - 1)
                self.setup_buttons()
                await interaction.response.edit_message(
                    embed=self.help_cog.get_categories_page(interaction.guild),
                    view=self
                )
        except Exception as e:
            print(f"Error in next_page callback: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "An error occurred while navigating pages.",
                    ephemeral=True
                )

    async def home_callback(self, interaction: discord.Interaction):
        """Handle return to home menu"""
        try:
            if not await self.check_rate_limit(str(interaction.user.id)):
                await interaction.response.send_message(
                    "Please wait before using buttons again.",
                    ephemeral=True
                )
                return

            async with await self.get_lock(f"view_{interaction.guild.id}_{interaction.user.id}"):
                self.current_category = None
                self.current_page = 0
                self.setup_buttons()
                await interaction.response.edit_message(
                    embed=self.help_cog.get_categories_page(interaction.guild),
                    view=self
                )
        except Exception as e:
            print(f"Error in home callback: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "An error occurred while returning to the main menu.",
                    ephemeral=True
                )

    async def on_timeout(self):
        """Enhanced timeout handling"""
        try:
            # Disable all buttons first
            for child in self.children:
                if isinstance(child, discord.ui.Button):
                    child.disabled = True

            # Update the message if it exists and is available
            if self.message is not None:
                try:
                    await self.message.edit(view=self)
                except discord.NotFound:
                    print("Message was deleted before timeout could be handled")
                except discord.Forbidden:
                    print("Missing permissions to update timed out view")
                except discord.HTTPException as e:
                    print(f"HTTP error updating timed out view: {e}")
                except Exception as e:
                    print(f"Unexpected error updating timed out view: {e}")

            # Clear stored data
            self.locks.clear()
            self.rate_limits.clear()

        except Exception as e:
            print(f"Error handling view timeout: {e}")

    async def get_lock(self, key: str) -> asyncio.Lock:
        """Get or create a lock for a specific key"""
        if key not in self.locks:
            self.locks[key] = asyncio.Lock()
        return self.locks[key]

    async def check_rate_limit(self, user_id: str) -> bool:
        """Enhanced rate limit checking with proper cleanup"""
        try:
            now = datetime.utcnow()

            # Cleanup old rate limits first
            cleanup_threshold = now - timedelta(minutes=5)
            self.rate_limits = {
                uid: timestamp
                for uid, timestamp in self.rate_limits.items()
                if timestamp > cleanup_threshold
            }

            if user_id in self.rate_limits:
                if (now - self.rate_limits[user_id]) < timedelta(seconds=2):
                    return False

            self.rate_limits[user_id] = now
            return True

        except Exception as e:
            print(f"Error checking rate limit: {e}")
            return True  # Allow action on error to prevent lockout



class HelpCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.category_emojis = {
            "General": "📜",
            "Moderation": "🔨",
            "Fun": "🎮",
            "Utility": "🔧",
            "Events": "📅",
            "Tickets": "🎫",
            "Logging": "📝",
            "Analytics": "📊",
            "Reputation": "⭐",
            "AutoMod": "🤖"
        }
        self.category_descriptions = {
            "General": "Basic bot commands and information",
            "Moderation": "Server moderation and management tools",
            "Fun": "Entertainment and engagement features",
            "Utility": "Helpful utility commands",
            "Events": "Create and manage server events",
            "Tickets": "Support ticket system",
            "Logging": "Server activity tracking",
            "Analytics": "Server statistics and insights",
            "Reputation": "User reputation system",
            "AutoMod": "Automatic moderation settings"
        }
        # Define required permissions per category
        self.category_required_perms = {
            "Moderation": ["manage_messages", "view_audit_log"],
            "Logging": ["view_audit_log"],
            "AutoMod": ["manage_guild"],
            "Events": ["manage_events"],
            "Reputation": ["manage_roles"],
            "Analytics": ["view_guild_insights"]
        }

    def get_category_emoji(self, category: str) -> str:
        return self.category_emojis.get(category, "🔹")

    def get_categories(self) -> List[str]:
        """Get all command categories"""
        return list(self.category_descriptions.keys())

    async def can_access_category(self, user: discord.Member, category: str, guild: discord.Guild) -> bool:
        """Check if a user has permission to access a category's commands"""
        try:
            if category not in self.category_required_perms:
                return True

            if not guild:
                return False

            member = guild.get_member(user.id)
            if not member:
                return False

            if member.guild_permissions.administrator:
                return True

            required_perms = self.category_required_perms[category]
            user_perms = member.guild_permissions

            return all(
                getattr(user_perms, perm, False)
                for perm in required_perms
            )
        except Exception as e:
            print(f"Error checking permissions for {user.id} in {guild.id}: {e}")
            return False

    def get_available_commands(self, category: str, guild: discord.Guild) -> List[app_commands.Command]:
        """Get all commands in a category that are available in the current guild"""
        try:
            commands = []
            for cmd in self.bot.tree.get_commands():
                cog = cmd.binding if hasattr(cmd, 'binding') else None
                if cog and cog.__class__.__name__.replace('Cog', '') == category:
                    if hasattr(cmd, 'guild_ids') and cmd.guild_ids:
                        if guild.id not in cmd.guild_ids:
                            continue
                    commands.append(cmd)
            return commands
        except Exception as e:
            print(f"Error getting commands for category {category} in guild {guild.id}: {e}")
            return []

    def get_categories_page(self, guild: discord.Guild) -> discord.Embed:
        """Generate the embed for categories overview"""
        try:
            embed = discord.Embed(
                title="📌 Bot Help Menu",
                color=discord.Color.blue(),
                description=(
                    "Welcome to the Bot Help Menu! Click a button below to view specific command categories.\n\n"
                    "**Required Bot Permissions**\n"
                    "• View Audit Log (For logging & moderation)\n"
                    "• Manage Messages & Roles\n"
                    "• Send Messages & Read History\n"
                    "• View Server Insights (For analytics)\n"
                    "• Manage Events (For event system)"
                )
            )

            for category in self.get_categories():
                emoji = self.get_category_emoji(category)
                if self.get_available_commands(category, guild):
                    desc = self.category_descriptions[category]

                    # Add permission requirements if any
                    if category in self.category_required_perms:
                        required_perms = self.category_required_perms[category]
                        formatted_perms = [perm.replace('_', ' ').title() for perm in required_perms]
                        desc += f"\n*Requires: {', '.join(formatted_perms)}*"

                    embed.add_field(
                        name=f"{emoji} {category}",
                        value=desc,
                        inline=True
                    )

            embed.set_footer(text=f"Server: {guild.name} • Use the buttons below to navigate")
            return embed
        except Exception as e:
            print(f"Error generating categories page for guild {guild.id}: {e}")
            return discord.Embed(
                title="Error",
                description="An error occurred while generating the help menu. Please try again.",
                color=discord.Color.red()
            )

    def get_commands_page(self, category: str, guild: discord.Guild) -> discord.Embed:
        """Generate the embed for a category's commands"""
        emoji = self.get_category_emoji(category)
        embed = discord.Embed(
            title=f"{emoji} {category} Commands",
            color=discord.Color.green(),
            description=self.category_descriptions[category]
        )

        commands = self.get_available_commands(category, guild)
        if not commands:
            embed.add_field(
                name="No Commands Available",
                value="This category currently has no commands available in this server.",
                inline=False
            )
            return embed

        for cmd in sorted(commands, key=lambda x: x.name):
            value = cmd.description or "No description available"

            # Only keep detailed parameters for logsettings
            if cmd.name == "logsettings":
                full_desc = [
                    "Configure server logging settings",
                    "\n**Parameters:**",
                    "• `retention_days`: How long to keep logs (1-90 days)",
                    "• `include_audit`: Enable/disable audit logs (true/false)",
                    "• `log_type`: Type of log to configure (message/member/server)",
                    "• `enabled`: Enable/disable the log type (true/false)",
                    "\n**Usage:**",
                    "• `/logsettings` - View current settings",
                    "• `/logsettings retention_days [1-90]`",
                    "• `/logsettings include_audit [true/false]`",
                    "• `/logsettings log_type [type] enabled [true/false]`",
                    "\n**Example:**",
                    "`/logsettings retention_days 30`"
                ]
                embed.add_field(
                    name=f"/{cmd.name}",
                    value="\n".join(full_desc),
                    inline=False
                )
                continue

            # For other commands, show basic usage with parameters but no detailed descriptions
            usage = f"/{cmd.name}"
            if cmd.parameters:
                param_list = []
                for param in cmd.parameters:
                    if param.required:
                        param_list.append(f"<{param.name}>")
                    else:
                        param_list.append(f"[{param.name}]")
                if param_list:
                    usage += " " + " ".join(param_list)

            # For specific categories, keep it more concise
            if category in ['Moderation', 'Fun', 'Utility']:
                embed.add_field(
                    name=f"/{cmd.name}",
                    value=f"{value}\n`{usage}`",
                    inline=False
                )
            else:
                full_desc = [value, f"\n**Usage:**\n`{usage}`"]
                embed.add_field(
                    name=f"/{cmd.name}",
                    value="\n".join(full_desc),
                    inline=False
                )

        embed.set_footer(text=f"Server: {guild.name} • Click 🏠 Back to Menu to return")
        return embed

    @app_commands.command(name="help", description="Browse through all available commands with detailed descriptions")
    async def help(self, interaction: discord.Interaction):
        """Browse and learn about available commands with an interactive menu

        Example usage:
        /help - Opens the main help menu
        """
        try:
            if not interaction.guild:
                await interaction.response.send_message(
                    "This command can only be used in a server.",
                    ephemeral=True
                )
                return

            view = HelpView(self)
            await interaction.response.send_message(
                embed=self.get_categories_page(interaction.guild),
                view=view,
                ephemeral=True
            )

            # Store message reference after sending
            try:
                original_response = await interaction.original_response()
                view.message = original_response
            except discord.NotFound:
                print("Failed to store message reference - response not found")
            except discord.HTTPException as e:
                print(f"HTTP error storing message reference: {e}")
            except Exception as e:
                print(f"Unexpected error storing message reference: {e}")

        except discord.Forbidden as e:
            print(f"Permission error showing help menu: {e}")
            await interaction.response.send_message(
                "I don't have permission to show the help menu. Please check my permissions.",
                ephemeral=True
            )
        except Exception as e:
            print(f"Error showing help menu: {e}")
            await interaction.response.send_message(
                "An error occurred while showing the help menu. Please try again.",
                ephemeral=True
            )

async def setup(bot):
    try:
        await bot.add_cog(HelpCog(bot))
        print("Successfully loaded HelpCog")
    except Exception as e:
        print(f"Error loading HelpCog: {str(e)}")