# BingoBanano

Bingo for house parties. 75-ball, Philippine style. The host shows a QR code on a
laptop, guests scan it with their phone camera, and they get cards instantly. The
caller draws balls, every player's card marks itself live, and the server decides
whether a BINGO is real.

Guests only enter a nickname. No accounts, no sign-up, no app to install.

> This is a party game, not a gambling platform. No money, no wallet, no
> payments. See [Not included](#not-included) for what's deliberately missing.

## What works

- **QR pairing** — one QR per guest, single-use, expires after 120 seconds
- **Card generation** — 75-ball, 5x5 grid, FREE center, generated server-side
- **19 patterns** — any line, rows, columns, diagonals, four corners, postage
  stamp, letter X, cross, kite, blackout
- **Live draw engine** — manual or auto-draw, no ball is ever drawn twice
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
| On mobile data, or a different network | Tunnel | `cloudflared`, no firewall rule |

Start with LAN. Switch to a tunnel only when someone cannot join, and read
[Guests on mobile data](#guests-on-mobile-data-or-another-network) first because
a tunnel puts the game on the internet.

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

In the host lobby, pick a pattern and name the round. For a first game, try
`any_line` — it finishes fastest. Press **Create round** and a join code appears.

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
- **Draw next ball** — one ball per press
- **Auto-draw** — set it to 6 seconds and let it run itself

Cards mark themselves on each guest's phone. When the "Needed" counter reaches 0,
the BINGO button lights up. They press it and the server decides.

Guests who want the real dabbing experience can switch off **Mark my numbers
automatically** and tap each number themselves. Tapping a number that has not
been called yet is refused with a nudge, so nobody marks ahead and then wonders
why their BINGO was rejected. Cells that were called but not yet dabbed are
outlined, so it is easy to catch up.

If two players complete the pattern on the same ball, both win — co-winners, the
same way a real bingo hall handles it.

## Guests on mobile data or another network

The LAN setup only works when everyone is on the same Wi-Fi. A guest on mobile
data cannot reach `192.168.1.185` — that address does not exist outside your
network, so their phone times out. The same is true if you are on Ethernet and
they are on a different Wi-Fi.

To let anyone join from anywhere, the server needs to be reachable from the
internet. The easiest way is a Cloudflare tunnel, which opens an outbound
connection from your machine and hands back a public HTTPS URL. No firewall rule,
no port forwarding, no router changes.

Install `cloudflared` once:

```powershell
winget install --id Cloudflare.cloudflared
```

Then let the script run both the tunnel and the server:

```powershell
.\scripts\start-tunnel.ps1 -StartServer
```

The script starts the server with the right flags, opens the tunnel, writes the
public URL into `.env`, restarts the server so it picks that URL up, and then
fetches the public URL itself to prove it works. It prints the host lobby link
when everything is confirmed.

Leave that window open for the whole game. Closing it kills the tunnel.

If you would rather run the server yourself, start it in a **separate** window
first and then run the script without `-StartServer`:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips="127.0.0.1"
```

Open the tunnel URL plus `/operator` and host the game exactly as before. The QR
now contains the public HTTPS link, so guests can scan it from mobile data,
another Wi-Fi, or another city.

### The tunnel URL is disposable

Every run of the script gets a **different** random URL, and the URL dies the
moment the tunnel closes. Two consequences that bite in practice:

- Old QR codes stop working. After restarting a tunnel, generate a fresh QR. A
  guest scanning a stale one gets **Cloudflare error 1033**.
- The server must be running with the current URL in `.env`. The script handles
  the restart for you when you pass `-StartServer`; otherwise it stops and waits
  for you to restart it manually.

When the tunnel closes, the script puts your previous `BINGO_PUBLIC_BASE_URL`
back into `.env`, so a dead tunnel URL is not left behind to generate broken QRs
the next time you play on the LAN.

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
| Phone tries to open itself | `localhost` is encoded in the QR | Set `BINGO_PUBLIC_BASE_URL` to the LAN IP or a tunnel URL |
| IP changed after rejoining Wi-Fi | New DHCP lease | Update `BINGO_PUBLIC_BASE_URL` and restart |
| Host cannot sign in, returns 429 | Login rate limit tripped | Wait a minute; it is 10 attempts per IP |
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
| `BINGO_PUBLIC_BASE_URL` | `http://127.0.0.1:8000` | The URL encoded into the QR |
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
tests/         # 84 tests
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

**Only a nickname is stored about a player.** No birth date, no email, no ID. A
player's live board is reachable through an unguessable capability URL rather than
a login.

## Development

```powershell
.\.venv\Scripts\python.exe -m pytest -q          # 84 tests
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format .
.\.venv\Scripts\python.exe -m mypy               # strict on app/domain
```

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
