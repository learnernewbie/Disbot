import asyncpg
import aiosqlite
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Union, Any
import json
from asyncpg.pool import Pool as AsyncpgPool

class Database:
    def __init__(self, connection_string: Optional[str] = None):
        self.connection_string = connection_string or os.getenv('DATABASE_URL')
        self._pool: Optional[Union[AsyncpgPool, aiosqlite.Connection]] = None
        
    async def connect(self):
        """Connect to the database and create tables if they don't exist"""
        if self.connection_string and self.connection_string.startswith('postgresql://'):
            self._pool = await asyncpg.create_pool(self.connection_string)
            await self._create_tables_postgres()
        else:
            # Fallback to SQLite
            self._pool = await aiosqlite.connect('data/bot.db')
            await self._create_tables_sqlite()
            
    async def _create_tables_postgres(self):
        """Create PostgreSQL tables if they don't exist"""
        async with self._pool.acquire() as conn:
            # Add indexes for guild_id to improve multi-server performance
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS analytics (
                    guild_id BIGINT PRIMARY KEY,
                    data JSONB NOT NULL,
                    last_updated TIMESTAMP NOT NULL DEFAULT NOW()
                )
            ''')
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS logging (
                    id SERIAL PRIMARY KEY,
                    guild_id BIGINT NOT NULL,
                    event_type VARCHAR(50) NOT NULL,
                    event_data JSONB NOT NULL,
                    timestamp TIMESTAMP NOT NULL DEFAULT NOW(),
                    CONSTRAINT idx_logging_guild UNIQUE (guild_id, event_type, timestamp)
                )
            ''')
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS command_usage (
                    guild_id BIGINT NOT NULL,
                    command_name VARCHAR(100) NOT NULL,
                    uses INTEGER NOT NULL DEFAULT 0,
                    last_used TIMESTAMP NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (guild_id, command_name)
                )
            ''')
    async def _create_tables_sqlite(self):
        """Create SQLite tables if they don't exist"""
        async with self._pool:
            await self._pool.execute('''
                CREATE TABLE IF NOT EXISTS analytics (
                    guild_id INTEGER PRIMARY KEY,
                    data TEXT NOT NULL,
                    last_updated TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            await self._pool.execute('''
                CREATE TABLE IF NOT EXISTS logging (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    event_data TEXT NOT NULL,
                    timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            await self._pool.execute('''
                CREATE TABLE IF NOT EXISTS command_usage (
                    guild_id INTEGER NOT NULL,
                    command_name TEXT NOT NULL,
                    uses INTEGER NOT NULL DEFAULT 0,
                    last_used TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (guild_id, command_name)
                )
            ''')
    
    async def update_analytics(self, guild_id: int, data: Dict):
        """Update analytics data for a guild with improved error handling"""
        try:
            if isinstance(self._pool, asyncpg.Pool):
                async with self._pool.acquire() as conn:
                    await conn.execute('''
                        INSERT INTO analytics (guild_id, data)
                        VALUES ($1, $2)
                        ON CONFLICT (guild_id) 
                        DO UPDATE SET data = $2, last_updated = NOW()
                    ''', guild_id, json.dumps(data))
            else:
                async with self._pool:
                    await self._pool.execute('''
                        INSERT INTO analytics (guild_id, data)
                        VALUES (?, ?)
                        ON CONFLICT (guild_id) 
                        DO UPDATE SET data = ?, last_updated = CURRENT_TIMESTAMP
                    ''', (guild_id, json.dumps(data), json.dumps(data)))
        except Exception as e:
            print(f"Error updating analytics for guild {guild_id}: {e}")
            raise

    async def get_analytics(self, guild_id: int) -> Optional[Dict]:
        """Get analytics data for a guild with improved error handling"""
        try:
            if isinstance(self._pool, asyncpg.Pool):
                async with self._pool.acquire() as conn:
                    record = await conn.fetchrow(
                        'SELECT data FROM analytics WHERE guild_id = $1',
                        guild_id
                    )
                    return json.loads(record['data']) if record else None
            else:
                async with self._pool:
                    async with self._pool.execute(
                        'SELECT data FROM analytics WHERE guild_id = ?',
                        (guild_id,)
                    ) as cursor:
                        row = await cursor.fetchone()
                        return json.loads(row[0]) if row else None
        except Exception as e:
            print(f"Error getting analytics for guild {guild_id}: {e}")
            return None

    async def log_event(self, guild_id: int, event_type: str, event_data: Dict):
        """Log an event"""
        if isinstance(self._pool, asyncpg.Pool):
            async with self._pool.acquire() as conn:
                await conn.execute('''
                    INSERT INTO logging (guild_id, event_type, event_data)
                    VALUES ($1, $2, $3)
                ''', guild_id, event_type, json.dumps(event_data))
        else:
            async with self._pool:
                await self._pool.execute('''
                    INSERT INTO logging (guild_id, event_type, event_data)
                    VALUES (?, ?, ?)
                ''', (guild_id, event_type, json.dumps(event_data)))
    
    async def get_events(
        self,
        guild_id: int,
        event_type: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 100
    ) -> List[Dict]:
        """Get logged events for a guild"""
        query = ['SELECT * FROM logging WHERE guild_id = $1']
        params = [guild_id]
        
        if event_type:
            query.append('AND event_type = $2')
            params.append(event_type)
        
        if since:
            query.append('AND timestamp > $' + str(len(params) + 1))
            params.append(since)
            
        query.append('ORDER BY timestamp DESC LIMIT $' + str(len(params) + 1))
        params.append(limit)
    
        if isinstance(self._pool, asyncpg.Pool):
            async with self._pool.acquire() as conn:
                records = await conn.fetch(' '.join(query), *params)
                return [
                    {
                        'id': r['id'],
                        'event_type': r['event_type'],
                        'event_data': json.loads(r['event_data']),
                        'timestamp': r['timestamp'].isoformat()
                    }
                    for r in records
                ]
        else:
            async with self._pool:
                query[0] = query[0].replace('$1', '?')
                for i in range(2, len(params) + 1):
                    query = [q.replace(f'${i}', '?') for q in query]
                
                async with self._pool.execute(' '.join(query), params) as cursor:
                    rows = await cursor.fetchall()
                    return [
                        {
                            'id': row[0],
                            'event_type': row[2],
                            'event_data': json.loads(row[3]),
                            'timestamp': row[4]
                        }
                        for row in rows
                    ]
    
    async def update_command_usage(self, guild_id: int, command_name: str):
        """Update command usage statistics"""
        if isinstance(self._pool, asyncpg.Pool):
            async with self._pool.acquire() as conn:
                await conn.execute('''
                    INSERT INTO command_usage (guild_id, command_name, uses)
                    VALUES ($1, $2, 1)
                    ON CONFLICT (guild_id, command_name) 
                    DO UPDATE SET 
                        uses = command_usage.uses + 1,
                        last_used = NOW()
                ''', guild_id, command_name)
        else:
            async with self._pool:
                await self._pool.execute('''
                    INSERT INTO command_usage (guild_id, command_name, uses)
                    VALUES (?, ?, 1)
                    ON CONFLICT (guild_id, command_name) 
                    DO UPDATE SET 
                        uses = uses + 1,
                        last_used = CURRENT_TIMESTAMP
                ''', (guild_id, command_name))
    
    async def get_command_usage(self, guild_id: int) -> List[Dict]:
        """Get command usage statistics for a guild"""
        if isinstance(self._pool, asyncpg.Pool):
            async with self._pool.acquire() as conn:
                records = await conn.fetch(
                    'SELECT * FROM command_usage WHERE guild_id = $1 ORDER BY uses DESC',
                    guild_id
                )
                return [
                    {
                        'command': r['command_name'],
                        'uses': r['uses'],
                        'last_used': r['last_used'].isoformat()
                    }
                    for r in records
                ]
        else:
            async with self._pool:
                async with self._pool.execute(
                    'SELECT * FROM command_usage WHERE guild_id = ? ORDER BY uses DESC',
                    (guild_id,)
                ) as cursor:
                    rows = await cursor.fetchall()
                    return [
                        {
                            'command': row[1],
                            'uses': row[2],
                            'last_used': row[3]
                        }
                        for row in rows
                    ]
    
    async def cleanup_old_data(self, days: int = 30):
        """Clean up old logging data"""
        cutoff = datetime.utcnow() - timedelta(days=days)
        if isinstance(self._pool, asyncpg.Pool):
            async with self._pool.acquire() as conn:
                await conn.execute(
                    'DELETE FROM logging WHERE timestamp < $1',
                    cutoff
                )
        else:
            async with self._pool:
                await self._pool.execute(
                    'DELETE FROM logging WHERE timestamp < ?',
                    (cutoff,)
                )