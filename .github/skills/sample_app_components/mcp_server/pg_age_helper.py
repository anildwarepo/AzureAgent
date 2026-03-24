# pg_age_helper.py

import os, asyncio, json, re
import psycopg
from psycopg import sql
from psycopg.rows import dict_row   # 👈 NEW
from dotenv import load_dotenv
from typing import Any, List

load_dotenv()

DSN = dict(
    host=os.getenv("PGHOST", "localhost"),
    port=int(os.getenv("PGPORT", "5432")),
    dbname=os.getenv("PGDATABASE", "postgres"),
    user=os.getenv("PGUSER", "postgres"),
    password=os.getenv("PGPASSWORD", "postgres"),
    sslmode=os.getenv("PGSSLMODE", "require"),
)

GRAPH = os.getenv("GRAPH_NAME")

if not GRAPH:
    raise ValueError("GRAPH environment variable must be set")

print("Using graph:", GRAPH)
print("Using DSN:", {k: (v if k != "password" else "****") for k, v in DSN.items()})

class PGAgeHelper:
    def __init__(self, conn: psycopg.AsyncConnection):
        self._conn = conn
        self.graph = GRAPH

    @classmethod
    async def create(cls) -> "PGAgeHelper":
        conn = await cls._create_connection()
        return cls(conn)

    @classmethod
    async def _create_connection(cls) -> psycopg.AsyncConnection:
        """Create and initialize a new database connection."""
        # row_factory=dict_row -> rows become dicts instead of tuples
        conn = await psycopg.AsyncConnection.connect(**DSN, row_factory=dict_row)
        
        async with conn.cursor() as cur:
            # Check if AGE extension exists, create it if not
            await cur.execute("SELECT 1 FROM pg_extension WHERE extname='age';")
            if not await cur.fetchone():
                print("AGE extension not found, creating it...")
                try:
                    await cur.execute("CREATE EXTENSION IF NOT EXISTS age CASCADE;")
                    await conn.commit()
                    print("AGE extension created successfully")
                except Exception as e:
                    print(f"Warning: Could not create AGE extension: {e}")
                    # Check again after attempting creation
                    await cur.execute("SELECT 1 FROM pg_extension WHERE extname='age';")
                    if not await cur.fetchone():
                        await conn.close()
                        raise RuntimeError(
                            "AGE extension is not installed in this database and could not be created. "
                            "Run as superuser: CREATE EXTENSION age;"
                        )
            
            # Set search path for this session
            await cur.execute("LOAD 'age';")
            await cur.execute('SET search_path = ag_catalog, "$user", public;')
            
            # Try to set search path for the database (requires permissions)
            try:
                await cur.execute('ALTER DATABASE postgres SET search_path = ag_catalog, "$user", public;')
                await conn.commit()
                print("Database search path configured")
            except Exception as e:
                print(f"Note: Could not set database-level search path (may require higher privileges): {e}")
            
            # Create graph if it doesn't exist
            try:
                await cur.execute(f"SELECT 1 FROM ag_catalog.ag_graph WHERE name = '{GRAPH}';")
                if not await cur.fetchone():
                    print(f"Graph '{GRAPH}' not found, creating it...")
                    await cur.execute(f"SELECT ag_catalog.create_graph('{GRAPH}');")
                    await conn.commit()
                    print(f"Graph '{GRAPH}' created successfully")
                else:
                    print(f"Graph '{GRAPH}' already exists")
            except Exception as e:
                print(f"Note: Could not check/create graph: {e}")

        return conn

    async def _ensure_connected(self) -> None:
        """Check if connection is alive and reconnect if needed."""
        try:
            # Check if connection is in a bad state
            if self._conn.closed or self._conn.broken:
                print("Connection is closed or broken, reconnecting...")
                await self._reconnect()
        except Exception as e:
            print(f"Connection check failed: {e}, attempting reconnect...")
            await self._reconnect()

    async def _reconnect(self) -> None:
        """Close existing connection (if any) and create a new one."""
        try:
            if self._conn and not self._conn.closed:
                await self._conn.close()
        except Exception as e:
            print(f"Error closing old connection: {e}")
        
        print("Creating new database connection...")
        self._conn = await self._create_connection()
        print("Reconnection successful")

    @staticmethod
    def _normalize_cypher_query(query: str) -> str:
        """
        Ensure we always call ag_catalog.cypher(...) instead of bare cypher(...).
        """
        # Matches FROM cypher( or JOIN cypher( with optional whitespace
        pattern = r'\b(FROM|JOIN)\s+cypher\s*\('
        replacement = r'\1 ag_catalog.cypher('
        return re.sub(pattern, replacement, query, flags=re.IGNORECASE)

    @staticmethod
    def _apply_graph_name(query: str, graph_name: str | None) -> str:
        if not graph_name:
            return query
        pattern = r"ag_catalog\.cypher\s*\(\s*'[^']*'\s*,"
        replacement = f"ag_catalog.cypher('{graph_name}',"
        return re.sub(pattern, replacement, query, flags=re.IGNORECASE)

    async def query_using_sql_cypher(self, query: str, graph_name: str | None = None) -> list[dict]:
        query = self._normalize_cypher_query(query)
        query = self._apply_graph_name(query, graph_name)
        print("Executing query:\n", query)

        max_retries = 2
        for attempt in range(max_retries):
            try:
                # Ensure connection is alive before executing
                await self._ensure_connected()
                
                async with self._conn.cursor() as cur:
                    # Ensure AGE library is loaded and search path includes ag_catalog
                    await cur.execute("LOAD 'age';")
                    await cur.execute('SET search_path = ag_catalog, "$user", public;')
                    await cur.execute(query)
                    rows = await cur.fetchall()
                    print("Raw rows:", rows)
                    return rows
            except psycopg.OperationalError as e:
                print(f"Connection error on attempt {attempt + 1}/{max_retries}: {e}")
                if attempt < max_retries - 1:
                    print("Attempting to reconnect and retry...")
                    try:
                        await self._reconnect()
                    except Exception as reconnect_error:
                        print(f"Reconnection failed: {reconnect_error}")
                        raise
                else:
                    print("Max retries reached, giving up.")
                    raise
            except Exception as e:
                print("Error executing query:", e)
                try:
                    print("Attempting to rollback transaction...")
                    await self._conn.rollback()
                    print("Rollback successful.")
                except Exception:
                    # If rollback itself failed, you may need to reconnect upstream
                    pass
                raise

   

#if __name__ == "__main__":
#    asyncio.run(main())
