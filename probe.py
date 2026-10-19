"""Duplicate check on the real Taukoor repeat (posts nothing)."""
import json, bot
rec = json.load(open(bot.STATE_DIR / "recent.json", encoding="utf-8"))
def run(idx, label):
    e = rec[idx]
    print(label, "->", bot.find_duplicate(e["orig"], e["summary"], rec[:idx]))
for i, e in enumerate(rec):
    if "оставили под стражей до 26" in e["title"]: run(i, "Taukoor remand (expect duplicate)")
    if "Élodie Thomas ранена пулей: трое" in e["title"]: run(i, "3 police officers bailed (expect new)")
    if "сержант Gautam Singh Taukoor снова в суде" in e["title"]: run(i, "first court post (expect new)")
