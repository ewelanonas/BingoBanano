---
inclusion: always
---

# Tech Stack: Python + QR Pairing + Card Generation

Ito ang stack conventions ng BingoBanano. Sumasama ito sa
`bingo-philippines.md` — doon ang domain rules, dito ang implementation.

## 1. Runtime at libraries

| Layer | Choice | Bakit |
|---|---|---|
| Language | Python 3.12+ | modern typing, mabilis |
| API | FastAPI (0.14x line) + Uvicorn | async, native WebSocket, auto OpenAPI |
| Validation | Pydantic v2 | required na ng bagong FastAPI |
| Settings | pydantic-settings + `.env` | typed config, walang hardcoded secrets |
| ORM | SQLAlchemy 2.x (2.0 stable line) | mature async support |
| Migrations | Alembic | schema history para sa audit |
| DB | SQLite sa local, PostgreSQL sa staging/prod | walang setup sa dev |
| Realtime | WebSocket (FastAPI) + Redis pub/sub kapag multi-worker na | draw events at pairing events |
| QR generate | `segno` | pure-Python, walang native DLL sa Windows |
| QR decode (server) | `opencv-python` na `cv2.QRCodeDetector` | walang external zbar binary |
| Printable cards | `reportlab` o `Pillow` | PDF/PNG ng physical cards |
| Tests | `pytest`, `pytest-asyncio`, `httpx` ASGITransport | in-process API tests |
| Quality | `ruff` (lint + format), `mypy --strict` sa domain layer | |
| Deps | `uv` + `uv.lock`, o `pip-tools` + hashes | pinned exact versions |

Huwag gumamit ng `pyzbar` — nangangailangan ito ng native zbar DLL at madaling
masira sa Windows dev machines. Ang `cv2.QRCodeDetector` ay sapat na para sa
server-side decoding.

Dependency rule: **exact pins** (`==`) sa manifest at laging may lock file.
Walang open ranges. Kapag nag-add ng bagong package, sabihin sa user kung bakit
kailangan.

## 2. Project layout

```
app/
  main.py            # FastAPI app factory, router wiring
  config.py          # pydantic-settings, PUBLIC_BASE_URL, DB URL
  api/
    pairing.py       # QR pairing endpoints + event stream
    cards.py         # card issuance at lookup
    rounds.py        # draws, claims, verification
  domain/            # pure logic, walang I/O: card gen, pattern bitmask, win check
    cards.py
    patterns.py
    rng.py
  db/
    models.py        # SQLAlchemy models
    session.py
  services/
    pairing.py       # nonce lifecycle
    issuance.py      # purchase -> card generation
  web/
    templates/       # QR display page, phone landing page
    static/
tests/
migrations/          # Alembic
```

Rule: ang `app/domain/` ay walang import ng FastAPI, SQLAlchemy, o network. Pure
functions lang para madali i-unit test at ma-audit ng third-party lab.

## 3. Local development (Windows / PowerShell)

```powershell
uv sync
# LAN mode: para sa bisitang kaparehong Wi-Fi
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Gamitin ang `;` bilang command separator, hindi `&&`.

Para sa bisitang nasa mobile data, tunnel mode ang kailangan — tingnan ang §3a
para sa tamang bind at flags.

Mahahalagang gotcha sa QR flow — huwag kalimutan ang mga ito:

1. **Huwag i-encode ang `localhost` o `127.0.0.1` sa QR.** Kapag in-scan ito ng
   phone, ang phone ang magiging `localhost`, kaya mabibigo. Kunin ang URL mula sa
   `PUBLIC_BASE_URL` setting. Never i-hardcode.
2. **Ang LAN IP ay para lang sa bisitang kaparehong network.** Ang
   `192.168.x.x` ay hindi umiiral sa labas ng Wi-Fi mo, kaya mag-ti-timeout ang
   bisitang nasa mobile data o sa ibang network. Iba ang solusyon doon, tingnan
   ang §3a.
3. **Windows Firewall** — kailangan ng inbound rule sa port 8000 para makaabot
   ang phone na kaparehong Wi-Fi. Sabihin sa user; huwag basta i-disable ang
   firewall.
4. **Camera API ay nangangailangan ng secure context.** Ang `getUserMedia` ay
   gumagana lang sa HTTPS o sa `localhost`. Ang `http://192.168.x.x` ay hindi
   secure context, kaya hindi tatakbo ang in-browser scanner sa phone. Dahil
   dito, ang default na design ay **URL-in-QR** (tingnan ang §4) na binubuksan ng
   native camera app — walang kailangang camera permission sa browser.
5. Huwag i-commit ang `.env`, ang mga local cert, o ang SQLite DB file.

## 3a. Dalawang mode ng pag-abot, at ang bind na kaakibat

May dalawang paraan lang na maaabot ang server, at magkaiba ang tamang bind sa
bawat isa. Ito ang pinakamadaling mapagkamalan, kaya laging tingnan kung alin ang
ginagamit bago magpayo.

| Mode | Sino ang kayang sumali | Bind | Karagdagang flag |
|---|---|---|---|
| LAN | kaparehong Wi-Fi lang | `--host 0.0.0.0` | wala; kailangan ng firewall rule |
| Tunnel | kahit saan, kasama ang mobile data | `--host 127.0.0.1` | `--proxy-headers --forwarded-allow-ips="127.0.0.1"` |

Mga rule na hindi puwedeng labagin:

- **Sa tunnel mode, `127.0.0.1` ang bind at hindi `0.0.0.0`.** Ang tunnel lang ang
  dapat na daan papasok. Kapag dalawa ang pintuan, may makakalampas sa tunnel
  mula sa LAN at mapepeke ang client IP na pinagbabatayan ng rate limiting.
- **Sa tunnel mode, kailangan ang `--proxy-headers`.** Kung wala ito, ang lahat ng
  request ay parang galing sa tunnel process, kaya isang shared bucket na lang ang
  rate limiting at hindi mamamarkahan ng `Secure` ang cookie.
- **`--forwarded-allow-ips` ay dapat `127.0.0.1`, hindi `*`.** Ang `*` ay
  nagpapahintulot kahit sino na magsabi ng client IP nila.
- Huwag basahin ang `X-Forwarded-For` o `X-Forwarded-Proto` nang direkta sa code.
  Ang `request.client` at `request.url.scheme` ang gamitin — inaayos na ni uvicorn
  ang mga iyon at hindi tumatalab ang spoofing kung hindi galing sa trusted proxy.
- Ang tunnel ay nag-e-expose ng makina sa internet. Sabihin ito nang tahasan sa
  user bago i-rekomenda, at ipaalala na isara pagkatapos.

Kapag naging internet-reachable ang app, ang `/operator` ay maaabot din ng kahit
sino. Kaya may sariling rate limit ang login at hindi puwedeng paikliin ang
operator key.

## 4. QR pairing flow (primary design)

Ang layunin: may QR sa website, i-scan ng player, at magkaka-bingo cards siya.
Ang QR ay naglalaman ng **URL na may one-time nonce**, hindi ng card data.

```
Browser/kiosk                Server                        Phone
     |  POST /api/pairing      |                              |
     |------------------------>|  gumawa ng PairingSession    |
     |                         |  nonce, expires_at=+120s     |
     |  {id, qr_svg, url}      |  status=pending              |
     |<------------------------|                              |
     |  WS /api/pairing/{id}/events (naghihintay)             |
     |                         |                              |
     |   [ipinapakita ang QR]  |         i-scan ng native camera app
     |                         |<-----GET /pair/{nonce}-------|
     |                         |  landing page + auth/guest   |
     |                         |<-----POST /pair/{nonce}/claim|
     |                         |  atomic consume ng nonce     |
     |                         |  age + exclusion check       |
     |                         |  generate cards server-side  |
     |  WS: cards_issued       |                              |
     |<------------------------|------cards payload---------->|
```

Non-negotiable na rules sa flow na ito:

- **Ang nonce ay credential.** 128-bit mula sa `secrets.token_urlsafe(32)`.
  Short-lived (120s default), **single-use**, at kino-consume nang atomic:
  `UPDATE pairing SET status='claimed' WHERE nonce=:n AND status='pending' AND
  expires_at > now()` — kung 0 rows ang naapektuhan, reject.
- **Walang PII o card data sa QR.** URL at nonce lang. Ang QR ay maaaring
  ma-photograph ng kahit sino sa harap ng screen.
- **Rate limit** ang `POST /api/pairing` at ang claim endpoint by IP at by
  device. Ito ang mga unauthenticated na pasukan ng sistema.
- Ang **cards ay ginagawa sa server** pagkatapos ng claim, hindi bago. Ang
  pairing record ay hindi entitlement — ang bayad o authorized purchase ang
  nagti-trigger ng issuance.
- I-run ang **21+ verification at exclusion-list check** bago mag-issue. Kapag
  nag-error ang check, i-deny (fail closed).
- I-log ang pairing attempt at outcome. Huwag i-log ang buong nonce — mask o
  hash lang.

Secondary flow: pag-scan ng QR ng **printed card** para i-bind sa account. Sa
browser, gamitin ang `BarcodeDetector` API kung available, at ang server-side
`cv2.QRCodeDetector` para sa uploaded image fallback at admin tooling. Ang
printed QR ay may card serial + HMAC signature; i-verify ang signature server-side
bago tanggapin.

## 5. Card generation rules

- RNG: `secrets.SystemRandom()` lang. **Huwag** gumamit ng `random` module para
  sa cards, draws, o kahit anong may kinalaman sa pera.
- Sample kada column ayon sa domain ranges: `B 1–15`, `I 16–30`, `N 31–45`,
  `G 46–60`, `O 61–75`. Ang center ay FREE.
- Bawat card ay may unique serial; unique constraint sa `(session_id, serial)`.
- **I-persist ang buong layout.** Huwag i-regenerate mula sa seed sa oras ng
  verification — ang stored layout ang source of truth sa dispute.
- Pattern at win check bilang 25-bit bitmask, FREE space pre-set. Ang win check
  ay `(marked & pattern) == pattern`.
- Deterministic testing: i-inject ang RNG bilang dependency para maka-seed sa
  tests. Ang seeded path ay tests only, hindi kailanman naa-abot sa production
  config.

## 6. Data at money

- Pera bilang integer centavos (`BigInteger`), hindi `Float`. Format lang sa
  presentation layer bilang `₱1,234.56`.
- Timestamps sa UTC sa DB (`DateTime(timezone=True)`), display sa `Asia/Manila`.
- Parameterized queries lang — SQLAlchemy constructs o bound params. Walang
  f-string na SQL.
- Append-only audit tables para sa draws, claims, at payouts. Walang `UPDATE` sa
  audit rows.
- Idempotency key sa claim at payout endpoints.

## 7. Testing at verification

- Bago mag-report ng tapos: `ruff check .` ; `mypy app/domain` ;
  `pytest -q`.
- API tests via `httpx.ASGITransport`, walang totoong network.
- Para sa pairing, i-test ang: expiry, replay ng ginamit na nonce, concurrent
  claim ng parehong nonce, at claim ng expired na nonce. Ito ang mga aktuwal na
  attack path.
- Card generation tests: column ranges, walang duplicate, FREE center, at
  uniqueness ng serial sa maraming issuance.
