<#
.SYNOPSIS
  Veyra on Windows: one command to set up, run, demo and load-test the whole stack.

.DESCRIPTION
  .\veyra.ps1                 same as .\veyra.ps1 up
  .\veyra.ps1 up              build and start everything, then smoke-check it
  .\veyra.ps1 demo            guided end-to-end walkthrough (add --auto for no pauses)
  .\veyra.ps1 load            Kafka + pipeline throughput test
  .\veyra.ps1 status | logs <svc> | down | reset | wipe | doctor | help

  Needs Docker Desktop (running) and git. Everything else runs in containers.
  This launcher runs ./veyra.sh:
    - through WSL, when a WSL distro can reach Docker (Docker Desktop > Settings > Resources
      > WSL integration), or
    - inside a small "toolbox" container otherwise.
  Force one with -Via wsl | toolbox. All other arguments go to veyra.sh unchanged.

.EXAMPLE
  .\veyra.ps1 up --profile workstation
  .\veyra.ps1 load --events 1000000000 --pipeline 5000000
#>
# No param() block on purpose: every argument (including --profile style flags, which
# PowerShell would otherwise try to bind) is passed to veyra.sh untouched. Only -Via is ours.
$Via = 'auto'
$Rest = @()
for ($i = 0; $i -lt $args.Count; $i++) {
    if ($args[$i] -eq '-Via' -and $i + 1 -lt $args.Count) { $Via = $args[$i + 1]; $i++ }
    else { $Rest += [string]$args[$i] }
}
if ($Via -notin 'auto', 'wsl', 'toolbox') { Write-Host "  -Via must be auto, wsl or toolbox" -ForegroundColor Red; exit 1 }

$ErrorActionPreference = 'Stop'
$Repo = $PSScriptRoot
$Parent = Split-Path $Repo -Parent
$ContractsUrl = if ($env:VEYRA_CONTRACTS_URL) { $env:VEYRA_CONTRACTS_URL } else { 'https://github.com/arnavmahajan630/contracts-repo' }

function Say([string]$Text, [string]$Color = 'Gray') { Write-Host "  $Text" -ForegroundColor $Color }
function Fail([string]$Text) { Write-Host "`n  x $Text" -ForegroundColor Red; exit 1 }

Write-Host "Veyra" -ForegroundColor White -NoNewline; Write-Host "  Windows launcher  ($Repo)" -ForegroundColor DarkGray

# ---------------------------------------------------------------- prerequisites
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Fail "Docker is not installed. Install Docker Desktop: https://docs.docker.com/desktop/setup/install/windows-install/"
}
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    $dd = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
    if (Test-Path $dd) {
        Say "Docker Desktop is not running; starting it ..." Yellow
        Start-Process $dd | Out-Null
        $deadline = (Get-Date).AddMinutes(3)
        do { Start-Sleep 5; docker info *> $null } until ($LASTEXITCODE -eq 0 -or (Get-Date) -gt $deadline)
    }
    docker info *> $null
    if ($LASTEXITCODE -ne 0) { Fail "Docker is not running. Start Docker Desktop and run this again." }
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail "git is not installed: winget install --id Git.Git -e" }

# ---------------------------------------------------------------- line endings
# A Windows checkout with core.autocrlf=true writes CRLF; bash then fails on "\r". Files that
# run or are read inside Linux containers are rewritten with LF (git sees no content change).
$lfFiles = @('veyra.sh', 'wazuh\entrypoint-veyra.sh', 'Caddyfile', 'Makefile', '.env.local') +
    (Get-ChildItem -Path (Join-Path $Repo 'compose'), (Join-Path $Repo 'edge'), (Join-Path $Repo 'profiles'), (Join-Path $Repo 'wazuh') -Recurse -Include *.yml, *.yaml, *.toml, *.tmpl, *.xml, *.csv, *.env, *.sh -File |
        ForEach-Object { $_.FullName.Substring($Repo.Length + 1) })
foreach ($rel in $lfFiles) {
    $path = Join-Path $Repo $rel
    if (-not (Test-Path $path)) { continue }
    $bytes = [IO.File]::ReadAllBytes($path)
    $text = [Text.Encoding]::UTF8.GetString($bytes)
    if ($text.Contains("`r`n")) {
        [IO.File]::WriteAllText($path, $text.Replace("`r`n", "`n"), (New-Object Text.UTF8Encoding($false)))
    }
}

# ---------------------------------------------------------------- contracts registry
$Contracts = Join-Path $Parent 'contracts-repo'
if (-not (Test-Path (Join-Path $Contracts '.git'))) {
    Say "cloning the Log Contract registry next to this repo (GitHub may ask you to sign in) ..."
    git clone --quiet $ContractsUrl $Contracts
    if ($LASTEXITCODE -ne 0) { Fail "could not clone $ContractsUrl into $Contracts. Clone it there yourself and re-run." }
}

# ---------------------------------------------------------------- pick a route
function Find-WslDistro {
    $wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
    if (-not $wsl) { return $null }
    $names = (& wsl.exe -l -q 2>$null) -replace "`0", '' | Where-Object { $_ -and $_ -notmatch '^docker-desktop' }
    foreach ($name in $names) {
        $name = $name.Trim()
        & wsl.exe -d $name -e sh -c 'command -v docker >/dev/null && docker info >/dev/null 2>&1' 2>$null
        if ($LASTEXITCODE -eq 0) { return $name }
    }
    return $null
}

$distro = $null
if ($Via -ne 'toolbox') { $distro = Find-WslDistro }
if ($Via -eq 'wsl' -and -not $distro) {
    Fail "no WSL distro can reach Docker. Enable it in Docker Desktop > Settings > Resources > WSL integration, or use -Via toolbox."
}

# veyra.sh prints its hints with the command the reviewer actually typed.
$env:VEYRA_CMD = '.\veyra.ps1'

if ($distro) {
    Say "running ./veyra.sh in WSL ($distro)" DarkGray
    $env:WSLENV = if ($env:WSLENV) { "$($env:WSLENV):VEYRA_CMD" } else { 'VEYRA_CMD' }
    & wsl.exe -d $distro --cd $Repo -e bash ./veyra.sh @Rest
    exit $LASTEXITCODE
}

# Toolbox route: the same script in a container with the Docker CLI. The repo's parent folder
# (so ../contracts-repo is there too) is mounted at Docker Desktop's host path, which is what
# makes compose's relative bind mounts point back at this Windows folder.
$drive = $Parent.Substring(0, 1).ToLower()
$hostPath = '/run/desktop/mnt/host/' + $drive + ($Parent.Substring(2) -replace '\\', '/')
$repoName = Split-Path $Repo -Leaf
Say "no WSL distro with Docker; running ./veyra.sh in the toolbox container" DarkGray
docker image inspect veyra/toolbox:1 *> $null
if ($LASTEXITCODE -ne 0) {
    Say "building the toolbox image (once) ..."
    docker build -q -t veyra/toolbox:1 -f (Join-Path $Repo 'docker\toolbox.Dockerfile') (Join-Path $Repo 'docker') | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "could not build the toolbox image" }
}
[string[]]$tty = if ([Console]::IsInputRedirected -or [Console]::IsOutputRedirected) { '-i' } else { '-it' }
docker run --rm @tty `
    -v //var/run/docker.sock:/var/run/docker.sock `
    -v "${Parent}:${hostPath}" `
    -w "$hostPath/$repoName" `
    -e TERM=xterm-256color `
    -e VEYRA_CMD `
    veyra/toolbox:1 ./veyra.sh @Rest
exit $LASTEXITCODE
