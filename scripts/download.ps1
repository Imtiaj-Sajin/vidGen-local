# Downloads ComfyUI portable + LTX-2.5 models in priority order.
# Resumable: re-run anytime, finished files are skipped, partial files continue.
# Usage: powershell -ExecutionPolicy Bypass -File download.ps1 [-Phase comfy|core|extras|int8|models|all]
param([string]$Phase = "all")

$Root   = Split-Path $PSScriptRoot -Parent
$Comfy  = "$Root\ComfyUI_windows_portable"
$Models = "$Comfy\ComfyUI\models"
$Mirror = "https://huggingface.co/comfyicu/LTX-2.5/resolve/main"   # ungated mirror of Lightricks/LTX-2.5
$DDB    = "https://huggingface.co/DmitryDB/LTX-2.5-ComfyUI-Quants/resolve/main"
$GGUF   = "https://huggingface.co/agosh/LTX-2.5-Comfy-GGUF/resolve/main"
$Log    = "$Root\scripts\download.log"

$comfy = @(
  @("https://github.com/Comfy-Org/ComfyUI/releases/download/v0.42.1/ComfyUI_windows_portable_nvidia.7z", "$Root\downloads\ComfyUI_windows_portable_nvidia.7z")
)
$core = @(
  @("$Mirror/vae/ltx-2.5-video-vae-bf16.safetensors",       "$Models\vae\ltx-2.5-video-vae-bf16.safetensors"),
  @("$Mirror/vae/ltx-2.5-video-vae-conv-bf16.safetensors",  "$Models\vae\ltx-2.5-video-vae-conv-bf16.safetensors"),
  @("$Mirror/vae/ltx-2.5-audio-vae-bf16.safetensors",       "$Models\vae\ltx-2.5-audio-vae-bf16.safetensors"),
  @("$Mirror/latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors",  "$Models\latent_upscale_models\ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors"),
  @("$Mirror/latent_upscale_models/ltx-2.5-latent-temporal-upscaler-x2-bf16-1.0.safetensors", "$Models\latent_upscale_models\ltx-2.5-latent-temporal-upscaler-x2-bf16-1.0.safetensors"),
  @("$Mirror/model_patches/ltx-2.5-duration-head-bf16.safetensors", "$Models\model_patches\ltx-2.5-duration-head-bf16.safetensors"),
  @("https://huggingface.co/Comfy-Org/gemma-4/resolve/main/text_encoders/gemma4_e2b_it_int8_convrot.safetensors", "$Models\text_encoders\gemma4_e2b_it_int8_convrot.safetensors"),
  @("$Mirror/text_encoders/gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors", "$Models\text_encoders\gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors"),
  # Main model: official NVFP4 - native FP4 on Blackwell (RTX 50xx)
  @("$Mirror/diffusion_models/ltx-2.5-22b-distilled-transformer-nvfp4.safetensors", "$Models\diffusion_models\ltx-2.5-22b-distilled-transformer-nvfp4.safetensors")
)
$extras = @(
  # Smaller NVFP4 (12.6 GB) - fits fully in 16 GB VRAM
  @("$DDB/diffusion_models/ltx-2.5-22b-distilled-transformer-nvfp4.safetensors", "$Models\diffusion_models\ltx-2.5-22b-distilled-transformer-nvfp4-ddb.safetensors"),
  @("$GGUF/ltx-2.5-22b-distilled-transformer-bf16-Q4_K_M.gguf", "$Models\diffusion_models\ltx-2.5-22b-distilled-transformer-Q4_K_M.gguf")
)
$int8 = @(
  # Highest quality that is still practical (20.5 GB, needs RAM offload)
  @("$Mirror/diffusion_models/ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors", "$Models\diffusion_models\ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors")
)

$list = switch ($Phase) {
  "comfy" { $comfy } "core" { $core } "extras" { $extras } "int8" { $int8 }
  "models" { $core + $extras + $int8 } default { $comfy + $core + $extras + $int8 }
}

function Say($m) { $l = "[{0:HH:mm:ss}] {1}" -f (Get-Date), $m; Write-Host $l; Add-Content $Log $l }

foreach ($item in $list) {
  $url, $dest = $item
  if (Test-Path $dest) { Say "skip (done) $(Split-Path $dest -Leaf)"; continue }
  New-Item -ItemType Directory -Force (Split-Path $dest) | Out-Null
  Say "start $(Split-Path $dest -Leaf)"
  $part = "$dest.part"
  for ($try = 1; $try -le 20; $try++) {
    curl.exe -L -C - --retry 5 --retry-delay 5 -s -S -f -o $part $url
    if ($LASTEXITCODE -eq 0) { break }
    Say "  retry $try (curl exit $LASTEXITCODE)"; Start-Sleep 5
  }
  if ($LASTEXITCODE -ne 0) { Say "FAILED $(Split-Path $dest -Leaf)"; continue }
  Move-Item $part $dest -Force
  Say ("done  {0} ({1:N0} MB)" -f (Split-Path $dest -Leaf), ((Get-Item $dest).Length / 1MB))
}
Say "phase '$Phase' finished"
