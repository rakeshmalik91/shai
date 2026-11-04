# Terminal output and colours

Shai supports ANSI SGR colours in iSH and other ANSI-compatible terminals.

| Output | Colour |
|---|---|
| User prompt | Green |
| Assistant response | Cyan |
| Tool activity line | Magenta |
| Tool batch counter | Yellow |
| System notices | Blue |
| Successful tool completion | Green |
| Tool errors | Bold red |
| Secondary information | Dim white |

The tool activity line is still a single carriage-return-updated line. This
does not reserve terminal rows or freeze chat output.

Colours are automatically enabled only when stdout is a TTY. Disable them with
`NO_COLOR=1`, `TERM=dumb`, or `SHAI_COLOR=0`. If the terminal supports ANSI but
TTY detection is unavailable, use `SHAI_COLOR=1` (TTY output is still required
for in-place updates).


The in-place tool line uses a real carriage-return and ANSI erase-line control sequence (not the printable `\\r\\033` text), and truncates long tool details to avoid wrapping on narrow terminals such as iSH.
