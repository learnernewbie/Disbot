import discord
from discord.ext import commands
import asyncio
import os
from config import TOKEN, INITIAL_EXTENSIONS
import logging
import sys
from datetime import datetime
import signal
import traceback
import json

def initialize_directories():
    """Create necessary directories for bot operation"""
    directories = ['logs', 'data']
    for directory in directories:
        os.makedirs(directory, exist_ok=True)

# Set up logging with more detailed configuration
initialize_directories()  # Create directories before setting up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(f'logs/discord-{datetime.now().strftime("%Y%m%d")}.log')
    ]
)
logger = logging.getLogger('discord')

class DiscordBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(
            command_prefix='!',
            intents=intents,
            help_command=None  # Disable default help command
        )
        self.initial_extensions = INITIAL_EXTENSIONS
        self._setup_logging()
        # Track connected guilds for validation
        self.connected_guilds = set()
        self.synced = False

    def _setup_logging(self):
        """Setup additional logging handlers and filters"""
        discord_logger = logging.getLogger('discord')
        discord_logger.setLevel(logging.INFO)

    async def setup_hook(self):
        """Setup hook for loading extensions and other initialization"""
        try:
            # Load extensions first
            for extension in self.initial_extensions:
                try:
                    await self.load_extension(extension)
                    logger.info(f'Loaded extension {extension}')
                except Exception as e:
                    logger.error(f'Failed to load extension {extension}: {e}')
                    logger.error(traceback.format_exc())

            # After loading extensions, sync commands globally
            if not self.synced:
                try:
                    logger.info("Syncing commands globally...")
                    await self.tree.sync()
                    logger.info("Global command sync complete")
                except Exception as e:
                    logger.error(f"Error syncing commands globally: {e}")
                    logger.error(traceback.format_exc())

        except Exception as e:
            logger.error(f"Critical error in setup_hook: {e}")
            logger.error(traceback.format_exc())

    async def sync_commands_with_retry(self, max_retries=5, delay=2):
        """Sync commands with retry logic and better rate limit handling"""
        retry_count = 0
        while retry_count < max_retries:
            try:
                logger.info("Attempting to sync commands...")
                # First sync globally
                synced = await self.tree.sync()
                logger.info(f'Slash commands synced globally: {len(synced)} commands')

                # Then sync to each guild individually with delays
                for guild in self.guilds:
                    try:
                        guild_commands = await self.tree.sync(guild=guild)
                        logger.info(f'Synced {len(guild_commands)} commands to guild {guild.name} ({guild.id})')
                        await asyncio.sleep(delay)  # Add delay between guild syncs
                    except discord.HTTPException as e:
                        if "rate limited" in str(e).lower():
                            wait_time = e.retry_after if hasattr(e, 'retry_after') else delay
                            logger.warning(f'Rate limited for guild {guild.name}, waiting {wait_time}s')
                            await asyncio.sleep(wait_time)
                            # Retry this guild
                            guild_commands = await self.tree.sync(guild=guild)
                            logger.info(f'Synced {len(guild_commands)} commands to guild {guild.name} after rate limit')
                        else:
                            logger.error(f'Error syncing commands for guild {guild.name}: {e}')
                    except Exception as e:
                        logger.error(f'Error syncing commands for guild {guild.name}: {e}')
                        continue

                self.synced = True
                return True

            except discord.HTTPException as e:
                retry_count += 1
                if "rate limited" in str(e).lower():
                    wait_time = e.retry_after if hasattr(e, 'retry_after') else delay
                    logger.warning(f'Rate limited globally, waiting {wait_time}s before retry {retry_count}/{max_retries}')
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f'Failed to sync commands: {e}')
                    return False

            except Exception as e:
                logger.error(f'Unexpected error syncing commands: {e}')
                return False

            await asyncio.sleep(delay)  # Brief pause between retries

        return False

    def validate_guild_data(self, guild):
        """Validate guild-specific data structures"""
        guild_id = str(guild.id)
        validation_errors = []

        # Check data files
        data_files = [
            'data/reputation.json',
            'data/tickets.json',
            'data/warnings.json',
            'data/violations.json'
        ]

        for file_path in data_files:
            if not os.path.exists(file_path):
                validation_errors.append(f"Missing data file: {file_path}")
                continue

            try:
                with open(file_path, 'r') as f:
                    data = json.load(f)
                    if guild_id in data and not isinstance(data[guild_id], dict):
                        validation_errors.append(f"Invalid data structure in {file_path} for guild {guild_id}")
            except Exception as e:
                validation_errors.append(f"Error reading {file_path}: {str(e)}")

        return validation_errors

    async def on_ready(self):
        """Called when the bot is ready and connected to Discord"""
        if self.synced:
            return

        logger.info(f'Logged in as {self.user} (ID: {self.user.id})')

        # Validate all connected guilds
        for guild in self.guilds:
            self.connected_guilds.add(guild.id)
            errors = self.validate_guild_data(guild)  # Not awaited since it's not an async function
            if errors:
                logger.error(f"Validation errors for guild {guild.name} (ID: {guild.id}):")
                for error in errors:
                    logger.error(f"  - {error}")

        # Sync commands with retry logic
        sync_success = await self.sync_commands_with_retry(max_retries=5, delay=2)
        if sync_success:
            logger.info("Successfully synced all commands")
        else:
            logger.error("Failed to sync commands after maximum retries")

    async def on_guild_join(self, guild):
        """Handle bot joining a new server with improved error handling"""
        logger.info(f'Joined new guild: {guild.name} (ID: {guild.id})')
        self.connected_guilds.add(guild.id)

        # Initialize server-specific data structures
        for cog_name, cog in self.cogs.items():
            if hasattr(cog, 'initialize_guild'):
                try:
                    await cog.initialize_guild(guild)
                    logger.info(f'Initialized {cog_name} for guild {guild.name}')
                except Exception as e:
                    logger.error(f'Error initializing {cog_name} for guild {guild.id}: {e}')
                    logger.error(traceback.format_exc())

        # Sync commands for the new guild with retries
        max_retries = 3
        retry_count = 0
        while retry_count < max_retries:
            try:
                logger.info(f'Attempting to sync commands for guild {guild.name} (Attempt {retry_count + 1}/{max_retries})')
                guild_commands = await self.tree.sync(guild=guild)
                logger.info(f'Successfully synced {len(guild_commands)} commands to new guild {guild.name} ({guild.id})')
                break
            except discord.HTTPException as e:
                retry_count += 1
                if "rate limited" in str(e).lower() and retry_count < max_retries:
                    wait_time = e.retry_after if hasattr(e, 'retry_after') else 5
                    logger.warning(f'Rate limited while syncing commands for {guild.name}, waiting {wait_time}s')
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f'Failed to sync commands for new guild {guild.name}: {e}')
                    break
            except Exception as e:
                logger.error(f'Unexpected error syncing commands for guild {guild.name}: {e}')
                logger.error(traceback.format_exc())
                break

        # Validate guild data after initialization
        errors = self.validate_guild_data(guild)
        if errors:
            logger.error(f"Validation errors after joining guild {guild.name} (ID: {guild.id}):")
            for error in errors:
                logger.error(f"  - {error}")

        try:
            system_channel = guild.system_channel
            if system_channel and system_channel.permissions_for(guild.me).send_messages:
                await system_channel.send(
                    "👋 Thanks for adding me! I'm getting set up. "
                    "Use `/help` to see available commands once they're ready."
                )
        except Exception as e:
            logger.error(f"Could not send welcome message in guild {guild.name}: {e}")

    async def on_guild_remove(self, guild):
        """Handle bot leaving a server"""
        logger.info(f'Left guild: {guild.name} (ID: {guild.id})')
        self.connected_guilds.discard(guild.id)

    async def on_error(self, event_method: str, *args, **kwargs):
        """Global error handler for all events"""
        logger.error(f'Error in {event_method}:')
        logger.error(''.join(traceback.format_exc()))

    async def on_command_error(self, ctx, error):
        """Handle command errors"""
        if isinstance(error, commands.errors.MissingPermissions):
            await ctx.send("You don't have permission to use this command!")
        elif isinstance(error, commands.errors.BotMissingPermissions):
            await ctx.send("I don't have the required permissions to execute this command!")
        else:
            logger.error(f'Command error: {error}')
            await ctx.send(f"An error occurred while executing the command: {str(error)}")

async def main():
    """Main entry point for the bot"""
    try:
        bot = DiscordBot()

        # Setup signal handlers for graceful shutdown
        async def shutdown(signal_name):
            logger.info(f'Received {signal_name}, shutting down...')
            try:
                await bot.close()
            except Exception as e:
                logger.error(f'Error during shutdown: {e}')
            finally:
                for task in asyncio.all_tasks():
                    task.cancel()

        try:
            loop = asyncio.get_running_loop()
            for signal_name in ('SIGINT', 'SIGTERM'):
                if sys.platform != 'win32':  # Signals not supported on Windows
                    loop.add_signal_handler(
                        getattr(signal, signal_name),
                        lambda s=signal_name: asyncio.create_task(shutdown(s))
                    )
        except NotImplementedError:
            pass  # Signals not supported on this platform

        async with bot:
            await bot.start(TOKEN)
    except Exception as e:
        logger.critical(f'Failed to start bot: {e}')
        raise

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info('Bot stopped by user')
    except Exception as e:
        logger.critical(f'Fatal error: {e}')
        sys.exit(1)