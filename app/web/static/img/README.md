# Party photos

Two photos live in this folder. You do not put them here by hand — sign in as
the host and use **Photos** in the top bar of the lobby, or go straight to
`/operator/photos`. That page works from a phone, which is where the photos
usually are.

| File | Where it shows up |
|---|---|
| `photo1.jpg` | Behind every called number on a guest's card, and in the BINGO popup |
| `photo2.jpg` | In the BINGO popup |

Both are optional. The app falls back to plain colours and a gradient when a
file is missing, so nothing breaks at the party if you never get round to it.

The popup picks one of the two at random every time somebody wins, so guests see
both over the course of a party. The called numbers on the cards always use
`photo1.jpg` — a grid of 24 cells switching between two faces is noisy, and the
number has to stay readable on top of it.

## What the upload does to your photo

You can hand it a straight-off-the-camera shot. The server decodes it, applies
the rotation the camera recorded, crops it square slightly above centre so a
face does not get cut off, shrinks it to 640px, and re-saves it as JPEG. That
means:

- Camera metadata is dropped, including GPS location.
- The saved file lands around 60-90 KB, which matters for guests on mobile data.
- A file that is not really an image cannot be saved, whatever it claims to be.

Upload limit is 12 MB, which is more than a phone camera produces.

## Notes

- Square-ish framing looks best. Both photos are masked into a circle.
- For `photo1.jpg`, avoid busy detail in the middle. A number sits on top of it,
  with a dark shadow so it stays readable either way.
- Press **Preview popup** on the photo page to see the result without winning a
  round. The same button is on the caller screen.
- These files are in `.gitignore`. They stay put across a `git pull`, and they
  never end up in the repo.

To change the look, edit `app/web/static/theme.css`. To change which photos the
popup rotates through, edit the `PHOTOS` list in `app/web/static/celebrate.js`.
