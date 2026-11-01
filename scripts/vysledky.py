#!/usr/bin/env python3
"""
Stáhne výsledky Chance Ligy z TheSportsDB a zapíše je do vysledky.json,
kterému rozumí tipovačka. Bez API klíče, jen standardní knihovna.

Zapisuje skóre, odložení zápasu (status PST) i oficiální termíny výkopů.
"""
import json, os, sys, unicodedata, urllib.request, urllib.error, datetime

LEAGUE  = os.environ.get("LEAGUE_ID", "4631")        # Czech First League
SEASON  = os.environ.get("SEASON", "2026-2027")
ROUNDS  = [int(x) for x in os.environ.get("ROUNDS", "11,12,13,14,15,16,17,18,19,20").split(",")]
KEY     = os.environ.get("TSDB_KEY", "123")          # veřejný testovací klíč
OUT     = os.environ.get("OUT", "vysledky.json")
TZ      = os.environ.get("TZ_OFFSET", "auto")        # auto = evropský letní/zimní čas

API = "https://www.thesportsdb.com/api/v1/json/{key}/eventsround.php?id={lg}&r={r}&s={s}"

# názvy z TheSportsDB → zkratky používané v tipovačce
MAP = {
    "slavia prague":"Slavia", "sk slavia prague":"Slavia", "slavia praha":"Slavia",
    "sparta prague":"Sparta", "ac sparta prague":"Sparta", "sparta praha":"Sparta",
    "viktoria plzen":"Plzeň", "fc viktoria plzen":"Plzeň", "plzen":"Plzeň",
    "banik ostrava":"Baník", "fc banik ostrava":"Baník", "ostrava":"Baník",
    "slovacko":"Slovácko", "1 fc slovacko":"Slovácko",
    "slovan liberec":"Liberec", "fc slovan liberec":"Liberec", "liberec":"Liberec",
    "jablonec":"Jablonec", "fk jablonec":"Jablonec",
    "sigma olomouc":"Olomouc", "sk sigma olomouc":"Olomouc", "olomouc":"Olomouc",
    "hradec kralove":"Hradec", "fc hradec kralove":"Hradec",
    "mlada boleslav":"Boleslav", "fk mlada boleslav":"Boleslav",
    "bohemians 1905":"Bohemians", "bohemians praha 1905":"Bohemians", "bohemians":"Bohemians",
    "teplice":"Teplice", "fk teplice":"Teplice",
    "pardubice":"Pardubice", "fk pardubice":"Pardubice", "slovan pardubice":"Pardubice",
    "zlin":"Zlín", "fc zlin":"Zlín", "fastav zlin":"Zlín",
    "zbrojovka brno":"Zbrojovka", "fc zbrojovka brno":"Zbrojovka",
    "artis brno":"Artis", "sk artis brno":"Artis", "artis":"Artis",
}

# Stavy, kdy je zápas opravdu dohraný. Všechno ostatní (1H, HT, 2H, ET, LIVE…)
# znamená, že se hraje a průběžné skóre se nesmí brát jako výsledek.
DOHRANO = {"FT","AET","PEN","AP","FT_PEN","PENALTIES","MATCH FINISHED","FINISHED","ENDED","AWARDED","WO"}
ODLOZENO = {"PST","POSTP","POSTPONED","CANC","CANCELLED","CANCELED","ABD","ABANDONED","SUSP","SUSPENDED","TBD"}
# Zápas včetně poločasu a nastavení trvá zhruba dvě hodiny. Dřív konečný být nemůže.
MIN_MINUT = 105

def je_dohrano(stav, ts):
    """Konečný výsledek bereme jen u jasně dohraných zápasů. Když zdroj stav nevyplní,
    pustíme skóre až s odstupem od výkopu — nikdy ne v průběhu."""
    if stav in DOHRANO:
        return True
    if stav and stav not in ("", "NS", "TBD"):
        return False                      # 1H / HT / 2H / ET / LIVE a podobně
    if not ts:
        return False
    try:
        vykop = datetime.datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return False
    ted = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    return (ted - vykop).total_seconds() >= MIN_MINUT * 60

def norm(s):
    s = unicodedata.normalize("NFD", str(s)).encode("ascii","ignore").decode()
    return " ".join(s.lower().replace(".", " ").replace("-", " ").split())

def team(name):
    n = norm(name)
    if n in MAP: return MAP[n]
    for k, v in MAP.items():                 # částečná shoda jako záloha
        if k in n or n in k: return v
    return None

def praha_offset(dt):
    """Středoevropský čas: letní od poslední neděle v březnu do poslední v říjnu."""
    if TZ != "auto":
        return datetime.timedelta(hours=int(TZ))
    def posledni_nedele(rok, mesic):
        d = datetime.date(rok, mesic, 31)
        while d.month != mesic: d -= datetime.timedelta(days=1)
        while d.weekday() != 6: d -= datetime.timedelta(days=1)
        return d
    zac = posledni_nedele(dt.year, 3); kon = posledni_nedele(dt.year, 10)
    letni = datetime.datetime.combine(zac, datetime.time(1,0)) <= dt < datetime.datetime.combine(kon, datetime.time(1,0))
    return datetime.timedelta(hours=2 if letni else 1)

def stahni(r):
    url = API.format(key=KEY, lg=LEAGUE, s=SEASON, r=r)
    req = urllib.request.Request(url, headers={"User-Agent":"tipovacka/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp).get("events") or []

def main():
    vysledky, nesparovano, rozehrane, chyby = [], set(), [], 0
    for r in ROUNDS:
        try:
            udalosti = stahni(r)
        except Exception as e:
            print(f"  {r}. kolo — nepovedlo se stáhnout: {e}", file=sys.stderr)
            chyby += 1
            continue
        if not udalosti:
            print(f"  {r}. kolo — zdroj nevrátil žádné zápasy")
            continue
        for e in udalosti:
            h, a = team(e.get("strHomeTeam")), team(e.get("strAwayTeam"))
            if not h or not a:
                nesparovano.add(f'{e.get("strHomeTeam")} – {e.get("strAwayTeam")}')
                continue
            radek = {"round": int(e.get("intRound") or r), "home": h, "away": a}

            # termín z UTC časové značky na místní čas
            ts = e.get("strTimestamp")
            if ts:
                try:
                    dt = datetime.datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
                    loc = dt + praha_offset(dt)
                    radek["date"] = loc.strftime("%Y-%m-%d")
                    radek["time"] = loc.strftime("%H:%M")
                except ValueError:
                    pass

            stav = (e.get("strStatus") or "").strip().upper()
            hs, as_ = e.get("intHomeScore"), e.get("intAwayScore")

            if stav in ODLOZENO or str(e.get("strPostponed","")).lower() == "yes":
                if hs is None or as_ is None:
                    radek["post"] = True
            elif hs is not None and as_ is not None and je_dohrano(stav, ts):
                radek["hs"], radek["as"] = int(hs), int(as_)
            elif hs is not None and as_ is not None:
                # zápas běží — průběžné skóre schválně nezapisujeme, bodovalo by se předčasně
                rozehrane.append(f'{h} – {a} ({stav or "bez stavu"} {hs}:{as_})')
            vysledky.append(radek)

    if chyby == len(ROUNDS):
        sys.exit("Nepovedlo se stáhnout ani jedno kolo — soubor nechávám být.")

    se_skore = sum(1 for x in vysledky if "hs" in x)
    odlozene = sum(1 for x in vysledky if x.get("post"))

    payload = {
        "updated": datetime.datetime.now(datetime.timezone.utc)
                   .replace(microsecond=0).isoformat().replace("+00:00","Z"),
        "source": "TheSportsDB · Czech First League",
        "results": vysledky,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    print(f"Zapsáno {len(vysledky)} zápasů do {OUT} — {se_skore} s výsledkem, {odlozene} odložených.")
    if rozehrane:
        print("Rozehrané zápasy (skóre zatím nezapisuji): " + ", ".join(rozehrane))
    if nesparovano:
        print("Nespárované týmy: " + ", ".join(sorted(nesparovano)), file=sys.stderr)

if __name__ == "__main__":
    main()
