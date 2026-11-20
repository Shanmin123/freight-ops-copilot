"""Download opt-in, local demo assets from their published sources."""

import argparse
import hashlib
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1] / "backend" / "var"
MODEL = "https://huggingface.co/Xenova/multilingual-e5-small/resolve/761b726dd34fb83930e26aab4e9ac3899aa1fa78/"
FONTS = "https://raw.githubusercontent.com/google/fonts/main/ofl/"


def download(client: httpx.Client, destination: str, url: str, checksum: str | None = None) -> None:
    path = ROOT / destination
    if path.is_file() and (not checksum or hashlib.sha256(path.read_bytes()).hexdigest() == checksum):
        print(f"Ready: {destination}", flush=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".download")
    digest = hashlib.sha256()
    try:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            with temporary.open("wb") as file:
                for chunk in response.iter_bytes():
                    digest.update(chunk)
                    file.write(chunk)
        if checksum and digest.hexdigest() != checksum:
            raise ValueError(f"Checksum mismatch: {destination}")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Downloaded: {destination} ({path.stat().st_size:,} bytes)", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fonts", action="store_true", help="Download Noto Sans SC and KR, around 28 MB.")
    parser.add_argument("--embeddings", action="store_true", help="Download the quantized local E5 model, around 135 MB.")
    args = parser.parse_args()
    if not args.fonts and not args.embeddings:
        parser.error("select --fonts, --embeddings, or both")
    with httpx.Client(follow_redirects=True, timeout=90) as client:
        if args.fonts:
            for lang in ("SC", "KR"):
                base = FONTS + "notosans" + lang.lower() + "/"
                download(client, f"fonts/NotoSans{lang}.ttf", base + f"NotoSans{lang}%5Bwght%5D.ttf")
                download(client, f"fonts/OFL-{lang}.txt", base + "OFL.txt")
        if args.embeddings:
            download(client, "kb-model/tokenizer.json", MODEL + "tokenizer.json",
                     "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39")
            download(client, "kb-model/model_quantized.onnx", MODEL + "onnx/model_quantized.onnx",
                     "f80102d3f2a1229f387d3c81909990d8945513e347b0eab049f7de3c6f98c193")
