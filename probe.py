"""Check the new proofreading chain on real errors (posts nothing)."""
import bot
CASES = [
 ("Несмотря на дело о нравах 2007 года: две автоматические повышения для Gautam Singh Taukoor", "Полицейский получил две автоматические повышения.", "Malgré une affaire de mœurs: deux promotions automatiques pour Gautam Singh Taukoor"),
 ("Отставка: Sydney Pierre покинул Партия труда", "Депутат Sydney Pierre официально покинул Партия труда.", "Démission: Sydney Pierre quitte le Parti travailliste"),
 ("Экзамены отложены: более 33 000 кандидатов затронут", "Это затронет 33 893 кандидатов.", "Examens reportés: plus de 33 000 candidats concernés"),
]
for t, s, o in CASES:
    print(bot.proofread(t, s, o))
