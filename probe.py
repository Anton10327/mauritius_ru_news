"""Re-judge the Kistnen case with the new duplicate rules (posts nothing)."""
import json, bot
recent = json.loads(bot.RECENT_FILE.read_text(encoding="utf-8"))
first = [x for x in recent if "Soopramanien Kistnen" in x.get("orig", "")][0]
first["summary"] = ("18 октября 2026 года исполняется шесть лет со дня обнаружения обгоревшего тела бывшего активиста MSM "
    "Soopramanien Kistnen в поле сахарного тростника в Telfair, Мока. Изначально признанное самоубийство, дело было "
    "переквалифицировано в убийство. Адвокат семьи Rama Valayden сожалеет, что расследование не продвинулось; образцы ДНК, "
    "отправленные в Англию, до сих пор не дали результатов.")
idx = recent.index(first) + 1
recent = recent[:idx]  # what the bot knew before the second article
url = "https://topfm.mu/actualites/affaire-kistnen-six-ans-apres-toujours-pas-de-reponses-deplore-me-rama-valayden"
body, _ = bot.article_text(url)
print("article text:", len(body), "chars")
d = bot.find_duplicate("Affaire Kistnen : six ans après, toujours pas de réponses, déplore Me Rama Valayden",
                       body.replace("Publicité", "").strip(), recent)
print("RESULT duplicate_of:", d, "->", recent[d-1]["orig"] if d else None)
