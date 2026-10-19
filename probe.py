"""Proofreader on an untranslated English quote (posts nothing)."""
import bot
t = "«Who cares?»: житель Baie-du-Tombeau жалуется на утечку воды"
s = "Житель района Port-Louis-Jin Fei в Baie-du-Tombeau сообщил о протечке водопровода."
o = "« Who cares ? » : un habitant de Baie-du-Tombeau dénonce une fuite d'eau"
print(bot.proofread(t, s, o))
