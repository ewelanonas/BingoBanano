---
inclusion: auto
name: stack-options
description: Menu ng alternatibo at karagdagang tech stack para sa BingoBanano — frontend, realtime, auth, payments, observability, deployment, at PH-specific hosting. Gamitin kapag pinag-uusapan ang arkitektura, kapag may bagong feature na kailangan ng bagong library, o kapag nagpapasya kung aling technology ang gagamitin.
---

# Stack Options: kung saan puwedeng lumaki ang BingoBanano

Menu ito, hindi mandato. Ang binding na stack ay nasa `python-stack.md`. Dito ang
mga alternatibo, kasama ang trade-off, para may basehan ang pagpili.

Prinsipyo sa pagpili: **huwag magdagdag ng dependency na hindi pa kailangan.**
Kapag nagdagdag, sabihin kung anong problema ang sinosolusyunan.

## 1. Frontend

Ang kasalukuyan ay server-rendered Jinja2 + vanilla JS. Kaya nito ang kiosk at
ang phone landing page nang walang build step.

| Option | Kailan ito tama | Trade-off |
|---|---|---|
| **HTMX** | Marami nang partial update (live draw board, claim list) pero ayaw pa ng SPA | Isa lang na script tag, pero kailangan mag-isip sa fragment endpoints |
| **Alpine.js** | Kailangan ng maliit na client state (auto-daub toggle, sound on/off) | 15KB, magaan, pero madaling maging spaghetti sa markup |
| **Tailwind CSS** | Lumalaking design surface, maraming screen | Nangangailangan ng Node build step; sa ngayon ay isang CSS file lang ang buong app |
| **React o Next.js** | Kapag ang player app ay talagang SPA na may mabilis na live board at animation | Dalawang codebase, dalawang deploy, at kailangan ng API contract discipline |
| **Vue o Svelte** | Katulad ng React pero mas maliit na bundle | Mas maliit na PH hiring pool kumpara sa React |
| **PWA (manifest + service worker)** | Installable sa phone, may offline shell | Hindi puwedeng i-cache ang cards o draws — laging server-authoritative |

Rekomendasyon kapag lumaki: **HTMX + Alpine** muna bago mag-SPA. Ang bingo UI ay
mostly server state, kaya bihirang kailangan ng buong client framework.

## 2. Realtime at scaling

Ang kasalukuyan ay in-process na `EventBroker` (asyncio queues) + FastAPI
WebSocket. Single-worker lang ito.

- **Redis pub/sub** — ang unang dapat idagdag pagdating ng pangalawang worker.
  Walang shared memory ang mga uvicorn worker, kaya hindi makakarating sa kiosk
  ang event kung iba ang worker na tumanggap ng claim. Client: `redis[hiredis]`.
- **Server-Sent Events (SSE)** — one-way lang (server → browser), mas simple
  kaysa WebSocket, at mas mabait sa reverse proxy. Sapat na ito para sa kiosk
  na nakikinig lang. Ang WebSocket ay para kapag may two-way na (live daub).
- **Redis Streams** — kapag kailangan ng replay ng draw events pagkatapos ng
  reconnect. Mas tama ito kaysa pub/sub para sa audit-sensitive na event.
- **Centrifugo o Soketi** — dedicated realtime server kapag libo-libo nang
  concurrent na player. Inaalis ang connection load sa Python.
- **Sticky sessions** — kung WebSocket sa likod ng load balancer, kailangan ito o
  kailangan ng shared backplane.

## 3. Background work at scheduling

- **arq** — Redis-based, async-native, magaan. Pinakamainam na tugma sa FastAPI.
- **Celery** — mature at may malaking ecosystem, pero sync-first at mabigat.
- **APScheduler** — sapat na para sa in-process na cron (pag-expire ng stale
  pairing, pag-generate ng session report).
- Gamit na kailangan nito: expiry sweeper, payout retry, EOD reconciliation,
  regulatory report generation.

## 4. Database at storage

- **PostgreSQL** — ang default pagkaalis sa SQLite. Kailangan ng
  `asyncpg`. Mas mahalaga: totoong concurrent writes at `SELECT ... FOR UPDATE`
  para sa payout locking.
- **Alembic** — kailangan na bago ang unang totoong schema change. Ang
  kasalukuyang `create_all()` ay scaffold-level lang at walang history, at ang
  schema history ay bahagi ng audit trail.
- **Partitioning ng draw at audit tables** — mabilis lumaki ang append-only na
  data. Mag-plano ng monthly partition o archival job.
- **Object storage (S3-compatible)** — para sa printable card PDF at sa mga
  export na report. Huwag i-store sa DB.

## 5. Auth at player identity

Ang kasalukuyan ay guest claim na may age declaration. Hindi ito sapat sa
totoong regulated na operasyon.

- **OTP via SMS** — ang pinaka-PH-normal na login. Providers na may PH coverage:
  Semaphore, Movider, Twilio. Kailangan ng rate limiting at anti-enumeration.
- **fastapi-users o Authlib** — kung kailangan ng full account system o OAuth.
- **KYC / ID verification** — para sa totoong 21+ enforcement, hindi sapat ang
  self-declared na birth date. Kailangan ng ID capture at verification vendor.
  Ito ang pinakamalaking compliance gap ng kasalukuyang scaffold.
- **Session storage sa Redis** — ang kasalukuyang operator session store ay
  in-memory at nawawala sa restart.

## 6. Payments (PH rails)

Walang payment sa scaffold. Ang cards ay ini-issue base sa operator-authorized
na pairing.

- **Aggregators na may GCash at Maya**: PayMongo, Xendit, Dragonpay, Maya
  Business. Isa lang ang i-integrate sa simula pero ilagay sa likod ng interface.
- **InstaPay at PESONet** — bank transfer. Ang PESONet ay batch (hindi
  real-time), kaya hindi ito puwede sa instant na card purchase.
- Non-negotiable sa payment code: integer centavos, idempotency key kada
  charge at kada payout, webhook signature verification, at reconciliation job.
  Huwag kailanman ilagay ang card issuance sa loob ng webhook handler nang
  walang idempotency guard.

## 7. QR at scanning options

- **segno** (gamit na) — pure Python, walang native dependency.
- **qrcode[pil]** — alternatibo, pero mas mabigat at mas maluwag sa spec.
- **In-browser scanning**: `BarcodeDetector` API kung available, `html5-qrcode` o
  `zxing-js` bilang fallback. Tandaan: kailangan ng secure context, kaya hindi
  ito gagana sa `http://192.168.x.x`.
- **Server-side decode**: `cv2.QRCodeDetector` para sa uploaded image at admin
  tooling. Iwasan ang `pyzbar` dahil sa native zbar DLL sa Windows.
- **Printable cards**: `reportlab` para sa PDF na may card serial at signed QR.
  I-HMAC ang QR payload para hindi mapeke ang printed card.
- **mkcert** — locally-trusted HTTPS cert para matestingan ang in-browser camera
  sa LAN. Mas mabuti ito kaysa tunnel kung LAN lang ang kailangan, dahil hindi
  ito nag-e-expose sa internet.

## 7a. Pag-abot sa bisitang wala sa parehong network

Ito ang pinakamadalas na kulang: ang LAN setup ay hindi maaabot ng bisitang nasa
mobile data. Tatlong landas ang puwede, mula sa pinakamagaan.

| Option | Kailan tama | Trade-off |
|---|---|---|
| **Cloudflare quick tunnel** (`cloudflared tunnel --url`) | Party ngayon, pansamantala lang | Random na URL kada takbo, at abot ng internet habang bukas |
| **Named Cloudflare tunnel** o **ngrok** na may account | Paulit-ulit na gamit, gustong stable na URL | Kailangan ng account at kaunting setup |
| **Tunggal na deploy** sa Fly.io, Railway, o Render | Regular na ginagamit, maraming laro | Kailangan ng Postgres o persistent disk, at totoong ops |

Kapag alinman sa mga ito ang gamit, mga bagay na dapat sabay na ayusin:

- **Bind sa `127.0.0.1` lang** kapag tunnel ang daan. Ang tunnel ang tanging
  pintuan; huwag magdagdag ng pangalawa.
- **`--proxy-headers`** at `--forwarded-allow-ips` na naka-limita sa proxy. Kung
  wala ito, mali ang client IP sa rate limiting at hindi mamamarkahan ng `Secure`
  ang cookie.
- **Rate limit sa login.** Ang admin o host page ay abot na ng internet.
- **Isara pagkatapos.** Ang quick tunnel ay namamatay kasama ng process, pero ang
  `.env` ay nananatili sa lumang URL — ibalik sa LAN address bago maglaro muli.
- Kapag totoong deploy na: Postgres, Alembic, secrets manager, at HTTPS na hindi
  nakadepende sa isang terminal window.

Huwag mag-alok ng tunnel nang hindi sinasabi na ilalagay nito ang app sa
internet. Desisyon ng user iyon, hindi default.

## 8. Observability at operations

- **structlog** — JSON logging na may correlation id. Mahalaga sa dispute
  investigation. Huwag i-log ang nonce, PII, o token.
- **OpenTelemetry** + `opentelemetry-instrumentation-fastapi` — tracing kapag
  maraming service na.
- **Sentry** — error tracking. I-configure ang PII scrubbing bago i-enable.
- **Prometheus** (`prometheus-fastapi-instrumentator`) — metrics: pairing
  created/claimed/expired, claim denial by reason, cards issued kada minuto. Ang
  denial-by-reason ay maagang senyales ng abuse.
- **Health at readiness endpoints** na hiwalay: ang readiness ay dapat
  sumusuri ng DB at Redis.

## 9. Testing beyond unit tests

- **Playwright** — E2E ng kiosk at phone flow, kasama ang mobile viewport.
  Kaya nitong i-emulate ang pag-scan sa pamamagitan ng direktang pagbisita sa
  pair URL.
- **Locust o k6** — load test ng WebSocket fan-out. Ito ang unang masisira sa
  linked jackpot na maraming branch.
- **Hypothesis** — property-based testing ng card generation at win detection.
  Magaling ito sa paghanap ng edge case sa pattern bitmask.
- **schemathesis** — fuzzing ng API base sa OpenAPI schema na binibigay na ng
  FastAPI.

## 10. Packaging at deployment

- **Docker** multi-stage build na may `uv` para sa mabilis at reproducible na
  install. Non-root user. Naka-pin na base image digest.
- **Reverse proxy**: Caddy (automatic HTTPS, madali) o nginx (mas kilala ng PH
  ops teams). Kailangan ng WebSocket upgrade config sa dalawa.
- **CI**: GitHub Actions na nagpapatakbo ng `ruff check`, `ruff format --check`,
  `mypy`, at `pytest`, plus `uv lock --check` para hindi mag-drift ang lock file.
- **PH data residency** — bago pumili ng hosting, i-verify kung hinihingi ng
  PAGCOR na nasa Pilipinas ang gaming system at ang audit data, at kung kailangan
  ng regulator access sa environment. Ito ay hosting decision na mahirap baligtarin,
  kaya i-check bago mag-commit sa isang cloud region. Huwag ipagpalagay na okay
  ang offshore region.
- **Secrets management**: environment variables sa maliit na setup, pero
  AWS Secrets Manager, Azure Key Vault, o HashiCorp Vault kapag may team na.
  Huwag kailanman sa repo.

## 11. Mga bagay na sadyang wala pa

Kapag nag-implement ng mga ito, sundin ang guardrails sa `bingo-philippines.md`:

- Draw engine at live caller (round lifecycle, ball sequence, at draw audit)
- Claim verification at payout ledger
- Wallet at transaction history na integer centavos
- Responsible gaming controls: deposit limit, session reminder, self-exclusion UI
- AML threshold monitoring at AMLC reporting hook
- Multi-branch linked jackpot
- RNG certification package para sa third-party lab
