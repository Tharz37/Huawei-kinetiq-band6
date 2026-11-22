param(
    [string]$Adb = $env:KINETIQ_ADB,
    [int]$Port = 8000,
    [int]$IntervalSeconds = 3
)

$ErrorActionPreference = "SilentlyContinue"

if (-not $Adb) {
    $standard = Join-Path $env:LOCALAPPDATA "Android\Sdk\platform-tools\adb.exe"
    if (Test-Path $standard) {
        $Adb = $standard
    } else {
        $Adb = "adb"
    }
}

$lastState = ""
$lastTunnel = $null
Write-Host "KinetiQ ADB tunnel watcher"
Write-Host "  adb: $Adb"
Write-Host "  tunnel: tcp:$Port -> tcp:$Port, checked every ${IntervalSeconds}s"
Write-Host "  close this window or run stop_demo.bat to stop it."

while ($true) {
    $state = (& $Adb get-state 2>$null | Select-Object -First 1)
    if ($state -ne "device") {
        if ($state -ne $lastState) {
            $stamp = Get-Date -Format "HH:mm:ss"
            Write-Host "[$stamp] waiting for an authorized device (state=$state)"
            $lastState = $state
        }
        $lastTunnel = $null
        Start-Sleep -Seconds $IntervalSeconds
        continue
    }

    $reverseList = ((& $Adb reverse --list 2>$null) -join "`n")
    $hasTunnel = $reverseList -match "tcp:$Port"

    if (-not $hasTunnel) {
        & $Adb reverse "tcp:$Port" "tcp:$Port" 2>$null | Out-Null
        $stamp = Get-Date -Format "HH:mm:ss"
        if ($LASTEXITCODE -eq 0) {
            Write-Host "[$stamp] tunnel restored: phone 127.0.0.1:$Port -> laptop $Port"
        } else {
            Write-Host "[$stamp] tunnel restore failed (phone may still be reconnecting)"
        }
    } elseif ($lastTunnel -ne $true) {
        $stamp = Get-Date -Format "HH:mm:ss"
        Write-Host "[$stamp] tunnel present"
    }

    $lastTunnel = $hasTunnel
    $lastState = $state
    Start-Sleep -Seconds $IntervalSeconds
}
