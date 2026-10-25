"""Leftover-word fix on the pesticides post (posts nothing)."""
import bot
names=[{"orig":"Pesticides Regulatory Office","type":"org"},{"orig":"Triolet","type":"mu_place"},{"orig":"Vasant Bunwaree","type":"person"},
 {"orig":"Preeadutt Sookar","type":"person"},{"orig":"Jugdish Goburdhun","type":"person"}]
t="Pesticides: в одном из овощей обнаружен хлороталонил в концентрации, превышающей допустимый предел в 317,5 раза"
s=("В июне 2026 года анализ 44 образцов овощей на Маврикии выявил остатки пестицидов в 20 из них. Концентрация в Triolet превысила "
   "предел в 317,5 раза, согласно отчёту Pesticides Regulatory Office. Эксперты, включая доктора Vasant Bunwaree и заместителя директора "
   "Agricultural Services Preeadutt Sookar, призывают к контролю. Бывший министр Jugdish Goburdhun рекомендует натуральные удобрения.")
print(bot.fix_leftovers(t, s, names, "Pesticides : un légume contient du chlorothalonil"))
