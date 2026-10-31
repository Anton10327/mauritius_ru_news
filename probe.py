"""Duplicate check on the four school-calendar posts, with GigaChat-3-Pro and gpt-oss (posts nothing)."""
import json, bot
rec = json.load(open(bot.STATE_DIR / "recent.json", encoding="utf-8"))
idx = [i for i, e in enumerate(rec) if (e.get("orig") or "").startswith("Calendrier scolaire 2027")]
print("calendar posts at", idx)
for m in ["GigaChat-3-Pro", "openai/gpt-oss-120b"]:
    bot.DUP_MODELS[:] = [m]
    print("====", m)
    for i in idx[1:]:
        e = rec[i]
        print(" ", e["orig"][:70], "->", bot.find_duplicate(e["orig"], e["summary"], rec[:i]))
# control: other posts of the same day must stay new
bot.DUP_MODELS[:] = ["GigaChat-3-Pro"]
others = [i for i in range(idx[0], len(rec)) if i not in idx][:12]
flag = [(rec[i]["title"][:70], d) for i in others if (d := bot.find_duplicate(rec[i].get("orig") or rec[i]["title"], rec[i]["summary"], rec[:i]))]
print("control: other posts flagged:", flag)
