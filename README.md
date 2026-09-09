# BingoBanano

Bingo for house parties. 75-ball, Philippine style. The host shows a QR code on a
laptop, guests scan it with their phone camera, and they are in. Two games:

- **Classic** — everyone gets a 5x5 card and races to complete a pattern
- **Elimination** — everyone gets a number instead. When it is called, you are
  out. Last one standing wins

Guests only enter a nickname. No accounts, no sign-up, no app to install.

> This is a party game, not a gambling platform. No money, no wallet, no
> payments. See [Not included](#not-included) for what's deliberately missing.

## What works

- **QR pairing** — one QR per guest, single-use, expires after 120 seconds
- **Two games** — classic pattern bingo, or last-one-standing elimination
- **Card generation** — 75-ball, 5x5 grid, FREE center, generated server-side
- **19 patterns** — any line, rows, columns, diagonals, four corners, postage
  stamp, letter X, cross, kite, blackout
- **Three ways to call numbers** — let the app draw them, spin your own machine
  and type each number in, or use the app purely as a card dispenser
- **Caller screen** — large `B-7` display and a 1-to-75 board
- **Player board** — tap your own numbers or let them mark themselves, plus a
  "numbers needed" counter and a BINGO button
- **Server-side verification** — no marking data is accepted from the phone

## Requirements

- Windows with PowerShell
- Python 3.13 or newer
- [uv](https://docs.astral.sh/uv/getting-started/installation/) — optional but
  faster; setup falls back to `venv` and `pip` without it

Two ways to run, depending on where your guests are:

| Guests are | Mode | What you need |
|---|---|---|
| On your Wi-Fi | LAN | A firewall rule for port 8000 |
| On mobile data, or a different network | Tunnel or hosted | See [Guests on mobile data](#guests-on-mobile-data-or-another-network) |

Start with LAN. It is the simplest thing that works, and at a party your guests
are usually standing next to you — your Wi-Fi password solves it. Reach for a
tunnel only when someone genuinely cannot be on your network, and read that
section first, because it puts the game on the internet.

## Setup

Open PowerShell **as Administrator**. Admin is needed for the firewall rule,
which is the single most common reason a phone cannot reach the server.

```powershell
git clone https://github.com/ewelanonas/BingoBanano.git
cd BingoBanano
powershell -ExecutionPolicy Bypass -File .\scripts\setup-dev.ps1 -AddFirewallRule
```

`-ExecutionPolicy Bypass` is required because Windows blocks local scripts by
default.

The script will:

1. Install pinned dependencies from `uv.lock`
2. Find the LAN IP your phone can actually reach, preferring Wi-Fi over VPN
   adapters
3. Generate a `.env` with a fresh operator key and the correct base URL
4. Add an inbound firewall rule for port 8000, scoped to the local subnet
5. Print the next steps

If you would rather skip the firewall rule for now, drop `-AddFirewallRule` and
run it without admin. Phones will not be able to scan until the rule exists.

<details>
<summary>Manual setup, if you prefer not to run the script</summary>

```powershell
uv sync
Copy-Item .env.example .env
```

Then edit `.env`:

- `BINGO_OPERATOR_API_KEY` — 32 characters minimum. Generate one with
  `python -c "import secrets; print(secrets.token_urlsafe(32))"`
- `BINGO_PUBLIC_BASE_URL` — your laptop's LAN IP, for example
  `http://192.168.1.185:8000`. Find it with `ipconfig`. Do **not** use
  `localhost` here.

Firewall rule, in an admin PowerShell:

```powershell
New-NetFirewallRule -DisplayName "BingoBanano dev 8000" -Direction Inbound `
  -Action Allow -Protocol TCP -LocalPort 8000 -Profile Public,Private `
  -RemoteAddress LocalSubnet
```

</details>

## How to play

### 1. Start the server

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

`--host 0.0.0.0` matters. Bound to `127.0.0.1`, only your laptop can reach it and
phones will time out.

### 2. Sign in as host

Copy the operator key to your clipboard:

```powershell
(Select-String -Path .env -Pattern '^BINGO_OPERATOR_API_KEY=(.+)$').Matches.Groups[1].Value | Set-Clipboard
```

Open `http://127.0.0.1:8000/operator` and paste it. The key is stored in an
HttpOnly cookie, so it never becomes visible to JavaScript on the page.

### 3. Create a round

First pick the game.

**Classic bingo** gives everyone a 5x5 card and a pattern to complete. Choose a
pattern too — `any_line` finishes fastest, good for a first game.

**Elimination** gives everyone a number instead of a card. When that number is
called, they are out, and the last one standing wins. You can hand each guest up
to 5 numbers to make rounds shorter. Numbers are unique across guests, so one call
knocks out exactly one person, which keeps it dramatic. There is no BINGO button
in this game because knockouts are automatic. It needs the app to know the
numbers, so cards-only calling is not available for it.

Then choose who calls the numbers:

| Mode | What you do | What the app does |
|---|---|---|
| **The app draws them** | Press a button, or let it auto-draw | Draws randomly, marks cards, checks BINGO |
| **My own machine, I type each number in** | Spin your real tambiolo, click that number on the caller board | Marks cards, checks BINGO |
| **My own machine, cards only** | Spin and call out loud | Hands out cards; guests mark freely; a BINGO shows you their card to check |

The middle option is worth knowing about. You keep the fun of a physical
tambiolo, and because the app still knows which numbers came out, cards mark
themselves and BINGO is still verified for you. One click per ball is all it
costs.

Pick the last option if you do not want to touch the laptop during the game at
all. The app becomes a card dispenser, guests tap their own numbers, and you
verify a BINGO by eye against the card the caller screen shows you.

Press **Create round** and a join code appears.

### 4. Let guests join

Set how many cards each guest gets, then press **Generate QR**.

On the guest's phone: open the **normal camera app** and point it at the QR. Do
not use an in-app scanner like the one in Facebook or Messenger. The link opens,
they type a nickname, and press **Generate my cards**.

Their cards appear along with an **Open your live board** link. They should open
it and leave that tab open — that is where each ball shows up and where the BINGO
button lives.

One QR per guest. Each is single-use, so press **Generate QR** again for the next
person. A QR expires after 120 seconds; just generate a new one if it lapses.

### 5. Start drawing

Once everyone has joined, press **Open caller screen**. From there:

- **Start round** — this closes joining, so every card in the round sees the same
  number of balls
- **Draw next ball** — one ball per press, in app-draws mode
- **Auto-draw** — set it to 6 seconds and let it run itself
- **The 1-to-75 board** — in "I type each number in" mode this is clickable. Spin
  your machine, click the number that came out, and it greys out so you cannot
  enter it twice

Cards mark themselves on each guest's phone. When the "Needed" counter reaches 0,
the BINGO button lights up. They press it and the server decides.

Guests who want the real dabbing experience can switch off **Mark my numbers
automatically** and tap each number themselves. Tapping a number that has not
been called yet is refused with a nudge, so nobody marks ahead and then wonders
why their BINGO was rejected. Cells that were called but not yet dabbed are
outlined, so it is easy to catch up.

In cards-only mode there is nothing to check a tap against, so guests can mark
anything freely and the refusal does not apply. Their marks are saved on their own
phone, so a locked screen or a closed tab does not wipe the card — which matters
because in that mode the server does not know the numbers and could not rebuild
them. There is a **Clear all marks** button for when the tapping gets away from
someone.

If two players complete the pattern on the same ball, both win — co-winners, the
same way a real bingo hall handles it.

## Guests on mobile data or another network

The LAN setup only works when everyone is on the same Wi-Fi. A guest on mobile
data cannot reach `192.168.1.185` — that address does not exist outside your
network, so their phone times out. The same is true if you are on Ethernet and
they are on a different Wi-Fi.

Before reaching for a tunnel, consider the boring option: **at a party, everyone
is in the same room.** Give guests your Wi-Fi password and LAN mode just works,
with no extra moving parts. Tunnels are for guests who genuinely are not there.

If you do need a public link, there are three routes. Try them in this order.

| Route | Setup | Reliability |
|---|---|---|
| Cloudflare tunnel | One install, no account | Good, but QUIC and DNS filtering trip it up |
| ngrok tunnel | One install plus a free account | Better: TCP only, and a local dashboard that shows every request |
| Hosting it | A deploy, no local networking at all | Best: your Wi-Fi, router, and firewall stop mattering |

### Route 1: Cloudflare tunnel

Opens an outbound connection from your machine and hands back a public HTTPS URL.
No firewall rule, no port forwarding, no router changes.

```powershell
winget install --id Cloudflare.cloudflared
```

Then let the script run both the tunnel and the server:

```powershell
.\scripts\start-tunnel.ps1 -StartServer
```

The script starts the server with the right flags, opens the tunnel, then fetches
the public URL itself to prove it works before telling you it is ready. It prints
a host lobby link at the end.

Open that link and host the game from it. Nothing in `.env` needs to change,
because the QR takes its address from whatever URL you opened the lobby with.

Leave that window open for the whole game. Closing it kills the tunnel.

If you would rather run the server yourself, start it in a **separate** window
first and then run the script without `-StartServer`:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips="127.0.0.1"
```

Open the tunnel URL plus `/operator` and host the game exactly as before. The QR
now contains the public HTTPS link, so guests can scan it from mobile data,
another Wi-Fi, or another city.

### Route 2: ngrok

If Cloudflare keeps failing, switch providers. ngrok only uses TCP 443, so the
QUIC problem cannot happen, and it runs a local dashboard at
`http://127.0.0.1:4040` that shows every request as it arrives — which makes it
obvious whether anything is reaching you at all.

```powershell
winget install --id Ngrok.Ngrok
```

Sign up free at [ngrok.com](https://dashboard.ngrok.com/signup), copy your
authtoken, and register it once:

```powershell
ngrok config add-authtoken YOUR_AUTHTOKEN_HERE
```

Keep the agent current. ngrok retires old agent versions, and once a version is
past its deadline it stops connecting entirely rather than warning you. Check and
update with:

```powershell
ngrok version
winget upgrade --id Ngrok.Ngrok   # if you installed it with winget
ngrok update                      # only if you installed it manually
```

Do not use `ngrok update` on a package-manager install; ngrok's own docs advise
against it because the two will fight over the binary.

Then:

```powershell
.\scripts\start-tunnel.ps1 -StartServer -Provider ngrok
```

The script reads the public URL from ngrok's local API rather than scraping log
output, so URL discovery is reliable.

### Route 3: host it somewhere

This removes the entire class of problem. Your Wi-Fi, router, firewall, ISP, and
laptop all stop being involved. Guests get a normal HTTPS URL that works from
anywhere, and you can host from your phone.

There is a `Dockerfile` in the repo. It works with any container host — Fly.io,
Render, Railway, Koyeb. The only setting you must provide is
`BINGO_OPERATOR_API_KEY`, which needs to be at least 32 characters:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

The image runs as a non-root user, listens on `$PORT`, and trusts forwarded
headers because inside a hosting platform the load balancer is the only route to
the container. Rounds live in an ephemeral SQLite file, which is fine since a
round only lasts an evening; mount a volume at `/app/data` if you want games to
survive restarts.

Free tiers change often, so check current terms before picking a host. Watch out
for platforms that idle your service to sleep — a cold start in the middle of a
game is annoying.

### If the script says it cannot confirm the public URL

The script tests the public URL from your own machine. That test can fail while
the tunnel is perfectly fine, because it depends on your local DNS, proxy, and
TLS setup. `*.trycloudflare.com` in particular is blocked by some ISPs, routers,
and antivirus suites since it gets abused for phishing.

So the script prints the real error rather than a guess, and tells you to check
from your phone before concluding anything:

```
https://<your-tunnel>.trycloudflare.com/healthz
```

`{"status":"ok"}` on the phone means the tunnel works and only the local test
failed. Go ahead and play.

**Error 1033 on the phone** means the tunnel really is not connected. Two things
to try, in order:

```powershell
# 1. Your router is probably blocking QUIC on UDP 7844, cloudflared's default.
.\scripts\start-tunnel.ps1 -StartServer -Http2

# 2. Still failing? Change provider.
.\scripts\start-tunnel.ps1 -StartServer -Provider ngrok
```

If neither works, stop fighting your network and use
[Route 3](#route-3-host-it-somewhere).

### The tunnel URL is disposable

Every run of the script gets a **different** random URL, and the URL dies the
moment the tunnel closes. So after restarting a tunnel, generate a fresh QR. A
guest scanning one from an earlier tunnel gets **Cloudflare error 1033**.

No restart is needed for a new tunnel URL. The QR is built from the address you
opened the host lobby with, so opening the new tunnel URL is enough. That also
means a dead URL can never linger in configuration and quietly produce broken
QRs.

### Read this before you tunnel

A tunnel puts your game on the public internet for as long as it runs. That
changes the threat model, so a few things are set up deliberately:

- **Bind to `127.0.0.1`, not `0.0.0.0`.** With a tunnel, the tunnel should be the
  only way in. Two doors means an attacker on your LAN can bypass the tunnel and
  forge the client IP that rate limiting depends on.
- **`--proxy-headers` is required.** Without it the app sees every request as
  coming from the tunnel process itself, so per-IP rate limiting collapses into
  one shared bucket and cookies are not marked `Secure`.
- **`--forwarded-allow-ips="127.0.0.1"`** means only the local tunnel is trusted
  to state the real client IP. Do not widen this to `*` while any other route to
  the server exists.
- **The host login is now internet-reachable.** `/operator` is rate limited to 10
  attempts per minute per IP, and the key is 43 random characters, so guessing it
  is not realistic. Do not shorten the key.
- **Close the tunnel when the party ends.** Ctrl+C in the tunnel window. The URL
  dies with it. Then re-run `setup-dev.ps1` to put your LAN address back in
  `.env`.

Anyone holding the link can open the join page. For a house party that is fine —
the worst case is a stranger getting bingo cards for a game they cannot see. Do
not treat the tunnel URL as a secret, and do not leave it running unattended.

If you would rather not use `cloudflared`, `ngrok http 8000` gives an equivalent
HTTPS URL; paste it into `.env` as `BINGO_PUBLIC_BASE_URL` yourself and start the
server with the same proxy flags.

## When phones cannot reach the server

This is the most common problem. First test:

Open `http://<LAN-IP>:8000/healthz` in the **phone's browser**. You should see
`{"status":"ok"}`.

| Symptom | Likely cause | Fix |
|---|---|---|
| "took long to respond" or a timeout | No firewall rule | Run the setup script with `-AddFirewallRule` in an admin PowerShell |
| Timeout, and the guest is on mobile data | LAN address in the QR | Use a [tunnel](#guests-on-mobile-data-or-another-network) |
| Timeout even with the firewall rule | Wi-Fi client isolation, common on office and condo networks | Use a tunnel, or start a hotspot on your phone and connect the laptop to it |
| "connection refused" | Server not running, or bound to `127.0.0.1` without a tunnel | Restart with `--host 0.0.0.0` |
| Phone tries to open itself | You opened the lobby on `localhost`, so the QR fell back to config | Open the lobby on your LAN IP or tunnel URL instead |
| IP changed after rejoining Wi-Fi | New DHCP lease | Update `BINGO_PUBLIC_BASE_URL` and restart |
| Host cannot sign in, returns 429 | Login rate limit tripped | Wait a minute; it is 10 attempts per IP |
| Server refuses to start, "created by an older version" | Your database predates a feature you just pulled | `.\scripts\setup-dev.ps1 -Reset` |
| **Cloudflare error 1033** | The tunnel is not connected: its window was closed, or the QR is from an older tunnel run | Restart `start-tunnel.ps1`, leave it open, and generate a **new** QR |
| Cloudflare error 502 through the tunnel | Tunnel is up but the server is not answering on `127.0.0.1:8000` | Start the server, or check it is bound to `127.0.0.1` and not another address |

The lobby shows the current QR base URL under the form, and the app returns a
warning when it points at `localhost`, so a misconfiguration is visible right
away.

If you are on a corporate VPN, double-check which IP was picked. VPN adapters are
usually `/32` and phones can never reach them.

## Configuration

Everything lives in `.env`, prefixed with `BINGO_`.

| Setting | Default | Purpose |
|---|---|---|
| `BINGO_OPERATOR_API_KEY` | none | Host password. 32 chars minimum, required |
| `BINGO_PUBLIC_BASE_URL` | `http://127.0.0.1:8000` | Fallback for the QR address, used only when you open the lobby on `localhost` |
| `BINGO_DATABASE_URL` | SQLite file | Swap for PostgreSQL if you outgrow it |
| `BINGO_PAIRING_TTL_SECONDS` | `120` | How long a QR stays valid |
| `BINGO_DEFAULT_CARD_COUNT` | `2` | Cards per guest |
| `BINGO_MAX_CARD_COUNT` | `12` | Ceiling per pairing |
| `BINGO_PAIRING_RATE_LIMIT_PER_MINUTE` | `20` | Limit on creating and claiming QRs |
| `BINGO_BINGO_RATE_LIMIT_PER_MINUTE` | `120` | Limit on pressing BINGO |
| `BINGO_LOGIN_RATE_LIMIT_PER_MINUTE` | `10` | Limit on host sign-in attempts |

`BINGO_OPERATOR_API_KEY` has no default. If it is missing or shorter than 32
characters the app refuses to start. That is deliberate: better not to run at all
than to run wide open.

## How it is built

```
app/
  domain/      # pure logic: cards, patterns, draws, RNG. No I/O.
  db/          # SQLAlchemy models and session
  services/    # pairing, rounds, issuance, audit, events
  api/         # HTTP and WebSocket routes
  web/         # Jinja2 templates and CSS
tests/         # 123 tests
scripts/       # setup-dev.ps1, start-tunnel.ps1
```

`app/domain/` imports no FastAPI, no SQLAlchemy, and no networking — pure
functions only, which keeps it easy to test.

Stack: FastAPI, Pydantic v2, SQLAlchemy 2.x async, SQLite, segno for QR
generation, Jinja2 and vanilla JS on the front end. No build step.

### Decisions worth knowing

**The QR holds a URL, not card data.** What is encoded is
`BASE_URL/pair/{nonce}`, one-time and valid for 120 seconds. Cards are only
created after the nonce is claimed. Anyone can photograph a QR off the screen, so
there is nothing sensitive inside it.

**Scanning uses the native camera.** The browser camera API only works over HTTPS
or on `localhost`, so an in-browser scanner cannot run on `http://192.168.x.x`.
Putting a URL in the QR routes it through the camera app instead and needs no
permission prompt.

**Database constraints are the real guard, not Python code.** The draw table has
two unique constraints: `(round_id, ball)` and `(round_id, sequence_no)`. If two
draw requests land at once, the second one fails, re-reads the current state, and
retries. No ball can repeat.

**Nonce consumption is atomic.** The guard lives in the `WHERE` clause rather than
in Python, so there is no check-then-act race. If zero rows were affected, the
claim is rejected.

**Joining closes when drawing starts.** If people could join mid-game, cards
would have seen different numbers of balls and the game would not be fair.

**A host-entered number is stored the same way a randomly drawn one is.** Manual
mode changes only who picks the ball; it goes into the same table under the same
unique constraints. So marking, win checking, co-winners and the live board all
work without a second code path. Cards-only mode is the one case with no numbers
to check against, and there the server refuses to declare a winner and hands the
decision to the host instead of guessing.

**In app-draws mode the host cannot choose a ball.** Supplying one is rejected,
because a host who could pick the numbers could pick the winner.

**Elimination numbers are unique per round, enforced by the database.** A unique
constraint on `(round_id, number)` means two guests can never be handed the same
number even if they scan at the same moment. That is also why one call removes
exactly one guest. If the very last survivor's number comes up, they still win,
having outlasted everyone.

**Only a nickname is stored about a player.** No birth date, no email, no ID. A
player's live board is reachable through an unguessable capability URL rather than
a login.

**The QR address comes from the request, not from configuration.** Whatever URL
the host opened the lobby with is what goes into the QR. This is why swapping
tunnels needs no restart and no config edit, and why a stale URL cannot linger and
silently produce dead QR codes. The one exception is `localhost`, which a phone
cannot use, so that falls back to `BINGO_PUBLIC_BASE_URL`.

## Development

```powershell
.\.venv\Scripts\python.exe -m pytest -q          # 123 tests
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format .
.\.venv\Scripts\python.exe -m mypy               # strict on app/domain
```

## After pulling an update

New features sometimes add database columns. Schema is created with
`create_all()`, which adds missing tables but **not** missing columns, so an
existing database file from an older version will be short a column or two.

The server checks for this at startup and refuses to run with a message naming
the columns and the fix, rather than letting it surface later as a mystery error
when you try to create a round. The fix:

```powershell
.\scripts\setup-dev.ps1 -Reset
```

That deletes the local database. Nothing of value is lost — rounds only last an
evening. If you ever need games to survive updates, that is the point to add
Alembic migrations.

## Not included

Deliberately absent:

- Money, wallets, and payments
- Accounts, passwords, and age verification — it is a party, not a casino
- Draw history replay after a player reconnects
- Multiple concurrent rounds on one caller screen
- Printable card PDFs
- Alembic migrations — still on `create_all()`, which is fine at this size

`.kiro/steering/` carries notes about Philippine bingo and about PAGCOR gaming
regulation from when this project had a more serious scope. None of it applies to
a party game, but it is kept as reference in case this ever grows up.

## Security

- `.env`, certificates, and the database are never committed
- The operator key never reaches browser JavaScript; an HttpOnly cookie plus a
  CSRF token is used instead
- The session cookie is marked `Secure` automatically when the connection is
  HTTPS, and left off over plain HTTP so LAN play still works
- Pairing nonces are never logged in full, only hashed
- Rate limits sit on host sign-in, QR creation, QR claiming, and BINGO presses
- The firewall rule is scoped to `LocalSubnet`, not the open internet
- The app inspects `BINGO_PUBLIC_BASE_URL` and warns when the setup cannot work
  or is unsafe: localhost, a LAN address guests cannot reach, or a public host
  without HTTPS

On the LAN this runs over plain HTTP, which is fine for a living room. Through a
tunnel it runs over HTTPS but is exposed to the internet — read
[Read this before you tunnel](#read-this-before-you-tunnel).

When the party is over, remove the firewall rule:

```powershell
Remove-NetFirewallRule -DisplayName "BingoBanano dev 8000"
```
