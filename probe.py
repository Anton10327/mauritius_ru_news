"""Check the 'about Mauritius' filter on real articles (posts nothing)."""
import requests, bot
H = bot.UA
cases = []
for q in ["Albanie", "Arabie saoudite", "Nirdeshak", "El Niño", "Macron"]:
    try:
        r = requests.get("https://www.inside.news/wp-json/wp/v2/posts",
                         params={"search": q, "per_page": 1, "_fields": "title,link"}, headers=H, timeout=30)
        for p in r.json():
            cases.append((p["title"]["rendered"], p["link"]))
    except Exception as e:
        print("search fail", q, e)
cases += [
    ("Trump annonce un cessez-le-feu énergétique entre l'Ukraine et la Russie", "https://defimedia.info/trump-annonce-un-cessez-le-feu-energetique-entre-lukraine-et-la-russie"),
    ("Angleterre : un homme tué par un tigre dans un parc animalier", "https://defimedia.info/angleterre-un-homme-tue-par-un-tigre-dans-un-parc-animalier"),
    ("Cinq disparus au Népal après une crue spectaculaire", "https://defimedia.info/cinq-disparus-au-nepal-apres-une-crue-spectaculaire"),
    ("Un pétrolier arraisonné au sud de La Réunion, Macron réaffirme sa lutte contre la « flotte fantôme »", "https://topfm.mu/actualites/un-petrolier-arraisonne-au-sud-de-la-reunion-macron-reaffirme-sa-lutte-contre-la-flotte-fantome"),
    ("Water Tank Grant Scheme : les inscriptions ouvertes ce lundi", "https://defimedia.info/water-tank-grant-scheme-les-inscriptions-ouvertes-ce-lundi"),
    ("Bourse de Maurice : Tim Hogben succédera à Sunil Benimadhu au poste de CEO", "https://defimedia.info/bourse-de-maurice-tim-hogben-succedera-sunil-benimadhu-au-poste-de-ceo"),
]
for title, link in cases:
    body, _ = bot.article_text(link)
    res = bot.summarize(title, body) if len(body) >= bot.MIN_TEXT_FOR_SUMMARY else None
    verdict = "?" if not res else ("MAURITIUS" if res[2] else "foreign  ")
    print(f"{verdict} | {title[:95]}")
print(f"LLM usage: {bot._llm_state['calls']} requests, {bot._llm_state['tokens']} tokens")
