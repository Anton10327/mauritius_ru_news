"""Check short-article summaries and name restoration (posts nothing)."""
import bot
for title, link in [
    ("Couvre-feu : de 20 h 30 ce mercredi à 5 heures jeudi matin", "https://defimedia.info/couvre-feu-de-20-h-30-ce-mercredi-5-heures-jeudi-matin"),
    ("Petite enfance : les crèches resteront fermées ce jeudi 15 octobre", "https://defimedia.info/petite-enfance-les-creches-resteront-fermees-ce-jeudi-15-octobre"),
]:
    e = {"title": title, "link": link, "summary": ""}
    post = bot.build_post("Defimedia", e, link, [])
    print(f"\n## {title}\n{post['text']}")
print("\n== name restoration")
for t, s, orig in [
    ("Дело Элоди Томас: Правинд Жогно говорит, что рад улучшению её состояния",
     "Лидер оппозиции Правинд Жогно заявил, что рад улучшению состояния Элоди Томас.",
     "Affaire Élodie Thomas : Pravind Jugnauth se dit soulagé de l'amélioration de son état de santé. Le leader de l'opposition Pravind Jugnauth..."),
]:
    print(bot.proofread(t, s, orig))
print(f"LLM usage: {bot._llm_state['calls']} requests, {bot._llm_state['tokens']} tokens")
