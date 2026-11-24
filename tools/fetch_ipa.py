#!/usr/bin/env python3
"""Look up the IPA for every card on German Wiktionary and write data/ipa.json.

Single words and der/die/das + noun: the IPA from the word's page ({{Lautschrift|...}} in the
German section's "Aussprache" block). Phrases: the phrase's own page if it exists, otherwise the
words' IPA stitched together and marked approximate ("approx": true). A phrase with any word
missing gets no IPA.
"""
import json, os, re, time, urllib.error, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = "https://de.wiktionary.org/w/api.php"
UA = {"User-Agent": "Sprechtrainer/0.12 (personal language-learning app; github.com/esaulgd/sprechtrainer)"}
# Articles in citation form; Wiktionary's entries for them are long multi-meaning pages.
ARTICLES = {"der": "deːɐ̯", "die": "diː", "das": "das"}
# Words Wiktionary has no German entry for (contractions, names).
MANUAL = {"geht's": "ɡeːt͡s", "saúl": "saˈuːl"}

def card_texts():
    html = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
    raw = re.search(r"const VOCAB_RAW = `(.*?)`;", html, re.S).group(1)
    seen, out = set(), []
    for line in raw.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 3 and parts[1] and parts[1] not in seen:
            seen.add(parts[1]); out.append(parts[1])
    return out

def api(params):
    q = urllib.parse.urlencode({"format": "json", "formatversion": "2", **params})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(urllib.request.Request(f"{API}?{q}", headers=UA), timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 5:
                time.sleep(int(e.headers.get("Retry-After") or 0) or 5 * (attempt + 1)); continue
            raise

def wikitext(titles):
    """Page wikitext for up to 20 titles (missing pages map to None)."""
    out = {t: None for t in titles}
    d = api({"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main",
             "titles": "|".join(titles), "redirects": "1"})
    q = d.get("query", {})
    back = {}
    for n in q.get("normalized", []): back[n["to"]] = n["from"]
    for rd in q.get("redirects", []): back[rd["to"]] = back.get(rd["from"], rd["from"])
    for p in q.get("pages", []):
        t = back.get(p["title"], p["title"])
        if t in out and not p.get("missing") and p.get("revisions"):
            out[t] = p["revisions"][0]["slots"]["main"]["content"]
    return out

def ipa_from(text):
    """First IPA in the German section's pronunciation block, or None."""
    if not text: return None
    m = re.search(r"\{\{Sprache\|Deutsch\}\}(.*?)(?=\n== [^=]|\Z)", text, re.S)
    sect = m.group(1) if m else text
    for line in sect.splitlines():
        if "{{IPA}}" in line:
            for v in re.findall(r"\{\{Lautschrift\|([^}|]+)", line):
                v = v.strip()
                if v and v not in ("…", "...", "-"): return v
    return None

def variants(word):
    w = word.strip()
    vs = [w, w.lower(), w[:1].upper() + w[1:].lower()]
    if "'" in w: vs += [x.replace("'", "’") for x in vs]
    return list(dict.fromkeys(vs))

def main():
    cards = card_texts()
    plan = {}
    for c in cards:
        clean = re.sub(r"[.!?,]", "", c).strip()
        m = re.match(r"^(der|die|das) (\S+)$", clean)
        if m: plan[c] = ("noun", m.group(1), [m.group(2)])
        elif " " not in clean: plan[c] = ("word", None, [clean])
        else: plan[c] = ("phrase", None, [clean] + clean.split())
    titles = sorted({v for kind, _, ws in plan.values() for w in ws for v in variants(w)})
    pages = {}
    for i in range(0, len(titles), 20):
        pages.update(wikitext(titles[i:i + 20])); time.sleep(1)

    def lookup(word):
        for v in variants(word):
            ipa = ipa_from(pages.get(v))
            if ipa: return ipa
        return None

    out, missing = {}, []
    for c, (kind, art, ws) in plan.items():
        if kind == "noun":
            ipa = lookup(ws[0])
            if ipa: out[c] = {"ipa": f"{ARTICLES[art]} {ipa}", "approx": False}
        elif kind == "word":
            ipa = lookup(ws[0])
            if ipa: out[c] = {"ipa": ipa, "approx": False}
        else:
            whole = lookup(ws[0])
            if whole:
                out[c] = {"ipa": whole, "approx": False}
            else:
                parts = [ARTICLES.get(w.lower()) or MANUAL.get(w.lower()) or lookup(w) for w in ws[1:]]
                if all(parts): out[c] = {"ipa": " ".join(parts), "approx": True}
        if c not in out: missing.append(c)
    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    json.dump({"source": "de.wiktionary.org (CC BY-SA)", "cards": out},
              open(os.path.join(ROOT, "data", "ipa.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"{len(out)} of {len(cards)} cards have IPA ({sum(v['approx'] for v in out.values())} stitched). Missing: {missing}")

if __name__ == "__main__":
    main()
