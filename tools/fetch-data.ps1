<#
  WildFind data fetcher - launcher
  --------------------------------
  Run this from tools\FETCH DATA.bat. It finds Python and hands over to
  tools\fetch.py, which does all the actual work. That split is deliberate:
  the work lives in Python, where it can be properly tested, and this file
  stays small enough to check by eye.

  Options, all optional:
      -Stage all|osm|photos      which part to run (default all)
      -MaxPhotoSites 35000       how many sites get photographs
      -PhotosPerSite 3
      -PhotoWidth 500            a standard Wikimedia size, served from cache
      -MaxPhotoGB 9.5
      -PhotoDelay 0.2            seconds between requests, at least
      -KeepExtract               keep the 2.1 GB OpenStreetMap file afterwards
#>

[CmdletBinding()]
param(
  [ValidateSet("all","osm","photos")] [string] $Stage = "all",
  [int]    $MaxPhotoSites = 35000,
  [int]    $PhotosPerSite = 3,
  [int]    $PhotoWidth    = 500,
  [double] $MaxPhotoGB    = 9.5,
  [double] $PhotoDelay    = 0.2,
  [switch] $KeepExtract
)

# Programs report success through exit codes. "Stop" would turn anything a
# program prints to its error stream into a script-ending error in Windows
# PowerShell 5.1, which is the opposite of what is wanted here.
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot

function Say([string]$m, [string]$colour = "Gray") { Write-Host $m -ForegroundColor $colour }

Say ""
Say "WildFind data fetcher" "White"

# An older version of this launcher may still be running. Two copies would
# fight over the same files, so stop any other process running this script.
# Nothing else is touched. (fetch.py also holds a lock of its own.)
try {
  $others = @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe' OR Name='pwsh.exe'" -ErrorAction Stop |
              Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -and $_.CommandLine -match 'fetch-data\.ps1' })
  foreach ($o in $others) {
    Say ("An older copy of this fetcher is still running (process {0}). Stopping it." -f $o.ProcessId) "Yellow"
    Stop-Process -Id $o.ProcessId -Force -ErrorAction SilentlyContinue
  }
  if ($others.Count) { Start-Sleep -Seconds 3 }
} catch {
  Say "Could not check for other running copies; if one is open, close it first." "DarkYellow"
}

function Find-Python {
  foreach ($cmd in @("python", "py", "python3")) {
    try {
      $v = & $cmd --version 2>&1
      if ($LASTEXITCODE -eq 0 -and "$v" -match 'Python (\d+)\.(\d+)') {
        if ([int]$Matches[1] -eq 3 -and [int]$Matches[2] -ge 9) { return $cmd }
      }
    } catch { }
  }
  return $null
}

$py = Find-Python
if (-not $py) {
  Say ""
  Say "Python is not installed, and this needs it." "Yellow"
  Say "Easiest: open the Microsoft Store, search for Python 3.12, click Get." "Yellow"
  Say "Or download it from https://www.python.org/downloads/windows/" "Yellow"
  Say "Then run this again." "Yellow"
  exit 1
}

$inv = [Globalization.CultureInfo]::InvariantCulture
$pyArgs = @(
  (Join-Path $PSScriptRoot "fetch.py"), $Root,
  "--stage",     $Stage,
  "--max-sites", $MaxPhotoSites.ToString($inv),
  "--per-site",  $PhotosPerSite.ToString($inv),
  "--width",     $PhotoWidth.ToString($inv),
  "--max-gb",    $MaxPhotoGB.ToString($inv),
  "--delay",     $PhotoDelay.ToString($inv)
)
if ($KeepExtract) { $pyArgs += "--keep-extract" }

& $py @pyArgs
exit $LASTEXITCODE
