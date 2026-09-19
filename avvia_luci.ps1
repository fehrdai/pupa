# avvia_luci.ps1 - avvia / ferma le luci PUPA (lights\pupa_luci.py) su Windows.
#
#   avvia_luci.bat              avvia (controlla QLC+, poi lancia le luci)
#   avvia_luci.bat restart      ferma e riavvia le luci
#   avvia_luci.bat stop         ferma le luci e azzera i fari
#   avvia_luci.bat status       mostra cosa sta girando
#
# Le luci sono un processo separato da pupa.py. Per fermare TUTTO: F12 in OBS.
# NB: QLC+ 5 su Windows si lancia con "-w -o <progetto>" (non ha -p). Percorso: $QlcExe sotto.
# Tasti: vedi TASTI.txt. Dettagli: LIGHTS_CONFIG.md.
param([string]$Cmd = "start")

$Dir     = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Dir
$QlcExe  = "C:\QLC+5\qlcplus-qml.exe"
$Proj    = Join-Path $Dir "QLC+\pupa.qxw"
$LogDir  = Join-Path $Dir "lights\logs"
$Stdout  = Join-Path $LogDir "stdout.log"
$Stderr  = Join-Path $LogDir "stderr.log"
New-Item -ItemType Directory -Force $LogDir | Out-Null

function Test-Port([int]$Port) {
    try { $c = New-Object System.Net.Sockets.TcpClient; $r = $c.BeginConnect("127.0.0.1", $Port, $null, $null)
          $ok = $r.AsyncWaitHandle.WaitOne(500) -and $c.Connected; $c.Close(); return $ok } catch { return $false }
}
function Get-Luci { Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'lights[\\/]pupa_luci\.py' } | Select-Object -First 1 }

function Stop-Luci {
    $p = Get-Luci
    if (-not $p) { Write-Host "[LUCI] non stanno girando."; return }
    Write-Host "[LUCI] stop (PID $($p.ProcessId))..."
    Stop-Process -Id $p.ProcessId -Force
    Start-Sleep -Milliseconds 500
    # lo stop forzato non esegue lo spegnimento pulito: azzero i fari a parte
    & python -u lights\pupa_luci.py --zero
}

function Show-Status {
    if (Test-Port 9996) { "QLC+ : ok (OS2L 9996)" } else { "QLC+ : NON pronto (9996 chiusa)" }
    if (Test-Port 4455) { "OBS  : ok (WebSocket 4455)" } else { "OBS  : WebSocket 4455 chiuso (hotkey luci non disponibili)" }
    $p = Get-Luci
    if ($p) { "Luci : in esecuzione (PID $($p.ProcessId))" } else { "Luci : ferme" }
    $sum = Select-String -Path (Join-Path $LogDir "lights.log") -Pattern "\[SUM\]" -ErrorAction SilentlyContinue | Select-Object -Last 1
    if ($sum) { $sum.Line.Substring(0, [Math]::Min(230, $sum.Line.Length)) }
}

switch ($Cmd) {
    "status"  { Show-Status; return }
    "stop"    { Stop-Luci; return }
    "restart" { Stop-Luci }
    "start"   { }
    default   { "uso: avvia_luci.bat [start|restart|stop|status]"; return }
}

if (Get-Luci) { Write-Host "[LUCI] gia' in esecuzione. Usa 'avvia_luci.bat restart' per riavviarle."; return }

if (Test-Port 9996) {
    Write-Host "[QLC+] ok (OS2L in ascolto sulla porta 9996)."
} else {
    if (-not (Test-Path $QlcExe)) { Write-Host "[QLC+] non trovato: $QlcExe (modifica `$QlcExe in avvia_luci.ps1)"; return }
    if (-not (Test-Path $Proj))   { Write-Host "[QLC+] progetto non trovato: $Proj"; return }
    if (Get-Process -Name "qlcplus-qml" -ErrorAction SilentlyContinue) {
        Write-Host "[QLC+] e' aperto ma NON ascolta OS2L (9996): chiudilo e rilancia questo script (si riapre col progetto)."; return
    }
    Write-Host "[QLC+] avvio col progetto..."
    Start-Process -FilePath $QlcExe -ArgumentList "-w", "-o", "`"$Proj`""
    $ok = $false
    for ($i = 0; $i -lt 25; $i++) { if (Test-Port 9996) { $ok = $true; break }; Start-Sleep -Seconds 1 }
    if (-not $ok) { Write-Host "[QLC+] non ascolta sulla 9996 dopo 25 s."; return }
    Write-Host "[QLC+] ok."
}
if (Test-Port 4455) { Write-Host "[OBS ] ok (WebSocket 4455)." } else { Write-Host "[OBS ] WebSocket 4455 chiuso: le luci partono lo stesso, ma senza i tasti." }

Write-Host "[LUCI] avvio..."
Set-Content -Path $Stdout -Value ""
Start-Process -FilePath "python" -ArgumentList "-u", "lights\pupa_luci.py" -WorkingDirectory $Dir `
    -RedirectStandardOutput $Stdout -RedirectStandardError $Stderr -WindowStyle Hidden
for ($i = 0; $i -lt 25; $i++) { if (Select-String -Path $Stdout -Pattern "\[SUM\]" -Quiet) { break }; Start-Sleep -Seconds 1 }
Select-String -Path $Stdout -Pattern "QLC\]|HOTKEY\] stato iniziale|AUDIO\] (Preflight|ALERT)|partenza|\[SUM\]|ERROR|Traceback" | ForEach-Object { $_.Line.Substring(0, [Math]::Min(200, $_.Line.Length)) }
Write-Host "-----"
if (Select-String -Path $Stdout -Pattern "\[SUM\].*qlc=ok" -Quiet) { Write-Host "OK: luci attive. Fermare: F12 in OBS oppure 'avvia_luci.bat stop'." }
else { Write-Host "ATTENZIONE: nessuna conferma qlc=ok - controlla QLC+ e $Stdout" }
