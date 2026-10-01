<#
.SYNOPSIS
  Expose the whole stack (SPA + API + mock BBB room) behind one HTTPS origin
  so camera and WebRTC work from phones / other PCs — and so you can verify
  the room connects with STUN only (no TURN server).

  Default: starts a free cloudflared quick tunnel (no account needed).
  The tunnel URL changes on every start; this script rewrites it into the
  config for you and restarts the api container.

  Usage (run from the repo root, in PowerShell):
    .\scripts\start-tunnel.ps1                          # start cloudflared + apply config
    .\scripts\start-tunnel.ps1 -Url https://your-host   # use your own tunnel (ngrok etc.)
    .\scripts\start-tunnel.ps1 -Stop                    # stop tunnel + revert to localhost
#>
param(
    [string]$Url = "",
    [switch]$Stop
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$rootEnv = Join-Path $root ".env"
$appEnv = Join-Path $root "backend\.env"
$stateDir = $env:TEMP
$log = Join-Path $stateDir "interviewops-tunnel.log"
$errLog = Join-Path $stateDir "interviewops-tunnel.err.log"
$pidFile = Join-Path $stateDir "interviewops-tunnel.pid"
$urlFile = Join-Path $stateDir "interviewops-tunnel.url"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Set-EnvValue([string]$file, [string]$key, [string]$value) {
    $existing = if (Test-Path $file) { @(Get-Content $file) } else { @() }
    $lines = [System.Collections.Generic.List[string]]$existing
    $found = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match "^$([regex]::Escape($key))=") {
            $lines[$i] = "$key=$value"
            $found = $true
        }
    }
    if (-not $found) { $null = $lines.Add("$key=$value") }
    [System.IO.File]::WriteAllLines($file, $lines, $utf8NoBom)
    Write-Host "  updated $key in $(Split-Path -Leaf $file)"
}

function Test-CloudflaredAlive {
    if (-not (Test-Path $pidFile)) { return $false }
    $procId = 0
    if (-not [int]::TryParse((Get-Content $pidFile -Raw).Trim(), [ref]$procId)) { return $false }
    $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
    return ($p -and $p.Path -like "*cloudflared*")
}

function Find-TunnelUrl {
    foreach ($f in @($log, $errLog)) {
        if (Test-Path $f) {
            $raw = Get-Content $f -Raw -ErrorAction SilentlyContinue
            if ($raw) {
                $m = [regex]::Match($raw, "https://[a-z0-9-]+\.trycloudflare\.com")
                if ($m.Success) { return $m.Value }
            }
        }
    }
    return $null
}

function Wait-Http([string]$target, [int]$seconds = 30) {
    for ($i = 0; $i -lt $seconds; $i++) {
        try {
            $r = Invoke-WebRequest -Uri $target -UseBasicParsing -TimeoutSec 5
            if ($r.StatusCode -eq 200) { return $true }
        } catch { Start-Sleep -Seconds 1 }
    }
    return $false
}

if ($Stop) {
    Write-Host "Stopping tunnel and reverting to localhost..."
    if (Test-CloudflaredAlive) {
        Stop-Process -Id (Get-Content $pidFile) -Force
        Write-Host "  cloudflared stopped"
    }
    Remove-Item $pidFile, $urlFile -ErrorAction SilentlyContinue
    Set-EnvValue $rootEnv "PUBLIC_APP_URL" "http://localhost:5174"
    Set-EnvValue $appEnv "PUBLIC_APP_URL" "http://localhost:5174"
    Set-EnvValue $appEnv "BBB_PUBLIC_URL" "http://localhost:5174/bigbluebutton"
    Push-Location $root
    docker compose up -d | Out-Null
    Pop-Location
    Write-Host "Reverted. App: http://localhost:5174"
    exit 0
}

# --- 1. resolve the tunnel URL -------------------------------------------
if (-not $Url) {
    $running = Test-CloudflaredAlive
    if ($running -and (Test-Path $urlFile)) {
        $Url = (Get-Content $urlFile -Raw).Trim()
        Write-Host "Reusing running tunnel: $Url"
    } else {
        if (-not $running) {
            $cf = (Get-Command cloudflared -ErrorAction SilentlyContinue).Source
            if (-not $cf) {
                $default = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
                if (Test-Path $default) { $cf = $default }
            }
            if (-not $cf) {
                Write-Error "cloudflared not found. Install it (winget install Cloudflare.cloudflared) or pass -Url https://your-tunnel"
            }
            Remove-Item $log, $errLog -ErrorAction SilentlyContinue
            Write-Host "Starting cloudflared quick tunnel -> http://localhost:5174 ..."
            $p = Start-Process -FilePath $cf -ArgumentList @("tunnel", "--url", "http://localhost:5174") `
                -RedirectStandardOutput $log -RedirectStandardError $errLog `
                -WindowStyle Hidden -PassThru
            $p.Id | Set-Content $pidFile -Encoding utf8
        } else {
            Write-Host "cloudflared is running but has no saved URL yet — reading logs..."
        }
        for ($i = 0; $i -lt 60; $i++) {
            Start-Sleep -Seconds 1
            $Url = Find-TunnelUrl
            if ($Url) { break }
        }
        if (-not $Url) {
            Write-Error "Tunnel did not report a URL. Check $errLog"
        }
        $Url = $Url.TrimEnd("/")
        $Url | Set-Content $urlFile -Encoding utf8
    }
}
$Url = $Url.TrimEnd("/")

# --- 2. point every browser-facing URL at the tunnel ----------------------
Write-Host "Applying tunnel URL: $Url"
Set-EnvValue $rootEnv "PUBLIC_APP_URL" $Url
Set-EnvValue $appEnv "PUBLIC_APP_URL" $Url
Set-EnvValue $appEnv "BBB_PUBLIC_URL" "$Url/bigbluebutton"

# --- 3. restart api so it picks up the new env ----------------------------
Push-Location $root
docker compose up -d | Out-Null
Pop-Location

Write-Host "Waiting for api..."
if (-not (Wait-Http "http://localhost:8001/api/v1/health" 90)) { Write-Error "API did not become healthy" }

Write-Host "Verifying through the tunnel..."
$checks = @("$Url/", "$Url/api/v1/health", "$Url/bigbluebutton/mock")
$ok = $true
foreach ($c in $checks) {
    if (Wait-Http $c 30) { Write-Host "  OK  $c" } else { Write-Host "  FAIL $c"; $ok = $false }
}
if (-not $ok) { Write-Error "Tunnel verification failed" }

Write-Host ""
Write-Host "=== TUNNEL READY ==="
Write-Host "  Open the app:  $Url"
Write-Host "  Invite links:  generated with this origin from now on"
Write-Host "  Room URLs:     $Url/bigbluebutton/..."
Write-Host ""
Write-Host "Next:"
Write-Host "  1. Log in ($Url) with admin@example.com / admin123 and schedule a NEW interview."
Write-Host "  2. Candidate link -> device A, interviewer link -> device B; join on both."
Write-Host "  3. Allow the camera on both devices: the tiles connect P2P (STUN only, no TURN)."
Write-Host "  4. If a tile fails, inspect chrome://webrtc-internals to see which candidate pair was tried."
Write-Host ""
Write-Host "cloudflared keeps running detached (log: $log). Stop + revert with:"
Write-Host "  .\scripts\start-tunnel.ps1 -Stop"
