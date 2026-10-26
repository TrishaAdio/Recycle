#!/usr/bin/env python3
"""
Interactive setup + launcher for the anti-spam userbot.

    python setup.py

Asks for your API id, API hash and phone number, sends you the login code,
asks for the OTP (and your 2FA password if you have one), saves everything,
then starts the bot.

Run it again later and it reuses the saved login - no OTP needed.
Flags:
    --reset     forget the saved credentials and session, start over
    --no-launch log in only, don't start the bot
"""

import asyncio
import getpass
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(HERE, ".env")
BOT_FILE = os.path.join(HERE, "antispam.py")

C_OK, C_WARN, C_ERR, C_DIM, C_B, C_0 = (
    ("\033[92m", "\033[93m", "\033[91m", "\033[90m", "\033[1m", "\033[0m")
    if sys.stdout.isatty() else ("", "", "", "", "", "")
)


def say(msg=""):
    print(msg, flush=True)


def ok(msg):
    say(f"{C_OK}✓{C_0} {msg}")


def warn(msg):
    say(f"{C_WARN}!{C_0} {msg}")


def err(msg):
    say(f"{C_ERR}✗{C_0} {msg}")


def head(msg):
    say(f"\n{C_B}{msg}{C_0}\n{'─' * len(msg)}")


# ---------------------------------------------------------------------------
# dependency check
# ---------------------------------------------------------------------------

def ensure_telethon():
    try:
        import telethon  # noqa: F401
        return True
    except ImportError:
        pass

    warn("Telethon is not installed.")
    if input("  Install it now with pip? [Y/n] ").strip().lower() in ("n", "no"):
        err("Telethon is required. Run: pip install -r requirements.txt")
        return False

    say(f"{C_DIM}  installing telethon…{C_0}")
    res = subprocess.run(
        [sys.executable, "-m", "pip", "install", "telethon>=1.36,<2"],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        err("pip install failed:")
        say(res.stderr.strip()[-800:])
        say("\nInstall it yourself, then run this script again:")
        say(f"  {sys.executable} -m pip install --user telethon")
        return False
    ok("Telethon installed.")
    return True


# ---------------------------------------------------------------------------
# input helpers
# ---------------------------------------------------------------------------

def ask(prompt, validate=None, default=None, secret=False):
    """Keep asking until `validate` accepts the answer. Returns the value."""
    suffix = f" [{default}]" if default else ""
    while True:
        try:
            raw = (getpass.getpass(f"{prompt}{suffix}: ") if secret
                   else input(f"{prompt}{suffix}: ")).strip()
        except (EOFError, KeyboardInterrupt):
            say()
            err("Cancelled.")
            sys.exit(1)
        if not raw and default is not None:
            raw = str(default)
        if not raw:
            err("  This can't be empty.")
            continue
        if validate is None:
            return raw
        valid, cleaned_or_msg = validate(raw)
        if valid:
            return cleaned_or_msg
        err(f"  {cleaned_or_msg}")


def v_api_id(raw):
    digits = raw.strip()
    if not digits.isdigit():
        return False, "API id is only digits, e.g. 1234567"
    if not (4 <= len(digits) <= 12):
        return False, "That doesn't look like an API id (5-10 digits)."
    return True, digits


def v_api_hash(raw):
    h = raw.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{32}", h):
        return False, "API hash is 32 hex characters, e.g. abcdef0123456789abcdef0123456789"
    return True, h


def v_phone(raw):
    p = re.sub(r"[\s\-()]", "", raw.strip())
    if not p.startswith("+"):
        p = "+" + p
    if not re.fullmatch(r"\+\d{7,15}", p):
        return False, "Use international format with country code, e.g. +919812345678"
    return True, p


def v_otp(raw):
    code = re.sub(r"\D", "", raw)
    if not (4 <= len(code) <= 6):
        return False, "The login code is 5 digits (check your Telegram app)."
    return True, code


# ---------------------------------------------------------------------------
# .env handling
# ---------------------------------------------------------------------------

def read_env():
    data = {}
    try:
        with open(ENV_FILE, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    data[k.strip()] = v.strip().strip("'\"")
    except OSError:
        pass
    return data


def write_env(values):
    existing = read_env()
    existing.update(values)
    with open(ENV_FILE, "w", encoding="utf-8") as fh:
        fh.write("# Saved by setup.py - keep this file private\n")
        for k, v in existing.items():
            fh.write(f"{k}={v}\n")
    try:
        os.chmod(ENV_FILE, 0o600)
    except OSError:
        pass
    ok(f"Saved to {C_DIM}{ENV_FILE}{C_0} (chmod 600)")


def mask(value, keep=4):
    if not value:
        return ""
    return value[:keep] + "…" + value[-keep:] if len(value) > keep * 2 else "…"


def reset():
    removed = []
    for path in (ENV_FILE,):
        if os.path.exists(path):
            os.remove(path)
            removed.append(os.path.basename(path))
    for name in os.listdir(HERE):
        if name.endswith(".session") or name.endswith(".session-journal"):
            os.remove(os.path.join(HERE, name))
            removed.append(name)
    ok(f"Removed: {', '.join(removed)}" if removed else "Nothing to remove.")


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------

async def login(api_id, api_hash, phone, session_name):
    from telethon import TelegramClient
    from telethon.errors import (
        ApiIdInvalidError,
        FloodWaitError,
        PhoneCodeExpiredError,
        PhoneCodeInvalidError,
        PhoneNumberInvalidError,
        SessionPasswordNeededError,
    )

    client = TelegramClient(os.path.join(HERE, session_name), int(api_id), api_hash)
    await client.connect()

    if await client.is_user_authorized():
        me = await client.get_me()
        ok(f"Already logged in as {C_B}{me.first_name}{C_0} (@{me.username or me.id}) — no OTP needed.")
        await client.disconnect()
        return True

    head("Login")
    say(f"Sending a login code to {C_B}{phone}{C_0} …")
    try:
        sent = await client.send_code_request(phone)
    except PhoneNumberInvalidError:
        err("Telegram rejected that phone number. Re-run and check the country code.")
        await client.disconnect()
        return False
    except ApiIdInvalidError:
        err("API id / API hash pair is invalid. Re-check them at my.telegram.org.")
        say(f"  (run {C_B}python setup.py --reset{C_0} to re-enter them)")
        await client.disconnect()
        return False
    except FloodWaitError as e:
        err(f"Telegram asked us to wait {e.seconds}s before requesting another code.")
        await client.disconnect()
        return False

    ok("Code sent. Check your Telegram app (or SMS).")

    for attempt in range(1, 4):
        code = ask("  Enter the OTP", v_otp)
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=sent.phone_code_hash)
            break
        except PhoneCodeInvalidError:
            err(f"Wrong code ({attempt}/3).")
        except PhoneCodeExpiredError:
            err("That code expired. Re-run setup.py to get a new one.")
            await client.disconnect()
            return False
        except SessionPasswordNeededError:
            head("Two-step verification")
            for pw_try in range(1, 4):
                pw = ask("  Your 2FA password", secret=True)
                try:
                    await client.sign_in(password=pw)
                    break
                except Exception as e:  # noqa: BLE001 - wrong password
                    err(f"Password rejected ({pw_try}/3): {type(e).__name__}")
            else:
                await client.disconnect()
                return False
            break
    else:
        err("Too many wrong codes.")
        await client.disconnect()
        return False

    if not await client.is_user_authorized():
        err("Login did not complete.")
        await client.disconnect()
        return False

    me = await client.get_me()
    ok(f"Logged in as {C_B}{me.first_name}{C_0} (@{me.username or me.id})")
    say(f"{C_DIM}  Session saved to {session_name}.session — future runs skip the OTP.{C_0}")
    await client.disconnect()
    return True


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    args = sys.argv[1:]
    if "-h" in args or "--help" in args:
        say(__doc__)
        return 0

    say(f"{C_B}Telegram anti-spam userbot — setup{C_0}")

    if "--reset" in args:
        reset()

    if not ensure_telethon():
        return 1

    saved = read_env()
    api_id = saved.get("API_ID", "")
    api_hash = saved.get("API_HASH", "")
    phone = saved.get("PHONE", "")
    session_name = saved.get("SESSION_NAME", "antispam")

    if api_id and api_hash:
        head("Saved credentials")
        say(f"  API_ID    {api_id}")
        say(f"  API_HASH  {mask(api_hash)}")
        say(f"  PHONE     {phone or '(not saved)'}")
        if input("\n  Reuse these? [Y/n] ").strip().lower() in ("n", "no"):
            api_id = api_hash = phone = ""

    if not (api_id and api_hash):
        head("Telegram API credentials")
        say(f"{C_DIM}  Get them from https://my.telegram.org → API development tools{C_0}")
        api_id = ask("  API id", v_api_id)
        api_hash = ask("  API hash", v_api_hash)

    if not phone:
        head("Your phone number")
        say(f"{C_DIM}  The account that is admin in the groups.{C_0}")
        phone = ask("  Phone (with country code)", v_phone)

    write_env({
        "API_ID": api_id,
        "API_HASH": api_hash,
        "PHONE": phone,
        "SESSION_NAME": session_name,
    })

    try:
        logged_in = asyncio.run(login(api_id, api_hash, phone, session_name))
    except KeyboardInterrupt:
        say()
        err("Cancelled.")
        return 1
    if not logged_in:
        return 1

    if "--no-launch" in args:
        ok("Setup done. Start the bot with: python antispam.py")
        return 0

    head("Starting the bot")
    say("Now open  Saved Messages  in Telegram and send:")
    say(f"   {C_B}.start dry{C_0}   preview what would be banned (changes nothing)")
    say(f"   {C_B}.start{C_0}       ban spammers + delete their messages")
    say(f"   {C_B}.close all{C_0}   lock all groups to admins only")
    say(f"   {C_B}.help{C_0}        full command list")
    say(f"\n{C_DIM}Press Ctrl+C here to stop the bot.{C_0}\n")

    env = dict(os.environ)
    env.update({"API_ID": api_id, "API_HASH": api_hash, "SESSION_NAME": session_name})
    try:
        return subprocess.call([sys.executable, BOT_FILE], cwd=HERE, env=env)
    except KeyboardInterrupt:
        say()
        ok("Bot stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
