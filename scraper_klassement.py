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


DATE_TIME_RE = re.compile(r"(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2})")


def scrape_upcoming_matches(soup):
    """Scrapt het blok 'Eerstvolgende wedstrijden' op een teampagina.

    De site gebruikt (vermoedelijk) automatisch gegenereerde/gehashte CSS-klassen,
    dus we vertrouwen niet op class-namen maar op stabiele content-kenmerken die
    op elke teampagina hetzelfde patroon volgen:
      - een kop met daarin de tekst "Eerstvolgende wedstrijden"
      - per wedstrijd een sub-kop met datum + tijd, bv. "19/09/2026 18:00"
      - twee <img alt="Home team logo"> / <img alt="Opposing team logo">, telkens
        gevolgd door de teamnaam
      - een link naar Google Maps (adres van de locatie)
      - een link naar het "Digitaal wedstrijdformulier" (bevat "MatchDetail")
    Als Basketbal Vlaanderen deze opbouw ooit wijzigt, geeft deze functie gewoon
    een lege lijst terug (geen crash) — check dan de Action-logs.
    """
    heading = soup.find(
        lambda tag: tag.name in ("h1", "h2", "h3", "h4")
        and "eerstvolgende wedstrijden" in tag.get_text(strip=True).lower()
    )
    if not heading:
        return []

    matches = []
    current = None

    for el in heading.find_all_next():
        if not getattr(el, "name", None):
            continue

        # Stop zodra de volgende hoofdsectie begint (bv. het klassement) of de
        # "volledige kalender"-link voorbijkomt.
        if el.name in ("h1", "h2"):
            break
        text = el.get_text(strip=True)
        if el.name == "a" and "volledige kalender" in text.lower():
            break

        # Nieuwe wedstrijd-kaart: een sub-kop met datum + tijd.
        if el.name in ("h3", "h4"):
            m = DATE_TIME_RE.search(text)
            if m:
                if current:
                    matches.append(current)
                current = {
                    "datum": m.group(1),
                    "tijd": m.group(2),
                    "thuisploeg": "",
                    "uitploeg": "",
                    "locatie": "",
                    "locatie_url": "",
                    "formulier_url": "",
                }
                continue

        if current is None:
            continue

        if el.name == "img" and "team logo" in (el.get("alt") or "").lower():
            naam = el.parent.get_text(strip=True) if el.parent else ""
            if not current["thuisploeg"]:
                current["thuisploeg"] = naam
            elif not current["uitploeg"]:
                current["uitploeg"] = naam
            continue

        if el.name == "a":
            href = el.get("href") or ""
            if href.startswith("https://www.google.com/maps/dir/") and not current["locatie"]:
                current["locatie"] = text
                current["locatie_url"] = href
            elif "matchdetail" in href.lower() and not current["formulier_url"]:
                current["formulier_url"] = href

    if current:
        matches.append(current)

    # ISO-datum toevoegen zodat het front-end makkelijk kan sorteren/formatteren.
    for wedstrijd in matches:
        try:
            dag, maand, jaar = wedstrijd["datum"].split("/")
            wedstrijd["datetime_iso"] = f"{jaar}-{maand}-{dag}T{wedstrijd['tijd']}:00"
        except ValueError:
            wedstrijd["datetime_iso"] = None

    return matches


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

    # 1. Eerstvolgende wedstrijden
    matches = scrape_upcoming_matches(soup)

    # 2. Klassement scrapen
    standings = []
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
