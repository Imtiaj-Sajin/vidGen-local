# One-command install on a Windows + NVIDIA machine.
# Usage (from the repo folder):
#   powershell -ExecutionPolicy Bypass -File setup.ps1                 # everything (~100 GB models)
#   powershell -ExecutionPolicy Bypass -File setup.ps1 -Models core    # only the default model set (~42 GB)
#   powershell -ExecutionPolicy Bypass -File setup.ps1 -Models none    # ComfyUI + nodes only
# Safe to re-run: finished steps are skipped, downloads resume.
param([ValidateSet("all", "core", "none")] [string]$Models = "all")

$ErrorActionPreference = "Stop"
$Root    = $PSScriptRoot
$Portable = "$Root\ComfyUI_windows_portable"
$Py      = "$Portable\python_embeded\python.exe"
$Nodes   = "$Portable\ComfyUI\custom_nodes"
$env:PIP_CACHE_DIR = "$Root\cache\pip"

# pinned versions that were tested together
$CustomNodes = @(
  @("city96/ComfyUI-GGUF",                 "6ea2651e7df66d7585f6ffee804b20e92fb38b8a"),
  @("Lightricks/ComfyUI-LTXVideo",         "44c8a36b7c31ee00b4271daed11dc2a4aa481140"),
  @("crystian/ComfyUI-Crystools",          "2f18256c5b5063937106f29a8e0a7db3ae3869b7"),
  @("Kosinkadink/ComfyUI-VideoHelperSuite", "4d907bee61e92c2e65af3bd6383a4e4d356126d1")
)

function Step($m) { Write-Host "`n=== $m" -ForegroundColor Cyan }

foreach ($tool in "git", "curl.exe", "tar") {
  if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "$tool not found. Install Git for Windows (git-scm.com) and use Windows 10 1803+ / 11." }
}

Step "ComfyUI portable"
if (-not (Test-Path $Py)) {
  & powershell -NoProfile -ExecutionPolicy Bypass -File "$Root\scripts\download.ps1" -Phase comfy
  tar -xf "$Root\downloads\ComfyUI_windows_portable_nvidia.7z" -C $Root
  if ($LASTEXITCODE -ne 0) { throw "Extracting ComfyUI failed (needs Windows 11 tar with 7z support, or extract the .7z with 7-Zip into $Root)" }
} else { Write-Host "already installed" }

Step "Custom nodes"
foreach ($n in $CustomNodes) {
  $repo, $commit = $n
  $dir = Join-Path $Nodes ($repo.Split("/")[1])
  if (-not (Test-Path $dir)) { git clone -q --depth 1 "https://github.com/$repo.git" $dir }
  if ((git -C $dir rev-parse HEAD) -ne $commit) {
    git -C $dir fetch -q --depth 1 origin $commit
    git -C $dir -c advice.detachedHead=false checkout -q $commit
  }
  if (Test-Path "$dir\requirements.txt") {
    & $Py -s -m pip install --disable-pip-version-check -q --no-warn-script-location -r "$dir\requirements.txt"
  }
  Write-Host "$repo @ $($commit.Substring(0,7))"
}

Step "LTX Lab + workflows"
$lab = "$Nodes\ltx_lab"
if (-not (Test-Path $lab)) { New-Item -ItemType Junction -Path $lab -Target "$Root\ltx_lab" | Out-Null }
$wf = "$Portable\ComfyUI\user\default\workflows"
New-Item -ItemType Directory -Force $wf | Out-Null
Copy-Item "$Root\workflows\*.json" $wf -Force
Write-Host "linked $lab"

if ($Models -ne "none") {
  Step "Models ($Models)"
  $phase = if ($Models -eq "core") { "core" } else { "models" }
  & powershell -NoProfile -ExecutionPolicy Bypass -File "$Root\scripts\download.ps1" -Phase $phase
}

Step "Check GPU"
& $Py -c "import torch; print('torch', torch.__version__, '| CUDA', torch.cuda.is_available(), '|', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'no GPU')"

Write-Host "`nDone. Start with Start-ComfyUI.bat, then open http://127.0.0.1:8188/ltxlab" -ForegroundColor Green
