---
inclusion: always
---

# Domain Knowledge: Bingo sa Pilipinas

Context para sa lahat ng gawain sa repo na ito. Ang BingoBanano ay isang bingo
product na Pilipinas ang market, kaya ang mga default ay PH-first: PHP na pera,
Asia/Manila na oras, Taglish na UI copy, at PAGCOR na regulator.

## 1. Core game mechanics (75-ball)

Ang standard sa Pilipinas ay **75-ball bingo**. Gamitin ito bilang default kung
walang ibang sinabi.

- Card: 5x5 grid, may **FREE** space sa gitna (center cell, index 12).
- Column ranges: `B 1–15`, `I 16–30`, `N 31–45`, `G 46–60`, `O 61–75`.
- Walang duplicate na number sa loob ng isang card.
- Draw: 1–75, walang replacement, isang bola kada call.
- Panalo kapag na-cover ang **declared pattern** para sa round na iyon. Ang
  pattern ay dineklara BAGO magsimula ang draws.

Karaniwang patterns na sinusuportahan:

| Pattern | Description |
|---|---|
| Straight line | horizontal, vertical, o diagonal |
| Four corners | apat na sulok |
| Blackout / Coverall | buong card |
| Letter X | dalawang diagonal |
| Postage stamp | 2x2 block sa sulok |
| Kite, Arrow, Cross | shape patterns, mas mataas na prize |
| Double/Triple line | dalawa o tatlong linya |

Implementation note: i-model ang pattern bilang bitmask (25 bits) para O(1) ang
win check. Ang FREE space ay pre-marked (bit 12 = 1) sa lahat ng card.

Ang **90-ball** (UK-style, 9x3 grid, 1-line/2-line/full house) ay hindi
mainstream sa PH bingo halls. Huwag itong idagdag maliban kung hiniling.

## 2. PH-specific formats

Iba't ibang product ang tinatawag na "bingo" sa Pilipinas. Alamin kung alin ang
ginagawa bago mag-design:

- **Traditional Bingo** — live caller, papel o tab card, physical `tambiolo`
  (drum) o electronic ball draw sa bingo hall. Session-based: may schedule ng
  games, may declared pattern kada game, may prize board.
- **Electronic Bingo (e-Bingo)** — terminal-based sa bingo halls at Casino
  Filipino. Mabilis, single-player pacing, may mga slot-like presentation at
  progressive jackpots. May sariling PAGCOR framework.
- **Rapid / Instant Bingo** — pinaikling round, minimal social element.
- **Linked / networked bingo** — pinagsasamang player pool sa maraming branch
  para sa malaking jackpot. Precedent: "Shower of Millions Bingo" (2013,
  PAGCOR, e-linked sa mga Casino Filipino branch, ₱12.4M total prize).
- **Charity / fiesta bingo** — ang bingo ay dumating sa PH via mga simbahan at
  misyonero, at malakas pa rin bilang fundraising tool ng mga parokya at civic
  group. Karaniwan sa `peria` tuwing fiesta.
- **Mall bingo halls** — malaking bahagi ng retail gaming footprint (halimbawa,
  mga bingo hall sa loob ng malls sa Metro Manila). Mataas ang foot traffic,
  matatanda at regular ang core players, social ang experience.

## 3. Player-facing vocabulary (Taglish)

Gamitin ang mga terminong pamilyar sa Pinoy players sa UI copy:

- `bola` — ball; `tambiolo` — draw drum; `kard` — card
- `daub` / `blotter` / `tapal` — marking ng number
- `BINGO!` — ang claim call; `verify` — ang confirmation step ng floor staff
- `isa na lang` — one number away (good hook para sa near-win UX)
- `pattern`, `jackpot`, `session`, `payout` — panatilihing English, iyon ang
  aktuwal na gamit sa halls

Rule: mixed Taglish ang UI, hindi purong formal Filipino. "I-claim ang panalo"
hindi "Angkinin ang tagumpay."

## 4. Regulatory guardrails (hindi negotiable)

Ang PAGCOR ang nagre-regulate ng lahat ng games of chance sa Philippine
territory, kasama ang bingo. Ang mga sumusunod ay dapat maging default sa disenyo:

- **Licensing model** — magkaibang role ang **Operator** (licensed na
  nagpapatakbo ng gaming site) at **Service Provider** (accredited na nagbibigay
  ng gaming system at content). Kung ang BingoBanano ay platform, malamang
  Service Provider ang role: kailangan ng accreditation, at ang Operator ang may
  performance cash bond kada gaming activity.
- **Age gate** — under PD 1869 as amended (RA 9487), bawal ang minors sa
  PAGCOR-licensed gaming. I-enforce ang **21+** at i-verify, huwag lang
  i-checkbox. Bawal din ang mga government official na konektado sa gaming
  operations at mga taong nasa exclusion list.
- **Responsible Gaming** — kailangan ng self-exclusion, family exclusion, at
  licensee-initiated exclusion; deposit at session limits; helpline links; at
  advertising na hindi nagta-target ng minors o nangangako ng panalo. May Table
  of Penalties ang PAGCOR RG Code of Practice, kaya feature requirement ito at
  hindi nice-to-have.
- **AML** — designated na covered persons ang casinos sa ilalim ng RA 10927
  (amendment sa AMLA). Ang single casino transaction na lumalampas sa
  **₱5,000,000** ay covered transaction na dapat i-report sa AMLC. Kailangan ng
  customer due diligence, transaction trail, at threshold monitoring.
- **Gaming Employment License** — ang staff ng bingo at egames sites ay dapat
  may valid GEL. Kung may staff/admin roles ang system, isama ang license
  reference sa employee record.
- **Data Privacy** — RA 10173 (Data Privacy Act). Minimize ang PII, i-encrypt at
  rest, huwag i-log ang unmasked ID numbers o KYC documents.
- **RNG certification** — ang draw engine ay dapat testable at auditable ng
  third-party lab. Isolate ang RNG sa likod ng interface, i-log ang seed at
  bawat draw, at huwag isabit sa presentation logic.

Kapag may design decision na tumatama sa mga ito, sabihin ito nang tahasan sa
user bago magpatuloy. Huwag gumawa ng feature na nag-bypass ng age check,
exclusion list, o transaction logging.

## 5. Engineering conventions para sa domain

- **Server-authoritative** ang lahat: draws, card generation, at win
  verification. Hindi kailanman pinagkakatiwalaan ang client na magdeklara ng
  panalo. Ang client ay nagre-render lang ng state na galing sa server.
- **Money as integers** — centavos (`int64`), hindi float. Format sa display
  bilang `₱1,234.56`.
- **Timezone** — `Asia/Manila` (UTC+8, walang DST). Store sa UTC, display sa PH
  time. Ang session schedules ng bingo halls ay PH local time.
- **Audit trail** — bawat round: round id, pattern, seed/RNG record, draw
  sequence with timestamps, participating cards, claims, verification result,
  payouts. Append-only. Ito ang unang hahanapin ng regulator sa dispute.
- **Card uniqueness** — unique serial kada card kada session; i-persist ang card
  layout, huwag i-regenerate mula sa seed lang sa oras ng verification.
- **Idempotent claims at payouts** — dapat may idempotency key ang claim
  submission at ang payout, para safe sa retry at network drop.
- **Fail closed** — kapag nag-error ang RNG, exclusion check, o balance check,
  i-deny ang aksyon. Huwag mag-default sa "allow."
- **Domain naming** — `Ball`, `Draw`, `Card`, `Pattern`, `GameRound`, `Session`,
  `Claim`, `Payout`, `Player`, `Wallet`. Panatilihing consistent sa buong stack.

## 6. Payments at localization

- Currency: PHP lang bilang default. Symbol `₱`, 2 decimals.
- Karaniwang rails sa PH: GCash, Maya, InstaPay at PESONet (bank transfer),
  over-the-counter sa mga hall. I-abstract ang payment provider, huwag
  i-hardcode ang isa.
- Mobile-first at data-light. Marami sa players ay nasa mid-range Android at
  mobile data, kaya bantayan ang payload size at reconnect behavior.
- Mahalaga ang graceful reconnect: kung nadiskonekta ang player mid-round, ang
  card at auto-daub state ay dapat mabuo pa rin ng server.

## Sources

- [PAGCOR Regulatory site](https://www.pagcor.ph/regulatory/) — gaming
  regulation at licensing sa Philippine territory
- [Gaming Site Regulatory Manual for Bingo Games](https://www.pagcor.ph/regulatory/pdf/GSRM/Regulatory%20Manuals/Gaming%20Site%20Regulatory%20Manual%20for%20Bingo%20Games%20v3.0.pdf)
- [Regulatory Framework for Accreditation of Gaming System Service Providers (EG, e-Bingo, Sports Betting)](https://www.pagcor.ph/regulatory/pdf/announcements/amended-regulatory-framework-for-the-accreditation-of-gaming-system-service-providers-of-EGEBSB.pdf)
- [PAGCOR Responsible Gaming Code of Practice](https://www.pagcor.ph/regulatory/pdf/RG-Code-of-Practice-v6.pdf)
- [Guidelines for Grant of Gaming Employment License (bingo/egames)](https://pagcor.ph/docs/gel-bingo-egames-guidelines.pdf)
- [RA 9487 (PAGCOR charter amendment)](https://www.pagcor.ph/transparency/docs/ra-9487.pdf)
- [AMLC — RA 10927 casino covered transaction threshold](http://www.amlc.gov.ph/13-laws)
- [Bingo in the Philippines — Wikipedia](https://en.wikipedia.org/wiki/Bingo_in_the_Philippines)

Content was rephrased for compliance with licensing restrictions. Ang
regulatory details ay maaaring mag-update; i-verify sa pinakabagong PAGCOR
issuance bago mag-commit sa compliance-critical na design.
