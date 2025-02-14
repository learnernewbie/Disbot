import discord
from discord import app_commands
from discord.ext import commands
import asyncio
from datetime import datetime, timedelta
import json
import os
from typing import Dict, Any, Optional, Set, Pattern
import re
import traceback
from collections import defaultdict
import sys
import time
import logging

logger = logging.getLogger(__name__)

class EventsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.events: Dict[str, Dict[str, Any]] = {}
        self.event_messages: Set[int] = set()
        self.default_reactions = ["✅", "❌", "❔"]
        self.guild_rate_limits: Dict[str, datetime] = {}
        self.locks: Dict[str, asyncio.Lock] = {}
        self.trigger_patterns: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)
        self.trigger_cache: Dict[str, Pattern] = {}
        self.trigger_timeouts: Dict[str, float] = {}
        self.load_data()
        self.cleanup_task = self.bot.loop.create_task(self.cleanup_old_events())
        self.reminder_task = self.bot.loop.create_task(self.check_event_reminders())

    def load_data(self):
        """Load event data and custom trigger patterns with enhanced error handling and validation"""
        try:
            # Load events data
            with open('data/events.json', 'r') as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    raise ValueError("Invalid events data format")

                events_data = data.get('events', {})
                message_ids = set(data.get('message_ids', []))

                validated_events = {}
                for event_id, event in events_data.items():
                    try:
                        if not all(key in event for key in ['guild_id', 'title', 'timestamp']):
                            print(f"Skipping invalid event {event_id}: Missing required fields")
                            continue

                        event_time = datetime.fromtimestamp(event['timestamp'])
                        if event_time < datetime.utcnow():
                            print(f"Skipping expired event {event_id}")
                            continue

                        event.setdefault('reactions', {})
                        event.setdefault('custom_emojis', self.default_reactions)
                        event.setdefault('creator_name', 'Unknown')

                        validated_events[event_id] = event

                    except (ValueError, TypeError) as e:
                        print(f"Error validating event {event_id}: {e}")
                        continue

                self.events = validated_events
                self.event_messages = message_ids

        except FileNotFoundError:
            print("No existing events data found, creating new data structure")
            self.events = {}
            self.event_messages = set()
        except json.JSONDecodeError:
            print("Error: events.json is corrupted, creating backup and new file")
            self._backup_corrupted_file('data/events.json')
            self.events = {}
            self.event_messages = set()
        except Exception as e:
            print(f"Unexpected error loading events data: {e}")
            traceback.print_exc()
            self.events = {}
            self.event_messages = set()

        self.save_data()
        self.load_triggers()

    def load_triggers(self):
        """Load custom trigger patterns with enhanced validation and error handling"""
        try:
            with open('data/custom_triggers.json', 'r') as f:
                triggers_data = json.load(f)

            # Clear existing patterns before loading
            self.trigger_patterns.clear()
            self.trigger_cache.clear()

            for guild_id, guild_triggers in triggers_data.items():
                if not isinstance(guild_triggers, dict):
                    print(f"Invalid trigger format for guild {guild_id}")
                    continue

                self.trigger_patterns[guild_id] = {}

                for trigger_name, trigger_info in guild_triggers.items():
                    if not isinstance(trigger_info, dict):
                        print(f"Invalid trigger info format for {trigger_name}")
                        continue

                    pattern = trigger_info.get('pattern', '')
                    if not pattern or not isinstance(pattern, str):
                        print(f"Invalid pattern for trigger {trigger_name}")
                        continue

                    try:
                        # Cache the compiled pattern
                        pattern_key = f"{guild_id}:{trigger_name}"
                        self.trigger_cache[pattern_key] = re.compile(pattern, re.IGNORECASE)

                        self.trigger_patterns[guild_id][trigger_name] = {
                            'type': trigger_info.get('type', 'message'),
                            'response': trigger_info.get('response', ''),
                            'emoji': trigger_info.get('emoji', '')
                        }
                    except re.error as e:
                        print(f"Invalid regex pattern for trigger {trigger_name}: {e}")
                        continue
                    except Exception as e:
                        print(f"Error processing trigger {trigger_name}: {e}")
                        continue

                # Set default timeout (5 seconds)
                self.trigger_timeouts[guild_id] = 5.0

        except FileNotFoundError:
            print("No custom triggers found")
        except json.JSONDecodeError as e:
            print(f"Error parsing custom triggers JSON: {e}")
            self._backup_corrupted_file('data/custom_triggers.json')
        except Exception as e:
            print(f"Error loading custom triggers: {e}")
            traceback.print_exc()

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
        """Save event data with atomic writes and validation"""
        lock_key = "save_data"
        temp_file = 'data/events_temp.json'

        try:
            save_data = {
                'events': self.events,
                'message_ids': list(self.event_messages)
            }

            with open(temp_file, 'w') as f:
                json.dump(save_data, f, indent=4)

            os.replace(temp_file, 'data/events.json')

        except Exception as e:
            print(f"Error saving events data: {e}")
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except:
                    pass

    async def get_lock(self, key: str) -> asyncio.Lock:
        """Get or create a lock for the given key"""
        if key not in self.locks:
            self.locks[key] = asyncio.Lock()
        return self.locks[key]

    async def check_permissions(self, guild: discord.Guild) -> bool:
        """Check if bot has required permissions for event operations"""
        try:
            if not guild or not guild.me:
                return False

            required_permissions = [
                "manage_messages",
                "read_message_history",
                "add_reactions",
                "embed_links",
                "attach_files",
                "read_messages",
                "send_messages",
                "view_channel"
            ]

            missing_permissions = []
            for perm in required_permissions:
                if not getattr(guild.me.guild_permissions, perm, False):
                    missing_permissions.append(perm)

            if missing_permissions:
                print(f"Missing event permissions in {guild.name}: {', '.join(missing_permissions)}")
                return False
            return True

        except Exception as e:
            print(f"Error checking permissions: {e}")
            return False

    async def get_guild_events(self, guild_id: int) -> dict:
        """Get all events for a specific guild with error handling"""
        try:
            if not guild_id:
                raise ValueError("Invalid guild ID")

            return {
                event_id: event for event_id, event in self.events.items()
                if event.get('guild_id') == str(guild_id)
            }
        except Exception as e:
            print(f"Error getting guild events for {guild_id}: {e}")
            return {}

    async def can_create_event(self, guild_id: str) -> bool:
        """Check if the guild can create a new event (rate limiting)"""
        if guild_id in self.guild_rate_limits:
            time_diff = datetime.utcnow() - self.guild_rate_limits[guild_id]
            if time_diff < timedelta(minutes=1):
                return False
        return True

    @app_commands.command(name="event")
    @app_commands.guild_only()
    @app_commands.describe(
        title="The title of the event",
        date="Date of the event (YYYY-MM-DD)",
        time="Time of the event (HH:MM)",
        description="Description of the event",
        custom_emojis="Optional: Custom emojis separated by spaces (max 15)"
    )
    async def create_event(
        self,
        interaction: discord.Interaction,
        title: str,
        date: str,
        time: str,
        description: str,
        custom_emojis: Optional[str] = None
    ):
        """Create a new event with enhanced error handling and validation"""
        try:
            # Verify guild context
            if not interaction.guild:
                await interaction.response.send_message("This command can only be used in a server", ephemeral=True)
                return

            # Validate input before deferring
            if not title or len(title.strip()) < 3:
                await interaction.response.send_message("Event title must be at least 3 characters long", ephemeral=True)
                return

            if not description or len(description.strip()) < 10:
                await interaction.response.send_message("Event description must be at least 10 characters long", ephemeral=True)
                return

            # Validate date and time
            try:
                event_time = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
                if event_time < datetime.utcnow():
                    await interaction.response.send_message("Cannot create events in the past", ephemeral=True)
                    return
                if event_time > datetime.utcnow() + timedelta(days=365):
                    await interaction.response.send_message("Cannot create events more than 1 year in advance", ephemeral=True)
                    return
            except ValueError:
                await interaction.response.send_message(
                    "Invalid date/time format. Use YYYY-MM-DD for date and HH:MM for time",
                    ephemeral=True
                )
                return

            # Verify permissions early
            if not interaction.guild.me.guild_permissions.send_messages:
                await interaction.response.send_message(
                    "I don't have permission to send messages in this channel.",
                    ephemeral=True
                )
                return

            # Process custom emojis before deferring
            reactions_to_use = self.default_reactions.copy()
            if custom_emojis:
                custom_list = custom_emojis.split()
                if len(custom_list) > 15:
                    await interaction.response.send_message("Maximum 15 custom emojis allowed", ephemeral=True)
                    return
                reactions_to_use = custom_list

            # Defer the response after all validations
            await interaction.response.defer(ephemeral=True)

            # Create event message
            event_id = f"{interaction.guild_id}/{str(interaction.id)}"
            async with await self.get_lock(f"event_{event_id}"):
                try:
                    embed = self.create_event_embed(title, description, event_time, reactions_to_use)
                    event_message = await interaction.channel.send(embed=embed)

                    for emoji in reactions_to_use:
                        try:
                            await event_message.add_reaction(emoji)
                            await asyncio.sleep(0.5)  # Add small delay between reactions to avoid rate limits
                        except discord.HTTPException as e:
                            print(f"Failed to add reaction {emoji}: {e}")
                            continue

                    self.events[event_id] = {
                        "id": event_id,
                        "guild_id": str(interaction.guild.id),
                        "title": title,
                        "description": description,
                        "timestamp": int(event_time.timestamp()),
                        "creator_id": interaction.user.id,
                        "creator_name": interaction.user.name,
                        "reactions": {},
                        "custom_emojis": reactions_to_use,
                        "message_id": event_message.id,
                        "channel_id": interaction.channel_id,
                        "created_at": datetime.utcnow().isoformat()
                    }

                    self.event_messages.add(event_message.id)
                    self.save_data()

                    await interaction.followup.send("Event created successfully!", ephemeral=True)

                except discord.Forbidden:
                    await interaction.followup.send(
                        "I don't have permission to manage messages in this channel.",
                        ephemeral=True
                    )
                except Exception as e:
                    print(f"Error creating event message: {e}")
                    await interaction.followup.send(
                        f"An error occurred while creating the event: {str(e)}",
                        ephemeral=True
                    )

        except Exception as e:
            error_message = f"An unexpected error occurred: {str(e)}"
            print(f"Critical error in create_event: {error_message}")
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(error_message, ephemeral=True)
                else:
                    await interaction.followup.send(error_message, ephemeral=True)
            except Exception as e2:
                print(f"Error sending error message: {str(e2)}")

    def create_event_embed(self, title: str, description: str, event_time: datetime, reactions: list) -> discord.Embed:
        """Create an event embed with proper formatting"""
        embed = discord.Embed(
            title=f"📅 {title}",
            description=description,
            color=discord.Color.blue()
        )

        timestamp = int(event_time.timestamp())
        embed.add_field(
            name="📆 Date & Time",
            value=f"<t:{timestamp}:F>\nTime Remaining: <t:{timestamp}:R>",
            inline=False
        )

        groups_text = self._generate_groups_text(reactions)
        embed.add_field(
            name="🎮 Activity Groups",
            value=groups_text,
            inline=False
        )

        embed.add_field(
            name="✅ Confirmed Participants",
            value="Total: 0\nNo confirmed participants yet",
            inline=False
        )

        return embed


    def _generate_groups_text(self, emojis: list) -> str:
        """Generate the groups text for the embed"""
        groups_text = ""
        for emoji in emojis:
            groups_text += f"\n{emoji} **(0)**\nNo participants yet\n"
        return groups_text.strip()

    async def update_event_display(self, channel: discord.TextChannel, message: discord.Message, event_id: str):
        """Update the event message display with error handling"""
        try:
            event = self.events.get(event_id)
            if not event:
                return

            embed = discord.Embed(
                title=f"📅 {event['title']}",
                description=event['description'],
                color=discord.Color.blue()
            )

            embed.add_field(
                name="📆 Date & Time",
                value=f"<t:{event['timestamp']}:F>\nTime Remaining: <t:{event['timestamp']}:R>",
                inline=False
            )

            groups_text = ""
            for emoji in event["custom_emojis"]:
                participants = event["reactions"].get(emoji, [])
                participant_count = len(participants)
                groups_text += f"\n{emoji} **({participant_count})**\n"
                if participants:
                    participant_mentions = []
                    for uid in participants:
                        member = channel.guild.get_member(uid)
                        if member:
                            participant_mentions.append(member.mention)
                    groups_text += " ".join(participant_mentions) if participant_mentions else "No participants yet"
                else:
                    groups_text += "No participants yet"
                groups_text += "\n"

            embed.add_field(
                name="🎮 Activity Groups",
                value=groups_text.strip(),
                inline=False
            )

            confirmed = self.get_confirmed_participants(event_id)
            confirmed_mentions = []
            for uid in confirmed:
                member = channel.guild.get_member(uid)
                if member:
                    confirmed_mentions.append(member.mention)

            if confirmed_mentions:
                embed.add_field(
                    name="✅ Confirmed Participants",
                    value=f"Total: {len(confirmed_mentions)}\n{' '.join(confirmed_mentions)}",
                    inline=False
                )
            else:
                embed.add_field(
                    name="✅ Confirmed Participants",
                    value="Total: 0\nNo confirmed participants yet",
                    inline=False
                )

            embed.set_footer(text=f"Event ID: {event_id} • Created by {event['creator_name']}")
            await message.edit(embed=embed)

        except discord.NotFound:
            if event_id in self.events:
                del self.events[event_id]
                self.save_data()
        except Exception as e:
            print(f"Error updating event display: {e}")
            traceback.print_exc()

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        """Handle reaction adds to event messages with enhanced error handling"""
        try:
            if payload.message_id not in self.event_messages or payload.user_id == self.bot.user.id:
                return

            try:
                channel = await self.bot.fetch_channel(payload.channel_id)
                message = await channel.fetch_message(payload.message_id)
                user = await self.bot.fetch_user(payload.user_id)
            except discord.NotFound:
                self.event_messages.discard(payload.message_id)
                self.save_data()
                return
            except Exception as e:
                print(f"Error fetching message/user data: {e}")
                return

            event_id = None
            async with await self.get_lock(f"guild_{payload.guild_id}"):
                guild_events = await self.get_guild_events(payload.guild_id)
                for eid, event in guild_events.items():
                    if event.get('message_id') == payload.message_id:
                        event_id = eid
                        break

            if not event_id:
                return

            emoji = str(payload.emoji)
            async with await self.get_lock(f"event_{event_id}"):
                if emoji not in self.events[event_id]["custom_emojis"]:
                    await message.remove_reaction(payload.emoji, user)
                    return

                if emoji not in self.events[event_id]["reactions"]:
                    self.events[event_id]["reactions"][emoji] = []

                for other_emoji in self.events[event_id]["custom_emojis"]:
                    if other_emoji != emoji:
                        try:
                            await message.remove_reaction(other_emoji, user)
                        except:
                            pass
                        if other_emoji in self.events[event_id]["reactions"]:
                            self.events[event_id]["reactions"][other_emoji] = [
                                uid for uid in self.events[event_id]["reactions"][other_emoji]
                                if uid != user.id
                            ]

                if user.id not in self.events[event_id]["reactions"][emoji]:
                    self.events[event_id]["reactions"][emoji].append(user.id)
                    self.save_data()

            await self.update_event_display(channel, message, event_id)

        except Exception as e:
            print(f"Error handling reaction add: {e}")
            traceback.print_exc()

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        """Handle reaction removes from event messages with enhanced error handling"""
        try:
            if payload.message_id not in self.event_messages or payload.user_id == self.bot.user.id:
                return

            try:
                channel = await self.bot.fetch_channel(payload.channel_id)
                message = await channel.fetch_message(payload.message_id)
                user = await self.bot.fetch_user(payload.user_id)
            except discord.NotFound:
                self.event_messages.discard(payload.message_id)
                self.save_data()
                return
            except Exception as e:
                print(f"Error fetching message/user data: {e}")
                return

            event_id = None
            async with await self.get_lock(f"guild_{payload.guild_id}"):
                guild_events = await self.get_guild_events(payload.guild_id)
                for eid, event in guild_events.items():
                    if event.get('message_id') == payload.message_id:
                        event_id = eid
                        break

            if not event_id:
                return

            emoji = str(payload.emoji)
            async with await self.get_lock(f"event_{event_id}"):
                if emoji in self.events[event_id]["reactions"]:
                    self.events[event_id]["reactions"][emoji] = [
                        uid for uid in self.events[event_id]["reactions"][emoji]
                        if uid != user.id
                    ]
                    self.save_data()

            await self.update_event_display(channel, message, event_id)

        except Exception as e:
            print(f"Error handling reaction remove: {e}")
            traceback.print_exc()

    def get_confirmed_participants(self, event_id: str) -> list:
        """Get list of users who reacted with anything except ❌ and ❔"""
        try:
            event = self.events.get(event_id)
            if not event:
                return []

            confirmed = set()
            if 'reactions' in event:
                for emoji, users in event["reactions"].items():
                    if emoji not in ["❌", "❔"]:
                        confirmed.update(users)
            return list(confirmed)
        except Exception as e:
            print(f"Error getting confirmed participants for {event_id}: {e}")
            return []

    async def cleanup_old_events(self):
        """Remove events that have ended"""
        while not self.bot.is_closed():
            try:
                current_time = datetime.utcnow().timestamp()
                events_to_remove = []

                for event_id, event in self.events.items():
                    if event['timestamp'] < current_time:
                        try:
                            channel = await self.bot.fetch_channel(event['channel_id'])
                            if channel:
                                message = await channel.fetch_message(event['message_id'])
                                if message:
                                    await message.delete()
                                    logger.info(f"Deleted expired event message for {event_id}")
                        except discord.NotFound:
                            logger.info(f"Message or channel already deleted for event {event_id}")
                        except discord.Forbidden:
                            logger.error(f"Missing permissions to delete message for event {event_id}")
                        except Exception as e:
                            logger.error(f"Error cleaning up event {event_id}: {e}")

                        events_to_remove.append(event_id)

                if events_to_remove:
                    for event_id in events_to_remove:
                        if event_id in self.events:
                            msg_id = self.events[event_id]['message_id']
                            del self.events[event_id]
                            self.event_messages.discard(msg_id)

                    self.save_data()
                    logger.info(f"Cleaned up {len(events_to_remove)} expired events")

            except Exception as e:
                logger.error(f"Error in cleanup task: {e}")
                traceback.print_exc()

            await asyncio.sleep(300)  # Check every 5 minutes

    def cog_unload(self):
        """Cleanup when cog is unloaded"""
        try:
            if hasattr(self, 'cleanup_task'):
                self.cleanup_task.cancel()
            if hasattr(self, 'reminder_task'):
                self.reminder_task.cancel()
            self.save_data()

            # Clear any remaining locks and caches
            self.locks.clear()
            self.trigger_patterns.clear()
            self.trigger_cache.clear()
            self.trigger_timeouts.clear()
            logger.info("Successfully unloaded EventsCog and cleared resources")
        except Exception as e:
            logger.error(f"Error unloading EventsCog: {e}")
            traceback.print_exc()

    @app_commands.command(name="cancel")
    @app_commands.describe(event_id="The ID of the event to cancel")
    async def cancel_event(self, interaction: discord.Interaction, event_id: str):
        """Cancel an event (only available to event creator)"""
        try:
            if event_id not in self.events:
                await interaction.response.send_message("Event not found!", ephemeral=True)
                return

            event = self.events[event_id]
            if event["creator_id"] != interaction.user.id:
                await interaction.response.send_message("You can only cancel events you created!", ephemeral=True)
                return

            async with await self.get_lock(f"event_{event_id}"):
                try:
                    channel = await self.bot.fetch_channel(event["channel_id"])
                    message = await channel.fetch_message(event["message_id"])
                    await message.delete()
                except:
                    pass

                try:
                    confirmed = self.get_confirmed_participants(event_id)
                    if confirmed:
                        mentions = " ".join([f"<@{uid}>" for uid in confirmed])
                        cancel_notice = (
                            f"❌ **Event Cancelled**\n"
                            f"The event '{event['title']}' has been cancelled by the organizer.\n"
                            f"Affected participants: {mentions}"
                        )
                        await interaction.channel.send(cancel_notice)
                except:
                    pass
                del self.events[event_id]
                if event['message_id'] in self.event_messages:
                    self.event_messages.discard(event['message_id'])
                self.save_data()

            await interaction.response.send_message(f"Event '{event['title']}' has been cancelled.", ephemeral=True)

        except Exception as e:
            print(f"Error cancelling event: {e}")
            traceback.print_exc()
            await interaction.response.send_message("An error occurred while cancelling the event.", ephemeral=True)

    @app_commands.command(name="events")
    async def list_events(self, interaction: discord.Interaction):
        """List all upcoming events"""
        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)

            current_time = datetime.utcnow().timestamp()
            guild_events = await self.get_guild_events(interaction.guild_id)
            upcoming_events = {
                event_id: event for event_id, event in guild_events.items()
                if event["timestamp"] > current_time
            }

            if not upcoming_events:
                await interaction.followup.send("No upcoming events!", ephemeral=True)
                return

            embed = discord.Embed(
                title=f"📅 Upcoming Events in {interaction.guild.name}",
                color=discord.Color.blue()
            )

            for event_id, event in sorted(upcoming_events.items(), key=lambda x: x[1]["timestamp"]):
                try:
                    confirmed_count = len(self.get_confirmed_participants(event_id))
                    embed.add_field(
                        name=f"{event['title']} (<t:{event['timestamp']}:R>)",
                        value=(
                            f"ID: {event_id}\n"
                            f"Date: <t:{event['timestamp']}:F>\n"
                            f"Confirmed Participants: {confirmed_count}\n"
                            f"Created by: {event['creator_name']}"
                        ),
                        inline=False
                    )
                except Exception as e:
                    print(f"Error processing event {event_id}: {e}")
                    continue

            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            error_msg = "An error occurred while listing events. Please try again later."
            print(f"Error in list_events: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(error_msg, ephemeral=True)
            else:
                await interaction.followup.send(error_msg, ephemeral=True)

    @app_commands.command(name="duplicate")
    @app_commands.describe(
        event_id="The ID of the event to duplicate",
        date="New date for the event (YYYY-MM-DD)",
        time="New time for the event (HH:MM)"
    )
    async def duplicate_event(
        self,
        interaction: discord.Interaction,
        event_id: str,
        date: str,
        time: str
    ):
        """Duplicate an existing event with a new date/time"""
        try:
            if event_id not in self.events:
                await interaction.response.send_message("Event not found!", ephemeral=True)
                return

            original_event = self.events[event_id]
            try:
                event_time = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
                if event_time < datetime.utcnow():
                    await interaction.response.send_message("Cannot create events in the past", ephemeral=True)
                    return
            except ValueError:
                await interaction.response.send_message(
                    "Invalid date/time format. Use YYYY-MM-DD for date and HH:MM for time",
                    ephemeral=True
                )
                return

            await interaction.response.defer(ephemeral=True)

            try:
                new_event_id = f"{interaction.guild_id}/{str(interaction.id)}"
                embed = self.create_event_embed(
                    original_event['title'],
                    original_event['description'],
                    event_time,
                    original_event['custom_emojis']
                )

                event_message = await interaction.channel.send(embed=embed)

                for emoji in original_event['custom_emojis']:
                    try:
                        await event_message.add_reaction(emoji)
                        await asyncio.sleep(0.5)
                    except discord.HTTPException as e:
                        print(f"Failed to add reaction {emoji}: {e}")
                        continue

                self.events[new_event_id] = {
                    "id": new_event_id,
                    "guild_id": str(interaction.guild.id),
                    "title": original_event['title'],
                    "description": original_event['description'],
                    "timestamp": int(event_time.timestamp()),
                    "creator_id": interaction.user.id,
                    "creator_name": interaction.user.name,
                    "reactions": {},
                    "custom_emojis": original_event['custom_emojis'],
                    "message_id": event_message.id,
                    "channel_id": interaction.channel_id,
                    "created_at": datetime.utcnow().isoformat()
                }

                self.event_messages.add(event_message.id)
                self.save_data()

                await interaction.followup.send(
                    f"Successfully duplicated event '{original_event['title']}' with new date/time.",
                    ephemeral=True
                )

            except Exception as e:
                print(f"Error duplicating event: {e}")
                traceback.print_exc()
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "A critical error occurred while duplicating the event.",
                        ephemeral=True
                    )
                else:
                    await interaction.followup.send(
                        "A critical error occurred while duplicating the event.",
                        ephemeral=True
                    )

        except Exception as e:
            print(f"Error in duplicate_event: {e}")
            traceback.print_exc()
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "An error occurred while duplicating the event.",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "An error occurred while duplicating the event.",
                    ephemeral=True
                )

    async def check_event_reminders(self):
        """Check for upcoming events and send reminders"""
        while not self.bot.is_closed():
            try:
                current_time = datetime.utcnow().timestamp()
                for event_id, event in self.events.items():
                    if event['timestamp'] - current_time <= 300 and event['timestamp'] > current_time:  # 5 minutes before
                        try:
                            channel = await self.bot.fetch_channel(event['channel_id'])
                            confirmed = self.get_confirmed_participants(event_id)
                            if confirmed:
                                mentions = " ".join([f"<@{uid}>" for uid in confirmed])
                                reminder = (
                                    f"🔔 **Event Reminder**\n"
                                    f"The event '{event['title']}' is starting in 5 minutes!\n"
                                    f"Participants: {mentions}"
                                )
                                await channel.send(reminder)
                        except Exception as e:
                            logger.error(f"Error sending reminder for event {event_id}: {e}")
                            continue

            except Exception as e:
                logger.error(f"Error in reminder task: {e}")
                traceback.print_exc()

            await asyncio.sleep(300)  # Check every 5 minutes

# Setup function must be at module level
async def setup(bot: commands.Bot) -> None:
    """Initialize the events cog with improved error handling"""
    try:
        # Initialize required data files
        if not os.path.exists('data'):
            os.makedirs('data')

        data_files = ['events.json', 'custom_triggers.json']
        for file in data_files:
            file_path = f'data/{file}'
            if not os.path.exists(file_path):
                with open(file_path, 'w') as f:
                    json.dump({}, f)

        # Initialize and add the cog
        events_cog = EventsCog(bot)
        await bot.add_cog(events_cog)
        logger.info("Successfully loaded EventsCog")
    except Exception as e:
        logger.error(f"Failed to load EventsCog: {e}")
        logger.error(traceback.format_exc())
        raise