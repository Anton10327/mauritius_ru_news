"""Which recent articles the new 'news' flag would skip (posts nothing)."""
import bot
URLS = ["https://defimedia.info/node/%d" % n for n in (185814, 185817, 185818, 185819, 185820, 185824)] + [
 "https://lexpress.mu/s/tamarisk-tree-met-tout-le-monde-daccord-563762",
 "https://lexpress.mu/s/epreuve-annulee-candidats-angoisses-563750",
 "https://lexpress.mu/s/ces-medecins-indiens-et-mauriciens-qui-ont-uni-leurs-competences-pour-lui-sauver-la-vie-563752",
 "https://lexpress.mu/s/deux-promotions-automatiques-pour-gautam-singh-taukoor-563747",
 "https://topfm.mu/actualites/octobre-rose-derriere-chaque-diagnostic-une-femme-des-reves-et-une-vie-rappelle-la-psychologue-dr",
 "https://topfm.mu/actualites/indemnites-de-risque-des-policiers-le-depute-khushal-lobine-appelle-revoir-le-plafond-de-rs-1-045",
]
for u in URLS:
    body, _ = bot.article_text(u)
    r = bot.summarize(body.split("\n")[0][:150], body)
    print(("POST  " if r and r[2] is True else f"SKIP({r[2] if r else '?'}) "), r[0][:100] if r else u)
