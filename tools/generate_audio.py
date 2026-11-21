#!/usr/bin/env python3
"""Generate model audio for every card in index.html with Azure neural TTS.

Usage:
  AZURE_SPEECH_KEY=... AZURE_SPEECH_REGION=japaneast python3 tools/generate_audio.py [--voice de-DE-ConradNeural] [--force]

Only cards without an existing file are generated, unless --force is given.
Writes audio/<hash>.mp3 and audio/manifest.json (German text -> file name).
The key is read from the environment and is never written anywhere.
"""
import argparse, hashlib, json, os, re, sys, time, urllib.request, urllib.error
from xml.sax.saxutils import escape

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIO_DIR = os.path.join(ROOT, "audio")
MANIFEST = os.path.join(AUDIO_DIR, "manifest.json")

def card_texts():
    html = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
    raw = re.search(r"const VOCAB_RAW = `(.*?)`;", html, re.S).group(1)
    seen, out = set(), []
    for line in raw.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 3 and parts[1] and parts[1] not in seen:
            seen.add(parts[1]); out.append(parts[1])
    return out

def file_for(text, voice):
    return hashlib.sha1(f"{voice}|{text}".encode("utf-8")).hexdigest()[:12] + ".mp3"

def synth(text, voice, key, region):
    ssml = (f'<speak version="1.0" xml:lang="de-DE"><voice name="{voice}">'
            f'{escape(text)}</voice></speak>').encode("utf-8")
    req = urllib.request.Request(
        f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1", data=ssml, method="POST",
        headers={"Ocp-Apim-Subscription-Key": key, "Content-Type": "application/ssml+xml",
                 "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3", "User-Agent": "sprechtrainer"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:      # free tier rate limit: back off and retry
                time.sleep(2 + attempt * 3); continue
            raise

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", default="de-DE-ConradNeural")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    key, region = os.environ.get("AZURE_SPEECH_KEY"), os.environ.get("AZURE_SPEECH_REGION", "japaneast")
    if not key: sys.exit("Set AZURE_SPEECH_KEY (and AZURE_SPEECH_REGION).")
    os.makedirs(AUDIO_DIR, exist_ok=True)
    texts = card_texts()
    files, made = {}, 0
    for t in texts:
        name = file_for(t, a.voice)
        path = os.path.join(AUDIO_DIR, name)
        if a.force or not os.path.exists(path):
            data = synth(t, a.voice, key, region)
            open(path, "wb").write(data); made += 1
            time.sleep(0.25)
        files[t] = name
    # drop files no card uses any more
    for f in os.listdir(AUDIO_DIR):
        if f.endswith(".mp3") and f not in files.values():
            os.remove(os.path.join(AUDIO_DIR, f))
    json.dump({"voice": a.voice, "files": files}, open(MANIFEST, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"{len(texts)} cards, {made} generated, voice {a.voice}")

if __name__ == "__main__":
    main()
