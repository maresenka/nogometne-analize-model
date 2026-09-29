#!/usr/bin/env python3
"""
Dnevni model za odabir najboljih utakmica (naredna 3 dana), sa svim
kategorijama analize koje smo dogovorili.

STRUKTURA: na vrhu je rječnik FEATURES koji uključuje/isključuje pojedine
kategorije. Sve što je trenutno True radi već sada (besplatno ili s tvoja dva
postojeća API ključa). Sve što je False ima već napisanu funkciju i mjesto u
izvještaju, ali čeka dodatni izvor podataka (plaćeni API plan ili treći
ključ) — kad ga nabaviš, samo prebaci na True, ništa drugo ne moraš mijenjati.

VAŽNE NAPOMENE:
- Ovo je polazna, ne stopostotno testirana verzija (nemam tvoje API ključeve
  da je pokrenem uživo). Prvi run napravi ručno i javi mi grešku ako je bude.
- Nijedan model ne jamči pogodak.
- ID-ove liga provjeri prije prvog pokretanja (API-Football /leagues).
"""

import json
import math
import os
from datetime import datetime, timedelta, timezone

import requests

# ---------------------------------------------------------------------------
# UKLJUČI / ISKLJUČI KATEGORIJE
# ---------------------------------------------------------------------------
# True  = radi već sada (besplatno ili postojećim ključevima)
# False = spremna funkcija, čeka dodatni izvor/ključ — prebaci u True kad ga nabaviš

FEATURES = {
    "elo_1x2": True,                 # Elo -> 1X2 (postojeći ključ)
    "poisson_golovi": True,          # Over/Under, BTTS, najvjerojatniji rezultat (postojeći ključ)
    "h2h": True,                     # međusobni susreti (postojeći ključ)
    "ozljede_sastavi": True,         # ozljede + sastavi kad su objavljeni (postojeći ključ, API-Football)
    "kontekst_lige": True,           # pozicija na ljestvici / borba za naslov-ispadanje (postojeći ključ)
    "kljucni_igraci": True,          # najbolji strijelci lige (postojeći ključ, jeftin poziv)
    "vrijeme": True,                 # vremenska prognoza (BESPLATNO, Open-Meteo, ne treba ključ)
    "handicap": True,                # azijski hendikep iz Poissona (koristi postojeće kvote ako ih Odds API nudi)

    "sudac_trend": False,            # treba akumulaciju statistike po sucu tijekom vremena — troši API kvotu
    "korneri_kartoni_tim": False,    # prosjek po momčadi — treba dodatne pozive po utakmici (statistics endpoint)
    "statistika_igraca_detaljno": False,  # 20 utakmica po igraču — treba API-Football Pro plan (puno poziva)
    "taktika": False,                # nema API izvora — ručne bilješke, vidi data/tim_biljeske.json
    "sentiment_vijesti": False,      # treba NewsAPI ključ (treći ključ, nismo još planirali)
}

# ---------------------------------------------------------------------------
# KONFIGURACIJA
# ---------------------------------------------------------------------------

API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY", "")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
NEWS_API_KEY = os.environ.get("NEWS_API_KEY", "")  # samo ako uključiš sentiment_vijesti

API_FOOTBALL_HOST = "https://v3.football.api-sports.io"
ODDS_API_HOST = "https://api.the-odds-api.com/v4"
OPEN_METEO_GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"

DAYS_AHEAD = 3
TOP_N = 10
HOME_ADV_ELO = 60
K_FACTOR = 20
DEFAULT_ELO = 1500
BASE_GOALS = 1.4
NIJE_DOSTUPNO = "Nije dostupno"

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
RATINGS_PATH = os.path.join(DATA_DIR, "elo_ratings.json")
FORM_PATH = os.path.join(DATA_DIR, "recent_form.json")
REFEREE_STATS_PATH = os.path.join(DATA_DIR, "referee_stats.json")
TEAM_STATS_PATH = os.path.join(DATA_DIR, "team_stats.json")
TEAM_NOTES_PATH = os.path.join(DATA_DIR, "tim_biljeske.json")  # ručne taktičke bilješke, opcionalno
PICKS_PATH = os.path.join(os.path.dirname(__file__), "docs", "picks.json")

# Provjeri stvarne ID-ove na https://dashboard.api-football.com (endpoint /leagues) prije prvog pokretanja.
LEAGUE_IDS = {
    210: "HNL (Hrvatska)",
    98: "1. Liga (BiH)",          # provjeri točan ID
    286: "Super liga (Srbija)",   # provjeri točan ID
    39: "Premier League",
    140: "La Liga",
    135: "Serie A",
    78: "Bundesliga",
    61: "Ligue 1",
    88: "Eredivisie",
    94: "Primeira Liga",
    179: "Škotska Premiership",   # provjeri točan ID
}

SPORT_KEY_SOCCER = "soccer"
session = requests.Session()


# ---------------------------------------------------------------------------
# POMOĆNE FUNKCIJE ZA SPREMANJE/UČITAVANJE LOKALNIH PODATAKA
# ---------------------------------------------------------------------------

def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# ELO RATINGS + NEDAVNA FORMA
# ---------------------------------------------------------------------------

def get_rating(ratings, team_id):
    return ratings.get(str(team_id), DEFAULT_ELO)


def update_elo_from_result(ratings, home_id, away_id, home_goals, away_goals):
    r_home = get_rating(ratings, home_id) + HOME_ADV_ELO
    r_away = get_rating(ratings, away_id)
    exp_home = 1 / (1 + 10 ** ((r_away - r_home) / 400))
    score_home = 1.0 if home_goals > away_goals else (0.5 if home_goals == away_goals else 0.0)
    goal_diff = abs(home_goals - away_goals)
    multiplier = 1 + math.log(max(goal_diff, 1)) * 0.5
    delta = K_FACTOR * multiplier * (score_home - exp_home)
    ratings[str(home_id)] = get_rating(ratings, home_id) + delta
    ratings[str(away_id)] = get_rating(ratings, away_id) - delta


def update_form(form, team_id, result_char):
    """result_char: 'W', 'D' ili 'L'. Drži zadnjih 5 po momčadi."""
    key = str(team_id)
    lst = form.get(key, [])
    lst.append(result_char)
    form[key] = lst[-5:]


def refresh_ratings_and_form(ratings, form):
    """Povuci utakmice odigrane u zadnja 2 dana i ažuriraj Elo + formu."""
    since = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for league_id in LEAGUE_IDS:
        try:
            resp = session.get(f"{API_FOOTBALL_HOST}/fixtures",
                                headers={"x-apisports-key": API_FOOTBALL_KEY},
                                params={"league": league_id, "from": since, "to": today, "status": "FT"},
                                timeout=20)
            resp.raise_for_status()
            data = resp.json().get("response", [])
        except Exception as e:
            print(f"[upozorenje] Rezultati nedostupni za ligu {league_id}: {e}")
            continue

        for match in data:
            goals = match.get("goals", {})
            teams = match.get("teams", {})
            if goals.get("home") is None or goals.get("away") is None:
                continue
            hg, ag = goals["home"], goals["away"]
            hid, aid = teams["home"]["id"], teams["away"]["id"]
            update_elo_from_result(ratings, hid, aid, hg, ag)
            if hg > ag:
                update_form(form, hid, "W"); update_form(form, aid, "L")
            elif hg < ag:
                update_form(form, hid, "L"); update_form(form, aid, "W")
            else:
                update_form(form, hid, "D"); update_form(form, aid, "D")

            # opcionalno: prikupljaj statistiku (korneri/kartoni/sudac) za buduću upotrebu
            if FEATURES["korneri_kartoni_tim"] or FEATURES["sudac_trend"]:
                collect_fixture_statistics(match["fixture"]["id"], match.get("fixture", {}).get("referee"), hid, aid)


# ---------------------------------------------------------------------------
# KORNERI / KARTONI PO MOMČADI + SUDAC (akumulira se tijekom vremena)
# ---------------------------------------------------------------------------

def collect_fixture_statistics(fixture_id, referee_name, home_id, away_id):
    """
    Best-effort: povuci statistiku (korneri, kartoni) za odigranu utakmicu i
    dodaj je u lokalnu, rastuću bazu (team_stats.json, referee_stats.json).
    Troši dodatni API poziv po utakmici — zato je iza FEATURES prekidača.
    """
    try:
        resp = session.get(f"{API_FOOTBALL_HOST}/fixtures/statistics",
                            headers={"x-apisports-key": API_FOOTBALL_KEY},
                            params={"fixture": fixture_id}, timeout=15)
        resp.raise_for_status()
        stats = resp.json().get("response", [])
    except Exception as e:
        print(f"[upozorenje] Statistika nedostupna za fixture {fixture_id}: {e}")
        return

    team_stats = load_json(TEAM_STATS_PATH, {})
    total_cards_match = 0

    for team_block in stats:
        team_id = str(team_block.get("team", {}).get("id"))
        corners, yellow, red = None, 0, 0
        for stat in team_block.get("statistics", []):
            t = stat.get("type", "")
            v = stat.get("value")
            if t == "Corner Kicks" and v is not None:
                corners = v
            elif t == "Yellow Cards" and v is not None:
                yellow = v or 0
            elif t == "Red Cards" and v is not None:
                red = v or 0
        total_cards_match += (yellow or 0) + (red or 0)

        if team_id:
            entry = team_stats.get(team_id, {"corners": [], "cards": []})
            if corners is not None:
                entry["corners"] = (entry.get("corners", []) + [corners])[-10:]
            entry["cards"] = (entry.get("cards", []) + [yellow + red])[-10:]
            team_stats[team_id] = entry

    save_json(TEAM_STATS_PATH, team_stats)

    if referee_name and FEATURES["sudac_trend"]:
        ref_stats = load_json(REFEREE_STATS_PATH, {})
        entry = ref_stats.get(referee_name, {"utakmice": 0, "ukupno_kartona": 0})
        entry["utakmice"] += 1
        entry["ukupno_kartona"] += total_cards_match
        ref_stats[referee_name] = entry
        save_json(REFEREE_STATS_PATH, ref_stats)


def get_team_stats_summary(team_id):
    if not FEATURES["korneri_kartoni_tim"]:
        return NIJE_DOSTUPNO + " (uključi 'korneri_kartoni_tim' u FEATURES)"
    team_stats = load_json(TEAM_STATS_PATH, {})
    entry = team_stats.get(str(team_id))
    if not entry or not entry.get("cards"):
        return NIJE_DOSTUPNO + " (još nema dovoljno prikupljenih podataka)"
    avg_corners = round(sum(entry.get("corners", [0])) / max(len(entry.get("corners", [1])), 1), 1)
    avg_cards = round(sum(entry.get("cards", [0])) / max(len(entry.get("cards", [1])), 1), 1)
    return {"prosjek_kornera": avg_corners, "prosjek_kartona": avg_cards, "zadnjih_n": len(entry.get("cards", []))}


def get_referee_summary(referee_name):
    if not FEATURES["sudac_trend"] or not referee_name:
        return NIJE_DOSTUPNO + " (uključi 'sudac_trend' u FEATURES; treba i naziv suca u fixture podacima)"
    ref_stats = load_json(REFEREE_STATS_PATH, {})
    entry = ref_stats.get(referee_name)
    if not entry or entry.get("utakmice", 0) < 3:
        return NIJE_DOSTUPNO + " (premalo prikupljenih utakmica za ovog suca još)"
    return {
        "sudac": referee_name,
        "prosjek_kartona_po_utakmici": round(entry["ukupno_kartona"] / entry["utakmice"], 1),
        "na_temelju_utakmica": entry["utakmice"],
    }


# ---------------------------------------------------------------------------
# DOHVAT PAROVA
# ---------------------------------------------------------------------------

def fetch_upcoming_fixtures():
    today = datetime.now(timezone.utc)
    date_from = today.strftime("%Y-%m-%d")
    date_to = (today + timedelta(days=DAYS_AHEAD)).strftime("%Y-%m-%d")

    fixtures = []
    for league_id, league_name in LEAGUE_IDS.items():
        try:
            resp = session.get(f"{API_FOOTBALL_HOST}/fixtures",
                                headers={"x-apisports-key": API_FOOTBALL_KEY},
                                params={"league": league_id, "from": date_from, "to": date_to},
                                timeout=20)
            resp.raise_for_status()
            data = resp.json().get("response", [])
        except Exception as e:
            print(f"[upozorenje] Parovi nedostupni za ligu {league_id}: {e}")
            continue

        for match in data:
            fixtures.append({
                "league_id": league_id,
                "league_name": league_name,
                "fixture_id": match["fixture"]["id"],
                "kickoff_utc": match["fixture"]["date"],
                "referee": match["fixture"].get("referee"),
                "venue_city": (match["fixture"].get("venue") or {}).get("city"),
                "home_id": match["teams"]["home"]["id"],
                "home_name": match["teams"]["home"]["name"],
                "away_id": match["teams"]["away"]["id"],
                "away_name": match["teams"]["away"]["name"],
            })
    return fixtures


# ---------------------------------------------------------------------------
# SASTAVI / OZLJEDE
# ---------------------------------------------------------------------------

def fetch_injuries(fixture_id):
    if not FEATURES["ozljede_sastavi"]:
        return NIJE_DOSTUPNO
    try:
        resp = session.get(f"{API_FOOTBALL_HOST}/injuries",
                            headers={"x-apisports-key": API_FOOTBALL_KEY},
                            params={"fixture": fixture_id}, timeout=15)
        resp.raise_for_status()
        data = resp.json().get("response", [])
    except Exception as e:
        print(f"[upozorenje] Ozljede nedostupne za fixture {fixture_id}: {e}")
        return NIJE_DOSTUPNO
    return [{"team": i.get("team", {}).get("name"), "player": i.get("player", {}).get("name"),
             "reason": i.get("player", {}).get("reason")} for i in data] if data else []


def fetch_lineups(fixture_id):
    """Sastavi su na API-Football dostupni tek ~1h prije utakmice, pa daleko unaprijed budi None."""
    if not FEATURES["ozljede_sastavi"]:
        return NIJE_DOSTUPNO
    try:
        resp = session.get(f"{API_FOOTBALL_HOST}/fixtures/lineups",
                            headers={"x-apisports-key": API_FOOTBALL_KEY},
                            params={"fixture": fixture_id}, timeout=15)
        resp.raise_for_status()
        data = resp.json().get("response", [])
    except Exception as e:
        print(f"[upozorenje] Sastavi nedostupni za fixture {fixture_id}: {e}")
        return NIJE_DOSTUPNO
    if not data:
        return NIJE_DOSTUPNO + " (još nisu objavljeni — obično ~1h prije utakmice)"
    return [{"tim": t.get("team", {}).get("name"), "formacija": t.get("formation"),
             "postava": [p.get("player", {}).get("name") for p in t.get("startXI", [])]} for t in data]


# ---------------------------------------------------------------------------
# MEĐUSOBNI SUSRETI (H2H)
# ---------------------------------------------------------------------------

def fetch_h2h(home_id, away_id, last=5):
    if not FEATURES["h2h"]:
        return NIJE_DOSTUPNO
    try:
        resp = session.get(f"{API_FOOTBALL_HOST}/fixtures/headtohead",
                            headers={"x-apisports-key": API_FOOTBALL_KEY},
                            params={"h2h": f"{home_id}-{away_id}", "last": last}, timeout=15)
        resp.raise_for_status()
        data = resp.json().get("response", [])
    except Exception as e:
        print(f"[upozorenje] H2H nedostupan za {home_id}-{away_id}: {e}")
        return NIJE_DOSTUPNO
    out = []
    for match in data:
        goals = match.get("goals", {})
        teams = match.get("teams", {})
        if goals.get("home") is None:
            continue
        out.append({"datum": match["fixture"]["date"][:10], "domacin": teams["home"]["name"],
                     "gost": teams["away"]["name"], "rezultat": f"{goals['home']}-{goals['away']}"})
    return out


# ---------------------------------------------------------------------------
# KONTEKST LIGE (borba za naslov / ispadanje) — iz ljestvice
# ---------------------------------------------------------------------------

_standings_cache = {}

def fetch_standings(league_id, season_year):
    if league_id in _standings_cache:
        return _standings_cache[league_id]
    try:
        resp = session.get(f"{API_FOOTBALL_HOST}/standings",
                            headers={"x-apisports-key": API_FOOTBALL_KEY},
                            params={"league": league_id, "season": season_year}, timeout=15)
        resp.raise_for_status()
        data = resp.json().get("response", [])
        table = data[0]["league"]["standings"][0] if data else []
    except Exception as e:
        print(f"[upozorenje] Ljestvica nedostupna za ligu {league_id}: {e}")
        table = []
    _standings_cache[league_id] = table
    return table


def team_league_context(league_id, team_id, season_year):
    if not FEATURES["kontekst_lige"]:
        return NIJE_DOSTUPNO
    table = fetch_standings(league_id, season_year)
    if not table:
        return NIJE_DOSTUPNO + " (ljestvica nedostupna, možda početak sezone)"
    n = len(table)
    for row in table:
        if row["team"]["id"] == team_id:
            pos = row["rank"]
            if pos <= 3:
                stakes = "borba za vrh/naslov"
            elif pos >= n - 2:
                stakes = "borba za ispadanje"
            else:
                stakes = "sredina ljestvice, manji ulog"
            return {"pozicija": pos, "od_ukupno": n, "bodovi": row.get("points"), "kontekst": stakes}
    return NIJE_DOSTUPNO + " (tim nije pronađen na ljestvici — provjeri sezonu)"


# ---------------------------------------------------------------------------
# KLJUČNI IGRAČI (najbolji strijelci lige — jeftin poziv, jednom po ligi)
# ---------------------------------------------------------------------------

_topscorers_cache = {}

def fetch_key_players(league_id, season_year, home_name, away_name):
    if not FEATURES["kljucni_igraci"]:
        return NIJE_DOSTUPNO
    if league_id not in _topscorers_cache:
        try:
            resp = session.get(f"{API_FOOTBALL_HOST}/players/topscorers",
                                headers={"x-apisports-key": API_FOOTBALL_KEY},
                                params={"league": league_id, "season": season_year}, timeout=15)
            resp.raise_for_status()
            _topscorers_cache[league_id] = resp.json().get("response", [])
        except Exception as e:
            print(f"[upozorenje] Strijelci nedostupni za ligu {league_id}: {e}")
            _topscorers_cache[league_id] = []

    relevant = []
    for p in _topscorers_cache[league_id]:
        team_name = p.get("statistics", [{}])[0].get("team", {}).get("name")
        if team_name in (home_name, away_name):
            goals = p.get("statistics", [{}])[0].get("goals", {}).get("total")
            relevant.append({"igrac": p["player"]["name"], "tim": team_name, "golova_ove_sezone": goals})

    if FEATURES["statistika_igraca_detaljno"]:
        pass  # ovdje bi išao detaljniji poziv po igraču (zadnjih 20 utakmica) — treba Pro plan

    return relevant if relevant else (NIJE_DOSTUPNO + " (nema podataka o strijelcima za ovu ligu)")


# ---------------------------------------------------------------------------
# VRIJEME (BESPLATNO — Open-Meteo, geokodiranje po gradu domaćina)
# ---------------------------------------------------------------------------

_geocode_cache = {}

def fetch_weather(venue_city, kickoff_utc):
    if not FEATURES["vrijeme"] or not venue_city:
        return NIJE_DOSTUPNO
    try:
        if venue_city not in _geocode_cache:
            geo = session.get(OPEN_METEO_GEOCODE, params={"name": venue_city, "count": 1}, timeout=10)
            geo.raise_for_status()
            results = geo.json().get("results")
            if not results:
                _geocode_cache[venue_city] = None
            else:
                _geocode_cache[venue_city] = (results[0]["latitude"], results[0]["longitude"])

        coords = _geocode_cache[venue_city]
        if not coords:
            return NIJE_DOSTUPNO + f" (grad '{venue_city}' nije pronađen u geokodiranju)"

        lat, lon = coords
        kickoff_date = kickoff_utc[:10]
        fc = session.get(OPEN_METEO_FORECAST, params={
            "latitude": lat, "longitude": lon,
            "daily": "temperature_2m_max,precipitation_probability_max,wind_speed_10m_max",
            "timezone": "UTC", "start_date": kickoff_date, "end_date": kickoff_date,
        }, timeout=10)
        fc.raise_for_status()
        daily = fc.json().get("daily", {})
        if not daily.get("time"):
            return NIJE_DOSTUPNO + " (prognoza još nije dostupna za taj datum — obično >16 dana unaprijed ne postoji)"
        return {
            "grad": venue_city,
            "temp_max_c": daily["temperature_2m_max"][0],
            "vjerojatnost_kise_%": daily["precipitation_probability_max"][0],
            "vjetar_max_kmh": daily["wind_speed_10m_max"][0],
        }
    except Exception as e:
        print(f"[upozorenje] Vrijeme nedostupno za {venue_city}: {e}")
        return NIJE_DOSTUPNO


# ---------------------------------------------------------------------------
# TAKTIKA (ručne bilješke, opcionalno — nema API izvora)
# ---------------------------------------------------------------------------

def get_tactical_notes(team_name):
    if not FEATURES["taktika"]:
        return NIJE_DOSTUPNO + " (nema API izvora; uključi i popuni data/tim_biljeske.json ručno)"
    notes = load_json(TEAM_NOTES_PATH, {})
    return notes.get(team_name, NIJE_DOSTUPNO + " (nema unesene bilješke za ovaj tim)")


# ---------------------------------------------------------------------------
# SENTIMENT VIJESTI (treba NewsAPI ključ)
# ---------------------------------------------------------------------------

def fetch_news_sentiment(team_name):
    if not FEATURES["sentiment_vijesti"] or not NEWS_API_KEY:
        return NIJE_DOSTUPNO + " (treba NEWS_API_KEY i uključen 'sentiment_vijesti')"
    # Namjerno ostavljeno kao mjesto za nadogradnju: dohvat članaka + analiza
    # sentimenta (npr. HuggingFace model) traži dodatnu biblioteku i vrijeme
    # obrade, pa nije implementirano dok se ne odluči vrijedi li uopće ulagati.
    return NIJE_DOSTUPNO + " (funkcija pripremljena, implementacija čeka odluku hoće li se koristiti)"


# ---------------------------------------------------------------------------
# MODEL: Elo -> 1X2
# ---------------------------------------------------------------------------

def elo_to_1x2(rating_home, rating_away):
    diff = rating_home - rating_away
    p_home_no_draw = 1 / (1 + 10 ** (-diff / 400))
    base_draw = 0.26
    p_draw = base_draw * math.exp(-abs(diff) / 400)
    remaining = 1 - p_draw
    p_home = remaining * p_home_no_draw
    p_away = remaining * (1 - p_home_no_draw)
    return {"1": round(p_home, 4), "X": round(p_draw, 4), "2": round(p_away, 4)}


# ---------------------------------------------------------------------------
# MODEL: Elo -> očekivani golovi -> Poisson (O/U, BTTS, najvjerojatniji rezultat, hendikep)
# ---------------------------------------------------------------------------

def elo_to_expected_goals(rating_home, rating_away):
    diff = rating_home - rating_away
    home_xg = BASE_GOALS * (10 ** (diff / 1000))
    away_xg = BASE_GOALS * (10 ** (-diff / 1000))
    return round(home_xg, 2), round(away_xg, 2)


def poisson_pmf(k, lam):
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def build_score_matrix(home_xg, away_xg, max_goals=8):
    matrix = {}
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            matrix[(h, a)] = poisson_pmf(h, home_xg) * poisson_pmf(a, away_xg)
    return matrix


def goal_market_probs(matrix):
    p_over25 = sum(p for (h, a), p in matrix.items() if h + a > 2.5)
    p_btts = sum(p for (h, a), p in matrix.items() if h >= 1 and a >= 1)
    most_likely = max(matrix, key=matrix.get)
    return {
        "over25": round(p_over25, 4), "under25": round(1 - p_over25, 4),
        "btts_da": round(p_btts, 4), "btts_ne": round(1 - p_btts, 4),
        "najvjerojatniji_rezultat": f"{most_likely[0]}-{most_likely[1]}",
        "vjerojatnost_tog_rezultata": round(matrix[most_likely], 4),
    }


def handicap_probs(matrix, line):
    """P(domaćin pokriva hendikep 'line'), npr. line=-1 znači domaćin -1."""
    if not FEATURES["handicap"]:
        return None
    cover = sum(p for (h, a), p in matrix.items() if (h + line) > a)
    push = sum(p for (h, a), p in matrix.items() if (h + line) == a)
    return {"linija": line, "pokriva_domacin": round(cover, 4), "push": round(push, 4)}


# ---------------------------------------------------------------------------
# PROCJENA RIZIKA
# ---------------------------------------------------------------------------

def risk_label(model_prob, odds_value=None):
    if odds_value is not None:
        if odds_value <= 1.6:
            return "Nizak"
        elif odds_value <= 2.4:
            return "Srednji"
        return "Visok"
    if model_prob >= 0.55:
        return "Nizak"
    elif model_prob >= 0.35:
        return "Srednji"
    return "Visok"


# ---------------------------------------------------------------------------
# DOHVAT KVOTA (1X2, Over/Under 2.5, BTTS, hendikep ako dostupan)
# ---------------------------------------------------------------------------

def fetch_odds():
    try:
        resp = session.get(f"{ODDS_API_HOST}/sports/{SPORT_KEY_SOCCER}/odds",
                            params={"apiKey": ODDS_API_KEY, "regions": "eu",
                                    "markets": "h2h,totals,btts,spreads", "oddsFormat": "decimal"},
                            timeout=20)
        resp.raise_for_status()
        events = resp.json()
    except Exception as e:
        print(f"[upozorenje] Kvote nedostupne: {e}")
        return {}

    odds_map = {}
    for event in events:
        home, away = event.get("home_team"), event.get("away_team")
        bookmakers = event.get("bookmakers", [])
        if not bookmakers:
            continue

        home_odds, draw_odds, away_odds = [], [], []
        over25_odds, under25_odds = [], []
        btts_yes_odds, btts_no_odds = [], []
        spreads = {}

        for bm in bookmakers:
            for market in bm.get("markets", []):
                key = market.get("key")
                if key == "h2h":
                    for o in market["outcomes"]:
                        if o["name"] == home: home_odds.append(o["price"])
                        elif o["name"] == away: away_odds.append(o["price"])
                        else: draw_odds.append(o["price"])
                elif key == "totals":
                    for o in market["outcomes"]:
                        if o.get("point") != 2.5: continue
                        if o["name"].lower() == "over": over25_odds.append(o["price"])
                        elif o["name"].lower() == "under": under25_odds.append(o["price"])
                elif key == "btts":
                    for o in market["outcomes"]:
                        if o["name"].lower() == "yes": btts_yes_odds.append(o["price"])
                        elif o["name"].lower() == "no": btts_no_odds.append(o["price"])
                elif key == "spreads" and FEATURES["handicap"]:
                    for o in market["outcomes"]:
                        if o["name"] == home:
                            spreads.setdefault(o.get("point"), []).append(o["price"])

        if not (home_odds and away_odds and draw_odds):
            continue

        entry = {"1": sum(home_odds)/len(home_odds), "X": sum(draw_odds)/len(draw_odds), "2": sum(away_odds)/len(away_odds)}
        if over25_odds and under25_odds:
            entry["over25"] = sum(over25_odds)/len(over25_odds)
            entry["under25"] = sum(under25_odds)/len(under25_odds)
        if btts_yes_odds and btts_no_odds:
            entry["btts_da"] = sum(btts_yes_odds)/len(btts_yes_odds)
            entry["btts_ne"] = sum(btts_no_odds)/len(btts_no_odds)
        if spreads:
            entry["spreads"] = {pt: sum(v)/len(v) for pt, v in spreads.items()}

        odds_map[(home, away)] = entry
    return odds_map


def implied_probs_no_margin(odds):
    raw = {k: 1 / v for k, v in odds.items()}
    total = sum(raw.values())
    return {k: v / total for k, v in raw.items()}


# ---------------------------------------------------------------------------
# GLAVNI TIJEK
# ---------------------------------------------------------------------------

def main():
    if not API_FOOTBALL_KEY or not ODDS_API_KEY:
        raise SystemExit("Nedostaju API_FOOTBALL_KEY i/ili ODDS_API_KEY (GitHub Actions secrets ili env varijable).")

    ratings = load_json(RATINGS_PATH, {})
    form = load_json(FORM_PATH, {})
    refresh_ratings_and_form(ratings, form)

    fixtures = fetch_upcoming_fixtures()
    odds_map = fetch_odds()
    current_season = datetime.now(timezone.utc).year  # pojednostavljeno — prilagodi po potrebi za lige s prijelazom sezone

    candidates = []
    for fx in fixtures:
        odds = odds_map.get((fx["home_name"], fx["away_name"]))

        r_home = get_rating(ratings, fx["home_id"]) + HOME_ADV_ELO
        r_away = get_rating(ratings, fx["away_id"])
        model_probs = elo_to_1x2(r_home, r_away)

        home_xg, away_xg = elo_to_expected_goals(r_home, r_away)
        matrix = build_score_matrix(home_xg, away_xg)
        goal_probs = goal_market_probs(matrix)
        handicap = handicap_probs(matrix, -1) if FEATURES["handicap"] else None

        entry = {
            "league": fx["league_name"], "home": fx["home_name"], "away": fx["away_name"],
            "kickoff_utc": fx["kickoff_utc"],
            "model_probs_1x2": model_probs,
            "model_xg": {"home": home_xg, "away": away_xg},
            "model_probs_golovi": goal_probs,
            "model_hendikep_-1_domacin": handicap,
            "elo_home": round(r_home, 1), "elo_away": round(r_away, 1),

            "forma_domacin_zadnjih_5": form.get(str(fx["home_id"]), []) or NIJE_DOSTUPNO,
            "forma_gost_zadnjih_5": form.get(str(fx["away_id"]), []) or NIJE_DOSTUPNO,
            "medusobni_susreti": fetch_h2h(fx["home_id"], fx["away_id"]),
            "ozljede_suspenzije": fetch_injuries(fx["fixture_id"]),
            "sastavi": fetch_lineups(fx["fixture_id"]),
            "kontekst_lige_domacin": team_league_context(fx["league_id"], fx["home_id"], current_season),
            "kontekst_lige_gost": team_league_context(fx["league_id"], fx["away_id"], current_season),
            "kljucni_igraci": fetch_key_players(fx["league_id"], current_season, fx["home_name"], fx["away_name"]),
            "vrijeme": fetch_weather(fx["venue_city"], fx["kickoff_utc"]),
            "sudac": get_referee_summary(fx.get("referee")),
            "korneri_kartoni_domacin": get_team_stats_summary(fx["home_id"]),
            "korneri_kartoni_gost": get_team_stats_summary(fx["away_id"]),
            "taktika_domacin": get_tactical_notes(fx["home_name"]),
            "taktika_gost": get_tactical_notes(fx["away_name"]),
            "sentiment_vijesti_domacin": fetch_news_sentiment(fx["home_name"]),
            "sentiment_vijesti_gost": fetch_news_sentiment(fx["away_name"]),
        }

        # top 3 preporuke po utakmici (1X2, O/U2.5, BTTS), rangirane po edge-u
        recommendations = []
        if odds:
            implied = implied_probs_no_margin({k: odds[k] for k in ["1", "X", "2"]})
            for k in ["1", "X", "2"]:
                edge = model_probs[k] - implied[k]
                recommendations.append({"market": "1X2", "pick": k, "model_prob": model_probs[k],
                                         "odds": round(odds[k], 2), "edge": round(edge, 4),
                                         "risk": risk_label(model_probs[k], odds[k])})
        else:
            best = max(model_probs, key=model_probs.get)
            recommendations.append({"market": "1X2", "pick": best, "model_prob": model_probs[best],
                                     "odds": None, "edge": None, "risk": risk_label(model_probs[best])})

        if odds and "over25" in odds:
            implied_ou = implied_probs_no_margin({"over25": odds["over25"], "under25": odds["under25"]})
            for k in ["over25", "under25"]:
                edge = goal_probs[k] - implied_ou[k]
                recommendations.append({"market": "Over/Under 2.5", "pick": k, "model_prob": goal_probs[k],
                                         "odds": round(odds[k], 2), "edge": round(edge, 4),
                                         "risk": risk_label(goal_probs[k], odds[k])})

        if odds and "btts_da" in odds:
            implied_bt = implied_probs_no_margin({"btts_da": odds["btts_da"], "btts_ne": odds["btts_ne"]})
            for k in ["btts_da", "btts_ne"]:
                edge = goal_probs[k] - implied_bt[k]
                recommendations.append({"market": "Oba tima daju gol", "pick": k, "model_prob": goal_probs[k],
                                         "odds": round(odds[k], 2), "edge": round(edge, 4),
                                         "risk": risk_label(goal_probs[k], odds[k])})

        recommendations.sort(key=lambda r: (r["edge"] if r["edge"] is not None else r["model_prob"]), reverse=True)
        entry["top_preporuke"] = recommendations[:3]

        edges_with_value = [r["edge"] for r in recommendations if r["edge"] is not None]
        entry["market_odds"] = {k: round(v, 2) for k, v in odds.items() if isinstance(v, (int, float))} if odds else None
        entry["edge"] = round(max(edges_with_value), 4) if edges_with_value else None
        entry["napomena_kvote"] = "Prosjek dostupnih kladionica u regiji; ne nužno SuperSport." if odds else NIJE_DOSTUPNO

        candidates.append(entry)

    with_edge = sorted([c for c in candidates if c["edge"] is not None], key=lambda c: c["edge"], reverse=True)
    without_edge = [c for c in candidates if c["edge"] is None]
    top = (with_edge + without_edge)[:TOP_N]

    output = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window_days": DAYS_AHEAD,
        "aktivne_kategorije": {k: v for k, v in FEATURES.items() if v},
        "neaktivne_kategorije_cekaju_nadogradnju": {k: v for k, v in FEATURES.items() if not v},
        "disclaimer": ("Ovo su statističke procjene, ne jamstvo ishoda. Klađenje nosi rizik; "
                       "kladi se odgovorno i samo novcem koji si možeš priuštiti izgubiti."),
        "upravljanje_bankrollom": ("Opći okvir: ne stavljaj više od 1-2% ukupnog bankrolla po tiketu, "
                                    "veći ulog za 'Nizak' rizik, manji za 'Visok'. Nije financijski savjet."),
        "picks": top,
    }

    save_json(PICKS_PATH, output)
    save_json(RATINGS_PATH, ratings)
    save_json(FORM_PATH, form)
    print(f"Gotovo. Zapisano {len(top)} utakmica u {PICKS_PATH}")


if __name__ == "__main__":
    main()
