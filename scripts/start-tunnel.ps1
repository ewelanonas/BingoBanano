<#
.SYNOPSIS
    Bumuo ng public HTTPS link para sa BingoBanano gamit ang Cloudflare Tunnel.

.DESCRIPTION
    Kailangan ito kapag ang mga bisita ay nasa mobile data o sa ibang network.
    Sa LAN-only na setup, ang QR ay may `192.168.x.x` na hindi nila maaabot.

    Ang tunnel ay gumagawa ng outbound connection papunta sa Cloudflare at
    nagbibigay ng `https://<random>.trycloudflare.com` na URL. Walang inbound
    port na bukas, kaya hindi na kailangan ng firewall rule.

    Ang script ay:
      1. Kinukuha ang tunnel URL
      2. Isinusulat ito sa .env bilang BINGO_PUBLIC_BASE_URL
      3. Pinapanatili ang tunnel na bukas hanggang Ctrl+C

    SEGURIDAD: kapag tumakbo ito, ang laro mo ay abot ng kahit sino sa internet
    na may URL. Kasama diyan ang /operator na login page. Dahil dito:
      - I-bind ang uvicorn sa 127.0.0.1 LAMANG, hindi sa 0.0.0.0
      - Patakbuhin ang uvicorn nang may --proxy-headers
      - Isara ang tunnel pagkatapos ng party
    Ipinapakita ng script ang tamang command sa dulo.

.PARAMETER Port
    Ang local port ng uvicorn. Default 8000.

.EXAMPLE
    .\scripts\start-tunnel.ps1
#>
[CmdletBinding()]
param(
    [int]$Port = 8000
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

function Write-Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Write-Note($text) { Write-Host "    $text" -ForegroundColor DarkGray }
function Write-Warn($text) { Write-Host "    $text" -ForegroundColor Yellow }

# --- 1. cloudflared -------------------------------------------------------

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
    Kunin ang https URL na ipinapakita nito, ilagay sa .env bilang
    BINGO_PUBLIC_BASE_URL, tapos i-restart ang server.
"@ -ForegroundColor Gray
    exit 1
}

Write-Step "Binubuksan ang tunnel papunta sa http://127.0.0.1:$Port"
Write-Note 'Ang unang koneksyon ay tumatagal ng ilang segundo.'

# --- 2. Simulan at hanapin ang URL ---------------------------------------

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

    # --- 3. Isulat sa .env ------------------------------------------------

    $envPath = Join-Path $repo '.env'
    if (-not (Test-Path $envPath)) {
        Write-Warn 'Walang .env. Patakbuhin muna ang .\scripts\setup-dev.ps1'
        if (-not $process.HasExited) { $process | Stop-Process -Force }
        exit 1
    }

    $lines = Get-Content $envPath
    $previous = ($lines | Select-String -Pattern '^BINGO_PUBLIC_BASE_URL=(.*)$').Matches.Groups[1].Value
    if ($lines -match '^BINGO_PUBLIC_BASE_URL=') {
        $lines = $lines -replace '^BINGO_PUBLIC_BASE_URL=.*$', "BINGO_PUBLIC_BASE_URL=$publicUrl"
    } else {
        $lines += "BINGO_PUBLIC_BASE_URL=$publicUrl"
    }
    [System.IO.File]::WriteAllLines($envPath, $lines, (New-Object System.Text.UTF8Encoding($false)))

    Write-Step 'Bukas na ang tunnel'
    Write-Host "    Public URL : $publicUrl" -ForegroundColor Green
    Write-Note "Dating base URL: $previous"
    Write-Note 'Naisulat na sa .env.'

    Write-Host @"

    SUSUNOD, sa ibang PowerShell window, i-restart ang server:

      .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port $Port --proxy-headers --forwarded-allow-ips="127.0.0.1"

    Bakit 127.0.0.1 at hindi 0.0.0.0: ang tunnel lang ang dapat na daan papasok.
    Kapag naka-bind sa 0.0.0.0 habang may tunnel, dalawa ang pintuan at
    mapepeke ang client IP sa rate limiting.

    Tapos buksan ang host lobby sa: $publicUrl/operator

    TANDAAN: abot ng internet ang laro habang bukas ito. Isara gamit ang Ctrl+C
    pagkatapos ng party, at itakbo muli ang setup script para maibalik ang LAN
    na base URL.

"@ -ForegroundColor Gray

    Write-Note 'Ctrl+C para isara ang tunnel.'
    $process.WaitForExit()
} finally {
    if ($process -and -not $process.HasExited) {
        Write-Step 'Isinasara ang tunnel'
        $process | Stop-Process -Force
    }
    Remove-Item -Force $logFile, "$logFile.out" -ErrorAction SilentlyContinue
    Write-Note 'Sarado na ang tunnel. Hindi na abot ng internet ang laro.'
    Write-Note 'Ang .env ay nasa tunnel URL pa rin — palitan bago maglaro sa LAN.'
}
