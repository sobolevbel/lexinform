# The mark

A Didone section sign with a red bar to its left. The bar is the margin rule that legal
typesetting puts beside an amended provision — which is what the bot does: it says which
provision moved and how long there is left to act on it.

One form on every surface; only the colours rotate, so the channel, the chat and the bot are
told apart at avatar size without reading anything. The technical channel is the exception
that proves the rule: same mark, accent off — nothing posted there is an alert for readers.

| Colour | Hex | Where |
| --- | --- | --- |
| Ink | `#10161F` | channel ground, the glyph on paper |
| Paper | `#F4EFE6` | chat ground, the glyph on ink, on red and on slate |
| Red | `#C0362C` | the change-bar, bot ground |
| Slate | `#2B3443` | technical channel ground |
| Slate muted | `#9AA4B4` | the change-bar with the accent off |

## Files

`channel.svg`, `chat.svg`, `bot.svg`, `tech.svg` and `favicon.svg` are the sources;
everything under `png/` is generated from them. Telegram crops an avatar to a circle, so
nothing reaches outside the inscribed circle — the square PNGs upload as they are.

| File | Surface |
| --- | --- |
| `png/channel-512.png` | Telegram channel |
| `png/chat-512.png` | Telegram chat |
| `png/bot-512.png` | Telegram bot (BotFather `/setuserpic`) |
| `png/tech-512.png` | the technical channel (`LEXINFORM_TELEGRAM_LOG_CHANNEL_ID`) |
| `favicon.svg`, `favicon.ico`, `png/favicon-{16,32,48}.png` | browser tab |
| `png/apple-touch-icon-180.png` | iOS home screen |
| `png/icon-{192,512}.png` | web app manifest |

The site mark is set larger and its bar thicker than the avatars': at 16 px the hairlines of
a Didone grey out and the bar is what stays recognisable.

## In the page

```html
<link rel="icon" href="/favicon.ico" sizes="32x32">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/apple-touch-icon-180.png">
<link rel="manifest" href="/site.webmanifest">
```

`favicon.svg` carries a `prefers-color-scheme: dark` rule that swaps the ground for the
glyph — an ink tile all but disappears in a dark tab strip, and this is the one thing a PNG
cannot do. It is what SVG is here for; sharpness is not, because a browser rasterises it at
16 px like anything else. A browser that takes the SVG prefers it over every PNG size, so
the small-size tuning has to live in the SVG itself, which is where it is.

The ICO stays: browsers request `/favicon.ico` from the root with no markup at all, and
bookmarks, readers and crawlers use it. The Apple touch icon and the manifest icons must be
PNG. Only the dark rule is CSS, so a rasteriser that ignores the stylesheet still gets the
light colours off the `fill` attributes — and the PNGs are deliberately rasterised from the
light mark, or they would follow the appearance setting of whichever machine ran the script.

## Regenerating

```sh
uv run --with pillow python assets/brand/make_marks.py
```

macOS only — it reads Didot's outlines with fontTools (the marks carry paths, not a font
reference, so nothing downstream needs the font) and rasterises through QuickLook.

The design canvas, with the three directions that were not taken and the reasons:
https://claude.ai/artifact/P4Geo2jjELE5kr4TnJsArP
