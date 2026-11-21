#!/usr/bin/env python3
"""Count how many cards have native-speaker recordings on German Wiktionary (Wikimedia Commons).

For each card it looks up the card text without the article (der Kaffee -> Kaffee) and, for
multi-word cards, also the phrase itself, then lists the audio files the Wiktionary page uses.
Writes qa/native-audio.json and qa/native-audio.md. Read-only: downloads no audio.
"""
import json, os, re, time, urllib.error, urllib.parse, urllib.request

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

def api(params):
    q = urllib.parse.urlencode({"format": "json", **params})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(urllib.request.Request(f"{API}?{q}", headers=UA), timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 5:
                time.sleep(int(e.headers.get("Retry-After") or 0) or 5 * (attempt + 1)); continue
            raise

def audio_by_title(titles):
    """Audio files used on each Wiktionary page, for up to 20 titles per request (follows continuation)."""
    out = {t: [] for t in titles}
    params = {"action": "query", "prop": "images", "titles": "|".join(titles), "imlimit": "max", "redirects": "1"}
    while True:
        data = api(params)
        q = data.get("query", {})
        back = {}
        for n in q.get("normalized", []): back[n["to"]] = n["from"]
        for rd in q.get("redirects", []): back[rd["to"]] = back.get(rd["from"], rd["from"])
        for page in q.get("pages", {}).values():
            t = back.get(page["title"], page["title"])
            for im in page.get("images", []):
                name = im["title"].split(":", 1)[1]
                if AUDIO.search(name) and t in out: out[t].append(name)
        if "continue" not in data: return out
        params = {**params, **data["continue"]}
        time.sleep(1)

def is_word_file(name, word):
    """True if the file is a recording of exactly this word (not a longer example phrase)."""
    n = AUDIO.sub("", name)
    n = re.sub(r"^(LL-Q188 \(deu\)-[^-]+-|CPIDL German - |De-(at-|ch-)?)", "", n, flags=re.I)
    n = re.sub(r"\d+$", "", n).strip()
    return n.casefold() == word.casefold()

def region(name):
    n = name.lower()
    if n.startswith("de-at-") or "österreich" in n: return "AT"
    if n.startswith("de-ch-") or "schweiz" in n: return "CH"
    if n.startswith("ll-q188"): return "Lingua Libre"
    if n.startswith("cpidl german"): return "CPIDL"
    if n.startswith("de-"): return "DE"
    return "other"

def lookups(text):
    plain = re.sub(r"[.!?,]", "", text).strip()
    m = re.match(r"^(der|die|das) (\S+)$", plain)
    if m: return [m.group(2)], "noun"
    if " " not in plain: return [plain], "word"
    return [plain], "phrase"

def main():
    cards = card_texts()
    plan = [(g, t, *lookups(t)) for g, t in cards]
    titles = sorted({x for _, _, ts, _ in plan for x in ts})
    pages, failed = {}, set()
    for i in range(0, len(titles), 20):
        chunk = titles[i:i + 20]
        try: pages.update(audio_by_title(chunk))
        except Exception as e: failed.update(chunk); print("failed:", chunk, e)
        time.sleep(1.5)
    results = []
    for group, text, titles_, kind in plan:
        found = []
        for t in titles_:
            for f in pages.get(t, []):
                if is_word_file(f, t): found.append({"file": f, "region": region(f), "page": t})
        results.append({"card": text, "group": group, "kind": kind, "looked_up": titles_,
                        "lookup_failed": any(t in failed for t in titles_),
                        "has_native": bool(found), "germany": any(f["region"] in ("DE", "Lingua Libre") for f in found),
                        "files": found})
    os.makedirs(os.path.join(ROOT, "qa"), exist_ok=True)
    json.dump(results, open(os.path.join(ROOT, "qa", "native-audio.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    by = {}
    for r in results:
        k = by.setdefault(r["kind"], [0, 0, 0]); k[0] += 1; k[1] += r["has_native"]; k[2] += r["germany"]
    lines = ["# Native recordings on German Wiktionary", "",
             f"{sum(r['has_native'] for r in results)} of {len(results)} cards have at least one recording "
             f"({sum(r['germany'] for r in results)} with a Germany-standard or Lingua Libre one). "
             f"Lookups failed for {sum(r['lookup_failed'] for r in results)} cards.", "",
             "| Card type | Cards | With recording | With DE / Lingua Libre |", "|---|---|---|---|"]
    names = {"noun": "der/die/das + noun", "word": "single word", "phrase": "phrase"}
    for k in ("noun", "word", "phrase"):
        if k in by: lines.append(f"| {names[k]} | {by[k][0]} | {by[k][1]} | {by[k][2]} |")
    lines += ["", "## Cards without a recording", ""]
    lines += [f"- {r['card']}" for r in results if not r["has_native"]]
    lines += ["", "## Cards with recordings", "", "| Card | Files (region) |", "|---|---|"]
    for r in results:
        if r["has_native"]:
            lines.append(f"| {r['card']} | " + ", ".join(f"{f['file']} ({f['region']})" for f in r["files"]) + " |")
    open(os.path.join(ROOT, "qa", "native-audio.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines[:12]))

if __name__ == "__main__":
    main()
