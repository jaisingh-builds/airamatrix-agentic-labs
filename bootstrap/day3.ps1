<#
.SYNOPSIS
  Day 3 on Windows: check the machine, start aira-ops, run every offline suite.

.DESCRIPTION
  The Windows equivalent of the bash steps on slide 6 and `make day3-test`.
  Works in Windows PowerShell 5.1 and PowerShell 7. Run it from the repo root:

    powershell -ExecutionPolicy Bypass -File bootstrap\day3.ps1 setup    # once: checks + token + tests
    powershell -ExecutionPolicy Bypass -File bootstrap\day3.ps1 start    # aira-ops in its own window
    powershell -ExecutionPolicy Bypass -File bootstrap\day3.ps1 test     # = make day3-test
    powershell -ExecutionPolicy Bypass -File bootstrap\day3.ps1 status   # is it up, is my token right?
    powershell -ExecutionPolicy Bypass -File bootstrap\day3.ps1 mcp      # Lab 4.2 second client: list tools
    powershell -ExecutionPolicy Bypass -File bootstrap\day3.ps1 clinic   # Lab 4.1: clinic.py --tools bad mine
    powershell -ExecutionPolicy Bypass -File bootstrap\day3.ps1 service  # Lab 4.3: starter service on :8160
    powershell -ExecutionPolicy Bypass -File bootstrap\day3.ps1 stop     # free ports 8150 and 8160

  Where the secret lives: AIRA_OPS_TOKEN is stored as a *user* environment
  variable (never in a repo file), so every new terminal, VS Code and Claude
  Code see the SAME token. Terminals that were already open keep the old
  environment: close and reopen them (and VS Code) after `setup`.
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet('setup', 'start', 'test', 'status', 'mcp', 'clinic', 'service', 'stop', 'help')]
    [string]$Command = 'help',
    [string[]]$Tools = @('bad', 'mine')
)

$ErrorActionPreference = 'Stop'
$OnWindows = ($PSVersionTable.PSVersion.Major -lt 6) -or $IsWindows
$Repo = Split-Path -Parent $PSScriptRoot
$D3 = Join-Path $Repo 'day3-integration-security'
$OpsPort = 8150; if ($env:AIRA_OPS_PORT) { $OpsPort = [int]$env:AIRA_OPS_PORT }
$ServicePort = 8160; if ($env:SERVICE_PORT) { $ServicePort = [int]$env:SERVICE_PORT }
if ($OpsPort -ne 8150 -and -not $env:AIRA_OPS_URL) { $env:AIRA_OPS_URL = "http://127.0.0.1:$OpsPort" }

# Python on Windows prints and reads files as cp1252 unless told otherwise; the labs contain UTF-8.
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

function Say([string]$m) { Write-Host "  $m" }
function Ok([string]$m) { Write-Host "  [ok]   $m" -ForegroundColor Green }
function Warn([string]$m) { Write-Host "  [warn] $m" -ForegroundColor Yellow }
function Fail([string]$m) { Write-Host "  [fail] $m" -ForegroundColor Red }

# ---------------------------------------------------------------- tools
function Find-Python {
    # `python3` on Windows is often the Microsoft Store stub; ask each candidate for its version.
    $candidates = @(@('py', '-3'), @('python'), @('python3'))
    foreach ($c in $candidates) {
        $exe = $c[0]
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        $pre = @(); if ($c.Count -gt 1) { $pre = $c[1..($c.Count - 1)] }
        try {
            $v = & $exe @pre -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>$null
        } catch { continue }
        if ($v -match '^(\d+)\.(\d+)$') {
            $real = & $exe @pre -c 'import sys; print(sys.executable)'
            return [pscustomobject]@{ Exe = $real.Trim(); Version = [version]$v }
        }
    }
    return $null
}

function Get-Py {
    $p = Find-Python
    if (-not $p) { throw 'Python 3.10+ not found. Install from python.org (tick "Add python.exe to PATH").' }
    return $p.Exe
}

function Invoke-Py([string]$Dir, [string[]]$PyArgs) {
    $py = Get-Py
    Push-Location $Dir
    # Out-Host keeps Python's output on screen instead of in this function's return value.
    # Native stderr is not an error here (unittest reports on stderr), so relax the preference.
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { & $py @PyArgs | Out-Host; return $LASTEXITCODE } finally { $ErrorActionPreference = $prev; Pop-Location }
}

# ---------------------------------------------------------------- env
function Get-UserEnv([string]$Name) {
    if ($OnWindows) { return [Environment]::GetEnvironmentVariable($Name, 'User') }
    return [Environment]::GetEnvironmentVariable($Name, 'Process')
}

function Set-UserEnv([string]$Name, [string]$Value) {
    if ($OnWindows) { [Environment]::SetEnvironmentVariable($Name, $Value, 'User') }
    Set-Item -Path "env:$Name" -Value $Value
}

function Use-Token {
    # Prefer the user-level value so every window agrees; fall back to this shell's.
    $t = Get-UserEnv 'AIRA_OPS_TOKEN'
    if (-not $t) { $t = $env:AIRA_OPS_TOKEN }
    if (-not $t) { throw 'AIRA_OPS_TOKEN is not set. Run: bootstrap\day3.ps1 setup' }
    $env:AIRA_OPS_TOKEN = $t
    return $t
}

function Use-Gateway {
    # Model-backed labs (4.1 clinic, 4.3 service) read ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN.
    # If this shell doesn't have them, borrow them from Claude Code's user settings. Never printed.
    if ($env:ANTHROPIC_BASE_URL -and ($env:ANTHROPIC_AUTH_TOKEN -or $env:ANTHROPIC_API_KEY)) { return $true }
    $f = Join-Path $HOME '.claude\settings.json'
    if (-not $OnWindows) { $f = Join-Path $HOME '.claude/settings.json' }
    if (Test-Path $f) {
        try {
            $s = Get-Content $f -Raw | ConvertFrom-Json
            if ($s.env) {
                foreach ($n in 'ANTHROPIC_BASE_URL', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_API_KEY') {
                    $v = $s.env.$n
                    if ($v -and -not (Get-Item "env:$n" -ErrorAction SilentlyContinue)) { Set-Item "env:$n" $v }
                }
            }
        } catch { Warn "could not read $f : $($_.Exception.Message)" }
    }
    if ($env:ANTHROPIC_BASE_URL -and ($env:ANTHROPIC_AUTH_TOKEN -or $env:ANTHROPIC_API_KEY)) {
        Ok "gateway: $($env:ANTHROPIC_BASE_URL) (key loaded, not shown)"; return $true
    }
    Fail 'No gateway settings. Put ANTHROPIC_BASE_URL and ANTHROPIC_AUTH_TOKEN in the "env" block of ~/.claude/settings.json (Day 1 setup), or set them in this shell.'
    return $false
}

# ---------------------------------------------------------------- ports
function Get-PortOwner([int]$Port) {
    if ($OnWindows) {
        $c = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($c) { return [int]$c.OwningProcess }
        return $null
    }
    $p = (& lsof -ti "tcp:$Port" -sTCP:LISTEN 2>$null | Select-Object -First 1)
    if ($p) { return [int]$p }
    return $null
}

function Stop-Port([int]$Port) {
    $procId = Get-PortOwner $Port
    if (-not $procId) { Say "port $Port is free"; return }
    $name = (Get-Process -Id $procId -ErrorAction SilentlyContinue).ProcessName
    Stop-Process -Id $procId -Force
    Start-Sleep -Milliseconds 500
    Ok "stopped $name (pid $procId) on port $Port"
}

function Test-Ops([string]$Token) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$OpsPort/config/ingest.max_concurrent_jobs" `
            -Headers @{ Authorization = "Bearer $Token" } -TimeoutSec 5
        return [int]$r.StatusCode
    } catch {
        if ($_.Exception.Response) { return [int]$_.Exception.Response.StatusCode }
        return 0
    }
}

# ---------------------------------------------------------------- commands
function Do-Setup {
    Write-Host "`n== Day 3 setup ==" -ForegroundColor Cyan
    $bad = 0

    if (Get-Command git -ErrorAction SilentlyContinue) { Ok ("git " + ((git --version) -replace 'git version ', '')) }
    else { Fail 'git not found - install Git for Windows (Claude Code needs its Git Bash too)'; $bad++ }

    $p = Find-Python
    if (-not $p) { Fail 'Python not found - install 3.12 from python.org, tick "Add python.exe to PATH"'; $bad++ }
    elseif ($p.Version -lt [version]'3.10') { Fail "Python $($p.Version) is too old; need 3.10+"; $bad++ }
    else { Ok "python $($p.Version)  ($($p.Exe))" }

    if (Get-Command node -ErrorAction SilentlyContinue) {
        $nv = [version]((node --version).TrimStart('v'))
        if ($nv -lt [version]'22.6') { Fail "node $nv is too old for server.ts; install Node 22 LTS (22.18+) or newer"; $bad++ }
        else {
            try { & node --experimental-strip-types --no-warnings -e "0" 2>$null } catch { }
            if ($LASTEXITCODE -eq 0) { Ok "node $nv (runs .ts)" } else { Fail "node $nv rejected --experimental-strip-types"; $bad++ }
        }
    } else { Fail 'node not found - install Node.js 22 LTS or newer from nodejs.org'; $bad++ }

    if (Get-Command claude -ErrorAction SilentlyContinue) { Ok "claude $((claude --version) -split ' ' | Select-Object -First 1)" }
    else { Warn 'claude not on PATH - needed for Lab 4.2 in Claude Code (npm install -g @anthropic-ai/claude-code)' }

    # Line endings: a CRLF checkout breaks byte-exact fixtures. .gitattributes pins LF; renormalise old clones.
    Push-Location $Repo
    try {
        $sample = Join-Path $D3 'aira-ops\aira_ops.py'
        if (-not $OnWindows) { $sample = Join-Path $D3 'aira-ops/aira_ops.py' }
        $raw = [IO.File]::ReadAllText($sample)
        if ($raw.Contains("`r`n")) {
            if (git status --porcelain) {
                Warn 'files were checked out with CRLF and you have local changes; commit or stash, then re-run setup'
            } else {
                git config core.autocrlf false
                git rm -r --cached -q . | Out-Null
                git reset -q --hard
                Ok 'line endings renormalised to LF'
            }
        } else { Ok 'line endings: LF' }
    } finally { Pop-Location }

    # Token: one value, stored at user level, so both terminals and Claude Code agree.
    $existing = Get-UserEnv 'AIRA_OPS_TOKEN'
    if ($existing) { $env:AIRA_OPS_TOKEN = $existing; Ok 'AIRA_OPS_TOKEN already set (user environment)' }
    else {
        $bytes = New-Object byte[] 16
        [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
        $tok = -join ($bytes | ForEach-Object { $_.ToString('x2') })
        Set-UserEnv 'AIRA_OPS_TOKEN' $tok
        Ok 'AIRA_OPS_TOKEN created and saved to your user environment (not shown)'
        Warn 'close and reopen other terminals and VS Code so they pick it up'
    }
    if ($OnWindows) { [Environment]::SetEnvironmentVariable('PYTHONUTF8', '1', 'User') }

    if ($bad) { Fail "$bad problem(s) above - fix them, then run setup again"; exit 1 }
    Do-Test
    Write-Host "`n  Next:  powershell -ExecutionPolicy Bypass -File bootstrap\day3.ps1 start`n"
}

function Do-Start {
    $tok = Use-Token
    $py = Get-Py
    $owner = Get-PortOwner $OpsPort
    if ($owner) {
        $code = Test-Ops $tok
        if ($code -eq 200) { Ok "aira-ops already running on :$OpsPort with your token (pid $owner)"; return }
        # The classic trap: an old instance with a different token holds the port and the new one never starts.
        Warn "port $OpsPort is held by pid $owner, which rejects your token (HTTP $code) - stopping it"
        Stop-Port $OpsPort
    }
    $ops = Join-Path $D3 'aira-ops\aira_ops.py'
    # Start-Process joins arguments without quoting, and repos live in paths like
    # "OneDrive - Company\Documents". Windows: pass the command base64-encoded; elsewhere: quote.
    if ($OnWindows) {
        $q = { param($x) "'" + ($x -replace "'", "''") + "'" }
        $cmd = "`$host.UI.RawUI.WindowTitle = 'aira-ops :$OpsPort (close this window to stop)'; " +
               "& $(& $q $py) $(& $q $ops) --reset"
        $enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($cmd))
        Start-Process powershell -ArgumentList '-NoExit', '-NoProfile', '-EncodedCommand', $enc -WorkingDirectory $D3 | Out-Null
    } else {
        $ops = Join-Path $D3 'aira-ops/aira_ops.py'
        $log = Join-Path ([IO.Path]::GetTempPath()) 'aira-ops.log'
        # Not Start-Process -Redirect*: pwsh pumps that pipe itself, so the server blocks once this script exits.
        & /bin/sh -c "cd `"$D3`" && nohup `"$py`" `"$ops`" --reset > `"$log`" 2>&1 &"
    }
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Milliseconds 500
        if ((Test-Ops $tok) -eq 200) { Ok "aira-ops is up on http://127.0.0.1:$OpsPort and accepts your token"; return }
    }
    Fail "aira-ops did not answer on :$OpsPort within 15 s - look at its window for the error"
    exit 1
}

function Do-Test {
    Write-Host "`n== Day 3 offline suites (no gateway calls, `$0) ==" -ForegroundColor Cyan
    if (-not $env:AIRA_OPS_TOKEN) { $t = Get-UserEnv 'AIRA_OPS_TOKEN'; if ($t) { $env:AIRA_OPS_TOKEN = $t } }
    $suites = @(
        @{ Name = 'day3: aira-ops';          Dir = 'aira-ops';             Args = @('-m', 'unittest', '-q', 'test_aira_ops') },
        @{ Name = 'day3: 4.2 mcp server';    Dir = 'lab4-2-mcp-server';    Args = @('-m', 'unittest', '-q', 'test_mcp_server') },
        @{ Name = 'day3: 4.3 agent service'; Dir = 'lab4-3-agent-service'; Args = @('-m', 'unittest', '-q', 'test_stream', 'test_service') }
    )
    $failed = @()
    foreach ($s in $suites) {
        Write-Host "=== $($s.Name) ==="
        $rc = Invoke-Py (Join-Path $D3 $s.Dir) $s.Args
        if ($rc -ne 0) { $failed += $s.Name }
    }
    Write-Host '=== lab4: untrusted web ==='
    $webDir = Join-Path $D3 'lab4-untrusted-web\python'
    if (-not $OnWindows) { $webDir = Join-Path $D3 'lab4-untrusted-web/python' }
    foreach ($t in (Get-ChildItem $webDir -Filter 'test_*.py' | Sort-Object Name)) {
        Write-Host "--- $($t.Name)"
        $rc = Invoke-Py $webDir @($t.Name)
        if ($rc -ne 0) { $failed += "lab4: $($t.Name)" }
    }
    if ($failed.Count) { Fail ("failed: " + ($failed -join ', ')); exit 1 }
    Ok 'every Day 3 suite passed'
}

function Do-Status {
    $tok = Get-UserEnv 'AIRA_OPS_TOKEN'; if (-not $tok) { $tok = $env:AIRA_OPS_TOKEN }
    if ($tok) { Ok 'AIRA_OPS_TOKEN is set' } else { Fail 'AIRA_OPS_TOKEN not set - run setup' }
    if ($env:AIRA_OPS_TOKEN -and $tok -and $env:AIRA_OPS_TOKEN -ne $tok) {
        Warn 'this shell has an OLD token - reopen the terminal'
    }
    foreach ($port in $OpsPort, $ServicePort) {
        $procId = Get-PortOwner $port
        if ($procId) { Say "port $port : pid $procId ($((Get-Process -Id $procId -ErrorAction SilentlyContinue).ProcessName))" }
        else { Say "port $port : free" }
    }
    if ($tok -and (Get-PortOwner $OpsPort)) {
        $code = Test-Ops $tok
        if ($code -eq 200) { Ok 'aira-ops accepts your token' }
        elseif ($code -eq 401) { Fail 'aira-ops rejects your token: it was started with another one. Run: stop, then start' }
        else { Warn "aira-ops answered HTTP $code" }
    }
}

function Do-Mcp {
    Use-Token | Out-Null
    $rc = Invoke-Py (Join-Path $D3 'lab4-2-mcp-server') @('client.py')
    exit $rc
}

function Do-Clinic {
    Use-Token | Out-Null
    if (-not (Use-Gateway)) { exit 1 }
    $rc = Invoke-Py (Join-Path $D3 'lab4-1-tool-clinic') (@('clinic.py', '--tools') + $Tools)
    exit $rc
}

function Do-Service {
    $tok = Use-Token
    if (-not (Use-Gateway)) { exit 1 }
    if ((Test-Ops $tok) -ne 200) { Fail "aira-ops is not answering with your token - run: start"; exit 1 }
    if (Get-PortOwner $ServicePort) { Stop-Port $ServicePort }
    Say "starter service on http://127.0.0.1:$ServicePort  (Ctrl-C to stop)"
    Say 'try limits:  $env:RUN_TIMEOUT_S=5   or   $env:RUN_BUDGET_USD=0.06   then run service again'
    $rc = Invoke-Py (Join-Path $D3 'lab4-3-agent-service') @('starter/service.py')
    exit $rc
}

switch ($Command) {
    'setup'   { Do-Setup }
    'start'   { Do-Start }
    'test'    { Do-Test }
    'status'  { Do-Status }
    'mcp'     { Do-Mcp }
    'clinic'  { Do-Clinic }
    'service' { Do-Service }
    'stop'    { Stop-Port $ServicePort; Stop-Port $OpsPort }
    default   { Get-Help $PSCommandPath -Detailed | Out-Host }
}
