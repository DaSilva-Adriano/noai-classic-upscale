# NOAI Classic Upscale

Batch-upscale videos with FFmpeg using only classic interpolators (**bicubic** and **lanczos**). Interpolation only — no denoise, sharpen, unsharp, eq, or other enhancement filters.

This is a **non-AI** baseline scaler for A/B comparison against AI upscales. It is **not** NVIDIA RTX Video Super Resolution (`rtx-vsr-lab`).

## Requirements

- Python 3.8+ with tkinter (standard library)
- FFmpeg (default: `C:\VSR\ffmpeg-9.0.1-full_build\bin\ffmpeg.exe`)

No extra pip packages.

## Run

Double-click `launch.bat`, or:

```bash
bash launch.sh
```

```text
python noai_classic_upscale.py
```

If `python` opens the Microsoft Store stub, use `launch.bat` / `launch.sh`.

## Output

Writes next to each source, never overwrites the original:

- `shot01.mp4` → `shot01-bicubic.mp4` and `shot01-lanczos.mp4`
- `my.video.final.mp4` → `my.video.final-bicubic.mp4`

Default encode: 4K `3840x2160`, `libx265`, CRF 12, preset `medium`, `yuv420p`, `hvc1`, audio copy.

## License

[GNU General Public License v3.0](LICENSE) (GPL-3.0-or-later).

Copyright (C) 2026 Adriano.
