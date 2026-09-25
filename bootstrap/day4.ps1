<#
.SYNOPSIS
  Day 4 on Windows: a venv with the two Day 4 packages, the offline suites, and the live labs.

.DESCRIPTION
  The Windows equivalent of the bash steps in day4-orchestration-evals-cicd/README.md.
  Works in Windows PowerShell 5.1 and PowerShell 7. Run it from the repo root:

    powershell -ExecutionPolicy Bypass -File bootstrap\day4.ps1 setup     # once: venv + pip + offline tests
    powershell -ExecutionPolicy Bypass -File bootstrap\day4.ps1 test      # = make day4-test
    powershell -ExecutionPolicy Bypass -File bootstrap\day4.ps1 starters  # your TODO progress (fails until done)
    powershell -ExecutionPolicy Bypass -File bootstrap\day4.ps1 tokens    # Lab 5.1: pipeline read + apply tokens
    powershell -ExecutionPolicy Bypass -File bootstrap\day4.ps1 run -Question "Ingest backlog on T-1001 ..."
    powershell -ExecutionPolicy Bypass -File bootstrap\day4.ps1 evals     # Lab 5.2: golden set (~$0.70)
    powershell -ExecutionPolicy Bypass -File bootstrap\day4.ps1 review -Base main -Head my-branch   # Lab 5.3

  aira-ops is Day 3's: start it with `bootstrap\day3.ps1 start` first (Lab 5.1 needs it; 5.2 starts its own).
  Tokens from `tokens` are stored as *user* environment variables, never in a repo file.
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet('setup', 'test', 'starters', 'tokens', 'run', 'evals', 'review', 'help')]
    [string]$Command = 'help',
    [string]$Account = 'ACC-1001',
    [string]$Question = '',
    [string]$Base = 'main',
    [string]$Head = 'HEAD'
)

$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$D4 = Join-Path $Repo 'day4-orchestration-evals-cicd'
$Venv = Join-Path $Repo '.venv'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

function Ok([string]$m) { Write-Host "  [ok]   $m" -ForegroundColor Green }
function Warn([string]$m) { Write-Host "  [warn] $m" -ForegroundColor Yellow }
function Fail([string]$m) { Write-Host "  [fail] $m" -ForegroundColor Red }

function Find-Python {
    # `python3` on Windows is often the Microsoft Store stub; ask each candidate for its real path.
    foreach ($c in @(@('py', '-3'), @('python'), @('python3'))) {
        if (-not (Get-Command $c[0] -ErrorAction SilentlyContinue)) { continue }
        $pre = @(); if ($c.Count -gt 1) { $pre = $c[1..($c.Count - 1)] }
        try { $v = & $c[0] @pre -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>$null } catch { continue }
        if ($v -match '^(\d+)\.(\d+)$' -and [version]$v -ge [version]'3.10') {
            return (& $c[0] @pre -c 'import sys; print(sys.executable)').Trim()
        }
    }
    throw 'Python 3.10+ not found. Install from python.org (tick "Add python.exe to PATH").'
}

function VenvPy {
    $win = Join-Path $Venv 'Scripts\python.exe'; $nix = Join-Path $Venv 'bin/python'
    if (Test-Path $win) { return $win }
    if (Test-Path $nix) { return $nix }
    throw "No venv yet - run: bootstrap\day4.ps1 setup"
}

function Py([string]$Dir, [string[]]$PyArgs) {
    $py = VenvPy
    Push-Location $Dir
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'   # unittest reports on stderr
    try { & $py @PyArgs | Out-Host; return $LASTEXITCODE } finally { $ErrorActionPreference = $prev; Pop-Location }
}

function Suites([string]$Target = '') {
    $env:LAB51_TARGET = $Target; $env:LAB52_TARGET = $Target; $env:LAB53_TARGET = $Target
    $bad = 0
    foreach ($s in @(
            @('common', @('-m', 'unittest', '-q', 'test_spans')),
            @('lab5-1-handoff', @('-m', 'unittest', '-q', 'test_pipeline', 'test_graph')),
            @('lab5-2-evals', @('-m', 'unittest', '-q', 'test_graders', 'test_judge')),
            @('lab5-3-pr-review', @('-m', 'unittest', '-q', 'test_review')))) {
        Write-Host "=== day4: $($s[0]) ==="
        if ((Py (Join-Path $D4 $s[0]) $s[1]) -ne 0) { $bad++ }
    }
    Remove-Item Env:LAB51_TARGET, Env:LAB52_TARGET, Env:LAB53_TARGET -ErrorAction SilentlyContinue
    return $bad
}

switch ($Command) {
    'setup' {
        $py = Find-Python
        Ok "python: $(& $py --version)"
        $node = Get-Command node -ErrorAction SilentlyContinue
        if (-not $node) { Fail 'node not found - install Node 22.6+ (nodejs.org); the MCP server needs it'; exit 1 }
        $nv = (& node --version).TrimStart('v')
        if ([version]$nv -lt [version]'22.6') { Fail "node $nv is too old for --experimental-strip-types; need 22.6+"; exit 1 }
        Ok "node: $nv"
        if (-not (Test-Path $Venv)) { & $py -m venv $Venv; Ok "venv: $Venv" }
        & (VenvPy) -m pip install -q --upgrade pip
        & (VenvPy) -m pip install -q -r (Join-Path $D4 'requirements.txt')
        if ($LASTEXITCODE -ne 0) { Fail 'pip install failed (proxy? try: pip install --proxy http://host:port ...)'; exit 1 }
        Ok 'claude-agent-sdk and langgraph installed in .venv'
        if (-not (Test-Path (Join-Path $Repo '.env'))) { Warn '.env missing - copy .env.example to .env and paste your gateway key (live labs need it)' }
        $bad = Suites
        if ($bad -eq 0) { Ok 'all Day 4 offline suites pass' } else { Fail "$bad suite(s) failed"; exit 1 }
    }
    'test' { $bad = Suites; if ($bad) { exit 1 } }
    'starters' {
        Write-Host 'Your TODOs, tested against starter/ - failures are expected until you finish them.'
        $bad = Suites 'starter'; Write-Host "$bad suite(s) still failing"
    }
    'tokens' {
        if (-not $env:AIRA_OPS_TOKEN) { Fail 'AIRA_OPS_TOKEN not set - run bootstrap\day3.ps1 setup first'; exit 1 }
        $out = & (VenvPy) (Join-Path $D4 'lab5-1-handoff\pipeline.py') tokens --account $Account
        foreach ($line in $out) {
            if ($line -match '^export (AIRA_OPS_(READ|APPLY)_TOKEN)=(.+)$') {
                [Environment]::SetEnvironmentVariable($Matches[1], $Matches[3], 'User')
                Set-Item -Path "Env:$($Matches[1])" -Value $Matches[3]
                Ok "$($Matches[1]) stored as a user environment variable (value not shown)"
            }
        }
        Warn 'restart aira-ops so it loads the new callers: bootstrap\day3.ps1 stop; then set AIRA_OPS_CALLERS and start'
        Write-Host "  `$env:AIRA_OPS_CALLERS = '$(Join-Path $Repo 'day3-integration-security\aira-ops\callers.json')'"
    }
    'run' {
        if (-not $Question) { Fail 'give -Question "..."'; exit 1 }
        exit (Py (Join-Path $D4 'lab5-1-handoff') @('pipeline.py', 'run', '--account', $Account, '--question', $Question))
    }
    'evals' { exit (Py (Join-Path $D4 'lab5-2-evals') @('run_evals.py')) }
    'review' {
        if (-not (Get-Command claude -ErrorAction SilentlyContinue)) { Fail 'claude (Claude Code) not on PATH'; exit 1 }
        exit (Py (Join-Path $D4 'lab5-3-pr-review') @('review.py', '--repo', $Repo, '--base', $Base, '--head', $Head))
    }
    default { Get-Help $PSCommandPath -Detailed | Out-Host }
}
