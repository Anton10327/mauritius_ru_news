"""Duplicate check: current vs compact listing, on gpt-oss-120b (Groq, free),
last 24h of posts. Then the disagreements once more on GigaChat-3-Pro."""
import json, time, bot
rec = json.load(open(bot.STATE_DIR / "recent.json", encoding="utf-8"))
start = next(i for i, e in enumerate(rec) if e["t"] > time.time() - 24 * 3600)
cases = list(range(start, len(rec)))
def run(model, compact, idx):
    bot.DUP_MODELS[:] = [model]
    bot.DUP_COMPACT = compact
    bot._llm_state.update(calls=0, tokens=0)
    bot._llm_state["exhausted"].discard(model)
    out = {i: bot.find_duplicate(rec[i].get("orig") or rec[i]["title"], rec[i]["summary"], rec[:i]) for i in idx}
    return out, bot._llm_state["calls"], bot._llm_state["tokens"]
old, c1, t1 = run("openai/gpt-oss-120b", False, cases)
new, c2, t2 = run("openai/gpt-oss-120b", True, cases)
print(f"{len(cases)} posts | current: {c1} calls, {t1} tokens ({t1 // max(c1,1)}/call), flags {sum(1 for v in old.values() if v)}"
      f" | compact: {c2} calls, {t2} tokens ({t2 // max(c2,1)}/call), flags {sum(1 for v in new.values() if v)}")
diff = [i for i in cases if bool(old[i]) != bool(new[i])]
for i in cases:
    if old[i] or new[i]:
        d = old[i] or new[i]
        print(f"#{i} current={'DUP' if old[i] else 'new'} compact={'DUP' if new[i] else 'new'}: {rec[i]['title'][:80]}\n      ~ {rec[d-1]['title'][:80]}")
print(f"\n{len(diff)} disagreements; checking them on GigaChat-3-Pro")
po, pc, pt = run("GigaChat-3-Pro", False, diff[:15])
pn, pc2, pt2 = run("GigaChat-3-Pro", True, diff[:15])
for i in diff[:15]:
    print(f"#{i} Pro current={'DUP' if po[i] else 'new'} Pro compact={'DUP' if pn[i] else 'new'}: {rec[i]['title'][:80]}")
print(f"Pro tokens: current {pt}, compact {pt2}")
