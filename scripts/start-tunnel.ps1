<#
.SYNOPSIS
    Bumuo ng public HTTPS link para sa BingoBanano gamit ang Cloudflare Tunnel.

.DESCRIPTION
    Kailangan ito kapag ang mga bisita ay nasa mobile data o sa ibang network.
    Sa LAN-only na setup, ang QR ay may `192.168.x.x` na hindi nila maaabot.

    Ang tunnel ay gumagawa ng outbound connection papunta sa Cloudflare at
    nagbibigay ng `https://<random>.trycloudflare.com` na URL. Walang inbound
    port na bukas, kaya hindi na kailangan ng firewall rule.

    MAHALAGA: bagong URL ang nakukuha kada takbo ng script. Kapag nasara ang
    tunnel, patay na ang lumang URL at lahat ng QR na binuo gamit iyon. Kaya:
      - Panatilihing bukas ang window na ito habang naglalaro
      - Pagkatapos mag-restart ng tunnel, i-restart din ang server at bumuo
        ng BAGONG QR. Ang mga lumang QR ay mag-e-error 1033.

    Ang script ay:
      1. Tinitingnan kung tumatakbo na ang server
      2. Kinukuha ang tunnel URL
      3. Isinusulat ito sa .env bilang BINGO_PUBLIC_BASE_URL
      4. Sinusubukan mismo ang public URL para malaman agad kung gumagana
      5. Ibinabalik ang dating base URL kapag nasara ang tunnel

    SEGURIDAD: habang bukas ito, ang laro mo ay abot ng kahit sino sa internet
    na may URL, kasama ang /operator login page. I-bind ang uvicorn sa
    127.0.0.1 lamang at gamitin ang --proxy-headers. Isara pagkatapos ng party.

.PARAMETER Port
    Ang local port ng uvicorn. Default 8000.

.PARAMETER StartServer
    Awtomatikong buksan ang server sa bagong window, may tamang flags na.
    Iniiwasan nito ang pinakamadaling pagkakamalian: pagpatay sa tunnel dahil
    sa pag-type ng uvicorn command sa parehong window.

.EXAMPLE
    .\scripts\start-tunnel.ps1 -StartServer

.EXAMPLE
    # Kung mano-mano mong pinapatakbo ang server sa ibang window
    .\scripts\start-tunnel.ps1
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

$envPath = Join-Path $repo '.env'
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

# --- 1. Mga kailangan ------------------------------------------------------

if (-not (Test-Path $envPath)) {
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
    Kunin ang https URL na ipinapakita nito, ilagay sa .env bilang
    BINGO_PUBLIC_BASE_URL, tapos i-restart ang server.
"@ -ForegroundColor Gray
    exit 1
}

# --- 2. Ang server ---------------------------------------------------------

Write-Step 'Tinitingnan ang local server'
$serverProcess = $null

if (Test-ServerUp) {
    Write-Good "Tumatakbo na sa http://127.0.0.1:$Port"
    Write-Note 'Kailangan nitong naka-bind sa 127.0.0.1 at may --proxy-headers.'
    Write-Note 'Kapag hindi, i-restart ito matapos makuha ang tunnel URL.'
} elseif ($StartServer) {
    if (-not (Test-Path $python)) {
        Write-Warn "Walang virtualenv sa $python. Patakbuhin ang .\scripts\setup-dev.ps1"
        exit 1
    }
    Write-Note 'Binubuksan ang server sa bagong window...'
    $serverProcess = Start-Process -FilePath $python -ArgumentList $serverArgs -PassThru
    for ($i = 0; $i -lt 20 -and -not (Test-ServerUp); $i++) { Start-Sleep -Milliseconds 500 }
    if (Test-ServerUp) {
        Write-Good 'Tumatakbo na ang server.'
    } else {
        Write-Warn 'Hindi umandar ang server. Tingnan ang bagong window para sa error.'
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
$previous = $null
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

    # --- 4. Isulat sa .env -------------------------------------------------

    $lines = Get-Content $envPath
    $previousMatch = $lines | Select-String -Pattern '^BINGO_PUBLIC_BASE_URL=(.*)$'
    $previous = if ($previousMatch) { $previousMatch.Matches[0].Groups[1].Value } else { $null }

    if ($lines -match '^BINGO_PUBLIC_BASE_URL=') {
        $lines = $lines -replace '^BINGO_PUBLIC_BASE_URL=.*$', "BINGO_PUBLIC_BASE_URL=$publicUrl"
    } else {
        $lines += "BINGO_PUBLIC_BASE_URL=$publicUrl"
    }
    [System.IO.File]::WriteAllLines($envPath, $lines, (New-Object System.Text.UTF8Encoding($false)))

    Write-Step 'Bukas na ang tunnel'
    Write-Good "Public URL: $publicUrl"
    Write-Note "Dating base URL: $previous (ibabalik kapag nasara ang tunnel)"

    # --- 5. Kailangan ng restart ang server para makita ang bagong URL -----

    Write-Step 'Ini-restart ang server para gamitin ang bagong URL'
    if ($serverProcess -and -not $serverProcess.HasExited) {
        $serverProcess | Stop-Process -Force
        Start-Sleep -Seconds 1
        $serverProcess = Start-Process -FilePath $python -ArgumentList $serverArgs -PassThru
        for ($i = 0; $i -lt 20 -and -not (Test-ServerUp); $i++) { Start-Sleep -Milliseconds 500 }
        Write-Good 'Naka-restart na, hawak na ang tunnel URL.'
    } else {
        Write-Warn 'I-restart ang server mo NGAYON sa kanyang window (Ctrl+C tapos patakbuhin muli).'
        Write-Note 'Hangga''t hindi, LAN URL pa ang ilalagay sa QR at hindi ito maabot ng bisita.'
        Read-Host '    Pindutin ang Enter kapag naka-restart na'
    }

    # --- 6. Subukan ang public URL mismo -----------------------------------

    Write-Step 'Sinusubukan ang public URL'
    $healthy = $false
    for ($i = 0; $i -lt 10 -and -not $healthy; $i++) {
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
        Write-Warn 'Hindi sumagot ang public URL. Karaniwang dahilan:'
        Write-Note '  - Hindi pa tapos ang tunnel; subukan muli sa loob ng ilang segundo'
        Write-Note '  - Naka-bind ang server sa ibang address, dapat 127.0.0.1'
        Write-Note "  - May firewall o proxy na humaharang sa cloudflared"
    }

    Write-Host @"

    HOST LOBBY : $publicUrl/operator

    Tandaan:
      - Panatilihing bukas ang window na ito. Kapag sarado, patay ang URL at
        lahat ng QR na binuo gamit iyon (error 1033 ang lalabas sa phone).
      - Kada takbo ng script ay bagong URL. Pagkatapos mag-restart, bumuo ng
        BAGONG QR — hindi na gagana ang mga luma.
      - Abot ng internet ang laro habang bukas ito.

"@ -ForegroundColor Gray

    Write-Note 'Ctrl+C para isara ang tunnel.'
    $process.WaitForExit()
} finally {
    if ($process -and -not $process.HasExited) {
        Write-Step 'Isinasara ang tunnel'
        $process | Stop-Process -Force
    }

    # Ibalik ang dating base URL. Kung hindi, may patay na tunnel URL na
    # matitira sa .env at bawat QR na susunod ay magbibigay ng error 1033.
    if ($previous) {
        $lines = Get-Content $envPath
        $lines = $lines -replace '^BINGO_PUBLIC_BASE_URL=.*$', "BINGO_PUBLIC_BASE_URL=$previous"
        [System.IO.File]::WriteAllLines($envPath, $lines, (New-Object System.Text.UTF8Encoding($false)))
        Write-Note "Naibalik na sa .env ang dating base URL: $previous"
        Write-Note 'I-restart ang server para tumugma.'
    }

    if ($serverProcess -and -not $serverProcess.HasExited) {
        Write-Note 'Isinasara din ang server na binuksan ng script.'
        $serverProcess | Stop-Process -Force
    }

    Remove-Item -Force $logFile, "$logFile.out" -ErrorAction SilentlyContinue
    Write-Note 'Sarado na ang tunnel. Hindi na abot ng internet ang laro.'
}
