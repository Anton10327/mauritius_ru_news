"""Proofreader shoot-out on real errors from the channel (posts nothing)."""
import json, time, bot

CASES = [  # (title, summary, expected fix or None if text is already correct)
    ("Несмотря на дело о нравах 2007 года: две автоматические повышения для Gautam Singh Taukoor",
     "Полицейский Gautam Singh Taukoor получил две автоматические повышения — от капрала до сержанта.", "два автоматических повышения"),
    ("Дело о чемоданах: Kobita Jugnauth прошла третье допрос в FCC",
     "14 октября Kobita Jugnauth явилась в FCC для третьего допроса.", "третий допрос"),
    ("GRNO: задержан за бросание посылки в территорию тюрьмы",
     "Полиция задержала 21-летнего электрика.", "на территорию"),
    ("Отставка: Sydney Pierre покинул Партия труда",
     "Депутат Sydney Pierre официально покинул Партия труда и правительство.", "Партию труда"),
    ("Экзамены отложены: более 33 000 кандидатов затронут по всей стране",
     "MES объявил о переносе экзаменов, это затронет 33 893 кандидатов.", "затронуты"),
    ("Полиция мобилизована для предотвращения любых непредвиденных обстоятельств",
     "Полицейские силы были мобилизованы в ответ на недавние волнения, которые вспыхнули в нескольких регионах страны.", None),
    ("Комендантский час: с 20:30 в среду до 5:00 в четверг",
     "В среду, 14 октября, на Маврикии вводится новый комендантский час. Ограничения вступят в силу в 20:30.", None),
    ("Нарушение комендантского часа: двенадцать подсудимых признали вину",
     "Двенадцать мужчин, арестованных в Bambous, Rose-Hill и Quatre-Bornes, признали вину. Им грозит штраф до 50 000 рупий.", None),
]
MODELS = ["qwen/qwen3.8-27b", "GigaChat-3-Lightning", "GigaChat-3-Pro", "GigaChat-3-Ultra"]
score = {m: [0, 0, 0] for m in MODELS}  # fixed, missed, damaged-correct
for m in MODELS:
    print(f"\n######## {m}")
    for t, s, want in CASES:
        msgs = [{"role": "system", "content": bot.PROOF_PROMPT},
                {"role": "user", "content": f"Заголовок: {t}\nТекст: {s}\n\nОригинал (для написания имён):\n(нет)"}]
        try:
            r = bot._llm_call(m, msgs, 700)
            if r.status_code == 429:
                time.sleep(20); r = bot._llm_call(m, msgs, 700)
            d = bot._parse_json(r.json()["choices"][0]["message"]["content"]) or {}
        except Exception as e:
            print("  FAIL", e); continue
        nt, ns = d.get("title", ""), d.get("summary", "")
        both = (nt + " " + ns).lower()
        if want:
            ok = want.lower() in both
            score[m][0 if ok else 1] += 1
            print(f"  {'FIXED ' if ok else 'MISSED'} | {nt[:90]}")
        else:
            changed = (nt.strip(), ns.strip()) != (t, s)
            if changed: score[m][2] += 1
            print(f"  {'CHANGED' if changed else 'kept   '} | {d.get('changes')}")
print("\nmodel: fixed / missed / changed a correct text")
for m, (f, mi, dmg) in score.items():
    print(f"  {m:24s} {f} / {mi} / {dmg}")
