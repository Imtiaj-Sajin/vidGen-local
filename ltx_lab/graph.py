"""Builds a ComfyUI API-format graph for LTX-2.5 distilled text/image-to-video (+audio).

Mirrors Lightricks' official LTX-2.5_T2V_I2V_{Two,Single}_Stage_Distilled workflows
(local Gemma encoder path only, no cloud API branch).
"""

STAGE1_SIGMAS = "1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0"
STAGE2_SIGMAS = "0.909375, 0.725, 0.421875, 0.0"

DEFAULTS = {
    "model": "ltx-2.5-22b-distilled-transformer-nvfp4.safetensors",
    "text_encoder": "gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors",
    "video_vae": "ltx-2.5-video-vae-bf16.safetensors",
    "audio_vae": "ltx-2.5-audio-vae-bf16.safetensors",
    "upscaler": "ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors",
    "two_stage": True,
    "width": 1088,          # final output size; stage 1 runs at half
    "height": 1920,
    "duration": 10.0,
    "fps": 24,
    "seed": 42,
    "prompt": "",
    "negative": "",
    "image": None,          # filename in ComfyUI input/ folder, or None for text-to-video
    "image_strength": 0.7,
    "img_compression": 18,
    "tile_size": 512,
    "tile_overlap": 64,
    "temporal_size": 128,
    "temporal_overlap": 32,
    "prefix": "ltxlab/run",
}


def snap32(v):
    return max(32, int(round(v / 32.0)) * 32)


def frame_count(fps, seconds):
    # LTX needs 1 + 8n frames
    return 1 + int(fps * seconds // 8) * 8


def stage_sizes(s):
    """Returns (stage1_w, stage1_h, final_w, final_h)."""
    if s["two_stage"]:
        w1, h1 = snap32(s["width"] / 2), snap32(s["height"] / 2)
        return w1, h1, w1 * 2, h1 * 2
    w, h = snap32(s["width"]), snap32(s["height"])
    return w, h, w, h


def build(settings):
    s = dict(DEFAULTS)
    s.update({k: v for k, v in settings.items() if v is not None or k == "image"})
    fps = float(s["fps"])
    length = frame_count(fps, float(s["duration"]))
    w1, h1, wf, hf = stage_sizes(s)
    has_image = bool(s["image"])
    g = {}

    def node(nid, cls, **inputs):
        g[nid] = {"class_type": cls, "inputs": inputs}
        return nid

    # --- models
    if s["model"].lower().endswith(".gguf"):
        node("model", "UnetLoaderGGUF", unet_name=s["model"])
    else:
        node("model", "UNETLoader", unet_name=s["model"], weight_dtype="default")
    node("clip", "CLIPLoader", clip_name=s["text_encoder"], type="ltxv", device="default")
    node("vae", "VAELoader", vae_name=s["video_vae"])
    node("avae", "VAELoader", vae_name=s["audio_vae"])

    # --- prompts
    node("pos", "CLIPTextEncode", clip=["clip", 0], text=s["prompt"])
    node("neg", "CLIPTextEncode", clip=["clip", 0], text=s["negative"])
    node("cond", "LTXVConditioning", positive=["pos", 0], negative=["neg", 0], frame_rate=fps)

    # --- latents (video + audio)
    node("empty", "EmptyLTXVLatentVideo", width=w1, height=h1, length=length, batch_size=1)
    node("empty_audio", "LTXVEmptyLatentAudio", audio_vae=["avae", 0], frames_number=length,
         frame_rate=int(round(fps)), batch_size=1)
    if has_image:
        node("img", "LoadImage", image=s["image"])
        node("img_resized", "ResizeImageMaskNode", input=["img", 0],
             **{"resize_type": "scale longer dimension", "resize_type.longer_size": 1536,
                "scale_method": "lanczos"})
        node("img_pre", "LTXVPreprocess", image=["img_resized", 0], img_compression=s["img_compression"])
        node("i2v1", "LTXVImgToVideoInplace", vae=["vae", 0], image=["img_pre", 0], latent=["empty", 0],
             strength=s["image_strength"], bypass=False)
        video_latent = ["i2v1", 0]
    else:
        video_latent = ["empty", 0]
    node("av1", "LTXVConcatAVLatent", video_latent=video_latent, audio_latent=["empty_audio", 0])

    # --- stage 1: 8 distilled steps
    node("noise1", "RandomNoise", noise_seed=int(s["seed"]))
    node("guider1", "CFGGuider", model=["model", 0], positive=["cond", 0], negative=["cond", 1], cfg=1.0)
    node("sampler1", "KSamplerSelect", sampler_name="euler_ancestral")
    node("sigmas1", "ManualSigmas", sigmas=STAGE1_SIGMAS)
    node("sample1", "SamplerCustomAdvanced", noise=["noise1", 0], guider=["guider1", 0],
         sampler=["sampler1", 0], sigmas=["sigmas1", 0], latent_image=["av1", 0])
    node("sep1", "LTXVSeparateAVLatent", av_latent=["sample1", 0])
    final_video, final_audio = ["sep1", 0], ["sep1", 1]

    # --- stage 2: 2x latent upscale + 3 refine steps
    if s["two_stage"]:
        node("upmodel", "LatentUpscaleModelLoader", model_name=s["upscaler"])
        node("up", "LTXVLatentUpsampler", samples=["sep1", 0], upscale_model=["upmodel", 0], vae=["vae", 0])
        if has_image:
            node("i2v2", "LTXVImgToVideoInplace", vae=["vae", 0], image=["img_pre", 0], latent=["up", 0],
                 strength=1.0, bypass=False)
            up_latent = ["i2v2", 0]
        else:
            up_latent = ["up", 0]
        node("av2", "LTXVConcatAVLatent", video_latent=up_latent, audio_latent=["sep1", 1])
        node("noise2", "RandomNoise", noise_seed=int(s["seed"]))
        node("guider2", "CFGGuider", model=["model", 0], positive=["cond", 0], negative=["cond", 1], cfg=1.0)
        node("sampler2", "KSamplerSelect", sampler_name="euler")
        node("sigmas2", "ManualSigmas", sigmas=STAGE2_SIGMAS)
        node("sample2", "SamplerCustomAdvanced", noise=["noise2", 0], guider=["guider2", 0],
             sampler=["sampler2", 0], sigmas=["sigmas2", 0], latent_image=["av2", 0])
        node("sep2", "LTXVSeparateAVLatent", av_latent=["sample2", 0])
        final_video, final_audio = ["sep2", 0], ["sep2", 1]

    # --- decode + save mp4 with audio
    node("decode", "VAEDecodeTiled", samples=final_video, vae=["vae", 0], tile_size=s["tile_size"],
         overlap=s["tile_overlap"], temporal_size=s["temporal_size"], temporal_overlap=s["temporal_overlap"])
    node("adecode", "LTXVAudioVAEDecode", samples=final_audio, audio_vae=["avae", 0])
    node("video", "CreateVideo", images=["decode", 0], audio=["adecode", 0], fps=fps)
    node("save", "SaveVideo", video=["video", 0], filename_prefix=s["prefix"], **{"format": "auto", "format.codec": "auto"})

    info = {"frames": length, "stage1": f"{w1}x{h1}", "output": f"{wf}x{hf}"}
    return g, info
