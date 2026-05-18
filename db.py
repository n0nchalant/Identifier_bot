"""
db.py — Central database connection and setup.
All tables are created here. Cogs import only the helpers they need.
"""
import os
import asyncpg


# ─────────────────────────────────────────────
#  Connection
# ─────────────────────────────────────────────
async def get_db() -> asyncpg.Connection:
    return await asyncpg.connect(os.environ["DATABASE_URL"])


# ─────────────────────────────────────────────
#  Schema setup  (called once on bot ready)
# ─────────────────────────────────────────────
async def setup_all_tables():
    conn = await get_db()
    try:
        # Triggers
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS triggers (
                channel_id     BIGINT PRIMARY KEY,
                message_count  INT    NOT NULL,
                custom_message TEXT   NOT NULL,
                next_count     INT    NOT NULL
            )
        """)

        # Reactions
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS reactions (
                id      SERIAL PRIMARY KEY,
                keyword TEXT   NOT NULL,
                emoji   TEXT   NOT NULL,
                UNIQUE(keyword)
            )
        """)

        # Custom commands  +  tracking columns
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS custom_commands (
                name        TEXT   PRIMARY KEY,
                text        TEXT,
                media_url   TEXT,
                added_by    BIGINT,
                added_by_name TEXT,
                use_count   INT    NOT NULL DEFAULT 0
            )
        """)

        # Migrate old rows that might be missing the new columns
        await conn.execute("""
            ALTER TABLE custom_commands
            ADD COLUMN IF NOT EXISTS added_by      BIGINT;
        """)
        await conn.execute("""
            ALTER TABLE custom_commands
            ADD COLUMN IF NOT EXISTS added_by_name TEXT;
        """)
        await conn.execute("""
            ALTER TABLE custom_commands
            ADD COLUMN IF NOT EXISTS use_count     INT NOT NULL DEFAULT 0;
        """)

        # Allowed roles
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS allowed_roles (
                role_id BIGINT PRIMARY KEY
            )
        """)
    finally:
        await conn.close()


# ─────────────────────────────────────────────
#  Triggers
# ─────────────────────────────────────────────
async def db_get_all_triggers():
    conn = await get_db()
    rows = await conn.fetch("SELECT * FROM triggers")
    await conn.close()
    return rows


async def db_get_trigger(channel_id: int):
    conn = await get_db()
    row = await conn.fetchrow("SELECT * FROM triggers WHERE channel_id = $1", channel_id)
    await conn.close()
    return row


async def db_add_trigger(channel_id: int, message_count: int, custom_message: str):
    conn = await get_db()
    await conn.execute("""
        INSERT INTO triggers (channel_id, message_count, custom_message, next_count)
        VALUES ($1, $2, $3, $2)
    """, channel_id, message_count, custom_message)
    await conn.close()


async def db_remove_trigger(channel_id: int):
    conn = await get_db()
    result = await conn.execute("DELETE FROM triggers WHERE channel_id = $1", channel_id)
    await conn.close()
    return result


async def db_update_trigger(channel_id: int, message_count: int, custom_message: str):
    conn = await get_db()
    await conn.execute("""
        UPDATE triggers
        SET message_count = $2, custom_message = $3, next_count = $2
        WHERE channel_id = $1
    """, channel_id, message_count, custom_message)
    await conn.close()


async def db_update_next_count(channel_id: int, next_count: int):
    conn = await get_db()
    await conn.execute(
        "UPDATE triggers SET next_count = $2 WHERE channel_id = $1",
        channel_id, next_count
    )
    await conn.close()


# ─────────────────────────────────────────────
#  Reactions
# ─────────────────────────────────────────────
async def db_get_all_reactions():
    conn = await get_db()
    rows = await conn.fetch("SELECT * FROM reactions ORDER BY id")
    await conn.close()
    return rows


async def db_add_reaction(keyword: str, emoji: str):
    conn = await get_db()
    await conn.execute(
        "INSERT INTO reactions (keyword, emoji) VALUES ($1, $2) "
        "ON CONFLICT (keyword) DO UPDATE SET emoji = $2",
        keyword.lower(), emoji
    )
    await conn.close()


async def db_remove_reaction(keyword: str):
    conn = await get_db()
    result = await conn.execute(
        "DELETE FROM reactions WHERE keyword = $1", keyword.lower()
    )
    await conn.close()
    return result


# ─────────────────────────────────────────────
#  Custom commands
# ─────────────────────────────────────────────
async def db_get_custom_command(name: str):
    conn = await get_db()
    row = await conn.fetchrow(
        "SELECT * FROM custom_commands WHERE name = $1", name.lower()
    )
    await conn.close()
    return row


async def db_get_all_custom_commands():
    conn = await get_db()
    rows = await conn.fetch("SELECT * FROM custom_commands ORDER BY name")
    await conn.close()
    return rows


async def db_save_custom_command(
    name: str,
    text: str | None,
    media_url: str | None,
    added_by: int | None = None,
    added_by_name: str | None = None,
):
    """Insert or update a custom command.
    added_by / added_by_name are only written on INSERT (not overwritten on edit).
    use_count is never touched here — incremented separately.
    """
    conn = await get_db()
    await conn.execute("""
        INSERT INTO custom_commands (name, text, media_url, added_by, added_by_name, use_count)
        VALUES ($1, $2, $3, $4, $5, 0)
        ON CONFLICT (name) DO UPDATE
            SET text       = $2,
                media_url  = $3
    """, name.lower(), text, media_url, added_by, added_by_name)
    await conn.close()


async def db_increment_use_count(name: str):
    conn = await get_db()
    await conn.execute(
        "UPDATE custom_commands SET use_count = use_count + 1 WHERE name = $1",
        name.lower()
    )
    await conn.close()


async def db_delete_custom_command(name: str):
    conn = await get_db()
    result = await conn.execute(
        "DELETE FROM custom_commands WHERE name = $1", name.lower()
    )
    await conn.close()
    return result


# ─────────────────────────────────────────────
#  Allowed roles
# ─────────────────────────────────────────────
async def db_get_allowed_roles():
    conn = await get_db()
    rows = await conn.fetch("SELECT role_id FROM allowed_roles")
    await conn.close()
    return [row["role_id"] for row in rows]


async def db_add_allowed_role(role_id: int):
    conn = await get_db()
    await conn.execute(
        "INSERT INTO allowed_roles (role_id) VALUES ($1) ON CONFLICT DO NOTHING",
        role_id
    )
    await conn.close()


async def db_remove_allowed_role(role_id: int):
    conn = await get_db()
    result = await conn.execute(
        "DELETE FROM allowed_roles WHERE role_id = $1", role_id
    )
    await conn.close()
    return result
