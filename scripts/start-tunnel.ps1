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

.PARAMETER Http2
    Para sa cloudflared: pilitin ang TCP 443 kaysa QUIC (UDP 7844). Hinaharangan
    ng maraming router ang QUIC, at ang resulta ay error 1033.

.PARAMETER Provider
    `cloudflared` (default) o `ngrok`.

    Ang ngrok ay mas maaasahan sa dalawang paraan: TCP 443 lang ang gamit niya
    kaya wala ang QUIC na problema, at may local API siya sa port 4040 kung saan
    direktang nakukuha ang public URL — hindi na kailangang maghalungkat sa log.
    Kailangan lang ng libreng account para sa authtoken.

.EXAMPLE
    .\scripts\start-tunnel.ps1 -StartServer

.EXAMPLE
    # Kapag ayaw gumana ang cloudflared
    .\scripts\start-tunnel.ps1 -StartServer -Provider ngrok
#>
[CmdletBinding()]
param(
    [int]$Port = 8000,
    [switch]$StartServer,
    [switch]$Http2,
    [ValidateSet('cloudflared', 'ngrok')]
    [string]$Provider = 'cloudflared'
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

# Ang PowerShell 5.1 ay minsan nagne-negotiate ng TLS 1.0 at tinatanggihan iyon
# ng Cloudflare. Itinatakda natin nang tahasan para hindi maling dahilan ang
# ipinapakita ng probe.
try {
    [Net.ServicePointManager]::SecurityProtocol =
        [Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls11
} catch {
    Write-Verbose 'Hindi maitakda ang TLS version; ipagpapatuloy sa default.'
}

function Test-ServerUp {
    try {
        $null = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/healthz" -UseBasicParsing -TimeoutSec 3
        return $true
    } catch {
        return $false
    }
}

function Test-PublicUrl([string]$Url) {
    <#
        Ibinabalik ang isang object na may Ok at Detail. Ang Detail ang totoong
        dahilan — dati ay itinatago ito at listahan ng hula ang ipinapakita.
    #>
    $host_ = ([Uri]$Url).Host

    # 1. DNS. Ang *.trycloudflare.com ay hinaharangan ng ilang ISP, router, at
    #    security suite dahil abusado ito sa phishing.
    try {
        $null = [Net.Dns]::GetHostEntry($host_)
    } catch {
        return [pscustomobject]@{
            Ok     = $false
            Detail = "Hindi ma-resolve ang $host_ mula sa makinang ito. Malamang " +
                     'hinaharangan ng DNS, router, o antivirus ang domain na iyon. ' +
                     'Baka gumagana pa rin ito sa phone na naka-mobile data. Kung ' +
                     'cloudflared ito, subukan ang -Provider ngrok.'
        }
    }

    # 2. HTTPS via PowerShell, na dumadaan sa system proxy.
    try {
        $probe = Invoke-WebRequest -Uri "$Url/healthz" -UseBasicParsing -TimeoutSec 10
        if ($probe.Content -match '"ok"') {
            return [pscustomobject]@{ Ok = $true; Detail = 'Sumagot ang public URL.' }
        }
        return [pscustomobject]@{
            Ok     = $false
            Detail = "Sumagot pero hindi healthz ang laman: $($probe.StatusCode)"
        }
    } catch {
        $psError = $_.Exception.Message
    }

    # 3. Pangalawang opinyon gamit ang curl.exe, na HINDI dumadaan sa system
    #    proxy. Kapag ito ang pumasa, ang PowerShell o ang proxy ang problema
    #    at hindi ang tunnel.
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        $code = & $curl.Source -s -o NUL -w '%{http_code}' --max-time 10 "$Url/healthz" 2>&1
        if ($code -eq '200') {
            return [pscustomobject]@{
                Ok     = $true
                Detail = 'Bumagsak ang PowerShell probe pero pumasa ang curl. ' +
                         'Gumagana ang tunnel; ang PowerShell o proxy ang may isyu.'
            }
        }
        return [pscustomobject]@{
            Ok     = $false
            Detail = "PowerShell: $psError | curl http_code: $code"
        }
    }

    return [pscustomobject]@{ Ok = $false; Detail = "PowerShell: $psError" }
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

$binaryName = if ($Provider -eq 'ngrok') { 'ngrok' } else { 'cloudflared' }
$binary = Get-Command $binaryName -ErrorAction SilentlyContinue

if (-not $binary) {
    Write-Warn "Walang $binaryName na naka-install."
    if ($Provider -eq 'ngrok') {
        Write-Host @"
    1. I-install:
         winget install --id Ngrok.Ngrok

    2. Gumawa ng libreng account sa https://dashboard.ngrok.com/signup
       tapos kopyahin ang authtoken mula sa
       https://dashboard.ngrok.com/get-started/your-authtoken

    3. Isagawa isang beses:
         ngrok config add-authtoken <ang-token-mo>

    4. Patakbuhin muli:
         .\scripts\start-tunnel.ps1 -StartServer -Provider ngrok
"@ -ForegroundColor Gray
    } else {
        Write-Host @"
    I-install ito sa isa sa mga paraang ito:

      winget install --id Cloudflare.cloudflared

    O i-download ang cloudflared-windows-amd64.exe mula sa
    https://github.com/cloudflare/cloudflared/releases at ilagay sa PATH.

    Kung paulit-ulit na bumibigo ang cloudflared, subukan ang ngrok:
      .\scripts\start-tunnel.ps1 -StartServer -Provider ngrok
"@ -ForegroundColor Gray
    }
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

if ($Provider -eq 'ngrok') {
    $tunnelArgs = @('http', "$Port", '--log', 'stdout')
} else {
    $tunnelArgs = @('tunnel', '--no-autoupdate', '--url', "http://127.0.0.1:$Port")
    if ($Http2) {
        # Ang default ng cloudflared ay QUIC sa UDP 7844. Hinaharangan iyon ng
        # maraming home router at ISP, at ang resulta ay tunnel na mukhang bukas
        # pero hindi naghahatid. Ang http2 ay dumadaan sa TCP 443.
        $tunnelArgs += @('--protocol', 'http2')
        Write-Note 'Gumagamit ng http2 protocol (TCP 443) kaysa QUIC.'
    }
}

$logFile = Join-Path ([System.IO.Path]::GetTempPath()) "bingobanano-tunnel-$PID.log"
$process = Start-Process -FilePath $binary.Source -ArgumentList $tunnelArgs `
    -RedirectStandardError $logFile -RedirectStandardOutput "$logFile.out" `
    -NoNewWindow -PassThru

function Get-NgrokUrl {
    <#
        Ang ngrok ay may local API sa 4040. Mas maaasahan itong pagkuhanan ng
        URL kaysa maghalungkat sa log output.
    #>
    try {
        $api = Invoke-RestMethod -Uri 'http://127.0.0.1:4040/api/tunnels' -TimeoutSec 3
        $https = $api.tunnels | Where-Object { $_.public_url -like 'https://*' } | Select-Object -First 1
        if ($https) { return $https.public_url }
    } catch {
        return $null
    }
    return $null
}

function Get-CloudflaredUrl([string[]]$Paths) {
    foreach ($path in $Paths) {
        if (-not (Test-Path $path)) { continue }
        $match = Select-String -Path $path -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' `
            -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($match) { return $match.Matches[0].Value }
    }
    return $null
}

$publicUrl = $null
$deadline = (Get-Date).AddSeconds(45)

try {
    while ((Get-Date) -lt $deadline -and -not $publicUrl) {
        Start-Sleep -Milliseconds 500
        if ($process.HasExited) { break }
        $publicUrl = if ($Provider -eq 'ngrok') {
            Get-NgrokUrl
        } else {
            Get-CloudflaredUrl @($logFile, "$logFile.out")
        }
    }

    if (-not $publicUrl) {
        Write-Warn "Hindi nakuha ang tunnel URL mula sa $binaryName sa loob ng 45 segundo."
        foreach ($path in @($logFile, "$logFile.out")) {
            if (Test-Path $path) {
                Get-Content $path -Tail 20 -ErrorAction SilentlyContinue |
                    ForEach-Object { Write-Note $_ }
            }
        }
        if ($Provider -eq 'ngrok') {
            Write-Note 'Kung may sinasabing authentication: ngrok config add-authtoken <token>'
        } else {
            Write-Note 'Subukan ang ngrok: .\scripts\start-tunnel.ps1 -StartServer -Provider ngrok'
        }
        if (-not $process.HasExited) { $process | Stop-Process -Force }
        exit 1
    }

    Write-Step 'Bukas na ang tunnel'
    Write-Good "Public URL: $publicUrl"
    if ($Provider -eq 'ngrok') {
        Write-Note 'ngrok dashboard: http://127.0.0.1:4040 (ipinapakita ang bawat request)'
    }

    # --- 4. Subukan mismo ang public URL ----------------------------------

    Write-Step 'Sinusubukan ang public URL'
    $result = $null
    for ($i = 0; $i -lt 6; $i++) {
        $result = Test-PublicUrl $publicUrl
        if ($result.Ok) { break }
        Start-Sleep -Seconds 3
    }

    if ($result.Ok) {
        Write-Good $result.Detail
        Write-Good 'Puwede nang mag-scan ang mga bisita.'
    } else {
        Write-Warn 'Hindi kumpirmado ang public URL mula sa makinang ito.'
        Write-Note $result.Detail
        Write-Host @"

    MAHALAGA: hindi ito nangangahulugang sira ang tunnel. Ang test na ito ay
    mula sa makinang ito, at maraming bagay ang puwedeng humarang lokal —
    DNS filtering ng trycloudflare.com, corporate proxy, antivirus, o TLS
    setting ng PowerShell.

    Bago mag-conclude, SUBUKAN ANG LINK SA PHONE MO:

        $publicUrl/healthz

    Kung {"status":"ok"} ang lumabas doon, gumagana ang tunnel at puwede ka
    nang maglaro — probe lang ang bigo. Kung error 1033 sa phone, doon lang
    talagang patay ang tunnel.

    Kung 1033 talaga, dalawang bagay ang subukan, sa ganitong pagkakasunod:

      1. QUIC ang default ng cloudflared sa UDP 7844, at hinaharangan iyon ng
         maraming router. Pilitin ang TCP 443:
             Ctrl+C, tapos: .\scripts\start-tunnel.ps1 -StartServer -Http2

      2. Kung bumigo pa rin, magpalit ng provider. Ang ngrok ay TCP 443 lang at
         may local dashboard sa http://127.0.0.1:4040 na nagpapakita ng bawat
         request, kaya malinaw agad kung may umaabot:
             .\scripts\start-tunnel.ps1 -StartServer -Provider ngrok

    Huling 15 linya ng $binaryName log:
"@ -ForegroundColor Yellow
        foreach ($path in @($logFile, "$logFile.out")) {
            if (Test-Path $path) {
                Get-Content $path -Tail 15 -ErrorAction SilentlyContinue |
                    ForEach-Object { Write-Note $_ }
            }
        }
    }

    Write-Host @"

    HOST LOBBY : $publicUrl/operator
    HEALTH TEST: $publicUrl/healthz

    Buksan ang HOST LOBBY sa browser. Iyon din ang mapupunta sa QR — kinukuha
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
