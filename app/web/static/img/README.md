# Party photos

Drop two square-ish photos here. Both are optional: the app falls back to plain
colours and a gradient if a file is missing, so nothing breaks at the party if
you forget.

| File | Where it shows up |
|---|---|
| `photo1.png` | Behind every called number on a guest's card, and in the BINGO popup |
| `photo2.png` | In the BINGO popup |

The popup picks one of the two at random every time somebody wins, so guests see
both over the course of a party. The called numbers on the cards always use
`photo1.png` — a grid of 24 cells switching between two faces is noisy, and the
number has to stay readable on top of it.

Tips:

- Square crops look best. Both are masked into a circle in the popup.
- Keep each file under about 200 KB. Guests load these on mobile data.
- For `photo1.png`, avoid busy detail in the middle. A number sits on top of it,
  with a dark shadow so it stays readable either way.
- PNG or JPG both work, but the filenames must end in `.png` since that is what
  the stylesheet and `celebrate.js` ask for.

After adding the files, restart the server and hard-refresh (`Ctrl`+`F5`). Press
**Preview popup** on the caller screen to check them without winning a round.

To change the look, edit `app/web/static/theme.css`. To change which photos the
popup rotates through, edit the `PHOTOS` list in `app/web/static/celebrate.js`.
