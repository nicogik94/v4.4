param(
    [switch]$Build,
    [switch]$RebuildApp,
    [int]$TimeoutSeconds = 120,
    [string]$ApiBase = "",
    [switch]$SkipComposeUp
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$composeDir = Split-Path -Parent $scriptDir
$dashboardPath = (Resolve-Path (Join-Path $composeDir "..\dashboards\index.html")).Path
$mountFailurePatterns = @(
    "error while creating mount source path",
    "mkdir /run/desktop/mnt/host/c: file exists",
    "unable to get image .*dockerDesktopLinuxEngine"
)

function Write-RecoveryGuidance {
    param([string]$Context)
    Write-Host ""
    Write-Host "Docker Desktop / WSL recovery guidance:"
    Write-Host "  docker compose down --remove-orphans"
    Write-Host "  wsl --shutdown"
    Write-Host "  Quit and reopen Docker Desktop"
    Write-Host "  Wait until Docker is running"
    Write-Host "  Retry this script"
    if ($Context) {
        Write-Host ""
        Write-Host "Raw error context:"
        Write-Host $Context
    }
}

function Test-MountFailureText {
    param([string]$Text)
    foreach ($pattern in $mountFailurePatterns) {
        if ($Text -match $pattern) {
            return $true
        }
    }
    return $false
}

function Invoke-Compose {
    param([string[]]$Arguments)
    $output = & docker @Arguments 2>&1
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        $text = ($output | Out-String)
        if (Test-MountFailureText $text) {
            Write-RecoveryGuidance $text
        } else {
            Write-Host $text
        }
        throw "docker $($Arguments -join ' ') failed with exit code $exitCode"
    }
    return $output
}

function Invoke-JsonGet {
    param([string]$Uri)
    try {
        return Invoke-RestMethod -Method Get -Uri $Uri -TimeoutSec 10
    } catch {
        $message = $_.Exception.Message
        if ($_.ErrorDetails -and $_.ErrorDetails.Message) {
            $message = "$message`n$($_.ErrorDetails.Message)"
        }
        throw "GET $Uri failed: $message"
    }
}

function Resolve-ApiBase {
    if ($ApiBase.Trim()) {
        return $ApiBase.Trim().TrimEnd("/")
    }
    try {
        Push-Location $composeDir
        $portOutput = (& docker compose port app 8000 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -eq 0 -and $portOutput) {
            $lastLine = ($portOutput -split "`r?`n" | Select-Object -Last 1).Trim()
            $port = ($lastLine -split ":")[-1]
            if ($port -match "^\d+$") {
                return "http://localhost:$port"
            }
        }
    } finally {
        Pop-Location
    }
    return "http://localhost:8001"
}

function Wait-ForHealth {
    param(
        [string]$Base,
        [int]$Timeout
    )
    $deadline = (Get-Date).AddSeconds($Timeout)
    $lastError = ""
    while ((Get-Date) -lt $deadline) {
        try {
            $health = Invoke-JsonGet "$Base/health"
            if ($health.status -eq "ok") {
                return $health
            }
            $lastError = "health status was '$($health.status)'"
        } catch {
            $lastError = $_.Exception.Message
        }
        Start-Sleep -Seconds 3
    }
    throw "Timed out waiting for /health at $Base after $Timeout seconds. Last error: $lastError"
}

try {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "Docker CLI is not available on PATH."
    }

    $dockerInfo = & docker info 2>&1
    if ($LASTEXITCODE -ne 0) {
        $text = ($dockerInfo | Out-String)
        if (Test-MountFailureText $text) {
            Write-RecoveryGuidance $text
        } else {
            Write-Host $text
            Write-Host "Docker daemon not running. Start Docker Desktop, wait until it is running, then retry."
        }
        exit 1
    }

    Push-Location $composeDir
    try {
        if (-not $SkipComposeUp) {
            if ($Build -or $RebuildApp) {
                Invoke-Compose @("compose", "build", "app") | Write-Host
            }
            Invoke-Compose @("compose", "up", "-d", "db", "redis", "app") | Write-Host
        }
    } finally {
        Pop-Location
    }

    $base = Resolve-ApiBase
    Write-Host "Resolved API base: $base"
    $health = Wait-ForHealth -Base $base -Timeout $TimeoutSeconds
    $preflight = Invoke-JsonGet "$base/runtime/preflight"
    $readiness = Invoke-JsonGet "$base/runtime/release-readiness"

    $blockerCount = @($readiness.blockers).Count
    $warningCount = @($readiness.warnings).Count

    Write-Host ""
    Write-Host "Local runtime summary"
    Write-Host "  API base: $base"
    Write-Host "  Dashboard: $dashboardPath"
    Write-Host "  App status: $($health.status)"
    Write-Host "  Version: $($health.version)"
    Write-Host "  Persistence: $($health.persistence)"
    Write-Host "  Tracing: $($health.tracing)"
    Write-Host "  Preflight status: $($preflight.status)"
    Write-Host "  Release gate: $($readiness.release_gate)"
    Write-Host "  Blockers: $blockerCount"
    Write-Host "  Warnings: $warningCount"

    if ($preflight.status -ne "ok") {
        Write-Host ""
        Write-Host "Preflight response:"
        $preflight | ConvertTo-Json -Depth 8
        exit 1
    }
    if ($readiness.release_gate -ne "pass") {
        Write-Host ""
        Write-Host "Release-readiness response:"
        $readiness | ConvertTo-Json -Depth 8
        exit 1
    }
    exit 0
} catch {
    Write-Host ""
    Write-Host "Local runtime check failed:"
    Write-Host $_.Exception.Message
    exit 1
}
