import re, bot, sys
print(sys.version)
line="Chloé Gabriel (person); Roméo Caliste (person); Richard Sunee (person); Pascal Telvar (person); Ravind Nadooa (person); Ferdinand Ilunga Luyoyo (person); Mathieu Souprayen (person); Fabrice Valérie (person); Ivans Frontin (person); Ziggy Agathe (person); Merven Clair (person); Fédération mauricienne de boxe (org); AMB (org); International Boxing Association (org); Africa Boxing (org); World Boxing (org); Fédération congolaise de boxe (org)"
names=[{"orig":m.group(1).strip(),"type":m.group(2)} for m in re.finditer(r"([^;]+?) \((\w+)\)",line)]
t=open("box_probe.txt").read()
print(bot.apply_names(t,names))
