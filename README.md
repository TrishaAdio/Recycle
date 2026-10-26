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

Everyone except the whitelist and the group admins gets banned for:

- **any link** (`BAN_ANY_LINK = True`) — plain urls, hidden `[text](url)` links,
  `t.me/+invite`, shorteners, `@channel` promos
- **any photo** (`BAN_PHOTOS = True`) — with or without a caption
- a **video / GIF / round video shorter than 5 seconds** — a 4.9s clip is spam, a
  5s or 9s video is left alone (`MIN_VIDEO_DURATION`)
- a GIF with no duration metadata (these are almost always spam loops)
- **flooding** — 8 messages inside 10 seconds (`FLOOD_COUNT`, `FLOOD_WINDOW`)

Once a user trips any rule, **all** of their scanned messages in that group are
deleted, not only the offending ones, and they are banned.

### Whitelist

```python
WHITELIST = [6522530722, 7450005478, 6687627877]
```

These three ids are never banned and never have messages deleted, no matter what
they post. Numeric ids are used **as-is and never sent to the API**, which is why
this still works for **deleted accounts** — a deleted account can't be resolved by
username or lookup any more, so an id is the only thing that identifies it. The
scan also labels them as `deleted account [id]` in the report instead of failing.

To add more, append the numeric id (not the @username) to the list.

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
WHITELIST          = [6522530722, 7450005478, 6687627877]  # never touched
BAN_ANY_LINK       = True  # any link = ban (False = only SPAM_LINK_PATTERNS)
BAN_PHOTOS         = True  # any photo = ban
MIN_VIDEO_DURATION = 5     # seconds; videos shorter than this are spam
BAN_ALL_GIFS       = False # True = every GIF is spam regardless of length
SCAN_LIMIT         = 2000  # messages per group to scan
SCAN_DAYS          = 30    # ignore messages older than this (0 = no limit)
DELETE_PHOTO_ONLY  = True  # delete captionless photos during `.close all`
```

## Speed

The scan runs groups **in parallel** and is tuned to be fast:

```python
CONCURRENCY       = 6   # groups scanned at the same time
BATCH_CONCURRENCY = 4   # delete/ban calls in flight per group
API_DELAY         = 0   # no artificial pause between calls
```

Other things that keep it quick: offender names come from the messages already
downloaded (zero extra lookups), deletions go out in parallel batches of 100,
`SCAN_DAYS` stops each group early instead of walking the whole history, and
whitelisted ids are filtered during the scan so they cost nothing.

The report ends with the wall-clock time, e.g.
`Done in 12.4s — banned 37, deleted 1904 message(s) across 23 group(s).`

If Telegram starts throttling you (you'll see `[floodwait] sleeping Ns` in the
console), lower `CONCURRENCY` to 2-3 or set `API_DELAY = 0.3`. FloodWait is
always caught and retried, so a scan won't die from it either way.

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
