# Party photos

Drop two square-ish photos here. Both are optional: the app falls back to plain
colours if a file is missing, so nothing breaks at the party if you forget.

| File | Where it shows up |
|---|---|
| `called.png` | Behind every number that has been called on a guest's card |
| `bingo.png` | Inside the round badge on the BINGO celebration popup |

Tips:

- Square crops look best. `bingo.png` is masked into a circle.
- Keep each file under about 200 KB. Guests load these on mobile data.
- `called.png` sits behind a number, so pick something without busy detail in
  the middle. The number has a dark shadow so it stays readable either way.
- PNG or JPG both work, but the filenames must end in `.png` since that is what
  the stylesheet asks for.

To change the look without touching these files, edit
`app/web/static/theme.css`.
