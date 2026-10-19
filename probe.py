"""Replay every post of the last 48h through the duplicate check with
GigaChat-3-Pro: which published posts would it have stopped? (posts nothing)"""
import json, bot
rec = json.load(open(bot.STATE_DIR / "recent.json", encoding="utf-8"))
bot.DUP_MODELS[:] = ["GigaChat-3-Pro"]
flag = 0
for i in range(len(rec)):
    e = rec[i]
    d = bot.find_duplicate(e.get("orig") or e["title"], e["summary"], rec[:i])
    if d:
        flag += 1
        p = rec[:i][d - 1]
        print(f"\n#{i} {e['source']}: {e['title']}\n   {e['summary'][:260]}\n   == {p['source']}: {p['title']}\n   {p['summary'][:260]}")
print(f"\nwould stop {flag} of {len(rec)} published posts")
