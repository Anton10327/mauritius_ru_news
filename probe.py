"""Leftover fix on the court name (posts nothing)."""
import bot
names=[{"orig":"Cour du Nord de Port-Louis","type":"org"},{"orig":"Port-Louis","type":"mu_place"},{"orig":"Navin Ramgoolam","type":"person"},
       {"orig":"Dreamer","type":"org"},{"orig":"Baie-du-Tombeau","type":"mu_place"},{"orig":"Sir Seewoosagur Ramgoolam","type":"mu_place"}]
t="Изъятие наркотиков на Маврикии на сумму 2,7 млрд рупий"
s=("Груз с яхты Dreamer перехватили у Baie-du-Tombeau. Премьер-министр Navin Ramgoolam допустил связь с беспорядками. Подозреваемый задержан "
   "27 октября в аэропорту Sir Seewoosagur Ramgoolam; семеро предстанут сегодня перед Cour du Nord de Port-Louis по обвинению в сговоре.")
print(bot.fix_leftovers(t, s, names, "devant la Cour du Nord de Port-Louis"))
