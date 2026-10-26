#!/usr/bin/env python3
"""
Telegram group anti-spam userbot.

Commands (send them to your own Saved Messages):

    .start              scan every group, ban spammers + delete their messages
    .start dry          same scan, but only report - nothing is banned/deleted
    .start <group>      scan one group only (id or @username)
    .close all          lock every group (admins only) + delete photo-only msgs
    .close <group>      lock one group only
    .open all           undo the lock, members can talk again
    .report             re-print the last scan report
    .undo               unban everyone banned by the last `.start`
    .help               list the commands

Spam = a non-admin message that contains
    * a video / GIF / round video shorter than MIN_VIDEO_DURATION (default 5s)
    * a spam link (see config.py)
    * part of a message flood (FLOOD_COUNT msgs in FLOOD_WINDOW seconds)

Videos of 5s or longer (e.g. 9s) are never flagged and stay in the group.
"""

import asyncio
import datetime
import json
import os
import re
import sys
import time
from collections import defaultdict

try:
    from telethon import TelegramClient, events
    from telethon.errors import (
        ChatAdminRequiredError,
        FloodWaitError,
        MessageDeleteForbiddenError,
        UserAdminInvalidError,
        UserNotParticipantError,
    )
    from telethon.tl.functions.channels import GetParticipantRequest
    from telethon.tl.types import (
        ChannelParticipantsAdmins,
        DocumentAttributeAnimated,
        DocumentAttributeVideo,
        MessageEntityMention,
        MessageEntityTextUrl,
        MessageEntityUrl,
    )
except ImportError:  # pragma: no cover
    sys.exit("Telethon is missing.  Run:  pip install -r requirements.txt")

import config

# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------

LAST_REPORT = "No scan has been run yet.  Send `.start` first."
LAST_BANS = []          # [(chat_id, user_id), ...] - used by `.undo`
RUNNING = False         # guards against two scans at once

SPAM_LINK_RE = [re.compile(p, re.I) for p in config.SPAM_LINK_PATTERNS]
URL_RE = re.compile(
    r"""(?:
          (?:https?://|www\.)\S+                                  # explicit url
        | \b[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.[a-z]{2,10}/\S*  # domain.tld/path
        | \b[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.                 # bare domain,
          (?:com|net|org|ru|xyz|top|club|link|me|io|biz|info|site|online|shop|app|live|vip|win|bet)\b
    )""",
    re.I | re.X,
)



# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def log_action(kind, **data):
    """Append one action to the json-lines log so `.undo` / audits are possible."""
    try:
        with open(config.ACTION_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.time(), "kind": kind, **data}) + "\n")
    except OSError:
        pass


async def safe(coro_factory, retries=3):
    """Run an API call, surviving FloodWait and permission errors.

    Returns (ok, error_text).
    """
    for attempt in range(retries):
        try:
            await coro_factory()
            await asyncio.sleep(config.API_DELAY)
            return True, None
        except FloodWaitError as e:
            wait = min(e.seconds + 2, 300)
            print(f"[floodwait] sleeping {wait}s")
            await asyncio.sleep(wait)
        except (
            ChatAdminRequiredError,
            UserAdminInvalidError,
            MessageDeleteForbiddenError,
        ) as e:
            return False, type(e).__name__
        except Exception as e:  # noqa: BLE001 - keep the scan alive
            if attempt == retries - 1:
                return False, f"{type(e).__name__}: {e}"
            await asyncio.sleep(1 + attempt)
    return False, "retries exhausted"


def video_duration(msg):
    """Duration in seconds of a video / GIF / round video, else None."""
    doc = getattr(msg, "document", None)
    if not doc:
        return None
    for attr in doc.attributes:
        if isinstance(attr, DocumentAttributeVideo):
            return float(attr.duration or 0)
    return None


def is_animation(msg):
    doc = getattr(msg, "document", None)
    if not doc:
        return False
    return any(isinstance(a, DocumentAttributeAnimated) for a in doc.attributes)


def has_link(msg):
    """True if the message carries a URL / invite link (text, caption or button)."""
    text = msg.raw_text or ""
    if msg.entities:
        for ent in msg.entities:
            if isinstance(ent, (MessageEntityUrl, MessageEntityTextUrl)):
                return True
            if isinstance(ent, MessageEntityMention) and config.BAN_ANY_LINK:
                # @channel style promotion
                return True
    if getattr(msg, "web_preview", None) or getattr(msg, "webpage", None):
        return True
    return bool(URL_RE.search(text))


def link_text(msg):
    parts = [msg.raw_text or ""]
    for ent, txt in (msg.get_entities_text() or []) if msg.entities else []:
        if isinstance(ent, MessageEntityTextUrl):
            parts.append(ent.url or "")
        parts.append(txt or "")
    return " ".join(parts)


def classify(msg):
    """Return a reason string if the message is spam, else None."""
    # --- short video / GIF -------------------------------------------------
    dur = video_duration(msg)
    gif = is_animation(msg)

    if gif and config.BAN_ALL_GIFS:
        return "GIF (all GIFs blocked)"

    if dur is not None:
        if dur < config.MIN_VIDEO_DURATION:
            kind = "GIF" if gif else "video"
            return f"short {kind} ({dur:g}s < {config.MIN_VIDEO_DURATION:g}s)"
        # >= threshold (9s etc.) -> explicitly allowed, keep checking links
    elif gif:
        # animation with no duration metadata -> treat as a short GIF
        return "GIF (no duration metadata)"

    # --- links -------------------------------------------------------------
    # known spam patterns are always checked, even if nothing looks like a link
    blob = link_text(msg)
    for rx in SPAM_LINK_RE:
        if rx.search(blob):
            return f"spam link ({rx.pattern})"

    if config.BAN_ANY_LINK and has_link(msg):
        return "link posted by member"

    return None


def find_floods(messages_by_user):
    """Return {user_id: reason} for users who flooded."""
    out = {}
    if not config.FLOOD_COUNT:
        return out
    for uid, msgs in messages_by_user.items():
        stamps = sorted(m.date.timestamp() for m in msgs)
        n, win = config.FLOOD_COUNT, config.FLOOD_WINDOW
        for i in range(len(stamps) - n + 1):
            if stamps[i + n - 1] - stamps[i] <= win:
                out[uid] = f"flood ({n} msgs in {win}s)"
                break
    return out


async def resolve_ids(client, items):
    """Turn a mixed list of ids/@usernames into a set of ids."""
    ids = set()
    for item in items or []:
        if isinstance(item, int):
            ids.add(item)
            continue
        try:
            ent = await client.get_entity(item)
            ids.add(ent.id)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] cannot resolve {item!r}: {e}")
    return ids


async def my_rights(client, chat):
    """Return (can_ban, can_delete, is_creator) for ourselves in `chat`."""
    try:
        me = await client.get_me()
        res = await client(GetParticipantRequest(chat, me.id))
        p = res.participant
        if getattr(p, "admin_rights", None):
            r = p.admin_rights
            return bool(r.ban_users), bool(r.delete_messages), False
        if type(p).__name__.endswith("Creator"):
            return True, True, True
        return False, False, False
    except (UserNotParticipantError, Exception):  # noqa: B014
        return False, False, False


async def admin_ids(client, chat):
    ids = set()
    try:
        async for u in client.iter_participants(chat, filter=ChannelParticipantsAdmins):
            ids.add(u.id)
    except Exception:  # noqa: BLE001 - basic groups / no rights
        pass
    return ids


async def all_groups(client, blacklist):
    groups = []
    async for d in client.iter_dialogs():
        ent = d.entity
        if not d.is_group:
            continue
        if ent.id in blacklist:
            continue
        if getattr(ent, "left", False) or getattr(ent, "deactivated", False):
            continue
        groups.append(d)
    return groups


def human(user):
    if user is None:
        return "unknown"
    name = " ".join(filter(None, [getattr(user, "first_name", ""), getattr(user, "last_name", "")])).strip()
    uname = f"@{user.username}" if getattr(user, "username", None) else ""
    return f"{name or uname or user.id} [{user.id}]"


async def edit_status(msg, text):
    try:
        await msg.edit(text[:4000])
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# .start  -  scan + ban + delete
# ---------------------------------------------------------------------------

async def run_scan(client, status_msg, target=None, dry=False):
    global LAST_REPORT, LAST_BANS

    whitelist = await resolve_ids(client, config.WHITELIST)
    blacklist = await resolve_ids(client, config.GROUP_BLACKLIST)
    me = await client.get_me()
    whitelist.add(me.id)

    if target:
        try:
            ent = await client.get_entity(target)
            groups = [type("D", (), {"entity": ent, "name": getattr(ent, "title", str(target))})()]
        except Exception as e:  # noqa: BLE001
            await edit_status(status_msg, f"❌ Cannot find group `{target}`: {e}")
            return
    else:
        groups = await all_groups(client, blacklist)

    cutoff = None
    if config.SCAN_DAYS:
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=config.SCAN_DAYS)

    mode = "DRY RUN (nothing will be touched)" if dry else "LIVE"
    report = [f"**Anti-spam scan — {mode}**", f"groups: {len(groups)}", ""]
    bans_done = []
    total_banned = total_deleted = 0
    skipped = []

    for gi, d in enumerate(groups, 1):
        chat = d.entity
        title = getattr(chat, "title", str(chat.id))
        await edit_status(status_msg, f"🔍 Scanning {gi}/{len(groups)}\n`{title}`\n\nbanned: {total_banned} | deleted: {total_deleted}")

        can_ban, can_del, creator = await my_rights(client, chat)
        if not (can_ban or can_del):
            skipped.append(f"• `{title}` — I'm not an admin there")
            continue

        protected = await admin_ids(client, chat) | whitelist
        offences = defaultdict(list)       # uid -> [reason, ...]
        msg_ids = defaultdict(list)        # uid -> [message id, ...]
        by_user = defaultdict(list)        # uid -> [message, ...] (flood check)

        try:
            async for msg in client.iter_messages(chat, limit=config.SCAN_LIMIT):
                if cutoff and msg.date < cutoff:
                    break
                uid = msg.sender_id
                if not uid or uid in protected or uid < 0:
                    continue
                by_user[uid].append(msg)
                msg_ids[uid].append(msg.id)
                reason = classify(msg)
                if reason:
                    offences[uid].append(reason)
        except Exception as e:  # noqa: BLE001
            skipped.append(f"• `{title}` — read failed: {type(e).__name__}")
            continue

        for uid, reason in find_floods(by_user).items():
            if uid not in protected:
                offences[uid].append(reason)

        if not offences:
            continue

        report.append(f"**{title}**")
        for uid, reasons in offences.items():
            try:
                user = await client.get_entity(uid)
            except Exception:  # noqa: BLE001
                user = None
            uniq = sorted(set(reasons))
            ids = msg_ids[uid]
            report.append(f"  · {human(user)} — {len(reasons)} spam msg(s): {', '.join(uniq[:3])}")

            if dry:
                report[-1] += f" → would delete {len(ids)} msg(s) + ban"
                continue

            # 1. delete every message of that user we collected
            deleted = 0
            if can_del:
                for i in range(0, len(ids), 100):
                    chunk = ids[i:i + 100]
                    ok, err = await safe(lambda c=chunk: client.delete_messages(chat, c))
                    if ok:
                        deleted += len(chunk)
                total_deleted += deleted
                log_action("delete", chat=chat.id, user=uid, count=deleted)

            # 2. ban
            banned_ok = False
            if can_ban:
                ok, err = await safe(lambda u=uid: client.edit_permissions(chat, u, until_date=None, view_messages=False))
                banned_ok = ok
                if ok:
                    total_banned += 1
                    bans_done.append((chat.id, uid))
                    log_action("ban", chat=chat.id, user=uid, reasons=uniq)
                else:
                    report[-1] += f" → ban failed ({err})"

            report[-1] += f" → deleted {deleted}" + (" + banned ✅" if banned_ok else "")
        report.append("")

    if skipped:
        report.append("**Skipped**")
        report.extend(skipped)
        report.append("")

    report.append(f"**Done — banned {total_banned}, deleted {total_deleted} message(s).**")
    if dry:
        report.append("_Dry run: nothing was changed. Run `.start` to apply._")
    else:
        report.append("Review it, then send `.close all` to lock the groups (or `.undo` to revert bans).")
        LAST_BANS = bans_done

    LAST_REPORT = "\n".join(report)
    await edit_status(status_msg, LAST_REPORT)
    if len(LAST_REPORT) > 4000:
        with open("last_report.txt", "w", encoding="utf-8") as fh:
            fh.write(LAST_REPORT)
        await client.send_file("me", "last_report.txt", caption="Full scan report")


# ---------------------------------------------------------------------------
# .close  -  lock groups + delete photo-only messages
# ---------------------------------------------------------------------------

async def run_close(client, status_msg, target=None, lock=True):
    blacklist = await resolve_ids(client, config.GROUP_BLACKLIST)

    if target and target != "all":
        try:
            ent = await client.get_entity(target)
            groups = [type("D", (), {"entity": ent})()]
        except Exception as e:  # noqa: BLE001
            await edit_status(status_msg, f"❌ Cannot find group `{target}`: {e}")
            return
    else:
        groups = await all_groups(client, blacklist)

    word = "Locking" if lock else "Unlocking"
    done, failed, photos_removed = [], [], 0

    for gi, d in enumerate(groups, 1):
        chat = d.entity
        title = getattr(chat, "title", str(chat.id))
        await edit_status(status_msg, f"🔒 {word} {gi}/{len(groups)}\n`{title}`")

        # In Telethon these flags mean "allowed".  allow = False while locking.
        allow = not lock
        ok, err = await safe(lambda c=chat, a=allow: client.edit_permissions(
            c,
            until_date=None,
            view_messages=True,        # members can still read the history
            send_messages=a,
            send_media=a,
            send_stickers=a,
            send_gifs=a,
            send_games=a,
            send_inline=a,
            embed_link_previews=a,
            send_polls=a,
            change_info=False,
            invite_users=True,
            pin_messages=False,
        ))
        if ok:
            done.append(title)
            log_action("lock" if lock else "unlock", chat=chat.id)
        else:
            failed.append(f"• `{title}` — {err}")
            continue

        # photo-only cleanup
        if lock and config.DELETE_PHOTO_ONLY:
            _, can_del, _ = await my_rights(client, chat)
            if can_del:
                ids = []
                try:
                    async for msg in client.iter_messages(chat, limit=config.PHOTO_CLEAN_LIMIT):
                        if msg.photo and not (msg.raw_text or "").strip():
                            ids.append(msg.id)
                except Exception:  # noqa: BLE001
                    ids = []
                for i in range(0, len(ids), 100):
                    chunk = ids[i:i + 100]
                    ok, _ = await safe(lambda c=chunk: client.delete_messages(chat, c))
                    if ok:
                        photos_removed += len(chunk)
                if ids:
                    log_action("photo_cleanup", chat=chat.id, count=len(ids))

    lines = [f"**{'Locked' if lock else 'Unlocked'} {len(done)} group(s)**"]
    lines += [f"• {t}" for t in done[:40]]
    if len(done) > 40:
        lines.append(f"…and {len(done) - 40} more")
    if lock and config.DELETE_PHOTO_ONLY:
        lines.append(f"\nPhoto-only messages deleted: **{photos_removed}**")
    if failed:
        lines.append("\n**Failed**")
        lines += failed
    if lock:
        lines.append("\nOnly admins can post now. Send `.open all` to reopen.")

    await edit_status(status_msg, "\n".join(lines)[:4000])


# ---------------------------------------------------------------------------
# .undo
# ---------------------------------------------------------------------------

async def run_undo(client, status_msg):
    if not LAST_BANS:
        await edit_status(status_msg, "Nothing to undo — no bans recorded in this session.")
        return
    ok_n = 0
    for chat_id, uid in LAST_BANS:
        ok, _ = await safe(lambda c=chat_id, u=uid: client.edit_permissions(
            c, u, until_date=None, view_messages=True, send_messages=True))
        if ok:
            ok_n += 1
    await edit_status(status_msg, f"Unbanned {ok_n}/{len(LAST_BANS)} user(s). Deleted messages cannot be restored.")


HELP = """**Anti-spam userbot**

`.start`          scan all groups → ban spammers + delete their messages
`.start dry`      preview only, changes nothing
`.start @group`   scan a single group
`.close all`      lock every group (admins only) + delete photo-only messages
`.close @group`   lock one group
`.open all`       reopen the groups for members
`.report`         show the last scan report again
`.undo`           unban everyone banned by the last scan
`.help`           this message

**Spam rules**
• video / GIF / round video shorter than {dur:g}s → spam (a 9s video stays)
• spam / promo links from non-admins → spam
• {flood} messages in {win}s from one user → spam
Admins, the whitelist and you are never touched."""


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def main():
    if not config.API_ID or not config.API_HASH:
        sys.exit("Set API_ID and API_HASH in config.py (or as environment variables) first.")

    client = TelegramClient(config.SESSION_NAME, config.API_ID, config.API_HASH)

    @client.on(events.NewMessage(outgoing=True, pattern=r"^\.(start|close|open|report|undo|help)\b(.*)"))
    async def handler(event):
        global RUNNING
        me = await client.get_me()
        # only react inside Saved Messages
        if event.chat_id != me.id:
            return

        cmd = event.pattern_match.group(1).lower()
        arg = (event.pattern_match.group(2) or "").strip()
        status = await event.reply("⏳ working…")

        if cmd == "help":
            await edit_status(status, HELP.format(
                dur=config.MIN_VIDEO_DURATION, flood=config.FLOOD_COUNT, win=config.FLOOD_WINDOW))
            return

        if cmd == "report":
            await edit_status(status, LAST_REPORT[:4000])
            return

        if RUNNING:
            await edit_status(status, "⚠️ A job is already running, wait for it to finish.")
            return

        RUNNING = True
        try:
            if cmd == "start":
                dry = arg.lower() in ("dry", "test", "preview")
                await run_scan(client, status, target=None if (dry or not arg) else arg, dry=dry)
            elif cmd == "close":
                await run_close(client, status, target=arg or "all", lock=True)
            elif cmd == "open":
                await run_close(client, status, target=arg or "all", lock=False)
            elif cmd == "undo":
                await run_undo(client, status)
        except Exception as e:  # noqa: BLE001
            await edit_status(status, f"❌ Crashed: `{type(e).__name__}: {e}`")
            raise
        finally:
            RUNNING = False

    with client:
        me = client.loop.run_until_complete(client.get_me())
        print(f"Logged in as {me.first_name} ({me.id}).")
        print("Open Saved Messages and send  .start  (or  .start dry  first).")
        client.run_until_disconnected()


if __name__ == "__main__":
    main()
