"""用 Whisper large-v3-turbo 把视频/音频转录成 static/txt/*.json（另附一份带时间轴的可读 .txt）。

输出 JSON 结构：{"text": ..., "segments": [{"start", "end", "text"}, ...], ...}
字段与 app.py 的 get_video_ts() 一致：用 text 定位命中位置，再用 segments 的 start 换算视频时间点。
其中 text 由各 segment 文本依次拼接，保证与 app.py 的累加定位逻辑严格对齐。

用法：
    python transcribe_video.py                      # 转 static/video 下的默认视频（自动识别语言）
    python transcribe_video.py <视频路径>            # 指定输入
    python transcribe_video.py <视频路径> --language en
    python transcribe_video.py <视频路径> --threads 4   # 手动限定 CPU 线程数
    python transcribe_video.py --list               # 列出 static/txt 里已有的结果

推理固定在 CPU 上（--device cpu 是默认值）：本机所有 conda 环境装的都是 torch 的 CPU 版本，
实测 large-v3-turbo 约 1 倍实时速度，7 分钟的片子大约跑 8 分钟。

运行环境：需要 torch + numpy，本机用 Anaconda 的 py39 环境（torch 2.0.1 CPU）：
    C:\\ProgramData\\Anaconda3\\envs\\py39\\python.exe transcribe_video.py

whisper 使用 py39 环境已安装的版本（需 >= 20250625 以支持 large-v3-turbo）
large-v3-turbo（它用 n_vocab == 51865 判断是否多语言，而 turbo 是 51866，会被当成英文模型）。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TEXT_DIR = REPO / "static" / "txt"
VIDEO_DIR = REPO / "static" / "video"
TMP_AUDIO_DIR = REPO / "_tmp_audio"
DEFAULT_MODEL = Path(r"D:\models\whisper\large-v3-turbo.pt")
DEFAULT_VIDEO = VIDEO_DIR / "2.02 - 1.1什么是神经网络(Av73508149,P2).mp4"

# CPU 推理线程数；0 表示交给 torch 自己决定。
# 实测（60s 音频 / 6 逻辑核）：6 线程 51.9s(1.16x 实时) > 3 线程 59.7s > 2 线程 78.6s，
# 所以这台机器上默认值（=全部逻辑核）就是最优的，不必手动调。
CPU_THREADS = 0



# Windows 控制台默认 GBK，whisper 可能吐出任意 Unicode 字符（幻觉），不改成 UTF-8 会崩
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def find_ffmpeg() -> str:
    local = shutil.which("ffmpeg")
    if local:
        return local
    for candidate in (r"C:\ffmpeg\bin\ffmpeg.exe", "/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if Path(candidate).exists():
            os.environ["PATH"] = str(Path(candidate).parent) + os.pathsep + os.environ.get("PATH", "")
            return candidate
    raise SystemExit("找不到 ffmpeg，请安装或把 ffmpeg 加入 PATH")


def decode_to_wav(media: Path, wav_path: Path) -> None:
    """用 ffmpeg 解码成 16kHz 单声道 wav。

    刻意让 ffmpeg 直接写文件、不捕获它的输出：whisper 自带的 load_audio() 用
    subprocess.PIPE 读取 ffmpeg，在管道受限的环境里会报 WinError 5。
    """
    ffmpeg = find_ffmpeg()
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg, "-nostdin", "-y", "-v", "error",
        "-i", str(media),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        str(wav_path),
    ]
    started = time.time()
    print(f"[2/4] ffmpeg 解码音频 -> {wav_path.name}", flush=True)
    subprocess.run(cmd, check=True)
    print(f"      完成，用时 {time.time() - started:.1f}s，{wav_path.stat().st_size / 1e6:.1f} MB", flush=True)


def read_wav_mono16k(wav_path: Path):
    """读成 float32 numpy 数组，避免 whisper 内部再调 ffmpeg。"""
    import numpy as np

    with wave.open(str(wav_path), "rb") as fh:
        if fh.getframerate() != 16000 or fh.getnchannels() != 1 or fh.getsampwidth() != 2:
            raise SystemExit("wav 参数不符合预期（需要 16kHz/单声道/16bit）")
        frames = fh.readframes(fh.getnframes())
    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    print(f"      采样点 {audio.shape[0]}，时长 {audio.shape[0] / 16000:.1f}s", flush=True)
    return audio


def output_stem(source: Path, language: str) -> str:
    """英文结果沿用项目里的 _en 后缀约定（如 static/txt/懂王50懂_en.json）。"""
    return source.stem + ("_en" if language == "en" else "")


def main() -> None:
    parser = argparse.ArgumentParser(description="Whisper large-v3-turbo 视频转录")
    parser.add_argument("media", nargs="?", default=str(DEFAULT_VIDEO), help="视频或音频文件路径")
    parser.add_argument("--model", default=str(DEFAULT_MODEL), help="whisper 模型名或 .pt 路径")
    parser.add_argument("--language", default="auto", help="语言代码（zh / en ...），auto 表示自动识别")
    parser.add_argument("--device", default="cpu", help="推理设备：cpu（默认）/ cuda")
    parser.add_argument("--threads", type=int, default=CPU_THREADS, help="CPU 线程数，0 表示交给 torch 自己决定")
    parser.add_argument("--out-dir", default=str(TEXT_DIR), help="输出目录")
    parser.add_argument("--verbose", action="store_true", help="打印 whisper 的分段时间轴")
    parser.add_argument("--list", action="store_true", help="列出已转好的结果后退出")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    if args.list:
        for path in sorted(out_dir.glob("*.json")):
            print(f"{path.stat().st_size / 1024:8.1f} KB  {path.name}")
        return

    media = Path(args.media)
    if not media.exists():
        raise SystemExit(f"输入文件不存在：{media}")
    model_path = Path(args.model)
    builtin = {"tiny", "base", "small", "medium", "large", "turbo", "large-v3-turbo"}
    if not model_path.exists() and args.model not in builtin:
        raise SystemExit(f"模型不存在：{model_path}")

    print(f"输入：{media.name}")
    print(f"模型：{Path(args.model).name}")

    import whisper
    import torch

    if args.device == "cuda" and not torch.cuda.is_available():
        raise SystemExit("当前 torch 是 CPU 版本（或没有可用显卡），无法使用 cuda；请去掉 --device cuda")
    if args.threads > 0:
        torch.set_num_threads(args.threads)
    print(f"      torch {torch.__version__}，cuda 可用={torch.cuda.is_available()}，"
          f"推理设备={args.device}，CPU 线程数={torch.get_num_threads()}", flush=True)

    print(f"[1/4] 加载模型（whisper {getattr(whisper, '__version__', '?')}）", flush=True)
    started = time.time()
    model = whisper.load_model(
        str(args.model) if model_path.exists() else args.model,
        device=args.device,
    )
    print(f"      完成，用时 {time.time() - started:.1f}s，多语言={model.is_multilingual}", flush=True)

    wav_path = TMP_AUDIO_DIR / (media.stem + ".wav")
    decode_to_wav(media, wav_path)
    audio = read_wav_mono16k(wav_path)
    duration = len(audio) / 16000.0

    print("[3/4] 开始转录（CPU 推理，请耐心等待）", flush=True)
    started = time.time()
    result = model.transcribe(
        audio,
        language=None if args.language == "auto" else args.language,
        task="transcribe",
        fp16=False,
        verbose=args.verbose,
        # 抑制长静音处的幻觉（本视频结尾就幻觉出了一段不存在的内容）
        hallucination_silence_threshold=2.0,
    )
    elapsed = time.time() - started

    segments = []
    dropped = 0
    for seg in result["segments"]:
        start, end = float(seg["start"]), float(seg["end"])
        if start >= duration:  # 超出音频长度的幻觉片段直接丢掉
            dropped += 1
            continue
        segments.append({"start": start, "end": min(end, duration), "text": seg["text"]})

    language = result.get("language") or args.language
    payload = {
        "text": "".join(seg["text"] for seg in segments),  # 与 app.py 累加定位严格一致
        "segments": segments,
        "language": language,
        "source": media.name,
        "model": Path(args.model).name,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / (output_stem(media, language) + ".json")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    txt_path = out_dir / (output_stem(media, language) + ".txt")
    with txt_path.open("w", encoding="utf-8") as fh:
        for seg in segments:
            fh.write(f"[{seg['start']:8.2f} - {seg['end']:8.2f}] {seg['text'].strip()}\n")

    wav_path.unlink(missing_ok=True)
    if not any(TMP_AUDIO_DIR.iterdir()):
        TMP_AUDIO_DIR.rmdir()

    print(f"[4/4] 完成：转录耗时 {elapsed:.1f}s（音频 {duration:.1f}s，约 {duration / elapsed:.1f}x 实时）")
    print(f"      JSON：{json_path}  ({json_path.stat().st_size / 1024:.1f} KB)")
    print(f"      文本：{txt_path}")
    print(f"      语言：{language}（自动识别），片段数：{len(segments)}，丢弃越界幻觉片段：{dropped}")
    print("------ 前 300 字 ------")
    print(payload["text"][:300])


if __name__ == "__main__":
    main()
