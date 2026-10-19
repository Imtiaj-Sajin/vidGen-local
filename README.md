# vidGen: LTX-2.5 local video + audio

Local LTX-2.5 (22B) text/image-to-video **with audio** on a 16 GB RTX GPU, plus **LTX Lab**:
a web page to compare quants/settings with live VRAM/RAM telemetry.

## Install on a new machine
Requires Windows 10/11, an NVIDIA GPU (16 GB+ recommended, RTX 50 series for native NVFP4),
recent NVIDIA driver, Git for Windows, ~120 GB free disk, 48 GB+ RAM recommended.

```powershell
git clone https://github.com/Imtiaj-Sajin/vidGen-local.git
cd vidGen-local
powershell -ExecutionPolicy Bypass -File setup.ps1              # everything (~100 GB of models)
powershell -ExecutionPolicy Bypass -File setup.ps1 -Models core # default model only (~42 GB)
```

## Start
Double-click `Start-ComfyUI.bat`, then open:
- **LTX Lab** (test page): http://127.0.0.1:8188/ltxlab
- **ComfyUI** (full editor): http://127.0.0.1:8188, with official workflows under Workflows → `LTX-2.5_T2V_I2V_Two_Stage_Distilled`

## Models (`ComfyUI_windows_portable/ComfyUI/models/diffusion_models`)
| File | Size | Notes |
|---|---|---|
| `...-nvfp4.safetensors` | 17.8 GB | Official NVFP4. Native FP4 on RTX 50. **Default** |
| `...-nvfp4-ddb.safetensors` | 12.6 GB | Community NVFP4. Fits fully in 16 GB VRAM |
| `...-Q4_K_M.gguf` | 11.6 GB | GGUF, community. Slower dequant, small |
| `...-comfy-int8-convrot.safetensors` | 20.5 GB | Official INT8. Highest quality, offloads to RAM |

Text encoder: `gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot` (official INT8).

## 1080p
Two-stage pipeline: stage 1 at 544×960, then 2× latent upscale and 3 refine steps, giving **1088×1920** output.
Crop 4 px top and bottom in your editor for exact 1080×1920, or just scale; nobody will notice.

## Downloads
`scripts\download.ps1` is resumable. Re-run it if anything failed (see `scripts\download.log`).
Sources: `comfyicu/LTX-2.5` (ungated mirror of `Lightricks/LTX-2.5`), `DmitryDB/LTX-2.5-ComfyUI-Quants`, `agosh/LTX-2.5-Comfy-GGUF`.

## License
LTX-2.x Community License: free for commercial use under $10M annual revenue.
