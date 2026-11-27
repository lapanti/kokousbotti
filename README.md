# kokousbotti

Avaa Discordin forum-kanavalle postauksen jokaisesta seuratun toimielimen uudesta esityslistasta.
Postaus saa toimielimen tägin, tägää konfiguraatiossa nimetyt jäsenet ja sisältää deterministisen
briiffin [kuntapaatokset](https://github.com/lapanti/kuntapaatokset)-datasta. Kunta, puolue, Discord-
palvelin ja toimielimet tulevat kaikki konfiguraatiosta; repossa ei ole mitään yhden kunnan tai
ryhmän omaa.

- **`full`-tila** (seurattavalla puolueella on edustus): jäsentägit, pykälälista, aiemmat käsittelyt,
  suurin mainittu €-summa, teeman äänestysosuus, `kiistanalainen?`-heuristiikka, same-side-osuudet ja
  aloitteet.
- **`short`-tila** (ei edustusta): enintään 5 painavinta pykälää, ei tägejä eikä embedejä.
- Toimielin ilman tägiä ei saa postausta; ajo päättyy virheeseen `tagi_puuttuu`.

Äänestysluvut ovat raakoja JAA/EI/TYHJÄ-lukuja. Botti ei koskaan tulkitse, kuka oli "puolesta".

## Rakenne

| Moduuli | Tehtävä |
|---|---|
| `bot/upcoming.py` | hakee seurattujen toimielinten uudet kokoukset kuntapaatokset-paketin crawlerilla |
| `bot/caseno.py` | diaarinumero kohdan sivulta (uudet kokoukset) tai otsikosta ja osioista (historia) |
| `bot/related.py` | saman asian aiemmat käsittelyt: diaarinumerolla, muuten otsikon sanoilla |
| `bot/brief.py` | briiffi yhdelle esityslistalle |
| `bot/render.py`, `bot/discord.py` | Discord-viesti ja REST-kutsut |

kuntapaatokset ei tallenna diaarinumeroa. Historiassa se luetaan kohdan osioista, joista puuttuu
sivun otsake: osioista löytyy numero noin puolelle niistä kohdista, joille koko sivulta löytyisi.

## Paikallinen testi

Konfiguraatio ja token pidetään repon ulkopuolella:

```sh
mkdir -p ~/.config/kokousbotti && chmod 700 ~/.config/kokousbotti
cp config.example.json ~/.config/kokousbotti/config.json   # täytä kunta, puolue ja oikeat id:t
printf '%s' '<bot token>' > ~/.config/kokousbotti/token && chmod 600 ~/.config/kokousbotti/token
export PYTHONPATH=../kuntapaatokset
export DISCORD_BOT_TOKEN="$(cat ~/.config/kokousbotti/token)"
C=~/.config/kokousbotti/config.json

python -m bot tags --config $C                                   # forumin tägit ja id:t
python -m bot run --dry-run --config $C --meeting <kokouksen id> # tulosta viesti, älä postaa
python -m bot run --config $C --meeting <kokouksen id>           # postaa testiforumiin
python -m bot run --config $C                                    # oikea ajo: uudet esityslistat
```

Huomioita:

- Ketjun nimi on `<Toimielimen> kokous d.m.yyyy` (esim. `Kunnanhallituksen kokous 5.10.2026`).
  Oman nimen voi antaa konfiguraatiossa (`"thread_name": "Kuntakehityslautakunnan kokous"`).
  Ruotsinkielisille toimielimille nimi pitää antaa aina.
- Tuplapostaukset estetään nimellä: kirjainkoko, vuosiluku ja etunollat ohitetaan, joten käsin
  avattu `Kuntakehityslautakunnan kokous 6.10.` estää botin postauksen. Kuivaharjoitus näyttää
  `thread_exists`-kentässä, löytyykö ketju jo, kun token on asetettu.
- Ajo ilman `--meeting`-parametria hakee sivut tyhjään väliaikaiseen välimuistiin ja kirjoittaa uudet
  esityslistat kuntapaatokset-checkoutin `data/`-kansioon. Historia (aiemmat käsittelyt,
  äänestysosuudet) luetaan samasta kansiosta, joten sen pitää olla ajettu kunnalle valmiiksi.

Id:t saa Discordista: Asetukset → Lisäasetukset → Kehittäjätila, sitten hiiren oikea → Kopioi ID.

## Discord-botti

Discord Developer Portal → New Application → Bot → Reset Token. Kutsu palvelimelle (OAuth2 → URL
Generator, scope `bot`) oikeuksin View Channels, Send Messages, Create Posts, Send Messages in
Threads, Read Message History. Botti käyttää vain REST-rajapintaa; gatewayta tai intenttejä ei tarvita.

## Konfiguraatio

Katso `bot/config.py` ja `config.example.json`. `municipality` on kuntapaatokset-konfiguraation
slug, `focus` puoluekoodi, ja `bodies`-avaimet ovat kuntapaatokset-datan toimielinkansioiden nimet.

## GitHub Actions

Secretit: `BOT_CONFIG` (konfiguraatio yhdellä rivillä), `DISCORD_BOT_TOKEN` ja
`KUNTAPAATOKSET_DEPLOY_KEY` (read-only deploy key kuntapaatokset-repoon). Loki on julkinen, joten
botti tulostaa vain lukumääriä. `--dry-run` ja `tags` kieltäytyvät toimimasta CI:ssä.

## Testit

```sh
PYTHONPATH=../kuntapaatokset python -m unittest discover -s tests
```

## Lisenssi

AGPL-3.0-or-later, ks. `LICENSE`.
