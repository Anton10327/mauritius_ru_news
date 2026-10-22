"""1) GigaChat model list; 2) duplicate check: Lite vs Pro on the last 48h (posts nothing)."""
import json, requests, bot
tok = bot._giga_token()
r = requests.get("https://api.giga.chat/v1/models", headers={"Authorization": f"Bearer {tok}"},
                 timeout=30, verify=bot._giga_verify())
print("models:", [m["id"] for m in r.json().get("data", [])])
rec = json.load(open(bot.STATE_DIR / "recent.json", encoding="utf-8"))
res = {}
for m in ["GigaChat-3-Pro", "GigaChat-3-Lightning", "GigaChat-2"]:
    bot.DUP_MODELS[:] = [m]
    bot._llm_state["calls"] = bot._llm_state["tokens"] = 0
    res[m] = [bot.find_duplicate(e.get("orig") or e["title"], e["summary"], rec[:i]) for i, e in enumerate(rec)]
    print(f"{m}: flags {sum(1 for d in res[m] if d)} of {len(rec)}; {bot._llm_state['calls']} calls, {bot._llm_state['tokens']} tokens")
ref = res["GigaChat-3-Pro"]
for m in ["GigaChat-3-Lightning", "GigaChat-2"]:
    print(f"\n#### {m} vs Pro")
    for i, (a, b) in enumerate(zip(ref, res[m])):
        if bool(a) != bool(b):
            d = a or b
            print(f"#{i} Pro={'DUP' if a else 'new'} {m}={'DUP' if b else 'new'}: {rec[i]['title'][:90]}\n      ~ {rec[d-1]['title'][:90]}")
