# Run on the authorized user's Windows computer.
# This script never writes the rclone OAuth configuration to the repository.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9_-]+$')]
    [string]$FolderId
)

$ErrorActionPreference = 'Stop'
$Repository = 'anzthebest0-cpu/wawp-forecast-engine'
$PullRequest = 4
$Label = 'archive-connection-test'
$ConfigDirectory = Join-Path $env:LOCALAPPDATA 'WAWPForecastEngine'
$ConfigPath = Join-Path $ConfigDirectory 'archive-rclone.conf'

function Assert-NativeSuccess([string]$Operation) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Operation failed (exit code $LASTEXITCODE)."
    }
}

function Require-Command([string]$Command, [string]$Package) {
    if (Get-Command $Command -ErrorAction SilentlyContinue) {
        return
    }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "Install $Command from its official website, then run this script again."
    }
    if ((Read-Host "$Command is required. Install $Package using winget? [y/N]") -notmatch '^(y|yes)$') {
        throw "$Command installation declined."
    }
    & winget install --id $Package --exact
    Assert-NativeSuccess "Installation of $Command"
    $MachinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $UserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$MachinePath;$UserPath;$env:Path"
    if (-not (Get-Command $Command -ErrorAction SilentlyContinue)) {
        throw "Open a new PowerShell window and run this script again."
    }
}

Require-Command 'rclone' 'Rclone.Rclone'
Require-Command 'gh' 'GitHub.cli'

& gh auth status --hostname github.com
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Sign in to GitHub as the owner of wawp-forecast-engine.'
    & gh auth login --hostname github.com --web --git-protocol https
    Assert-NativeSuccess 'GitHub sign-in'
}

$RepositoryPermission = & gh repo view $Repository --json viewerPermission --jq '.viewerPermission'
Assert-NativeSuccess 'Repository permission check'
if ($RepositoryPermission.Trim() -ne 'ADMIN') {
    throw "GitHub authorization must have ADMIN access to $Repository."
}

New-Item -ItemType Directory -Force -Path $ConfigDirectory | Out-Null
if (-not (Test-Path -LiteralPath $ConfigPath)) {
    Write-Host 'Authorize Google Drive in the browser. Select the account with access to the specified archive folder.'
    Write-Host 'The Drive OAuth scope permits access to that account''s Drive; the folder ID restricts the archive destination.'
    Write-Host 'A dedicated archival Google account with access only to the WAWP folder is recommended.'
    if ((Read-Host 'Proceed with Google Drive authorization? [y/N]') -notmatch '^(y|yes)$') {
        throw 'Google Drive authorization declined.'
    }
    & rclone --config $ConfigPath config create wawp drive scope drive root_folder_id $FolderId | Out-Null
    Assert-NativeSuccess 'rclone Google Drive authorization'
}

$Remotes = @(& rclone --config $ConfigPath listremotes)
Assert-NativeSuccess 'Dedicated configuration check'
if ($Remotes.Count -ne 1 -or $Remotes[0] -ne 'wawp:') {
    throw 'The dedicated configuration must contain exactly one remote named wawp.'
}

# Restrict the destination even if a previously created dedicated config
# has a different root folder in its local settings.
$env:RCLONE_CONFIG_WAWP_ROOT_FOLDER_ID = $FolderId
$Directories = & rclone --config $ConfigPath lsf 'wawp:' --dirs-only
Assert-NativeSuccess 'Drive folder read permission'
if ('incremental/' -notin $Directories) {
    throw "The selected Google Drive folder does not contain the expected incremental subfolder."
}

$ProofDirectory = Join-Path ([System.IO.Path]::GetTempPath()) ('wawp-connection-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $ProofDirectory | Out-Null
try {
    $ProofName = 'local-' + [guid]::NewGuid().ToString('N') + '.txt'
    $ProofPath = Join-Path $ProofDirectory $ProofName
    $ReadbackPath = Join-Path $ProofDirectory 'readback.txt'
    $RemoteFile = "wawp:connection-tests/$ProofName"
    [IO.File]::WriteAllText($ProofPath, "WAWP archive connection test $ProofName")
    & rclone --config $ConfigPath copyto --immutable --checksum $ProofPath $RemoteFile
    Assert-NativeSuccess 'Drive write test'
    & rclone --config $ConfigPath copyto --immutable --checksum $RemoteFile $ReadbackPath
    Assert-NativeSuccess 'Drive readback test'
    if ((Get-FileHash -LiteralPath $ProofPath -Algorithm SHA256).Hash -ne
        (Get-FileHash -LiteralPath $ReadbackPath -Algorithm SHA256).Hash) {
        throw 'Drive readback SHA-256 differs from the uploaded test file.'
    }
    Write-Host 'PASS: local Google Drive upload and readback.' -ForegroundColor Green
}
finally {
    Remove-Item -LiteralPath $ProofDirectory -Recurse -Force
}

$ExistingSecrets = & gh secret list --repo $Repository --json name --jq '.[].name'
Assert-NativeSuccess 'Repository secret inventory'
$ExistingVariables = & gh variable list --repo $Repository --json name,value --jq '.[] | select(.name=="WAWP_INCREMENTAL_ARCHIVE_ENABLED") | .value'
Assert-NativeSuccess 'Production archival status check'
if ($ExistingVariables -eq 'true') {
    throw 'Production archival is already enabled. Stop and review the live configuration before replacing credentials.'
}
if ('WAWP_ARCHIVE_RCLONE_CONFIG' -in $ExistingSecrets) {
    if ((Read-Host 'The archive secret exists. Replace it with this authorized config? [y/N]') -notmatch '^(y|yes)$') {
        throw 'Existing archive secret preserved.'
    }
}
Write-Host "The dedicated OAuth configuration will be stored as a GitHub Actions secret in $Repository."
if ((Read-Host 'Authorize this credential transfer and run the isolated connection test? [y/N]') -notmatch '^(y|yes)$') {
    throw 'GitHub secret configuration declined.'
}

# Pipe directly to gh, so the credential is never shown in the command line,
# terminal output, repository files, or chat.
Get-Content -LiteralPath $ConfigPath -Raw | & gh secret set WAWP_ARCHIVE_RCLONE_CONFIG --repo $Repository
Assert-NativeSuccess 'GitHub Actions secret configuration'
& gh variable set WAWP_ARCHIVE_FOLDER_ID --repo $Repository --body $FolderId
Assert-NativeSuccess 'GitHub Actions archive folder configuration'

$ExistingLabels = & gh label list --repo $Repository --limit 100 --json name --jq '.[].name'
Assert-NativeSuccess 'Repository label inventory'
if ($Label -notin $ExistingLabels) {
    & gh label create $Label --repo $Repository --color '0e8a16' --description 'Run isolated WAWP archive connection test'
    Assert-NativeSuccess 'Connection-test label creation'
}
$CurrentLabels = & gh pr view $PullRequest --repo $Repository --json labels --jq '.labels[].name'
Assert-NativeSuccess 'Pull request label inventory'
if ($Label -in $CurrentLabels) {
    & gh pr edit $PullRequest --repo $Repository --remove-label $Label
    Assert-NativeSuccess 'Reset connection-test trigger'
}
& gh pr edit $PullRequest --repo $Repository --add-label $Label
Assert-NativeSuccess 'Trigger isolated GitHub Actions connection test'

Write-Host 'Setup complete. The isolated GitHub Actions test is now queued.' -ForegroundColor Green
Write-Host "Review https://github.com/$Repository/pull/$PullRequest/checks"
Write-Host 'Production archival is still disabled. Retain the local config securely for future credential rotation.'
