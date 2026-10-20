"""Daily GigaChat budgets from the balance (posts nothing)."""
import os, bot
os.environ.pop("BOT_TEST", None)  # allow the balance request in this probe
bot._state_set("giga_balance", {})
for m in ("GigaChat-3-Ultra", "GigaChat-3-Pro"):
    print(m, "daily budget:", f"{bot._giga_cap(m):,}", "| days left:", bot._giga_days_left())
