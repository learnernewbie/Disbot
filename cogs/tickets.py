import discord
from discord import app_commands
from discord.ext import commands
import asyncio
from datetime import datetime
import json
import os
from typing import Dict, List, Optional, Set, Any

class TicketsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.ticket_messages: Dict[str, List[int]] = {}  # guild_id -> list of message IDs
        self.active_tickets: Dict[str, Dict[str, datetime]] = {}  # guild_id -> {user_id: creation_time}
        self.locks: Dict[str, asyncio.Lock] = {}
        self.rate_limits: Dict[str, datetime] = {}  # user_id -> last_ticket_time
        os.makedirs('data', exist_ok=True)
        self.load_data()

    async def get_lock(self, key: str) -> asyncio.Lock:
        """Get or create a lock for a specific key"""
        if key not in self.locks:
            self.locks[key] = asyncio.Lock()
        return self.locks[key]

    async def can_create_ticket(self, user_id: str) -> bool:
        """Check if a user can create a new ticket (rate limiting)"""
        now = datetime.utcnow()
        if user_id in self.rate_limits:
            if (now - self.rate_limits[user_id]).total_seconds() < 300:  # 5 minutes cooldown
                return False
        self.rate_limits[user_id] = now
        return True

    def load_data(self):
        """Load ticket data with enhanced error handling and validation"""
        try:
            # Load ticket messages
            with open('data/tickets.json', 'r') as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    raise ValueError("Invalid ticket data format")
                self.ticket_messages = {
                    str(guild_id): [int(msg_id) for msg_id in msg_ids]
                    for guild_id, msg_ids in data.items()
                }

        except FileNotFoundError:
            print("No existing ticket data found, creating new file")
            self.ticket_messages = {}
        except json.JSONDecodeError:
            print("Error: tickets.json is corrupted, creating backup")
            self._backup_corrupted_file('data/tickets.json')
            self.ticket_messages = {}
        except Exception as e:
            print(f"Error loading ticket data: {e}")
            self.ticket_messages = {}

        # Load active tickets data
        try:
            with open('data/active_tickets.json', 'r') as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    raise ValueError("Invalid active tickets data format")
                self.active_tickets = {
                    guild_id: {
                        user_id: datetime.fromisoformat(timestamp)
                        for user_id, timestamp in tickets.items()
                    }
                    for guild_id, tickets in data.items()
                }
        except FileNotFoundError:
            self.active_tickets = {}
        except json.JSONDecodeError:
            print("Error: active_tickets.json is corrupted, creating backup")
            self._backup_corrupted_file('data/active_tickets.json')
            self.active_tickets = {}
        except Exception as e:
            print(f"Error loading active tickets: {e}")
            self.active_tickets = {}

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
        """Save ticket data with atomic writes"""
        try:
            # Save ticket messages
            temp_file = 'data/tickets_temp.json'
            with open(temp_file, 'w') as f:
                json.dump(self.ticket_messages, f, indent=4)
            os.replace(temp_file, 'data/tickets.json')

            # Save active tickets
            active_tickets_data = {
                guild_id: {
                    user_id: timestamp.isoformat()
                    for user_id, timestamp in tickets.items()
                }
                for guild_id, tickets in self.active_tickets.items()
            }

            temp_file = 'data/active_tickets_temp.json'
            with open(temp_file, 'w') as f:
                json.dump(active_tickets_data, f, indent=4)
            os.replace(temp_file, 'data/active_tickets.json')

        except Exception as e:
            print(f"Error saving ticket data: {e}")
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except:
                    pass

    async def check_permissions(self, guild: discord.Guild) -> bool:
        """Check if bot has required permissions for ticket operations"""
        if not guild or not guild.me:
            return False

        me = guild.me
        required_permissions = [
            "manage_channels",
            "manage_roles",
            "view_channel",
            "send_messages",
            "manage_messages",
            "embed_links",
            "attach_files",
            "read_message_history",
            "add_reactions"
        ]

        missing_permissions = []
        for perm in required_permissions:
            if not getattr(me.guild_permissions, perm, False):
                missing_permissions.append(perm)

        if missing_permissions:
            print(f"Missing permissions in guild {guild.name}: {', '.join(missing_permissions)}")
            return False
        return True

    async def create_ticket_channel(self, guild: discord.Guild, user: discord.User, category: discord.CategoryChannel = None) -> Optional[discord.TextChannel]:
        """Create a ticket channel with enhanced error handling and rate limiting"""
        try:
            # Check permissions first
            if not await self.check_permissions(guild):
                return None

            # Check rate limiting
            guild_id = str(guild.id)
            user_id = str(user.id)

            if not await self.can_create_ticket(user_id):
                raise ValueError("Please wait 5 minutes between creating tickets")

            # Create or get category
            if not category:
                try:
                    category = discord.utils.get(guild.categories, name="Tickets")
                    if not category:
                        category = await guild.create_category("Tickets")
                except discord.Forbidden:
                    print(f"Failed to create ticket category in guild {guild.name}: Missing permissions")
                    return None
                except Exception as e:
                    print(f"Error creating ticket category in guild {guild.name}: {e}")
                    return None

            # Sanitize username for channel name
            safe_username = ''.join(c for c in user.name if c.isalnum() or c == '-')[:20]
            channel_name = f"ticket-{safe_username}"

            # Check for existing ticket
            existing_ticket = discord.utils.get(guild.text_channels, name=channel_name)
            if existing_ticket:
                raise ValueError(f"You already have an open ticket: {existing_ticket.mention}")

            # Set up permissions
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
                guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
            }

            # Add support role permissions if exists
            support_role = discord.utils.get(guild.roles, name="Support")
            if support_role:
                overwrites[support_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

            # Create the channel
            async with await self.get_lock(f"ticket_{guild_id}_{user_id}"):
                channel = await category.create_text_channel(
                    name=channel_name,
                    overwrites=overwrites,
                    topic=f"Support ticket for {user.name} | Created: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}"
                )

                # Update active tickets
                if guild_id not in self.active_tickets:
                    self.active_tickets[guild_id] = {}
                self.active_tickets[guild_id][user_id] = datetime.utcnow()
                self.save_data()

                print(f"Created ticket channel {channel.name} in guild {guild.name}")
                return channel

        except discord.Forbidden:
            print(f"Failed to create ticket channel in guild {guild.name}: Missing permissions")
            return None
        except ValueError as e:
            raise
        except Exception as e:
            print(f"Error creating ticket channel in guild {guild.name}: {e}")
            return None

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        """Handle ticket creation from reactions with enhanced error handling"""
        try:
            # Basic validation
            if payload.user_id == self.bot.user.id:
                return

            guild_id = str(payload.guild_id)
            if guild_id not in self.ticket_messages or payload.message_id not in self.ticket_messages[guild_id]:
                return

            guild = self.bot.get_guild(payload.guild_id)
            if not guild:
                return

            user = await self.bot.fetch_user(payload.user_id)
            if user.bot:
                return

            channel = await self.bot.fetch_channel(payload.channel_id)
            if not channel:
                return

            message = await channel.fetch_message(payload.message_id)
            await message.remove_reaction(payload.emoji, user)

            try:
                ticket_channel = await self.create_ticket_channel(guild, user)
                if not ticket_channel:
                    await channel.send(
                        "⚠️ Unable to create ticket channel. Please ensure I have the required permissions: "
                        "Manage Channels, Manage Permissions, View Channels, Send Messages, and Embed Links.",
                        delete_after=10
                    )
                    return

                embed = discord.Embed(
                    title=f"Support Ticket - {guild.name}",
                    description=f"{user.mention} created a ticket.\nPlease describe your issue and wait for staff to respond.",
                    color=discord.Color.green(),
                    timestamp=datetime.utcnow()
                )
                await ticket_channel.send(embed=embed)

            except ValueError as e:
                await channel.send(f"{user.mention} {str(e)}", delete_after=10)
            except Exception as e:
                print(f"Error creating ticket: {e}")
                await channel.send(
                    f"{user.mention} An error occurred while creating your ticket. Please try again later.",
                    delete_after=10
                )

        except Exception as e:
            print(f"Error handling ticket reaction: {e}")

    @app_commands.command(name="panel", description="Create a new ticket panel for users to open support tickets")
    @app_commands.default_permissions(administrator=True)
    async def panel(self, interaction: discord.Interaction):
        """Create a ticket panel for this server with enhanced error handling"""
        try:
            if not interaction.guild:
                raise ValueError("This command can only be used in a server")

            if not interaction.user.guild_permissions.administrator:
                raise discord.Forbidden("You need administrator permissions to use this command")

            if not await self.check_permissions(interaction.guild):
                await interaction.response.send_message(
                    "⚠️ I don't have the required permissions to manage tickets. Please check my role permissions.",
                    ephemeral=True
                )
                return

            guild_id = str(interaction.guild.id)

            # Check for existing panels
            if guild_id in self.ticket_messages and self.ticket_messages[guild_id]:
                try:
                    # Verify if old panels are still valid
                    valid_messages = []
                    for msg_id in self.ticket_messages[guild_id]:
                        try:
                            channel = interaction.channel
                            await channel.fetch_message(msg_id)
                            valid_messages.append(msg_id)
                        except:
                            continue

                    if valid_messages:
                        await interaction.response.send_message(
                            "A ticket panel already exists in this server. Would you like to create another one?",
                            ephemeral=True
                        )
                        return
                except:
                    pass

            try:
                embed = discord.Embed(
                    title=f"Create a Ticket - {interaction.guild.name}",
                    description="React with 📩 to open a ticket!\n\n" \
                              "Please note:\n" \
                              "• One ticket per user at a time\n" \
                              "• 5-minute cooldown between tickets\n" \
                              "• Be patient while waiting for staff response",
                    color=discord.Color.blue()
                )
                msg = await interaction.channel.send(embed=embed)
                await msg.add_reaction("📩")

                if guild_id not in self.ticket_messages:
                    self.ticket_messages[guild_id] = []
                self.ticket_messages[guild_id].append(msg.id)
                self.save_data()

                await interaction.response.send_message("Ticket panel created!", ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message(
                    "I don't have permission to send messages or add reactions in this channel.",
                    ephemeral=True
                )
            except Exception as e:
                await interaction.response.send_message(
                    f"An error occurred while creating the ticket panel: {str(e)}",
                    ephemeral=True
                )
        except Exception as e:
            if isinstance(e, (ValueError, discord.Forbidden)):
                await interaction.response.send_message(str(e), ephemeral=True)
            else:
                await interaction.response.send_message(
                    "An unexpected error occurred.",
                    ephemeral=True
                )

    @app_commands.command(name="close", description="Close a ticket and save its transcript")
    @app_commands.describe(
        reason="Optional reason for closing the ticket"
    )
    async def close(self, interaction: discord.Interaction, reason: Optional[str] = None):
        """Close the ticket with enhanced error handling and transcript generation"""
        try:
            if not interaction.channel or not interaction.guild:
                raise ValueError("This command can only be used in a server channel")

            if 'ticket-' not in interaction.channel.name:
                await interaction.response.send_message(
                    "This is not a ticket channel.",
                    ephemeral=True
                )
                return

            # Check if user has permission to close tickets
            member = interaction.guild.get_member(interaction.user.id)
            if not (member.guild_permissions.administrator or
                   any(role.name == "Support" for role in member.roles)):
                raise discord.Forbidden("You don't have permission to close tickets")

            try:
                # Generate transcript
                transcript = []
                async with await self.get_lock(f"transcript_{interaction.channel.id}"):
                    async for message in interaction.channel.history(limit=100):
                        # Format attachments
                        attachments = ''
                        if message.attachments:
                            attachments = f" [Attachments: {', '.join([a.filename for a in message.attachments])}]"

                        # Format embeds
                        embeds = ''
                        if message.embeds:
                            embeds = ' [Embedded content]'

                        transcript.append(
                            f"[{message.created_at.strftime('%Y-%m-%d %H:%M:%S')}] "
                            f"{message.author.name}: {message.content}{attachments}{embeds}"
                        )

                transcript_text = "\n".join(reversed(transcript))
                transcript_filename = f"transcript-{interaction.channel.name}.txt"

                # Save transcript atomically
                temp_filename = f"{transcript_filename}.tmp"
                with open(temp_filename, "w", encoding="utf-8") as f:
                    f.write(f"Ticket Transcript - {interaction.guild.name}\n")
                    f.write(f"Channel: {interaction.channel.name}\n")
                    f.write(f"Closed by: {interaction.user.name}\n")
                    if reason:
                        f.write(f"Reason: {reason}\n")
                    f.write(f"Date: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n")
                    f.write(transcript_text)

                os.replace(temp_filename, transcript_filename)

                # Clean up active tickets
                async with await self.get_lock(f"guild_{interaction.guild.id}"):
                    channel_name = interaction.channel.name.lower()
                    if channel_name.startswith('ticket-'):
                        username = channel_name[7:]  # Remove 'ticket-' prefix
                        guild_id = str(interaction.guild.id)
                        if guild_id in self.active_tickets:
                            # Find and remove the ticket
                            for user_id in list(self.active_tickets[guild_id].keys()):
                                user = interaction.guild.get_member(int(user_id))
                                if user and user.name.lower() == username:
                                    del self.active_tickets[guild_id][user_id]
                                    break
                            self.save_data()

                # Send transcript and close
                await interaction.response.send_message(
                    "Generating transcript and closing the ticket in 5 seconds..."
                )
                await interaction.channel.send(file=discord.File(transcript_filename))
                await asyncio.sleep(5)
                await interaction.channel.delete()

                # Clean up transcript file
                try:
                    os.remove(transcript_filename)
                except:
                    pass

            except discord.Forbidden:
                raise discord.Forbidden("I don't have permission to manage this ticket channel")
            except Exception as e:
                raise ValueError(f"Failed to close the ticket: {str(e)}")

        except Exception as e:
            error_msg = str(e) if isinstance(e, (ValueError, discord.Forbidden)) else "An unexpected error occurred"
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(f"Error: {error_msg}", ephemeral=True)
                else:
                    await interaction.response.send_message(f"Error: {error_msg}", ephemeral=True)
            except:
                pass

async def setup(bot):
    try:
        await bot.add_cog(TicketsCog(bot))
        print("Successfully loaded TicketsCog")
    except Exception as e:
        print(f"Error loading TicketsCog: {str(e)}")