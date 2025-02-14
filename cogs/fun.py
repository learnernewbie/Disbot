import discord
from discord import app_commands
from discord.ext import commands
import random
import json
from datetime import datetime, timedelta

class FunCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.reputation = {}
        self.rep_cooldowns = {}
        self.rep_history = {}
        self.polls = {}
        self.load_data()

    def load_data(self):
        try:
            with open('data/reputation.json', 'r') as f:
                data = json.load(f)
                self.reputation = data.get('points', {})
                self.rep_history = data.get('history', {})
            with open('data/polls.json', 'r') as f:
                self.polls = json.load(f)
        except FileNotFoundError:
            pass

    def save_data(self):
        with open('data/reputation.json', 'w') as f:
            json.dump({
                'points': self.reputation,
                'history': self.rep_history
            }, f, indent=4)
        with open('data/polls.json', 'w') as f:
            json.dump(self.polls, f, indent=4)

    def get_level(self, points):
        """Calculate level based on reputation points"""
        return max(1, int(points ** 0.5))

    def get_next_level_points(self, current_level):
        """Calculate points needed for next level"""
        return (current_level + 1) ** 2

    @app_commands.command(name="oldrep")
    async def oldrep(self, interaction: discord.Interaction, member: discord.Member):
        """Give reputation to a member (Legacy command)"""
        if member.id == interaction.user.id:
            await interaction.response.send_message("You cannot give reputation to yourself!")
            return

        # Check cooldown
        cooldown_key = str(interaction.user.id)
        if cooldown_key in self.rep_cooldowns:
            last_use = datetime.fromisoformat(self.rep_cooldowns[cooldown_key])
            if datetime.utcnow() - last_use < timedelta(hours=24):
                remaining = timedelta(hours=24) - (datetime.utcnow() - last_use)
                await interaction.response.send_message(
                    f"You can give reputation again in {remaining.seconds // 3600} hours"
                )
                return

        # Update reputation
        member_id = str(member.id)
        if member_id not in self.reputation:
            self.reputation[member_id] = 0

        old_level = self.get_level(self.reputation[member_id])
        self.reputation[member_id] += 1
        new_level = self.get_level(self.reputation[member_id])

        # Record reputation history
        if member_id not in self.rep_history:
            self.rep_history[member_id] = []

        self.rep_history[member_id].append({
            'from_user': interaction.user.id,
            'timestamp': datetime.utcnow().isoformat(),
            'points': 1
        })

        self.rep_cooldowns[cooldown_key] = datetime.utcnow().isoformat()
        self.save_data()

        # Create response embed
        embed = discord.Embed(
            title="🌟 Reputation Given!",
            color=discord.Color.gold()
        )
        embed.add_field(
            name="Recipient",
            value=member.mention,
            inline=True
        )
        embed.add_field(
            name="Total Rep",
            value=str(self.reputation[member_id]),
            inline=True
        )
        embed.add_field(
            name="Level",
            value=str(new_level),
            inline=True
        )

        if new_level > old_level:
            embed.add_field(
                name="🎉 Level Up!",
                value=f"Advanced to Level {new_level}!",
                inline=False
            )

        next_level_points = self.get_next_level_points(new_level)
        current_points = self.reputation[member_id]
        points_needed = next_level_points - current_points

        embed.add_field(
            name="Next Level",
            value=f"{points_needed} more points needed for Level {new_level + 1}",
            inline=False
        )

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="replb")
    async def replb(self, interaction: discord.Interaction):
        """Show the reputation leaderboard"""
        if not self.reputation:
            await interaction.response.send_message("No reputation data available!")
            return

        sorted_rep = sorted(
            self.reputation.items(),
            key=lambda x: x[1],
            reverse=True
        )[:10]  # Top 10

        embed = discord.Embed(
            title="🏆 Reputation Leaderboard",
            color=discord.Color.gold()
        )

        for i, (user_id, points) in enumerate(sorted_rep, 1):
            user = self.bot.get_user(int(user_id))
            if user:
                level = self.get_level(points)
                embed.add_field(
                    name=f"{i}. {user.name}",
                    value=f"Level {level} (Rep: {points})",
                    inline=False
                )

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="repinfo")
    async def repinfo(self, interaction: discord.Interaction, member: discord.Member = None):
        """Show reputation information for a user"""
        member = member or interaction.user
        member_id = str(member.id)

        if member_id not in self.reputation:
            await interaction.response.send_message(f"{member.mention} has no reputation yet!")
            return

        points = self.reputation[member_id]
        level = self.get_level(points)
        next_level = level + 1
        next_level_points = self.get_next_level_points(level)
        points_needed = next_level_points - points

        embed = discord.Embed(
            title=f"Reputation Info for {member.name}",
            color=discord.Color.blue()
        )
        embed.set_thumbnail(url=member.display_avatar.url)

        embed.add_field(name="Level", value=str(level), inline=True)
        embed.add_field(name="Total Rep", value=str(points), inline=True)
        embed.add_field(
            name="Next Level",
            value=f"{points_needed} more points needed for Level {next_level}",
            inline=False
        )

        if member_id in self.rep_history:
            recent_history = self.rep_history[member_id][-5:]  # Last 5 reputation gains
            history_text = ""
            for entry in recent_history:
                giver = self.bot.get_user(entry['from_user'])
                giver_name = giver.name if giver else "Unknown"
                timestamp = datetime.fromisoformat(entry['timestamp'])
                history_text += f"From {giver_name} at <t:{int(timestamp.timestamp())}:R>\n"

            embed.add_field(
                name="Recent Rep History",
                value=history_text or "No recent history",
                inline=False
            )

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="coinflip", description="Flip a coin and get heads or tails")
    async def coinflip(self, interaction: discord.Interaction):
        """Simple coin flip game"""
        result = random.choice(["Heads", "Tails"])
        embed = discord.Embed(
            title="Coinflip",
            description=f"The coin landed on: **{result}**!",
            color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="poll", description="Create an interactive poll with multiple options")
    @app_commands.describe(
        question="The main question for your poll (Example: 'What's your favorite color?')",
        options="List your options, separated by commas (Example: 'Red, Blue, Green')",
        duration="How long the poll should last (Example: 1h, 30m, 1d)",
        anonymous="Hide who voted for what options",
        multiple_choice="Allow people to vote for multiple options"
    )
    async def poll(
        self,
        interaction: discord.Interaction,
        question: str,
        options: str,
        duration: str = "1h",
        anonymous: bool = False,
        multiple_choice: bool = False
    ):
        """Create an advanced interactive poll with multiple options"""
        options_list = [opt.strip() for opt in options.split(",")]
        if len(options_list) > 9:
            await interaction.response.send_message("Maximum 9 options allowed!")
            return

        # Calculate end time
        try:
            duration_seconds = sum(
                int(t[:-1]) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[t[-1]]
                for t in duration.replace(" ", "").split(",")
                if t[-1] in "smhd" and t[:-1].isdigit()
            )
            end_time = datetime.utcnow() + timedelta(seconds=duration_seconds)
        except (ValueError, KeyError):
            await interaction.response.send_message("Invalid duration format! Use format like 1h, 30m, 1d")
            return

        # Create poll embed with enhanced formatting
        embed = discord.Embed(
            title=f"📊 {question}",
            description=f"**Type:** {'Anonymous' if anonymous else 'Public'} | " +
                       f"{'Multiple Choice' if multiple_choice else 'Single Choice'}\n\n" +
                       "React with the corresponding number to vote!",
            color=discord.Color.blue()
        )

        emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣"]
        for i, option in enumerate(options_list):
            embed.add_field(
                name=f"{emojis[i]} {option}",
                value="No votes yet",
                inline=False
            )

        # Add time information with Discord timestamp
        unix_time = int(end_time.timestamp())
        embed.add_field(
            name="⏰ Time Information",
            value=f"Ends: <t:{unix_time}:F>\nTime Remaining: <t:{unix_time}:R>",
            inline=False
        )

        # Send poll message
        poll_message = await interaction.channel.send(embed=embed)
        await interaction.response.send_message("Poll created successfully!", ephemeral=True)

        # Add reaction options
        for i in range(len(options_list)):
            await poll_message.add_reaction(emojis[i])

        # Store enhanced poll data
        self.polls[str(poll_message.id)] = {
            "question": question,
            "options": options_list,
            "votes": {},
            "anonymous": anonymous,
            "multiple_choice": multiple_choice,
            "end_time": end_time.isoformat(),
            "channel_id": poll_message.channel.id,
            "author_id": interaction.user.id
        }
        self.save_data()

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == self.bot.user.id:
            return

        poll_id = str(payload.message_id)
        if poll_id not in self.polls:
            return

        poll = self.polls[poll_id]
        if datetime.fromisoformat(poll["end_time"]) < datetime.utcnow():
            return

        emoji = str(payload.emoji)
        emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣"]

        if emoji not in emojis[:len(poll["options"])]:
            return

        # Handle voting
        user_id = str(payload.user_id)
        option_idx = emojis.index(emoji)

        # Remove previous vote if not multiple choice
        if not poll["multiple_choice"]:
            for votes in poll["votes"].values():
                if user_id in votes:
                    votes.remove(user_id)

        # Add new vote
        if str(option_idx) not in poll["votes"]:
            poll["votes"][str(option_idx)] = []
        if user_id not in poll["votes"][str(option_idx)]:
            poll["votes"][str(option_idx)].append(user_id)

        self.save_data()

        # Update poll message
        channel = self.bot.get_channel(payload.channel_id)
        if channel:
            try:
                message = await channel.fetch_message(payload.message_id)
                embed = message.embeds[0]

                # Update vote counts and participant list
                for i, option in enumerate(poll["options"]):
                    vote_count = len(poll["votes"].get(str(i), []))
                    total_votes = sum(len(votes) for votes in poll["votes"].values())
                    percentage = (vote_count / total_votes * 100) if total_votes > 0 else 0
                    bar_length = 20
                    filled_bars = int(percentage / 100 * bar_length)
                    progress_bar = "█" * filled_bars + "░" * (bar_length - filled_bars)

                    if poll["anonymous"]:
                        value = f"{progress_bar} ({vote_count} votes, {percentage:.1f}%)"
                    else:
                        voters = [f"<@{uid}>" for uid in poll["votes"].get(str(i), [])]
                        value = f"{progress_bar} ({vote_count} votes, {percentage:.1f}%)\n" + \
                                f"Voters: {', '.join(voters) if voters else 'None'}"

                    embed.set_field_at(
                        i,
                        name=f"{emojis[i]} {option}",
                        value=value,
                        inline=False
                    )

                await message.edit(embed=embed)
            except discord.HTTPException:
                pass

    @app_commands.command(name="endpoll")
    @app_commands.default_permissions(manage_messages=True)
    async def endpoll(self, interaction: discord.Interaction, message_id: str):
        """End a poll early and show final results"""
        if message_id not in self.polls:
            await interaction.response.send_message("Poll not found!")
            return

        poll = self.polls[message_id]

        # Create results embed
        embed = discord.Embed(
            title=f"📊 Poll Results: {poll['question']}",
            description="Final Results:",
            color=discord.Color.green()
        )

        total_votes = sum(len(votes) for votes in poll["votes"].values())

        for i, option in enumerate(poll["options"]):
            vote_count = len(poll["votes"].get(str(i), []))
            percentage = (vote_count / total_votes * 100) if total_votes > 0 else 0

            bar_length = 20
            filled_bars = int(percentage / 100 * bar_length)
            progress_bar = "█" * filled_bars + "░" * (bar_length - filled_bars)

            value = f"{progress_bar} ({vote_count} votes, {percentage:.1f}%)"
            if not poll["anonymous"]:
                voters = [f"<@{uid}>" for uid in poll["votes"].get(str(i), [])]
                value += f"\nVoters: {', '.join(voters) if voters else 'None'}"

            embed.add_field(
                name=f"{option}",
                value=value,
                inline=False
            )

        embed.set_footer(text=f"Total Votes: {total_votes}")

        # Clean up
        try:
            channel = self.bot.get_channel(poll["channel_id"])
            if channel:
                original_message = await channel.fetch_message(int(message_id))
                await original_message.delete()
        except:
            pass

        del self.polls[message_id]
        self.save_data()

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="pollstats")
    @app_commands.default_permissions(manage_messages=True)
    async def pollstats(self, interaction: discord.Interaction):
        """Show statistics about all polls in the server"""
        guild_polls = [
            (poll_id, poll_data) for poll_id, poll_data in self.polls.items()
            if self.bot.get_channel(poll_data["channel_id"]).guild.id == interaction.guild.id
        ]

        if not guild_polls:
            await interaction.response.send_message("No active polls in this server!")
            return

        embed = discord.Embed(
            title="📊 Active Polls Overview",
            color=discord.Color.blue()
        )

        for poll_id, poll in guild_polls:
            total_votes = sum(len(votes) for votes in poll["votes"].values())
            end_time = datetime.fromisoformat(poll["end_time"])
            time_left = end_time - datetime.utcnow()

            if time_left.total_seconds() > 0:
                status = "🟢 Active"
            else:
                status = "🔴 Ended"

            embed.add_field(
                name=f"{status}: {poll['question']}",
                value=f"ID: {poll_id}\n"
                      f"Total Votes: {total_votes}\n"
                      f"Options: {len(poll['options'])}\n"
                      f"Type: {'Anonymous' if poll['anonymous'] else 'Public'}\n"
                      f"Ends: <t:{int(end_time.timestamp())}:R>",
                inline=False
            )

        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(FunCog(bot))