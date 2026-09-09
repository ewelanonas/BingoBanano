<#
.SYNOPSIS
    Bumuo ng public HTTPS link para sa BingoBanano gamit ang Cloudflare Tunnel.

.DESCRIPTION
    Kailangan ito kapag ang mga bisita ay nasa mobile data o sa ibang network.
    Sa LAN-only na setup, ang QR ay may `192.168.x.x` na hindi nila maaabot.

    Ang tunnel ay gumagawa ng outbound connection papunta sa Cloudflare at
    nagbibigay ng `https://<random>.trycloudflare.com` na URL. Walang inbound
    port na bukas, kaya hindi na kailangan ng firewall rule.

    Hindi hinahawakan ng script na ito ang .env at hindi ito nagre-restart ng
    server. Kinukuha ng app ang base URL ng QR mula sa address na binuksan ng
    host, kaya sapat na ang buksan ang tunnel URL sa browser.

    MAHALAGA: bagong URL ang nakukuha kada takbo. Kapag nasara ang tunnel,
    patay na ang lumang URL at lahat ng QR na binuo gamit iyon — error 1033 ang
    lalabas sa phone. Panatilihing bukas ang window na ito habang naglalaro.

    SEGURIDAD: habang bukas ito, ang laro mo ay abot ng kahit sino sa internet
    na may URL, kasama ang /operator login page. I-bind ang uvicorn sa
    127.0.0.1 lamang at gamitin ang --proxy-headers. Isara pagkatapos ng party.

.PARAMETER Port
    Ang local port ng uvicorn. Default 8000.

.PARAMETER StartServer
    Buksan din ang server, may tamang flags na. Iniiwasan nito ang pinakamadaling
    pagkakamalian: pagpatay sa tunnel dahil sa pag-type ng uvicorn command sa
    parehong window.

.EXAMPLE
    .\scripts\start-tunnel.ps1 -StartServer
#>
[CmdletBinding()]
param(
    [int]$Port = 8000,
    [switch]$StartServer
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

function Write-Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Write-Note($text) { Write-Host "    $text" -ForegroundColor DarkGray }
function Write-Warn($text) { Write-Host "    $text" -ForegroundColor Yellow }
function Write-Good($text) { Write-Host "    $text" -ForegroundColor Green }

$python = Join-Path $repo '.venv\Scripts\python.exe'
$serverArgs = @(
    '-m', 'uvicorn', 'app.main:app',
    '--host', '127.0.0.1', '--port', $Port,
    '--proxy-headers', '--forwarded-allow-ips=127.0.0.1'
)

function Test-ServerUp {
    try {
        $null = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/healthz" -UseBasicParsing -TimeoutSec 3
        return $true
    } catch {
        return $false
    }
}

function Wait-ForServer([int]$Seconds = 15) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-ServerUp) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

# --- 1. Mga kailangan ------------------------------------------------------

if (-not (Test-Path (Join-Path $repo '.env'))) {
    Write-Warn 'Walang .env. Patakbuhin muna ang .\scripts\setup-dev.ps1'
    exit 1
}

$cloudflared = Get-Command cloudflared -ErrorAction SilentlyContinue
if (-not $cloudflared) {
    Write-Warn 'Walang cloudflared na naka-install.'
    Write-Host @"
    I-install ito sa isa sa mga paraang ito:

      winget install --id Cloudflare.cloudflared

    O i-download ang cloudflared-windows-amd64.exe mula sa
    https://github.com/cloudflare/cloudflared/releases at ilagay sa PATH.

    Alternatibo kung ayaw mo ng cloudflared: ngrok.
      winget install --id Ngrok.Ngrok
      ngrok http $Port
    Buksan ang https URL na ibibigay nito, tapos maglaro. Hindi na kailangang
    baguhin ang .env.
"@ -ForegroundColor Gray
    exit 1
}

# --- 2. Ang server ---------------------------------------------------------

Write-Step 'Tinitingnan ang local server'
$serverProcess = $null

if (Test-ServerUp) {
    Write-Good "Tumatakbo na sa http://127.0.0.1:$Port"
    Write-Note 'Siguraduhing may --proxy-headers ito, kung hindi ay mali ang'
    Write-Note 'client IP sa rate limiting at hindi Secure ang cookie.'
} elseif ($StartServer) {
    if (-not (Test-Path $python)) {
        Write-Warn "Walang virtualenv sa $python. Patakbuhin ang .\scripts\setup-dev.ps1"
        exit 1
    }
    Write-Note 'Binubuksan ang server sa bagong window...'
    $serverProcess = Start-Process -FilePath $python -ArgumentList $serverArgs `
        -WorkingDirectory $repo -PassThru

    if (Wait-ForServer) {
        Write-Good 'Sumasagot na ang server.'
    } else {
        Write-Warn 'Hindi umandar ang server sa loob ng 15 segundo.'
        Write-Note 'Tingnan ang bagong window para sa error. Karaniwan: naka-'
        Write-Note "gamit na ang port $Port ng ibang process."
        if ($serverProcess -and -not $serverProcess.HasExited) {
            $serverProcess | Stop-Process -Force
        }
        exit 1
    }
} else {
    Write-Warn "Walang server sa port $Port."
    Write-Host @"
    Dalawang pagpipilian:

      A. Hayaan ang script na buksan ito:
           .\scripts\start-tunnel.ps1 -StartServer

      B. Sa IBANG window, patakbuhin ito, tapos itakbo muli ang script:
           .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port $Port --proxy-headers --forwarded-allow-ips="127.0.0.1"

    Huwag i-type ang uvicorn command sa window na ito. Papatayin iyon ang tunnel.
"@ -ForegroundColor Gray
    exit 1
}

# --- 3. Buksan ang tunnel --------------------------------------------------

Write-Step "Binubuksan ang tunnel papunta sa http://127.0.0.1:$Port"
Write-Note 'Ang unang koneksyon ay tumatagal ng ilang segundo.'

$logFile = Join-Path ([System.IO.Path]::GetTempPath()) "bingobanano-tunnel-$PID.log"
$process = Start-Process -FilePath $cloudflared.Source `
    -ArgumentList 'tunnel', '--no-autoupdate', '--url', "http://127.0.0.1:$Port" `
    -RedirectStandardError $logFile -RedirectStandardOutput "$logFile.out" `
    -NoNewWindow -PassThru

$publicUrl = $null
$deadline = (Get-Date).AddSeconds(45)

try {
    while ((Get-Date) -lt $deadline -and -not $publicUrl) {
        Start-Sleep -Milliseconds 500
        if ($process.HasExited) { break }
        foreach ($path in @($logFile, "$logFile.out")) {
            if (-not (Test-Path $path)) { continue }
            $match = Select-String -Path $path -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' `
                -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($match) {
                $publicUrl = $match.Matches[0].Value
                break
            }
        }
    }

    if (-not $publicUrl) {
        Write-Warn 'Hindi nakuha ang tunnel URL sa loob ng 45 segundo.'
        if (Test-Path $logFile) { Get-Content $logFile -Tail 15 | ForEach-Object { Write-Note $_ } }
        if (-not $process.HasExited) { $process | Stop-Process -Force }
        exit 1
    }

    Write-Step 'Bukas na ang tunnel'
    Write-Good "Public URL: $publicUrl"

    # --- 4. Subukan mismo ang public URL ----------------------------------

    Write-Step 'Sinusubukan ang public URL'
    $healthy = $false
    for ($i = 0; $i -lt 12 -and -not $healthy; $i++) {
        try {
            $probe = Invoke-WebRequest -Uri "$publicUrl/healthz" -UseBasicParsing -TimeoutSec 8
            $healthy = $probe.Content -match '"ok"'
        } catch {
            Start-Sleep -Seconds 2
        }
    }

    if ($healthy) {
        Write-Good 'Sagot ang public URL. Puwede nang mag-scan ang mga bisita.'
    } else {
        Write-Warn 'Hindi sumagot ang public URL pagkatapos ng ~25 segundo.'
        Write-Note '  - Kung 1033 ang lumabas: hindi pa tapos ang tunnel, hintayin'
        Write-Note '  - Kung 502: hindi sumasagot ang server sa 127.0.0.1'
        Write-Note "  - Tingnan: Invoke-WebRequest http://127.0.0.1:$Port/healthz"
        Write-Note '  - Baka may firewall o proxy na humaharang sa cloudflared'
    }

    Write-Host @"

    HOST LOBBY : $publicUrl/operator

    Buksan ang link na iyon sa browser. Iyon din ang mapupunta sa QR — kinukuha
    ito ng app mula sa address na binuksan mo, kaya walang .env na babaguhin at
    walang restart na kailangan.

    Tandaan:
      - Panatilihing bukas ang window na ito. Kapag sarado, patay ang URL at
        lahat ng QR na binuo gamit iyon (error 1033 sa phone).
      - Kada takbo ng script ay bagong URL. Bumuo ng bagong QR pagkatapos.
      - Abot ng internet ang laro habang bukas ito.

"@ -ForegroundColor Gray

    Write-Note 'Ctrl+C para isara ang tunnel.'
    $process.WaitForExit()
} finally {
    if ($process -and -not $process.HasExited) {
        Write-Step 'Isinasara ang tunnel'
        $process | Stop-Process -Force
    }
    if ($serverProcess -and -not $serverProcess.HasExited) {
        Write-Note 'Isinasara din ang server na binuksan ng script.'
        $serverProcess | Stop-Process -Force
    }
    Remove-Item -Force $logFile, "$logFile.out" -ErrorAction SilentlyContinue
    Write-Note 'Sarado na ang tunnel. Hindi na abot ng internet ang laro.'
}
