import json
import re
import sys
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.basketbal.vlaanderen"
START_URL = f"{BASE_URL}/resultaten/bbc-coveco-niel"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# Trefwoorden om te verifiëren dat het om Niel / Rupel Pirates gaat
KEYWORDS = ["niel", "rupel pirates", "coveco"]


def is_relevant(text):
    """Controleert of een tekst minimaal één van de trefwoorden bevat."""
    return any(keyword in text.lower() for keyword in KEYWORDS)


def fetch(url, retries=2, timeout=20):
    """GET met timeout en een paar retries, zodat een tijdelijk netwerkprobleem
    tijdens de geplande 6-uurlijkse run niet meteen het hele script laat falen."""
    last_err = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=timeout)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            last_err = e
    raise last_err


def get_team_links():
    """Haalt enkel de 11 ploeglinks van Niel op van de hoofdpagina."""
    response = fetch(START_URL)
    soup = BeautifulSoup(response.text, "html.parser")

    team_links = []
    # Zoek specifiek naar de extended links
    links = soup.find_all("a", class_=re.compile(r"link--extended"))

    for link in links:
        href = link.get("href")
        team_name = link.text.strip()

        # Check of de link of teamnaam 'niel', 'rupel pirates' of 'coveco' bevat
        if href and team_name and is_relevant(team_name + href):
            full_url = BASE_URL + href if href.startswith("/") else href
            team_links.append({"name": team_name, "url": full_url})

    return team_links


def scrape_team_data(team_url):
    """Scrapt gericht de wedstrijden en het klassement per teampagina."""
    response = fetch(team_url)
    soup = BeautifulSoup(response.text, "html.parser")

    matches = []
    standings = []

    # 1. Wedstrijden filteren op Niel / Rupel Pirates
    match_blocks = soup.find_all("div", class_=re.compile(r"match|game|fixture"))
    for block in match_blocks:
        lines = [line.strip() for line in block.get_text(separator="\n").split("\n") if line.strip()]
        full_text = " ".join(lines)
        
        # Alleen toevoegen als de wedstrijd over Niel / Rupel Pirates gaat
        if is_relevant(full_text):
            matches.append(lines)

    # 2. Klassement scrapen
    tables = soup.find_all("table")
    for table in tables:
        rows = table.find_all("tr")
        for row in rows:
            cols = [col.text.strip() for col in row.find_all(["td", "th"])]
            # Voeg rij toe als er data in staat
            if cols and len(cols) > 1:
                standings.append(cols)

    return {"matches": matches, "standings": standings}


def main():
    print("Starten met ophalen van Niel / Rupel Pirates teamlinks...")
    try:
        teams = get_team_links()
    except requests.RequestException as e:
        print(f"FOUT: kon de hoofdpagina niet ophalen ({e}). data.json wordt niet aangepast.")
        sys.exit(1)

    print(f"{len(teams)} relevante ploegen gevonden.\n")

    # Veiligheidscheck: als de site structuur wijzigt of tijdelijk niet bereikbaar is,
    # kan get_team_links() een lege/onvolledige lijst teruggeven. In dat geval data.json
    # NIET overschrijven, zodat de laatst gekende goede stand online blijft staan.
    MIN_EXPECTED_TEAMS = 8
    if len(teams) < MIN_EXPECTED_TEAMS:
        print(
            f"FOUT: slechts {len(teams)} ploeg(en) gevonden (verwacht >= {MIN_EXPECTED_TEAMS}). "
            "Vermoedelijk is de site-structuur gewijzigd of niet bereikbaar. "
            "data.json wordt niet aangepast."
        )
        sys.exit(1)

    all_data = {}
    failed = []

    for team in teams:
        print(f"Scrapen van: {team['name']}...")
        try:
            team_data = scrape_team_data(team["url"])
        except requests.RequestException as e:
            print(f"  -> mislukt ({e}), sla deze ploeg over.")
            failed.append(team["name"])
            continue
        all_data[team["name"]] = {
            "url": team["url"],
            "matches": team_data["matches"],
            "standings": team_data["standings"],
        }

    all_data["_meta"] = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "team_count": len(all_data),
        "failed_teams": failed,
    }

    # Data opslaan in JSON
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)

    print("\nKlaar! Gefilterde data opgeslagen in 'data.json'.")
    if failed:
        print(f"Let op: {len(failed)} ploeg(en) konden niet opgehaald worden: {', '.join(failed)}")


if __name__ == "__main__":
    main()