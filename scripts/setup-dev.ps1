<#
.SYNOPSIS
    Bootstrap ng BingoBanano sa bagong Windows machine.

.DESCRIPTION
    Gumagawa ng virtualenv, nag-i-install ng pinned dependencies, at bumubuo ng
    .env na may generated secrets at ang tamang LAN IP para ma-scan ng phone ang
    QR code.

    Hindi ito nagpapalit ng existing .env. Kung may .env na, iiwan ito at
    ipapakita lang ang kasalukuyang setting.

.PARAMETER AddFirewallRule
    Magdagdag ng inbound allow rule para sa port 8000, limitado sa local subnet.
    Kailangan ng administrator. Kung wala ito, hindi maaabot ng phone ang server.

.PARAMETER Port
    Ang port na gagamitin. Default 8000.

.EXAMPLE
    .\scripts\setup-dev.ps1

.EXAMPLE
    # Sa administrator PowerShell, kasama ang firewall rule
    .\scripts\setup-dev.ps1 -AddFirewallRule
#>
[CmdletBinding()]
param(
    [switch]$AddFirewallRule,
    [int]$Port = 8000
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

function Write-Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Write-Note($text) { Write-Host "    $text" -ForegroundColor DarkGray }
function Write-Warn($text) { Write-Host "    $text" -ForegroundColor Yellow }

# --- 1. Ang LAN IP na makikita ng phone ------------------------------------

function Get-LanAddress {
    # Ang interface na may default gateway ang tunay na kumokonekta sa LAN.
    # Iniiwasan ang /32 (VPN adapter) at ang 169.254.x.x (link-local) dahil
    # hindi maaabot ng phone ang mga iyon.
    $candidates = Get-NetIPConfiguration |
        Where-Object { $_.IPv4DefaultGateway -and $_.IPv4Address } |
        ForEach-Object {
            foreach ($address in $_.IPv4Address) {
                if ($address.PrefixLength -lt 32 -and $address.IPAddress -notlike '169.254.*') {
                    [pscustomobject]@{
                        IPAddress = $address.IPAddress
                        Alias     = $_.InterfaceAlias
                        IsWifi    = $_.InterfaceAlias -match 'Wi-?Fi|Wireless'
                    }
                }
            }
        }

    if (-not $candidates) { return $null }
    # Mas gusto ang Wi-Fi: doon karaniwang nakakonekta ang phone.
    ($candidates | Sort-Object -Property @{ Expression = 'IsWifi'; Descending = $true } |
        Select-Object -First 1)
}

Write-Step 'Hinahanap ang LAN IP'
$lan = Get-LanAddress
if ($lan) {
    Write-Note "Napili: $($lan.IPAddress) ($($lan.Alias))"
    $baseUrl = "http://$($lan.IPAddress):$Port"
} else {
    Write-Warn 'Walang nakitang LAN address. Gagamit ng 127.0.0.1 pansamantala.'
    Write-Warn 'Hindi ma-scan ng phone ang QR habang localhost ang base URL.'
    $baseUrl = "http://127.0.0.1:$Port"
}

# --- 2. Virtualenv at dependencies ----------------------------------------

$uv = Get-Command uv -ErrorAction SilentlyContinue
$python = Join-Path $repo '.venv\Scripts\python.exe'

if ($uv) {
    Write-Step 'Ini-install ang dependencies gamit ang uv'
    & uv sync
} else {
    Write-Step 'Walang uv. Gagamit ng python -m venv at pip'
    Write-Note 'Mas mabilis ang uv: https://docs.astral.sh/uv/getting-started/installation/'
    if (-not (Test-Path $python)) { & python -m venv .venv }
    & $python -m pip install --quiet --upgrade pip
    # Ang uv.lock ang may exact pins; kapag walang uv, ang pyproject pins ang gamit.
    & $python -m pip install --quiet .
    & $python -m pip install --quiet pytest pytest-asyncio httpx ruff mypy
}

if (-not (Test-Path $python)) {
    throw "Hindi nagawa ang virtualenv sa $python"
}

# --- 3. .env --------------------------------------------------------------

$envPath = Join-Path $repo '.env'
if (Test-Path $envPath) {
    Write-Step 'May .env na, hindi ito ginagalaw'
    Select-String -Path $envPath -Pattern '^BINGO_PUBLIC_BASE_URL=' |
        ForEach-Object { Write-Note $_.Line }
    Write-Note "Kung mali na ang IP, palitan ito ng $baseUrl at i-restart ang server."
} else {
    Write-Step 'Bumubuo ng .env'
    $apiKey = & $python -c 'import secrets; print(secrets.token_urlsafe(32))'
    $content = @(
        "BINGO_OPERATOR_API_KEY=$apiKey",
        "BINGO_PUBLIC_BASE_URL=$baseUrl",
        'BINGO_DATABASE_URL=sqlite+aiosqlite:///./bingobanano.db',
        'BINGO_PAIRING_TTL_SECONDS=120',
        'BINGO_DEFAULT_CARD_COUNT=2',
        'BINGO_MAX_CARD_COUNT=12',
        'BINGO_PAIRING_RATE_LIMIT_PER_MINUTE=20',
        'BINGO_BINGO_RATE_LIMIT_PER_MINUTE=120',
        'BINGO_LOGIN_RATE_LIMIT_PER_MINUTE=10'
    )
    # Walang BOM: hindi mabasa ng ibang parser ang unang key kapag may BOM.
    [System.IO.File]::WriteAllLines($envPath, $content, (New-Object System.Text.UTF8Encoding($false)))
    Write-Note 'Nabuo na ang .env na may bagong secrets. Gitignored ito.'
}

# --- 4. Firewall ----------------------------------------------------------

$ruleName = "BingoBanano dev $Port"
$existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue

Write-Step 'Firewall'
if ($existing) {
    Write-Note "May rule na: $ruleName"
} elseif ($AddFirewallRule) {
    $isAdmin = ([Security.Principal.WindowsPrincipal] `
        [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

    if (-not $isAdmin) {
        Write-Warn 'Kailangan ng administrator para makapagdagdag ng firewall rule.'
        Write-Warn 'Patakbuhin muli ang script sa admin PowerShell.'
    } else {
        New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow `
            -Protocol TCP -LocalPort $Port -Profile Public, Private `
            -RemoteAddress LocalSubnet | Out-Null
        Write-Note "Naidagdag: $ruleName (local subnet lamang)"
    }
} else {
    Write-Warn "Walang firewall rule para sa port $Port."
    Write-Warn 'Kung wala ito, mag-ti-timeout ang phone kapag nag-scan ng QR.'
    Write-Warn 'Patakbuhin sa admin PowerShell: .\scripts\setup-dev.ps1 -AddFirewallRule'
}

# --- 5. Susunod na hakbang ------------------------------------------------

Write-Step 'Ready na'
Write-Host @"
    1. Patakbuhin ang server:
         .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port $Port

    2. Kopyahin ang operator key sa clipboard:
         (Select-String -Path .env -Pattern '^BINGO_OPERATOR_API_KEY=(.+)$').Matches.Groups[1].Value | Set-Clipboard

    3. Sa laptop, buksan ang http://127.0.0.1:$Port/operator at i-paste ang key.

    4. Gumawa ng round, pindutin ang "Bumuo ng QR", tapos i-scan ng camera app
       ng phone. Gamitin ang normal na camera, hindi in-app scanner.

    5. Test ng koneksyon mula sa phone browser:
         $baseUrl/healthz
       Dapat may {"status":"ok"}. Kung timeout, firewall o Wi-Fi client
       isolation ang problema.

    Ang setup na ito ay para sa mga bisitang kaparehong Wi-Fi. Kung ang mga
    bisita ay nasa mobile data o sa ibang network, kailangan ng public link:

         .\scripts\start-tunnel.ps1
"@ -ForegroundColor Gray
