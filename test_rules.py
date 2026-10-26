"""Offline check of the spam rules - no Telegram connection needed.

    python test_rules.py
"""

import datetime

from telethon.tl.types import (
    DocumentAttributeAnimated,
    DocumentAttributeFilename,
    DocumentAttributeVideo,
    MessageEntityTextUrl,
    MessageEntityUrl,
)

import config
from antispam import classify, find_floods


class FakeDoc:
    def __init__(self, attributes):
        self.attributes = attributes


class FakeMsg:
    """Minimal stand-in for telethon's Message."""

    def __init__(self, text="", duration=None, gif=False, entities=None,
                 photo=None, date=None):
        self.raw_text = text
        self.message = text
        self.entities = entities
        self.photo = photo
        self.web_preview = None
        self.date = date or datetime.datetime.now(datetime.timezone.utc)
        attrs = []
        if duration is not None:
            attrs.append(DocumentAttributeVideo(duration=duration, w=640, h=480))
        if gif:
            attrs.append(DocumentAttributeAnimated())
        if attrs:
            attrs.append(DocumentAttributeFilename("clip.mp4"))
            self.document = FakeDoc(attrs)
        else:
            self.document = None

    def get_entities_text(self):
        if not self.entities:
            return []
        return [(e, self.raw_text) for e in self.entities]


def url_entities(text):
    return [MessageEntityUrl(offset=0, length=len(text))]


CASES = [
    # (description, message, expected_spam?)
    ("plain hello",                      FakeMsg("hello guys"),                                False),
    ("photo, no caption",                FakeMsg(photo=object()),                              True),
    ("photo with caption",               FakeMsg("my dog", photo=object()),                    True),
    ("2s video",                         FakeMsg(duration=2),                                  True),
    ("4.9s video",                       FakeMsg(duration=4.9),                                True),
    ("exactly 5s video",                 FakeMsg(duration=5),                                  False),
    ("9s video (must stay)",             FakeMsg(duration=9),                                  False),
    ("60s video",                        FakeMsg(duration=60),                                 False),
    ("3s GIF",                           FakeMsg(duration=3, gif=True),                        True),
    ("9s GIF",                           FakeMsg(duration=9, gif=True),                        False),
    ("GIF without duration",             FakeMsg(gif=True),                                    True),
    ("bare link",                        FakeMsg("http://spam.example.com", entities=url_entities("x")), True),
    ("t.me invite",                      FakeMsg("join https://t.me/+AbCdEf"),                 True),
    ("bit.ly short link",                FakeMsg("check bit.ly/abc"),                          True),
    ("hidden text url",                  FakeMsg("click here", entities=[MessageEntityTextUrl(0, 5, "http://bad.xyz")]), True),
    ("9s video with no link",            FakeMsg("my cat", duration=9),                        False),
    ("sticker (not a photo)",            FakeMsg(),                                            False),
]


def main():
    print(f"MIN_VIDEO_DURATION = {config.MIN_VIDEO_DURATION}s | "
          f"BAN_ANY_LINK = {config.BAN_ANY_LINK} | BAN_ALL_GIFS = {config.BAN_ALL_GIFS}\n")
    failed = 0
    for desc, msg, expect_spam in CASES:
        reason = classify(msg)
        got = reason is not None
        mark = "ok  " if got == expect_spam else "FAIL"
        if got != expect_spam:
            failed += 1
        verdict = f"SPAM ({reason})" if got else "allowed"
        print(f"[{mark}] {desc:<28} -> {verdict}")

    # flood rule
    now = datetime.datetime.now(datetime.timezone.utc)
    flooder = [FakeMsg("spam", date=now + datetime.timedelta(seconds=i))
               for i in range(config.FLOOD_COUNT)]
    slow = [FakeMsg("hi", date=now + datetime.timedelta(seconds=i * 60))
            for i in range(config.FLOOD_COUNT)]
    floods = find_floods({111: flooder, 222: slow})
    ok = 111 in floods and 222 not in floods
    failed += 0 if ok else 1
    print(f"\n[{'ok  ' if ok else 'FAIL'}] flood rule            "
          f"-> fast user flagged: {111 in floods}, slow user flagged: {222 in floods}")

    # whitelist - must work for ids of deleted accounts, i.e. with no API call
    import asyncio

    from antispam import resolve_ids

    class NoNetClient:
        async def get_entity(self, _):
            raise AssertionError("whitelisted numeric ids must NEVER be looked up "
                                 "(deleted accounts cannot be resolved)")

    ids = asyncio.run(resolve_ids(NoNetClient(), config.WHITELIST))
    expected = {6522530722, 7450005478, 6687627877}
    wl_ok = ids == expected
    failed += 0 if wl_ok else 1
    print(f"[{'ok  ' if wl_ok else 'FAIL'}] whitelist (offline)   -> {sorted(ids)}")

    mixed_ok = asyncio.run(resolve_ids(NoNetClient(), [6522530722, "7450005478"])) == \
        {6522530722, 7450005478}
    failed += 0 if mixed_ok else 1
    print(f"[{'ok  ' if mixed_ok else 'FAIL'}] numeric strings too   -> {mixed_ok}")

    print(f"\n{'All rules behave as expected.' if not failed else f'{failed} case(s) FAILED.'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
