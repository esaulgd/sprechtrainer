#!/usr/bin/env python3
"""Pick native-speaker recordings from Wiktionary / Wikimedia Commons for each card.

Steps per card (candidates come from qa/native-audio.json, made by check_native.py):
  1. Download each candidate file from Commons (with author + license).
  2. Trim silence, normalise loudness, convert to mono MP3 (ffmpeg).
  3. Score it with Azure pronunciation assessment against the word itself.
  4. Keep the best-scoring file per origin: German, Swiss, and Austrian (Austrian only when
     there is no German or Swiss one). Files that score badly are dropped.

A recording passes if it scores >= 70, or (for words Azure's scorer handles badly) if it is
within 15 points of what Azure's own model voice scores on that word.

Usage: AZURE_SPEECH_KEY=... AZURE_SPEECH_REGION=japaneast python3 tools/fetch_native.py
Writes audio/native/*.mp3, audio/native/manifest.json and qa/native-selection.md.
"""
import hashlib, json, os, re, subprocess, sys, time, urllib.error, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_audio  # noqa: E402  (to_wav16k, assess, summarize)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "audio", "native")
COMMONS = "https://commons.wikimedia.org/w/api.php"
UA = {"User-Agent": "Sprechtrainer/0.8 (personal language-learning app; github.com/esaulgd/sprechtrainer)"}
PASS_ABS, PASS_REL = 70, 15

def origin(name):
    """german / swiss / austrian / other (Lingua Libre, CPIDL, unprefixed: region not stated)."""
    n = name.lower()
    if n.startswith("de-ch-"): return "swiss"
    if n.startswith("de-at-"): return "austrian"
    if n.startswith("de-"): return "german"
    return "other"

def http_json(url, params):
    q = urllib.parse.urlencode({"format": "json", **params})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(urllib.request.Request(f"{url}?{q}", headers=UA), timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 5:
                time.sleep(int(e.headers.get("Retry-After") or 0) or 5 * (attempt + 1)); continue
            raise

def file_info(names):
    """Commons URL, author and license for up to 50 files."""
    out = {}
    data = http_json(COMMONS, {"action": "query", "prop": "imageinfo", "iiprop": "url|extmetadata",
                               "titles": "|".join("File:" + n for n in names)})
    back = {n["to"]: n["from"] for n in data.get("query", {}).get("normalized", [])}
    for page in data.get("query", {}).get("pages", {}).values():
        ii = (page.get("imageinfo") or [None])[0]
        if not ii: continue
        meta = ii.get("extmetadata", {})
        strip = lambda v: re.sub(r"<[^>]+>", "", (v or {}).get("value", "")).strip()
        title = back.get(page["title"], page["title"]).split(":", 1)[1]
        out[title] = {"url": ii["url"], "page": ii.get("descriptionurl", ""),
                      "artist": strip(meta.get("Artist")) or "unknown",
                      "license": strip(meta.get("LicenseShortName")) or "see Commons"}
    return out

def download(url):
    for attempt in range(6):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 5:
                time.sleep(int(e.headers.get("Retry-After") or 0) or 5 * (attempt + 1)); continue
            raise

TRIM = ("silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.12,areverse,"
        "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.2,areverse,"
        "loudnorm=I=-18:TP=-2:LRA=11")

def to_mp3(raw, src_name):
    ext = os.path.splitext(src_name)[1] or ".ogg"
    tmp = os.path.join("/tmp", "native_in" + ext)
    open(tmp, "wb").write(raw)
    return subprocess.run(["ffmpeg", "-v", "error", "-i", tmp, "-af", TRIM, "-ac", "1", "-ar", "24000",
                           "-b:a", "64k", "-f", "mp3", "-"], check=True, capture_output=True).stdout

def duration(mp3):
    p = os.path.join("/tmp", "native_dur.mp3"); open(p, "wb").write(mp3)
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p],
                         capture_output=True, text=True).stdout.strip()
    return float(out or 0)

def main():
    key, region = os.environ.get("AZURE_SPEECH_KEY"), os.environ.get("AZURE_SPEECH_REGION", "japaneast")
    if not key: sys.exit("Set AZURE_SPEECH_KEY.")
    cards = json.load(open(os.path.join(ROOT, "qa", "native-audio.json"), encoding="utf-8"))
    base = {r["text"]: r for r in json.load(open(os.path.join(ROOT, "qa", "model-audio-report.json"), encoding="utf-8"))["results"]}
    os.makedirs(OUT, exist_ok=True)

    names = sorted({f["file"] for c in cards for f in c["files"]})
    info = {}
    for i in range(0, len(names), 50):
        info.update(file_info(names[i:i + 50])); time.sleep(1)

    manifest, report = {}, []
    for c in cards:
        if not c["has_native"]: continue
        word = c["looked_up"][0]
        # Azure's own model voice score on this word (lowest over its words), to judge the scorer
        b = base.get(c["card"], {})
        bwords = [w["score"] for w in b.get("words", []) if w.get("score") is not None and
                  re.sub(r"\W", "", w["word"]).casefold() in {re.sub(r"\W", "", x).casefold() for x in word.split()}]
        baseline = min(bwords) if bwords else None
        scored = []
        for f in c["files"]:
            name, meta = f["file"], info.get(f["file"])
            row = {"file": name, "origin": origin(name)}
            if not meta:
                row["result"] = "not on Commons"; scored.append(row); continue
            try:
                mp3 = to_mp3(download(meta["url"]), name)
                dur = duration(mp3)
                if not 0.2 <= dur <= 6:
                    row["result"] = f"rejected: {dur:.1f} s long"; scored.append(row); continue
                s = check_audio.summarize(word, check_audio.assess(word, check_audio.to_wav16k_bytes(mp3), key, region))
                score = s["overall"]
                ok = score is not None and (score >= PASS_ABS or (baseline is not None and score >= baseline - PASS_REL))
                row.update({"score": round(score) if score is not None else None, "heard": s.get("heard", ""),
                            "duration": round(dur, 2), "result": "ok" if ok else "rejected: low score",
                            "_mp3": mp3 if ok else None, **{k: meta[k] for k in ("page", "artist", "license")}})
            except Exception as e:
                row["result"] = f"error: {e}"
            scored.append(row)
            time.sleep(0.4)
        good = [r for r in scored if r["result"] == "ok"]
        pick = {}
        # German: prefer "De-" files; region-unknown files (Lingua Libre etc.) only as a fallback
        for org in ("german", "swiss", "austrian"):
            cand = [r for r in good if r["origin"] == org]
            if org == "german" and not cand:
                cand = [r for r in good if r["origin"] == "other"]
            if cand: pick[org] = max(cand, key=lambda r: r["score"])
        if ("german" in pick or "swiss" in pick) and "austrian" in pick:
            del pick["austrian"]                       # Austrian only when it's the only option
        entry = {"word": word}
        for org, r in pick.items():
            fn = "native/" + hashlib.sha1(r["file"].encode()).hexdigest()[:12] + ".mp3"
            open(os.path.join(ROOT, "audio", fn), "wb").write(r["_mp3"])
            entry[org] = {"file": fn, "source": r["file"], "page": r["page"], "artist": r["artist"],
                          "license": r["license"], "score": r["score"]}
            r["picked"] = org
        if len(entry) > 1: manifest[c["card"]] = entry
        report.append({"card": c["card"], "word": word, "baseline": baseline,
                       "candidates": [{k: v for k, v in r.items() if k != "_mp3"} for r in scored]})

    used = {e[o]["file"] for e in manifest.values() for o in ("german", "swiss", "austrian") if o in e}
    for f in os.listdir(OUT):
        if f.endswith(".mp3") and "native/" + f not in used: os.remove(os.path.join(OUT, f))
    json.dump({"cards": manifest}, open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    n = lambda o: sum(o in e for e in manifest.values())
    lines = ["# Native recordings selected", "",
             f"{len(manifest)} cards have a native recording: {n('german')} German, {n('swiss')} Swiss, "
             f"{n('austrian')} Austrian (only where nothing else passed).",
             f"A file passes with score >= {PASS_ABS}, or within {PASS_REL} of Azure's own model-voice score on the word.", "",
             "| Card | Picked | Rejected / unused |", "|---|---|---|"]
    for r in report:
        picked = ", ".join(f"{c['picked']}: {c['file']} ({c['score']})" for c in r["candidates"] if c.get("picked")) or "–"
        other = ", ".join(f"{c['file']} ({c.get('score', '–')}, {c['result']})" for c in r["candidates"] if not c.get("picked"))
        lines.append(f"| {r['card']} | {picked} | {other} |")
    open(os.path.join(ROOT, "qa", "native-selection.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines[:4]))

if __name__ == "__main__":
    main()
