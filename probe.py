import bot
t="Дело Элоди Томас: Правинд Жогно говорит, что рад улучшению её состояния"
s="Лидер оппозиции Правинд Жогно заявил, что рад улучшению состояния Элоди Томас."
orig="Affaire Élodie Thomas : Pravind Jugnauth se dit soulagé de l'amélioration de son état de santé. Le leader de l'opposition Pravind Jugnauth..."
r = bot._llm_call(bot.PROOF_MODELS[0], [{"role": "system", "content": bot.PROOF_PROMPT},
     {"role": "user", "content": f"Заголовок: {t}\nТекст: {s}\n\nОригинал (для написания имён):\n{orig}"}], 700)
d = bot._parse_json(r.json()["choices"][0]["message"]["content"])
print(repr(d))
ns = d.get("summary", "")
print("safe:", bot._safe_edit(s, ns, orig), "ratio:", len(ns)/len(s))
import re
latin = lambda x: re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’.-]*", x)
print("new latin:", latin(ns), "| orig latin sample:", latin(orig)[:12])
