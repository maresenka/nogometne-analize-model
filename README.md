# Automatski dnevni model nogometnih prognoza

Ovaj projekt svaki dan sam izračuna 10 statistički najzanimljivijih utakmica
za naredna 3 dana i objavi ih na jednostavnoj web stranici. Idući dan prozor
se automatski pomakne (dodaje se novi treći dan), bez da moraš išta ručno raditi.

**Prije nego krenete: ovo je polazna točka.** Model je transparentna, ali
pojednostavljena statistička procjena (Elo + heuristika za 1X2), ne
"siguran pogodak". ID-ovi liga i točan oblik API odgovora provjereni su prema
javnoj dokumentaciji, ali prvi pokušaj pokreni ručno i provjeri izlaz prije
nego pustiš automatizaciju.

## Što trebaš

1. **GitHub račun** (besplatan) — https://github.com/join
2. **API-Football** ključ (parovi, rezultati) — https://www.api-football.com
   - Besplatna razina ima ograničen broj poziva dnevno; za 11 liga x dnevni
     poziv to bi trebalo biti dovoljno za početak, ali provjeri limite.
3. **The Odds API** ključ (kvote) — https://the-odds-api.com
   - Besplatna razina ima ograničen broj poziva mjesečno.

## Koraci postavljanja

### 1. Napravi repozitorij
Na GitHubu klikni **New repository**, nazovi ga npr. `nogometne-analize`,
označi kao **Public** (potrebno za besplatni GitHub Pages) i stvori ga prazan.

### 2. Ubaci ove datoteke
Učitaj (upload) sve datoteke i mape iz ovog paketa u taj repozitorij, zadržavajući
strukturu:
```
model.py
requirements.txt
data/elo_ratings.json
docs/index.html
.github/workflows/daily.yml
```

### 3. Dodaj API ključeve kao "Secrets"
U repozitoriju: **Settings → Secrets and variables → Actions → New repository secret**
- `API_FOOTBALL_KEY` = tvoj API-Football ključ
- `ODDS_API_KEY` = tvoj The Odds API ključ

Ključevi se nikad ne vide u kodu ni u logovima.

### 4. Uključi GitHub Pages
**Settings → Pages** → pod "Build and deployment" odaberi:
- Source: **Deploy from a branch**
- Branch: **main**, folder **/docs**

Nakon spremanja, GitHub će za par minuta dati link oblika
`https://<tvoj-korisnik>.github.io/nogometne-analize/`.

### 5. Pokreni model prvi put ručno
U repozitoriju: tab **Actions** → odaberi "Dnevni model prognoza" →
**Run workflow** (gumb desno). Nakon što završi (~1 minuta), provjeri je li
se pojavio `docs/picks.json` s podacima, i otvori link sa stranicom da vidiš
prikaz.

### 6. Od sada radi samo
Radni tijek (`.github/workflows/daily.yml`) pokreće se svaki dan u 06:00 UTC
(promijeni vrijeme u toj datoteci ako želiš drugi termin) i sam ažurira
`picks.json`. Stranica se svaki put učita s najnovijim podacima.

## Prekidači za kategorije (FEATURES)

Na vrhu `model.py` nalazi se rječnik `FEATURES` — svaka kategorija analize ima
`True` (radi već sada) ili `False` (čeka dodatni izvor/pretplatu). Kad nabaviš
ono što treba, samo promijeni `False` u `True`, ništa drugo se ne mora
mijenjati:

| Kategorija | Status | Treba za uključivanje |
|---|---|---|
| 1X2, Over/Under, BTTS, hendikep, h2h, ozljede/sastavi, kontekst lige, ključni igrači, vrijeme | ✅ Uključeno | Ništa dodatno — radi s postojeća dva ključa (vrijeme je čak potpuno besplatno, Open-Meteo) |
| Trendovi suca (kartoni po sucu) | ⏳ Isključeno | Vremena da se akumulira dovoljno utakmica (skuplja se sam, samo uključi) |
| Korneri/kartoni po momčadi | ⏳ Isključeno | Isto — akumulira se tijekom vremena, ali troši dodatne API pozive dnevno |
| Detaljna statistika igrača (zadnjih 20 utakmica) | ⏳ Isključeno | API-Football **Pro** plan (puno poziva po igraču) |
| Taktička analiza | ⏳ Isključeno | Nema API izvora — popuni `data/tim_biljeske.json` ručno (naziv tima → bilješka), pa uključi |
| Sentiment vijesti | ⏳ Isključeno | Treći API ključ (**NewsAPI**), dodaj kao `NEWS_API_KEY` secret |

**Napomena o kategorijama koje "čekaju vrijeme"** (sudac, korneri/kartoni po
momčadi): te se ne mogu uključiti odmah čak ni s parama, jer trebaju vlastitu
povijest podataka koju skuplja skripta iz dana u dan. Uključi ih odmah, ali ne
očekuj smislene brojke prvih par tjedana.

## Što je dodano u ovoj verziji

Model sada, uz 1X2, računa i **Over/Under 2.5** i **oba tima daju gol** (iz
pojednostavljenog Poissonovog modela izvedenog iz Elo razlike), dohvaća
**međusobne susrete** i **ozljede/suspenzije** (kad ih API plan dopušta), i za
svaku utakmicu daje **top 3 preporuke** (ne samo jednu) s oznakom rizika
(Nizak/Srednji/Visok). Kategorije koje trenutno nemaju automatski izvor
(vrijeme, suđenje, taktika, korneri, kartoni, statistika pojedinih igrača,
kontekst utakmice) eksplicitno su označene kao "Nije dostupno" u `picks.json`
i na stranici, umjesto da budu prešućene.

## Što još treba doraditi prije nego staviš pravim kupcima

- **Provjeri ID-ove liga** u `model.py` (`LEAGUE_IDS`) — nekoliko je označeno
  kao "provjeri točan ID", jer moraš potvrditi u API-Football dokumentaciji.
- **Uparivanje naziva momčadi** između API-Football i The Odds API je po
  imenu, što je krhko (npr. "Man United" vs "Manchester United"). Ako
  primijetiš utakmice bez kvota, to je vjerojatno razlog — javi se pa
  doradimo uparivanje po ID-u ili fuzzy-matchu.
- **Spajanje s prodajnom stranicom**: ovo je zasebna, jednostavna stranica.
  Ako želiš da izgleda kao prethodna (tamna tema s cijenama, FAQ),
  javi se pa spojimo dizajn — ova stranica može biti samo "Dnevne preporuke"
  potkategorija, dostupna pretplatnicima.
- **Zaključavanje sadržaja iza plaćanja**: ova stranica trenutno prikazuje
  picks.json svima. Za pravo naplaćivanje treba dodati provjeru pretplate
  (npr. link samo pretplatnicima, ili ručno slanje preko Telegrama kao što
  smo ranije dogovorili).

## Troškovi

- GitHub + GitHub Pages + GitHub Actions: besplatno za ovaj obujam.
- API-Football i The Odds API: besplatne razine za početak; provjeri
  cjenike ako ti zatreba više poziva ili više liga.
