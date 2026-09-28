# Local website fonts

Source: https://github.com/google/fonts/tree/23e54b51ddffbc7713c583748e3bd86f62b1fa4a/ofl

Both families are distributed under the SIL Open Font License 1.1; the original copyright
notices and license terms are retained in `literata-OFL.txt` and `fira-OFL.txt`.

| Output | Original path beneath `ofl/` | Weights |
| --- | --- | --- |
| literata.woff2 | literata/Literata[opsz,wght].ttf | 200–900, optical size 7–72 |
| literata-italic.woff2 | literata/Literata-Italic[opsz,wght].ttf | 200–900, optical size 7–72 |
| fira-sans-regular.woff2 | firasans/FiraSans-Regular.ttf | 400 |
| fira-sans-semibold.woff2 | firasans/FiraSans-SemiBold.ttf | 600 |
| fira-sans-bold.woff2 | firasans/FiraSans-Bold.ttf | 700 |

Converted with fontTools 4.64.0 and Brotli, without subsetting or changing font tables:
`font = TTFont(source); font.flavor = "woff2"; font.save(target)`.
These build tools are not application dependencies. Keep the complete upstream glyph coverage.
The five outputs were checked for the English, Polish, Russian, Belarusian and Ukrainian
alphabets, digits, section sign, typographic apostrophe, en dash and em dash.

Literata supplies reading text and real italics; Fira Sans supplies navigation and notices.
CSS declares only the included weights. Fonts load from the same static origin with `swap`,
so blocked downloads retain the serif/system fallbacks without hiding text.
