# Telegram group anti-spam userbot

Scans every group you administer, bans the spammers, deletes their messages, then
locks the groups down on your command. Driven from your own **Saved Messages**.

## Files

| file | purpose |
|---|---|
| `setup.py` | interactive setup — asks api id / hash / phone / OTP, then launches |
| `antispam.py` | the userbot |
| `config.py` | all thresholds, whitelists, toggles |
| `test_rules.py` | offline check of the spam rules (no Telegram needed) |
| `requirements.txt` | `telethon` |

> `setup.py` here is a launcher, not a packaging script — don't run `pip install .`

## Setup

```bash
python setup.py
```

It asks for your **API id**, **API hash** (from
[my.telegram.org](https://my.telegram.org) → API development tools) and **phone
number**, sends the login code, asks for the **OTP** (plus your 2FA password if
you use one), saves it all to `.env`, and then launches the bot.

Run it again later and it reuses the saved session — no OTP the second time.

```bash
python setup.py --reset      # forget saved credentials + session, start over
python setup.py --no-launch  # log in only, don't start the bot
python antispam.py           # start the bot directly once set up
```

Leave it running. Then open **Saved Messages** and type the commands.

## Commands

| command | what it does |
|---|---|
| `.start dry` | scans everything and reports what *would* happen — changes nothing |
| `.start` | scans all groups, bans spammers, deletes their messages, prints a report |
| `.start @group` | same, one group only |
| `.close all` | restricts every group so **only admins can send messages**, and deletes photo-only messages |
| `.close @group` | locks one group |
| `.open all` | reopens the groups for members |
| `.report` | prints the last report again |
| `.undo` | unbans everyone banned by the last `.start` (deleted messages can't come back) |
| `.help` | command list + the active rules |

Intended flow: `.start` → read the report → `.close all`.

## What counts as spam

A message from a **non-admin** is spam when it has:

- a **video / GIF / round video shorter than 5 seconds** — a 4.9s clip is spam, a
  5s or 9s video is left alone (`MIN_VIDEO_DURATION`)
- a GIF with no duration metadata (these are almost always spam loops)
- a **link** — any link by default, or only the promo/shortener/invite patterns if
  you set `BAN_ANY_LINK = False` (`SPAM_LINK_PATTERNS`)
- **flooding** — 8 messages inside 10 seconds (`FLOOD_COUNT`, `FLOOD_WINDOW`)

Once a user trips any rule, **all** of their scanned messages in that group are
deleted, not only the offending ones, and they are banned.

## Safety

- Group admins, the group creator, your own account and anything in `WHITELIST`
  are never banned or deleted.
- Groups listed in `GROUP_BLACKLIST` are skipped entirely.
- Groups where you are not an admin are skipped and listed under "Skipped".
- `FloodWait` is handled by sleeping and retrying; `API_DELAY` paces the calls.
- Every ban / delete / lock is appended to `actions.jsonl`.
- Use `.start dry` first — it is read-only.

## Tuning

Edit `config.py` (every value also reads an env var of the same name):

```python
MIN_VIDEO_DURATION = 5     # seconds; videos shorter than this are spam
BAN_ALL_GIFS       = False # True = every GIF is spam regardless of length
BAN_ANY_LINK       = True  # False = only SPAM_LINK_PATTERNS count
SCAN_LIMIT         = 2000  # messages per group to scan
SCAN_DAYS          = 30    # ignore messages older than this (0 = no limit)
DELETE_PHOTO_ONLY  = True  # delete captionless photos during `.close all`
```

After changing the rules, verify them without touching Telegram:

```bash
python test_rules.py
```

## Notes

- This is a **userbot** (your own account), which is required because `.start`
  must be read from Saved Messages. Keep the thresholds sane and avoid mass bans
  in groups you don't own — aggressive automation can get an account limited.
- Telegram only lets you delete other people's messages in groups where you hold
  the *Delete messages* admin right, and ban with *Ban users*. Grant both.
- `.close all` sets the group-wide default permissions; existing individual
  restrictions are untouched.
