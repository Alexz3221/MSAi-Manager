from __future__ import annotations
import os
import logging
from functools import lru_cache

from google.cloud.sql.connector import Connector

LOGGER = logging.getLogger(__name__)

VALID_CHANNEL_TYPES = {"slack", "gchat"}

_IN_MEMORY_WEBHOOKS: dict[tuple[str, str], str] = {}    # (channel_type, company_id) -> url
_IN_MEMORY_NOTIFIED: set[tuple[str, str, str]] = set()  # (msa_id, company_id, channel_type)

CLOUD_SQL_CONNECTION_NAME = os.environ.get(
    "CLOUD_SQL_CONNECTION_NAME",
    "sprinternship-bld-2026:europe-west1:msai-company-channels",
)
CLOUD_SQL_DB = os.environ.get("CLOUD_SQL_DB", "msai_manager")
CLOUD_SQL_USER = os.environ.get("CLOUD_SQL_USER", "postgres")
CLOUD_SQL_PASSWORD = os.environ.get("CLOUD_SQL_PASSWORD")


@lru_cache(maxsize=1)
def _connector() -> Connector:
    return Connector()


def _get_connection():
    if not CLOUD_SQL_PASSWORD:
        raise RuntimeError("CLOUD_SQL_PASSWORD environment variable is not set.")
    return _connector().connect(
        CLOUD_SQL_CONNECTION_NAME,
        "pg8000",
        user=CLOUD_SQL_USER,
        password=CLOUD_SQL_PASSWORD,
        db=CLOUD_SQL_DB,
    )


def mask_webhook(url: str) -> str:
    return f"…{url[-8:]}" if len(url) > 8 else "…"


def check_table() -> None:
    if not CLOUD_SQL_PASSWORD:
        LOGGER.info("CLOUD_SQL_PASSWORD not set. Using in-memory store for channel webhooks.")
        return
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS company_channels (
                company_id TEXT NOT NULL,
                channel_type TEXT NOT NULL,
                webhook_url TEXT NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (company_id, channel_type)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS notification_log (
                msa_id TEXT NOT NULL,
                company_id TEXT NOT NULL,
                channel_type TEXT NOT NULL,
                sent_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (msa_id, company_id, channel_type)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def already_notified(msa_id: str, company_id: str, channel_type: str) -> bool:
    if not CLOUD_SQL_PASSWORD:
        return (msa_id, company_id, channel_type) in _IN_MEMORY_NOTIFIED
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM notification_log WHERE msa_id = %s AND company_id = %s AND channel_type = %s",
            (msa_id, company_id, channel_type),
        )
        return cursor.fetchone() is not None
    finally:
        conn.close()


def record_notified(msa_id: str, company_id: str, channel_type: str) -> None:
    if not CLOUD_SQL_PASSWORD:
        _IN_MEMORY_NOTIFIED.add((msa_id, company_id, channel_type))
        return
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO notification_log (msa_id, company_id, channel_type)
            VALUES (%s, %s, %s)
            ON CONFLICT (msa_id, company_id, channel_type) DO NOTHING
            """,
            (msa_id, company_id, channel_type),
        )
        conn.commit()
    finally:
        conn.close()


def get_webhook(company_id: str, channel_type: str) -> str | None:
    if not CLOUD_SQL_PASSWORD:
        return _IN_MEMORY_WEBHOOKS.get((channel_type, company_id))
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT webhook_url FROM company_channels WHERE company_id = %s AND channel_type = %s",
            (company_id, channel_type),
        )
        row = cursor.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def upsert_webhook(company_id: str, channel_type: str, webhook_url: str) -> None:
    if not CLOUD_SQL_PASSWORD:
        _IN_MEMORY_WEBHOOKS[(channel_type, company_id)] = webhook_url
        return
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO company_channels (company_id, channel_type, webhook_url, updated_at)
            VALUES (%s, %s, %s, now())
            ON CONFLICT (company_id, channel_type)
            DO UPDATE SET webhook_url = EXCLUDED.webhook_url, updated_at = now()
            """,
            (company_id, channel_type, webhook_url),
        )
        conn.commit()
    finally:
        conn.close()


def delete_webhook(company_id: str, channel_type: str) -> None:
    if not CLOUD_SQL_PASSWORD:
        _IN_MEMORY_WEBHOOKS.pop((channel_type, company_id), None)
        return
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM company_channels WHERE company_id = %s AND channel_type = %s",
            (company_id, channel_type),
        )
        conn.commit()
    finally:
        conn.close()


def get_all_webhooks_for_channel(channel_type: str) -> dict[str, str]:
    if not CLOUD_SQL_PASSWORD:
        return {
            company_id: url
            for (ctype, company_id), url in _IN_MEMORY_WEBHOOKS.items()
            if ctype == channel_type
        }
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT company_id, webhook_url FROM company_channels WHERE channel_type = %s",
            (channel_type,),
        )
        return dict(cursor.fetchall())
    finally:
        conn.close()