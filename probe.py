"""First-pass classifier (Lite) vs the decisions Ultra made (posts nothing)."""
import json, bot
neg = set(l.strip()[:100] for l in open("labels_neg.txt", encoding="utf-8") if l.strip())
pos = set((e.get("orig") or "")[:100] for e in json.load(open(bot.STATE_DIR / "recent.json", encoding="utf-8")))
cases = []
for src in bot.FEEDS:
    kind = src[2] if len(src) > 2 else "rss"
    try:
        items, _ = bot.collect(src[0], src[1], kind, src[3] if len(src) > 3 else None, set())
    except Exception as ex:
        print("skip", src[0], ex); continue
    for e, uid, img, ts in items[:25]:
        if kind == "html":
            e = bot.page_entry(uid)
            if not e: continue
        t = (e.get("title") or "").strip()
        lab = True if t[:100] in pos else False if t[:100] in neg else None
        if lab is None: continue
        link = e.get("link", uid).replace("http://", "https://", 1)
        body, _ = bot.article_text(link)
        if len(body) >= bot.MIN_TEXT_FOR_SUMMARY:
            cases.append((t, body, lab))
print(len(cases), "labelled articles:", sum(1 for c in cases if c[2]), "posted,", sum(1 for c in cases if not c[2]), "skipped")
for m in ["GigaChat-3-Lightning", "GigaChat-2"]:
    bot.CLASSIFY_MODELS[:] = [m]
    bot._llm_state.update(calls=0, tokens=0)
    lost, caught, missed = [], 0, 0
    for t, body, lab in cases:
        c = bot.classify(t, body)
        ok = c is None or (c[0] and c[1])
        if lab and not ok: lost.append((t, c))
        if not lab: caught += (not ok); missed += ok
    print(f"\n{m}: would wrongly drop {len(lost)} real posts; catches {caught} of {caught+missed} skipped; "
          f"{bot._llm_state['tokens']} tokens / {bot._llm_state['calls']} calls")
    for t, c in lost: print("   LOST:", t[:100], c)
