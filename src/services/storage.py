from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite


MAX_DINOS_PER_USER = 2


@dataclass(slots=True)
class SaveResult:
    slot: int
    replaced: bool
    expires_at: str


class DinoStorage:
    def __init__(self, database_path: str) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)

    async def init(self) -> None:
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS dinos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    slot INTEGER NOT NULL,
                    steam_id TEXT,
                    name TEXT NOT NULL,
                    species TEXT NOT NULL,
                    age REAL NOT NULL DEFAULT 100,
                    health REAL NOT NULL DEFAULT 100,
                    growth REAL NOT NULL,
                    location TEXT NOT NULL,
                    server_id TEXT NOT NULL DEFAULT 'default',
                    cluster_id TEXT NOT NULL DEFAULT 'default',
                    fingerprint TEXT NOT NULL DEFAULT '',
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, slot)
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id TEXT PRIMARY KEY,
                    locale TEXT NOT NULL DEFAULT 'en'
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS steam_links (
                    user_id TEXT PRIMARY KEY,
                    steam_id TEXT NOT NULL UNIQUE,
                    verified_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await self._run_migrations(db)
            await db.commit()

    async def _run_migrations(self, db: aiosqlite.Connection) -> None:
        cursor = await db.execute("PRAGMA table_info(dinos)")
        columns = {row[1] for row in await cursor.fetchall()}

        statements: list[str] = []
        if "steam_id" not in columns:
            statements.append("ALTER TABLE dinos ADD COLUMN steam_id TEXT")
        if "growth" not in columns:
            statements.append("ALTER TABLE dinos ADD COLUMN growth REAL NOT NULL DEFAULT 100")
        if "server_id" not in columns:
            statements.append("ALTER TABLE dinos ADD COLUMN server_id TEXT NOT NULL DEFAULT 'default'")
        if "cluster_id" not in columns:
            statements.append("ALTER TABLE dinos ADD COLUMN cluster_id TEXT NOT NULL DEFAULT 'default'")
        if "fingerprint" not in columns:
            statements.append("ALTER TABLE dinos ADD COLUMN fingerprint TEXT NOT NULL DEFAULT ''")
        if "expires_at" not in columns:
            now_plus_week = (datetime.now(UTC) + timedelta(days=7)).isoformat()
            statements.append(f"ALTER TABLE dinos ADD COLUMN expires_at TEXT NOT NULL DEFAULT '{now_plus_week}'")

        for statement in statements:
            await db.execute(statement)

    async def get_user_dinos(self, user_id: int) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.database_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT slot, steam_id, name, species, growth, location, server_id, cluster_id,
                       expires_at, created_at, updated_at
                FROM dinos
                WHERE user_id = ?
                ORDER BY slot ASC
                """,
                (str(user_id),),
            )
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_dino_in_slot(self, user_id: int, slot: int) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.database_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT slot, steam_id, name, species, growth, location, server_id, cluster_id,
                       fingerprint, expires_at, created_at, updated_at
                FROM dinos
                WHERE user_id = ? AND slot = ?
                """,
                (str(user_id), slot),
            )
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def set_steam_link(self, user_id: int, steam_id: str) -> None:
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                INSERT INTO steam_links (user_id, steam_id)
                VALUES (?, ?)
                ON CONFLICT(user_id)
                DO UPDATE SET steam_id = excluded.steam_id, verified_at = CURRENT_TIMESTAMP
                """,
                (str(user_id), steam_id),
            )
            await db.commit()

    async def get_steam_link(self, user_id: int) -> str | None:
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                "SELECT steam_id FROM steam_links WHERE user_id = ?",
                (str(user_id),),
            )
            row = await cursor.fetchone()
            return row[0] if row else None

    async def get_user_id_by_steam(self, steam_id: str) -> str | None:
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                "SELECT user_id FROM steam_links WHERE steam_id = ?",
                (steam_id,),
            )
            row = await cursor.fetchone()
            return row[0] if row else None

    async def save_dino(
        self,
        user_id: int,
        *,
        steam_id: str,
        name: str,
        species: str,
        growth: float,
        location: str,
        server_id: str,
        cluster_id: str,
        expiration_hours: int,
        requested_slot: int | None = None,
    ) -> SaveResult:
        if requested_slot is not None and requested_slot not in (1, 2):
            raise ValueError("Slot must be 1 or 2")

        dinos = await self.get_user_dinos(user_id)
        used_slots = {int(d["slot"]) for d in dinos}

        if requested_slot is None:
            for candidate in (1, 2):
                if candidate not in used_slots:
                    requested_slot = candidate
                    break

        if requested_slot is None:
            raise ValueError("No free slot. Choose slot 1 or 2 to replace.")

        fingerprint = self._build_fingerprint(
            steam_id=steam_id,
            species=species,
            growth=growth,
            location=location,
            cluster_id=cluster_id,
        )
        duplicate_exists = await self._fingerprint_exists(
            user_id=user_id,
            fingerprint=fingerprint,
            ignore_slot=requested_slot,
        )
        if duplicate_exists:
            raise ValueError("Duplicate save blocked by anti-duplication protection.")

        expires_at = (datetime.now(UTC) + timedelta(hours=expiration_hours)).isoformat()
        replaced = requested_slot in used_slots

        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                INSERT INTO dinos (
                    user_id, slot, steam_id, name, species, age, health, growth, location,
                    server_id, cluster_id, fingerprint, expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, slot)
                DO UPDATE SET
                    steam_id = excluded.steam_id,
                    name = excluded.name,
                    species = excluded.species,
                    age = excluded.age,
                    health = excluded.health,
                    growth = excluded.growth,
                    location = excluded.location,
                    server_id = excluded.server_id,
                    cluster_id = excluded.cluster_id,
                    fingerprint = excluded.fingerprint,
                    expires_at = excluded.expires_at,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    str(user_id),
                    requested_slot,
                    steam_id,
                    name,
                    species,
                    growth,
                    100,
                    growth,
                    location,
                    server_id,
                    cluster_id,
                    fingerprint,
                    expires_at,
                ),
            )
            await db.commit()

        return SaveResult(slot=requested_slot, replaced=replaced, expires_at=expires_at)

    async def _fingerprint_exists(self, user_id: int, fingerprint: str, ignore_slot: int | None) -> bool:
        async with aiosqlite.connect(self.database_path) as db:
            query = "SELECT 1 FROM dinos WHERE user_id = ? AND fingerprint = ?"
            args: list[Any] = [str(user_id), fingerprint]
            if ignore_slot is not None:
                query += " AND slot != ?"
                args.append(ignore_slot)
            query += " LIMIT 1"
            cursor = await db.execute(query, tuple(args))
            row = await cursor.fetchone()
            return row is not None

    def _build_fingerprint(
        self,
        *,
        steam_id: str,
        species: str,
        growth: float,
        location: str,
        cluster_id: str,
    ) -> str:
        payload = "|".join(
            [
                steam_id,
                species.strip().lower(),
                f"{growth:.3f}",
                location.strip().lower(),
                cluster_id.strip().lower(),
            ]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def delete_slot(self, user_id: int, slot: int) -> bool:
        if slot not in (1, 2):
            raise ValueError("Slot must be 1 or 2")

        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                "DELETE FROM dinos WHERE user_id = ? AND slot = ?",
                (str(user_id), slot),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def cleanup_expired(self) -> int:
        now = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                "DELETE FROM dinos WHERE expires_at <= ?",
                (now,),
            )
            await db.commit()
            return cursor.rowcount

    async def get_locale(self, user_id: int) -> str:
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                "SELECT locale FROM user_settings WHERE user_id = ?",
                (str(user_id),),
            )
            row = await cursor.fetchone()
            if not row:
                return "en"
            return row[0] if row[0] in ("en", "de") else "en"

    async def set_locale(self, user_id: int, locale: str) -> None:
        if locale not in ("en", "de"):
            raise ValueError("Locale must be en or de")

        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                INSERT INTO user_settings (user_id, locale)
                VALUES (?, ?)
                ON CONFLICT(user_id)
                DO UPDATE SET locale = excluded.locale
                """,
                (str(user_id), locale),
            )
            await db.commit()
