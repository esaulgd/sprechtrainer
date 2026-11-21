#!/usr/bin/env python3
"""Count how many cards have native-speaker recordings on German Wiktionary (Wikimedia Commons).

For each card it looks up the card text without the article (der Kaffee -> Kaffee) and, for
multi-word cards, also the phrase itself, then lists the audio files the Wiktionary page uses.
Writes qa/native-audio.json and qa/native-audio.md. Read-only: downloads no audio.
"""
import json, os, re, time, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = "https://de.wiktionary.org/w/api.php"
UA = {"User-Agent": "Sprechtrainer/0.7 (personal language-learning app; github.com/esaulgd/sprechtrainer)"}
AUDIO = re.compile(r"\.(ogg|oga|opus|wav|mp3|flac)$", re.I)

def card_texts():
    html = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
    raw = re.search(r"const VOCAB_RAW = `(.*?)`;", html, re.S).group(1)
    seen, out = set(), []
    for line in raw.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 3 and parts[1] and parts[1] not in seen:
            seen.add(parts[1]); out.append((parts[0], parts[1]))
    return out

def page_audio(title):
    q = urllib.parse.urlencode({"action": "query", "format": "json", "prop": "images", "titles": title,
                                "imlimit": "100", "redirects": "1"})
    with urllib.request.urlopen(urllib.request.Request(f"{API}?{q}", headers=UA), timeout=30) as r:
        data = json.load(r)
    files = []
    for page in data.get("query", {}).get("pages", {}).values():
        for im in page.get("images", []):
            name = im["title"].split(":", 1)[1]
            if AUDIO.search(name):
                files.append(name)
    return files

def region(name):
    n = name.lower()
    if n.startswith("de-at-") or "österreich" in n: return "AT"
    if n.startswith("de-ch-") or "schweiz" in n: return "CH"
    if n.startswith("ll-q188"): return "Lingua Libre"
    if n.startswith("de-"): return "DE"
    return "other"

def lookups(text):
    plain = re.sub(r"[.!?,]", "", text).strip()
    m = re.match(r"^(der|die|das) (\S+)$", plain)
    if m: return [m.group(2)], "noun"
    if " " not in plain: return [plain], "word"
    return [plain], "phrase"

def main():
    results = []
    for group, text in card_texts():
        titles, kind = lookups(text)
        found = []
        for t in titles:
            try:
                found += [{"file": f, "region": region(f), "page": t} for f in page_audio(t)]
            except Exception as e:
                found.append({"error": str(e), "page": t})
            time.sleep(0.3)
        good = [f for f in found if "file" in f]
        results.append({"card": text, "group": group, "kind": kind, "looked_up": titles,
                        "has_native": bool(good), "germany": any(f["region"] in ("DE", "Lingua Libre") for f in good),
                        "files": found})
    os.makedirs(os.path.join(ROOT, "qa"), exist_ok=True)
    json.dump(results, open(os.path.join(ROOT, "qa", "native-audio.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    by = {}
    for r in results:
        k = by.setdefault(r["kind"], [0, 0, 0]); k[0] += 1; k[1] += r["has_native"]; k[2] += r["germany"]
    lines = ["# Native recordings on German Wiktionary", "",
             f"{sum(r['has_native'] for r in results)} of {len(results)} cards have at least one recording "
             f"({sum(r['germany'] for r in results)} with a Germany-standard or Lingua Libre one).", "",
             "| Card type | Cards | With recording | With DE / Lingua Libre |", "|---|---|---|---|"]
    names = {"noun": "der/die/das + noun", "word": "single word", "phrase": "phrase"}
    for k in ("noun", "word", "phrase"):
        if k in by: lines.append(f"| {names[k]} | {by[k][0]} | {by[k][1]} | {by[k][2]} |")
    lines += ["", "## Cards without a recording", ""]
    lines += [f"- {r['card']}" for r in results if not r["has_native"]]
    lines += ["", "## Cards with recordings", "", "| Card | Files (region) |", "|---|---|"]
    for r in results:
        if r["has_native"]:
            lines.append(f"| {r['card']} | " + ", ".join(f"{f['file']} ({f['region']})" for f in r["files"] if "file" in f) + " |")
    open(os.path.join(ROOT, "qa", "native-audio.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines[:12]))

if __name__ == "__main__":
    main()
