import discord
from discord import app_commands
from discord.ext import commands, tasks
import json
from datetime import datetime, timedelta
from typing import Dict, List, Set
import time
import io
import os

# Wrap matplotlib imports in try-except to handle potential import errors
try:
    import matplotlib.pyplot as plt
    from matplotlib.dates import DateFormatter
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    print("Warning: matplotlib not available. Graph generation features will be disabled.")
    MATPLOTLIB_AVAILABLE = False

class AnalyticsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.analytics_data = {
            "last_update": datetime.utcnow().isoformat(),
            "guilds": {}
        }
        self.command_usage = {}
        self.channel_stats = {
            "last_update": datetime.utcnow().isoformat(),
            "channels": {}
        }
        self.user_activity = {}
        self.growth_metrics = {}
        self.load_data()

        # Start both tracking and cleanup tasks
        if MATPLOTLIB_AVAILABLE:
            try:
                self.track_analytics.start()
                self.cleanup_old_data.start()  # Start cleanup task
                print("Analytics tracking and cleanup tasks started")
            except Exception as e:
                print(f"Failed to start analytics tasks: {e}")
        else:
            print("Analytics tracking started without graph generation support")

    def cog_unload(self):
        try:
            if hasattr(self, 'track_analytics'):
                self.track_analytics.cancel()
            if hasattr(self, 'cleanup_old_data'):
                self.cleanup_old_data.cancel()
        except Exception as e:
            print(f"Error unloading analytics tracking: {e}")

    async def check_permissions(self, guild: discord.Guild) -> bool:
        """Check if bot has required permissions for analytics operations"""
        try:
            if not guild or not guild.me:
                return False

            required_permissions = [
                "view_channel",
                "read_message_history",
                "view_audit_log"
            ]

            missing_permissions = []
            for perm in required_permissions:
                if not getattr(guild.me.guild_permissions, perm, False):
                    missing_permissions.append(perm)

            if missing_permissions:
                print(f"Missing analytics permissions in {guild.name}: {', '.join(missing_permissions)}")
                return False
            return True
        except Exception as e:
            print(f"Error checking permissions: {e}")
            return False

    def load_data(self):
        """Load analytics data with proper error handling"""
        try:
            with open('data/analytics.json', 'r') as f:
                data = json.load(f)
                if isinstance(data, dict):
                    self.analytics_data = data
                else:
                    print("Warning: analytics.json contained invalid data, resetting to default")
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"Error loading analytics data: {e}")
            self.save_data()  # Create the file with default structure

        try:
            with open('data/command_usage.json', 'r') as f:
                data = json.load(f)
                if isinstance(data, dict):
                    self.command_usage = data
        except (FileNotFoundError, json.JSONDecodeError):
            self.command_usage = {}

        try:
            with open('data/channel_stats.json', 'r') as f:
                data = json.load(f)
                if isinstance(data, dict) and "channels" in data:
                    self.channel_stats = data
                else:
                    print("Warning: channel_stats.json had invalid format, resetting")
        except (FileNotFoundError, json.JSONDecodeError):
            pass  # Will use default initialized structure

        try:
            with open('data/user_activity.json', 'r') as f:
                data = json.load(f)
                if isinstance(data, dict):
                    self.user_activity = data
        except (FileNotFoundError, json.JSONDecodeError):
            pass  # Will use default initialized structure

        try:
            with open('data/growth_metrics.json', 'r') as f:
                data = json.load(f)
                if isinstance(data, dict):
                    self.growth_metrics = data
        except (FileNotFoundError, json.JSONDecodeError):
            pass  # Will use default initialized structure

        print(f"Successfully loaded analytics data. Tracking {len(self.analytics_data.get('guilds', {}))} guilds")

    def save_data(self):
        """Save analytics data with atomic writes and error handling"""
        try:
            # Create copies of data structures for saving
            channel_stats_save = {
                "last_update": datetime.utcnow().isoformat(),
                "channels": {}
            }

            # Process channel stats
            if "channels" in self.channel_stats:
                for channel_id, stats in self.channel_stats["channels"].items():
                    if isinstance(stats, dict):
                        channel_stats_save["channels"][channel_id] = {
                            "total_messages": stats.get("total_messages", 0),
                            "active_users": list(stats["active_users"]) if isinstance(stats.get("active_users"), set) else []
                        }

            # Process user activity
            user_activity_save = {}
            for guild_id, guild_data in self.user_activity.items():
                if isinstance(guild_data, dict):
                    user_activity_save[guild_id] = {}
                    for user_id, user_stats in guild_data.items():
                        if isinstance(user_stats, dict):
                            user_activity_save[guild_id][user_id] = {
                                "message_count": user_stats.get("message_count", 0),
                                "active_channels": list(user_stats["active_channels"]) if isinstance(user_stats.get("active_channels"), set) else []
                            }

            # Save files individually with atomic writes
            files_to_save = [
                ('data/analytics.json', self.analytics_data),
                ('data/command_usage.json', self.command_usage),
                ('data/channel_stats.json', channel_stats_save),
                ('data/user_activity.json', user_activity_save),
                ('data/growth_metrics.json', self.growth_metrics)
            ]

            for filename, data in files_to_save:
                temp_file = f"{filename}.tmp"
                try:
                    with open(temp_file, 'w') as f:
                        json.dump(data, f, indent=4)
                    os.replace(temp_file, filename)
                except Exception as e:
                    print(f"Error saving {filename}: {str(e)}")
                    if os.path.exists(temp_file):
                        try:
                            os.remove(temp_file)
                        except:
                            pass

            print(f"Successfully saved analytics data. Current stats: {len(self.analytics_data.get('guilds', {}))} guilds tracked")
        except Exception as e:
            print(f"Error in save_data: {str(e)}")

    async def ensure_guild_initialized(self, guild: discord.Guild) -> bool:
        """Initialize guild data structures with validation"""
        try:
            if not guild:
                return False

            guild_id = str(guild.id)

            # Check permissions first
            if not await self.check_permissions(guild):
                return False

            # Initialize guild data structures if needed
            if guild_id not in self.analytics_data["guilds"]:
                self.analytics_data["guilds"][guild_id] = {
                    "joined_at": datetime.utcnow().isoformat(),
                    "member_count": guild.member_count,
                    "channel_count": len(guild.channels),
                    "role_count": len(guild.roles)
                }

            if guild_id not in self.user_activity:
                self.user_activity[guild_id] = {}

            if guild_id not in self.growth_metrics:
                self.growth_metrics[guild_id] = {"member_growth": []}

            self.save_data()
            return True

        except Exception as e:
            print(f"Error initializing guild {guild.id if guild else 'Unknown'}: {str(e)}")
            return False

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        """Handle new guild joins with proper initialization"""
        try:
            if await self.ensure_guild_initialized(guild):
                print(f"Successfully initialized analytics for new guild: {guild.name}")
            else:
                print(f"Failed to initialize analytics for guild: {guild.name}")
        except Exception as e:
            print(f"Error handling guild join for {guild.name}: {str(e)}")

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        """Handle guild removals gracefully"""
        try:
            guild_id = str(guild.id)
            # Archive data instead of deleting it
            if guild_id in self.analytics_data["guilds"]:
                self.analytics_data["guilds"][guild_id]["left_at"] = datetime.utcnow().isoformat()
            self.save_data()
        except Exception as e:
            print(f"Error handling guild remove for {guild.name}: {str(e)}")

    @app_commands.command(name="serverstats")
    @app_commands.default_permissions(manage_guild=True)
    async def serverstats(self, interaction: discord.Interaction, timeframe: str = "7d"):
        """Display detailed server statistics and trends"""
        try:
            if not interaction.guild:
                raise ValueError("This command can only be used in a server")

            if not await self.check_permissions(interaction.guild):
                await interaction.response.send_message(
                    "❌ I don't have the required permissions to access analytics data.",
                    ephemeral=True
                )
                return

            guild_id = str(interaction.guild.id)
            if not await self.ensure_guild_initialized(interaction.guild):
                await interaction.response.send_message(
                    "Failed to initialize analytics data. Please try again later.",
                    ephemeral=True
                )
                return

            try:
                days = int(timeframe[:-1]) if timeframe[-1] == 'd' else 7
                if not 1 <= days <= 30:
                    raise ValueError()
            except ValueError:
                await interaction.response.send_message(
                    "Invalid timeframe. Please use format: 7d (maximum 30 days)",
                    ephemeral=True
                )
                return

            data = self.analytics_data["guilds"].get(guild_id, {})
            growth_data = self.growth_metrics.get(guild_id, {})

            embed = discord.Embed(
                title=f"📈 Server Analytics for {interaction.guild.name}",
                description=f"Statistics for the last {days} days",
                color=discord.Color.blue()
            )

            # Growth Metrics
            growth = growth_data.get("member_growth", [])
            recent_growth = growth[-days:] if growth else []
            net_growth = sum(day.get("net", 0) for day in recent_growth)

            embed.add_field(
                name="📊 Growth",
                value=f"Net Growth: {net_growth:+}\n"
                      f"Daily Average: {net_growth/days:+.1f}\n"
                      f"Peak Growth: {max((day.get('net', 0) for day in recent_growth), default=0):+}",
                inline=False
            )

            # Engagement Stats
            msg_stats = data.get("current_period", {}).get("messages", {})
            daily_msgs = msg_stats.get("daily_average", 0)

            embed.add_field(
                name="💬 Engagement",
                value=f"Messages per Day: {daily_msgs:.1f}\n"
                      f"Active Users: {len(self.user_activity.get(guild_id, {}))}\n"
                      f"Active Channels: {len(msg_stats.get('channels', {}))}",
                inline=False
            )

            # Channel Activity
            channel_stats = self.channel_stats.get("channels",{})
            top_channels = sorted(
                channel_stats.items(),
                key=lambda x: x[1].get("total_messages", 0),
                reverse=True
            )[:3]

            channel_text = "\n".join(
                f"#{self.bot.get_channel(int(cid)).name}: {stats.get('total_messages', 0)} messages"
                for cid, stats in top_channels
                if self.bot.get_channel(int(cid))
            )

            embed.add_field(
                name="📱 Most Active Channels",
                value=channel_text or "No channel data available",
                inline=False
            )

            # User Activity
            user_stats = self.user_activity.get(guild_id, {})
            top_users = sorted(
                user_stats.items(),
                key=lambda x: x[1].get("message_count", 0),
                reverse=True
            )[:3]

            user_text = "\n".join(
                f"{self.bot.get_user(int(uid)).name}: {stats.get('message_count', 0)} messages"
                for uid, stats in top_users
                if self.bot.get_user(int(uid))
            )

            embed.add_field(
                name="👥 Most Active Users",
                value=user_text or "No user data available",
                inline=False
            )

            # Command Usage
            guild_commands = self.command_usage.get(guild_id, {})
            top_commands = sorted(guild_commands.items(), key=lambda x: x[1], reverse=True)[:5]
            if top_commands:
                cmd_text = "\n".join(f"/{cmd}: {count}" for cmd, count in top_commands)
                embed.add_field(
                    name="🤖 Top Commands",
                    value=cmd_text,
                    inline=False
                )

            await interaction.response.send_message(embed=embed)
        except Exception as e:
            error_msg = str(e) if isinstance(e, ValueError) else "An unexpected error occurred"
            await interaction.response.send_message(f"Error: {error_msg}", ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        guild_id = str(message.guild.id)
        channel_id = str(message.channel.id)
        user_id = str(message.author.id)

        if not await self.ensure_guild_initialized(message.guild):
            return

        try:
            # Initialize channel stats if needed
            if channel_id not in self.channel_stats["channels"]:
                self.channel_stats["channels"][channel_id] = {
                    "total_messages": 0,
                    "active_users": set(),  # Initialize as set
                    "last_activity": datetime.utcnow().isoformat()
                }

            # Update channel stats
            channel_data = self.channel_stats["channels"][channel_id]
            channel_data["total_messages"] += 1
            if isinstance(channel_data["active_users"], list):
                # Convert list to set if needed
                channel_data["active_users"] = set(channel_data["active_users"])
            channel_data["active_users"].add(user_id)
            channel_data["last_activity"] = datetime.utcnow().isoformat()

            # Initialize user activity if needed
            if guild_id not in self.user_activity:
                self.user_activity[guild_id] = {}

            if user_id not in self.user_activity[guild_id]:
                self.user_activity[guild_id][user_id] = {
                    "message_count": 0,
                    "active_channels": set(),  # Initialize as set
                    "last_active": datetime.utcnow().isoformat()
                }

            # Update user activity
            user_data = self.user_activity[guild_id][user_id]
            user_data["message_count"] += 1
            if isinstance(user_data["active_channels"], list):
                # Convert list to set if needed
                user_data["active_channels"] = set(user_data["active_channels"])
            user_data["active_channels"].add(channel_id)
            user_data["last_active"] = datetime.utcnow().isoformat()

            self.save_data()
        except Exception as e:
            print(f"Error updating analytics on message: {str(e)}")


    @app_commands.command(name="activitymap")
    @app_commands.default_permissions(manage_guild=True)
    async def activitymap(self, interaction: discord.Interaction):
        """Display server activity heatmap"""
        guild_id = str(interaction.guild.id)
        if not await self.ensure_guild_initialized(interaction.guild):
            await interaction.response.send_message("Failed to initialize analytics data.", ephemeral=True)
            return

        data = self.analytics_data["guilds"].get(guild_id, {}).get("current_period",{})
        active_hours = data.get("active_hours", {})

        embed = discord.Embed(
            title=f"📊 Activity Heatmap for {interaction.guild.name}",
            color=discord.Color.blue()
        )

        # Create activity heatmap
        hours = range(24)
        max_activity = max((int(active_hours.get(str(h), 0)) for h in hours), default=1)

        heatmap = ""
        for h in hours:
            activity = int(active_hours.get(str(h), 0))
            percentage = activity / max_activity
            bars = "█" * int(percentage * 20)
            heatmap += f"`{h:02d}:00` {bars} ({activity} messages)\n"

        embed.description = heatmap
        embed.set_footer(text="Times are in UTC")

        await interaction.response.send_message(embed=embed)

    @tasks.loop(minutes=5)
    async def track_analytics(self):
        """Update analytics data periodically"""
        for guild in self.bot.guilds:
            guild_id = str(guild.id)
            if not await self.ensure_guild_initialized(guild):
                continue

            current_period = self.analytics_data["guilds"][guild_id].get("current_period", {})

            # Ensure all required fields exist
            if "messages" not in current_period:
                current_period["messages"] = {
                    "total": 0,
                    "today": 0,
                    "daily_average": 0,
                    "channels": {}
                }

            try:
                # Update daily averages
                messages = current_period["messages"]
                days_tracked = (datetime.utcnow() - datetime.fromisoformat(
                    self.analytics_data["guilds"][guild_id]["joined_at"]
                )).days or 1
                messages["daily_average"] = messages["total"] / days_tracked

                self.save_data()
            except Exception as e:
                print(f"Error updating analytics for guild {guild_id}: {str(e)}")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild_id = str(member.guild.id)
        if not await self.ensure_guild_initialized(member.guild):
            return

        today = datetime.utcnow().date().isoformat()
        growth_data = self.growth_metrics[guild_id]["member_growth"]

        if not growth_data or growth_data[-1]["date"] != today:
            growth_data.append({"date": today, "joins": 1, "leaves": 0, "net": 1})
        else:
            growth_data[-1]["joins"] += 1
            growth_data[-1]["net"] = growth_data[-1]["joins"] - growth_data[-1]["leaves"]

        self.save_data()

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        guild_id = str(member.guild.id)
        if not await self.ensure_guild_initialized(member.guild):
            return

        today = datetime.utcnow().date().isoformat()
        growth_data = self.growth_metrics[guild_id]["member_growth"]

        if not growth_data or growth_data[-1]["date"] != today:
            growth_data.append({"date": today, "joins": 0, "leaves": 1, "net": -1})
        else:
            growth_data[-1]["leaves"] += 1
            growth_data[-1]["net"] = growth_data[-1]["joins"] - growth_data[-1]["leaves"]

        self.save_data()

    @commands.Cog.listener()
    async def on_app_command_completion(self, interaction: discord.Interaction, command: app_commands.Command):
        """Track command usage with proper initialization"""
        try:
            guild_id = str(interaction.guild.id)
            if not await self.ensure_guild_initialized(interaction.guild):
                return

            # Initialize command usage tracking for this guild if needed
            if guild_id not in self.command_usage:
                self.command_usage[guild_id] = {}

            command_name = command.name
            if command_name not in self.command_usage[guild_id]:
                self.command_usage[guild_id][command_name] = 0

            self.command_usage[guild_id][command_name] += 1
            self.save_data()
        except Exception as e:
            print(f"Error tracking command usage: {e}")

    def generate_activity_chart(self, data: Dict[str, int], title: str) -> io.BytesIO:
        """Generate a line chart for activity data"""
        if not MATPLOTLIB_AVAILABLE:
            raise RuntimeError("matplotlib is not available for graph generation")

        plt.figure(figsize=(10, 6))
        plt.clf()

        # Convert string hours to integers and sort
        hours = sorted([int(h) for h in data.keys()])
        values = [data[str(h)] for h in hours]

        plt.plot(hours, values, marker='o')
        plt.title(title)
        plt.xlabel('Hour (UTC)')
        plt.ylabel('Message Count')
        plt.grid(True)

        # Set x-axis ticks to show all 24 hours
        plt.xticks(range(0, 24, 2))

        # Save plot to bytes buffer
        buf = io.BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        plt.close()

        return buf

    def generate_growth_chart(self, growth_data: List[Dict]) -> io.BytesIO:
        """Generate a growth chart showing member joins and leaves"""
        if not MATPLOTLIB_AVAILABLE:
            raise RuntimeError("matplotlib is not available for graph generation")
        plt.figure(figsize=(10, 6))
        plt.clf()

        dates = [datetime.fromisoformat(day['date']) for day in growth_data]
        joins = [day['joins'] for day in growth_data]
        leaves = [-day['leaves'] for day in growth_data]  # Negative for better visualization
        net = [day['net'] for day in growth_data]

        plt.plot(dates, joins, 'g-', label='Joins', marker='o')
        plt.plot(dates, leaves, 'r-', label='Leaves', marker='o')
        plt.plot(dates, net, 'b-', label='Net Change', marker='s')

        plt.title('Server Growth Over Time')
        plt.xlabel('Date')
        plt.ylabel('Number of Members')
        plt.legend()
        plt.grid(True)

        # Format dates on x-axis
        plt.gcf().autofmt_xdate()
        plt.gca().xaxis.set_major_formatter(DateFormatter('%Y-%m-%d'))

        # Save plot to bytes buffer
        buf = io.BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        plt.close()

        return buf

    def generate_command_usage_chart(self, command_data: Dict[str, int]) -> io.BytesIO:
        """Generate a bar chart of command usage"""
        if not MATPLOTLIB_AVAILABLE:
            raise RuntimeError("matplotlib is not available for graph generation")
        plt.figure(figsize=(10, 6))
        plt.clf()

        commands = list(command_data.keys())
        usage = list(command_data.values())

        plt.bar(commands, usage)
        plt.title('Command Usage Statistics')
        plt.xlabel('Commands')
        plt.ylabel('Times Used')

        # Rotate labels if there are many commands
        if len(commands) > 5:
            plt.xticks(rotation=45, ha='right')

        plt.tight_layout()

        # Save plot to bytes buffer
        buf = io.BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        plt.close()

        return buf

    @app_commands.command(name="activitygraph")
    @app_commands.default_permissions(manage_guild=True)
    async def activitygraph(self, interaction: discord.Interaction):
        """Display a graph of server activity patterns"""
        if not MATPLOTLIB_AVAILABLE:
            await interaction.response.send_message(
                "Sorry, the graph generation feature is currently unavailable. Please contact the bot administrator.",
                ephemeral=True
            )
            return

        guild_id = str(interaction.guild.id)
        if not await self.ensure_guild_initialized(interaction.guild):
            await interaction.response.send_message("Failed to initialize analytics data.", ephemeral=True)
            return

        data = self.analytics_data["guilds"].get(guild_id, {}).get("current_period", {})
        active_hours = data.get("active_hours", {})

        if not active_hours:
            await interaction.response.send_message("Not enough data to generate a graph yet.")
            return

        try:
            # Generate the activity chart
            chart_file = self.generate_activity_chart(
                active_hours,
                f"Message Activity Pattern - {interaction.guild.name}"
            )

            # Create embed
            embed = discord.Embed(
                title="📊 Server Activity Analysis",
                description="Message frequency by hour of day (UTC)",
                color=discord.Color.blue()
            )

            # Send the embed with the chart
            await interaction.response.send_message(
                embed=embed,
                file=discord.File(chart_file, filename="activity.png")
            )
            chart_file.close()
        except Exception as e:
            await interaction.response.send_message(
                f"An error occurred while generating the graph: {str(e)}",
                ephemeral=True
            )

    @app_commands.command(name="growthgraph")
    @app_commands.default_permissions(manage_guild=True)
    async def growthgraph(self, interaction: discord.Interaction):
        """Display a graph of server growth over time"""
        guild_id = str(interaction.guild.id)
        if not await self.ensure_guild_initialized(interaction.guild):
            await interaction.response.send_message("Failed to initialize analytics data.", ephemeral=True)
            return

        growth_data = self.growth_metrics.get(guild_id, {}).get("member_growth", [])
        if not growth_data:
            await interaction.response.send_message("Not enough data to generate a graph yet.")
            return

        # Generate the growth chart
        chart_file = self.generate_growth_chart(growth_data)

        # Create embed
        embed = discord.Embed(
            title="📈 Server Growth Analysis",
            description="Member joins, leaves, and net change over time",
            color=discord.Color.blue()
        )

        total_joins = sum(day['joins'] for day in growth_data)
        total_leaves = sum(day['leaves'] for day in growth_data)
        net_change = sum(day['net'] for day in growth_data)

        embed.add_field(name="Total Joins", value=str(total_joins), inline=True)
        embed.add_field(name="Total Leaves", value=str(total_leaves), inline=True)
        embed.add_field(name="Net Change", value=f"{net_change:+}", inline=True)

        # Send the embed with the chart
        await interaction.response.send_message(
            embed=embed,
            file=discord.File(chart_file, filename="growth.png")
        )
        chart_file.close()

    @app_commands.command(name="cmdstats")
    @app_commands.default_permissions(manage_guild=True)
    async def cmdstats(self, interaction: discord.Interaction):
        """Display a graph of command usage statistics"""
        guild_id = str(interaction.guild.id)
        if not await self.ensure_guild_initialized(interaction.guild):
            await interaction.response.send_message("Failed to initialize analytics data.", ephemeral=True)
            return

        # Generate the command usage chart
        chart_file = self.generate_command_usage_chart(self.command_usage.get(guild_id,{}))

        # Create embed
        embed = discord.Embed(
            title="🤖 Command Usage Statistics",
            description="Frequency of command usage in this server",
            color=discord.Color.blue()
        )

        total_commands = sum(self.command_usage.get(guild_id,{}).values())
        most_used = max(self.command_usage.get(guild_id,{}).items(), key=lambda x: x[1]) if self.command_usage.get(guild_id,{}) else ("No Data",0)

        embed.add_field(name="Total Commands Used", value=str(total_commands), inline=True)
        embed.add_field(name="Most Used Command", value=f"/{most_used[0]} ({most_used[1]} uses)", inline=True)

        # Send the embed with the chart
        await interaction.response.send_message(
            embed=embed,
            file=discord.File(chart_file, filename="commands.png")
        )
        chart_file.close()

    @tasks.loop(hours=24)
    async def cleanup_old_data(self):
        """Cleanup old analytics data to prevent excessive storage usage"""
        try:
            print("Starting analytics data cleanup...")
            current_time = datetime.utcnow()

            # Clean up growth metrics older than 30 days (changed from 90)
            for guild_id in self.growth_metrics:
                if "member_growth" in self.growth_metrics[guild_id]:
                    cutoff_date = (current_time - timedelta(days=30)).date().isoformat()
                    self.growth_metrics[guild_id]["member_growth"] = [
                        day for day in self.growth_metrics[guild_id]["member_growth"]
                        if day["date"] >= cutoff_date
                    ]

            # Clean up user activity data older than 30 days
            for guild_id in self.user_activity.copy():
                for user_id in self.user_activity[guild_id].copy():
                    # Remove users with no activity in 30 days
                    if "last_active" in self.user_activity[guild_id][user_id]:
                        last_active = datetime.fromisoformat(
                            self.user_activity[guild_id][user_id]["last_active"]
                        )
                        if (current_time - last_active).days > 30:
                            del self.user_activity[guild_id][user_id]

            # Clean up channel stats older than 30 days
            for channel_id in self.channel_stats["channels"].copy():
                if "last_activity" in self.channel_stats["channels"][channel_id]:
                    last_activity = datetime.fromisoformat(
                        self.channel_stats["channels"][channel_id]["last_activity"]
                    )
                    if (current_time - last_activity).days > 30:
                        del self.channel_stats["channels"][channel_id]

            # Remove empty guilds
            for guild_id in list(self.user_activity.keys()):
                if not self.user_activity[guild_id]:
                    del self.user_activity[guild_id]

            self.save_data()
            print("Analytics data cleanup completed successfully")
        except Exception as e:
            print(f"Error during analytics cleanup: {e}")

async def setup(bot):
    try:
        await bot.add_cog(AnalyticsCog(bot))
        print("Successfully loaded AnalyticsCog")
    except Exception as e:
        print(f"Error loading AnalyticsCog: {str(e)}")