#!/usr/bin/env python3
"""Quality check for the model audio: score every audio file with Azure pronunciation
assessment (de-DE) against its own text, and write a report of the weakest ones.

Usage (needs ffmpeg):
  AZURE_SPEECH_KEY=... AZURE_SPEECH_REGION=japaneast python3 tools/check_audio.py

Writes qa/model-audio-report.json and qa/model-audio-report.md.
Synthetic speech should score very high; a low score marks a file worth listening to.
"""
import base64, json, os, subprocess, sys, time, urllib.error, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLAG_OVERALL, FLAG_WORD, FLAG_PHONE = 85, 80, 60

def to_wav16k(path):
    return subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", "16000",
                           "-sample_fmt", "s16", "-f", "wav", "-"], check=True, capture_output=True).stdout

def assess(text, wav, key, region):
    params = {"ReferenceText": text, "GradingSystem": "HundredMark", "Granularity": "Phoneme",
              "Dimension": "Comprehensive", "EnableMiscue": True}
    req = urllib.request.Request(
        f"https://{region}.stt.speech.microsoft.com/speech/recognition/conversation/cognitiveservices/v1"
        "?language=de-DE&format=detailed", data=wav, method="POST",
        headers={"Ocp-Apim-Subscription-Key": key,
                 "Content-Type": "audio/wav; codecs=audio/pcm; samplerate=16000",
                 "Accept": "application/json",
                 "Pronunciation-Assessment": base64.b64encode(json.dumps(params).encode()).decode()})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 4:
                time.sleep(3 + attempt * 5); continue
            raise

def summarize(text, res):
    if res.get("RecognitionStatus") != "Success" or not res.get("NBest"):
        return {"text": text, "status": res.get("RecognitionStatus", "NoResult"), "overall": None, "words": []}
    nb = res["NBest"][0]
    pa = nb.get("PronunciationAssessment", nb)
    words = []
    for w in nb.get("Words", []):
        p = w.get("PronunciationAssessment", w)
        words.append({"word": w.get("Word"), "score": p.get("AccuracyScore"), "error": p.get("ErrorType", "None"),
                      "phones": [round((ph.get("PronunciationAssessment", ph)).get("AccuracyScore", 0))
                                 for ph in w.get("Phonemes", [])]})
    return {"text": text, "status": "Success", "heard": nb.get("Display", ""),
            "overall": pa.get("PronScore"), "accuracy": pa.get("AccuracyScore"),
            "fluency": pa.get("FluencyScore"), "completeness": pa.get("CompletenessScore"), "words": words}

def flagged(r):
    if r["overall"] is None: return True
    if r["overall"] < FLAG_OVERALL: return True
    return any(w["error"] != "None" or (w["score"] or 0) < FLAG_WORD or any(p < FLAG_PHONE for p in w["phones"])
               for w in r["words"])

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="audio", help="folder with manifest.json and the audio files")
    ap.add_argument("--out", default="qa/model-audio-report", help="report path without extension")
    a = ap.parse_args()
    key, region = os.environ.get("AZURE_SPEECH_KEY"), os.environ.get("AZURE_SPEECH_REGION", "japaneast")
    if not key: sys.exit("Set AZURE_SPEECH_KEY.")
    adir = os.path.join(ROOT, a.dir)
    manifest = json.load(open(os.path.join(adir, "manifest.json"), encoding="utf-8"))
    results = []
    for text, f in manifest["files"].items():
        try:
            res = assess(text, to_wav16k(os.path.join(adir, f)), key, region)
            results.append(summarize(text, res))
        except Exception as e:
            results.append({"text": text, "status": f"error: {e}", "overall": None, "words": []})
        time.sleep(0.4)
    results.sort(key=lambda r: (r["overall"] is not None, r["overall"] or 0))
    out = os.path.join(ROOT, a.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump({"voice": manifest.get("voice"), "results": results},
              open(out + ".json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    bad = [r for r in results if flagged(r)]
    scores = [r["overall"] for r in results if r["overall"] is not None]
    ov = manifest.get("overrides") or {}
    title = manifest.get("voice") + (f", overrides: {', '.join(f'{k} = {v}' for k, v in ov.items())}" if ov else "")
    lines = [f"# Model audio check ({title})", "",
             f"{len(results)} cards scored. Median overall: {sorted(scores)[len(scores)//2] if scores else '–'}. "
             f"Flagged: {len(bad)} (overall < {FLAG_OVERALL}, a word < {FLAG_WORD}, a sound < {FLAG_PHONE}, or a word error).", "",
             "| Card | Overall | Heard | Weak words (score, sound scores) |", "|---|---|---|---|"]
    for r in bad:
        weak = "; ".join(f"{w['word']} {round(w['score'] or 0)}{' ' + w['error'] if w['error'] != 'None' else ''} {w['phones']}"
                         for w in r["words"] if w["error"] != "None" or (w["score"] or 0) < FLAG_WORD or any(p < FLAG_PHONE for p in w["phones"]))
        ov = r["overall"] if r["overall"] is None else round(r["overall"])
        lines.append(f"| {r['text']} | {ov if ov is not None else r['status']} | {r.get('heard', '')} | {weak} |")
    open(out + ".md", "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines))

if __name__ == "__main__":
    main()
