"""Observability handlers for Telegram bot.

Issues #81-#86: /status, /audit_log, Prometheus metrics, Sentry.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import AuditAction, AuditLog, Chat, ChatMember
from services import observability

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

router = Router(name="observability")


# --- Helper: check admin ---

async def _is_chat_admin(session: AsyncSession, chat_id: int, user_id: int) -> bool:
    q = select(ChatMember).where(
        ChatMember.chat_id == chat_id,
        ChatMember.user_id == user_id,
        ChatMember.role.in_(["admin", "root"]),
    )
    return (await session.execute(q)).scalar_one_or_none() is not None


# --- /status command ---

@router.message(Command("status"))
async def cmd_status(message: Message, session: AsyncSession):
    """Show system status (admin only)."""
    q = select(Chat).where(Chat.telegram_id == message.chat.id)
    chat = (await session.execute(q)).scalar_one_or_none()
    if not chat:
        await message.answer("Чат не найден в БД.")
        return

    is_admin = await _is_chat_admin(session, chat.id, message.from_user.id)
    if not is_admin:
        await message.answer("Только админ может смотреть статус.")
        return

    status = await observability.get_system_status(session)

    # Format metrics
    metrics_lines = []
    for line in status["metrics"].split("\n"):
        if line and not line.startswith("#"):
            metrics_lines.append(f"`{line}`")

    await message.answer(
        f"📊 **System Status**\n"
        f"Chats: {status['chats']}\n"
        f"Active bookings: {status['active_bookings']}\n"
        f"Active vote sessions: {status['active_vote_sessions']}\n"
        f"TV devices: {status['tv_devices']['online']}/{status['tv_devices']['total']} online\n"
        f"\n"
        f"📈 **Metrics**\n"
        f"{chr(10).join(metrics_lines[:20])}\n"
        f"... (truncated)\n"
        f"\n"
        f"🕒 **Recent Audit Log**\n"
        f"{_format_audit_log(status['recent_audit'][:5])}\n"
        f"\n"
        f"Time: {status['timestamp']}",
        parse_mode="Markdown",
    )


def _format_audit_log(entries: list[dict]) -> str:
    if not entries:
        return "Нет записей."
    lines = []
    for e in entries:
        target = e.get("target", "-")
        lines.append(f"• {e['action']} | {target} | user={e.get('user_id', '-')} | chat={e.get('chat_id', '-')} | {e['time']}")
    return "\n".join(lines)


# --- /audit_log command ---

@router.message(Command("audit_log"))
async def cmd_audit_log(message: Message, command: CommandObject, session: AsyncSession):
    """Show audit log entries (admin only).
    
    Usage: /audit_log [limit] [action]
    """
    args = (command.args or "").strip().split()
    limit = 20
    action_filter = None
    
    if args:
        if args[0].isdigit():
            limit = min(int(args[0]), 100)
        else:
            try:
                action_filter = AuditAction(args[0])
            except ValueError:
                await message.answer(f"Неизвестное действие: {args[0]}")
                return
    
    if len(args) > 1 and args[1].isdigit():
        limit = min(int(args[1]), 100)

    q = select(Chat).where(Chat.telegram_id == message.chat.id)
    chat = (await session.execute(q)).scalar_one_or_none()
    if not chat:
        await message.answer("Чат не найден в БД.")
        return

    is_admin = await _is_chat_admin(session, chat.id, message.from_user.id)
    if not is_admin:
        await message.answer("Только админ может смотреть audit log.")
        return

    entries = await observability.get_audit_log(
        session,
        chat_id=chat.id,
        action=action_filter,
        limit=limit,
    )

    if not entries:
        await message.answer("Записей не найдено.")
        return

    lines = ["📋 **Audit Log**"]
    for e in entries:
        target = f"{e.target_type}:{e.target_id}" if e.target_type else "-"
        details = f" | {e.details_json}" if e.details_json else ""
        lines.append(
            f"• {e.action.value} | {target} | user={e.user_id} | chat={e.chat_id} | {e.created_at.isoformat()}{details}"
        )

    await message.answer("\n".join(lines), parse_mode="Markdown")


# --- /metrics command ---

@router.message(Command("metrics"))
async def cmd_metrics(message: Message, session: AsyncSession):
    """Show Prometheus metrics (admin only)."""
    q = select(Chat).where(Chat.telegram_id == message.chat.id)
    chat = (await session.execute(q)).scalar_one_or_none()
    if not chat:
        await message.answer("Чат не найден в БД.")
        return

    is_admin = await _is_chat_admin(session, chat.id, message.from_user.id)
    if not is_admin:
        await message.answer("Только админ.")
        return

    status = await observability.get_system_status(session)
    await message.answer(f"```\n{status['metrics']}\n```", parse_mode="Markdown")


def register(dp):
    dp.include_router(router)