#!/usr/bin/env python3
"""
Look up journal and conference rankings.

Routing:
- Names containing "journal", but not "proceedings" or "conference":
  SCImago first; if no quartile is found, try CORE.
- All other names:
  CORE first; if no CORE rank is found, try SCImago.

Input:  venues.txt (one journal/conference name per line)
Output: rankings_results.md (Markdown table)

Install:
	pip install requests beautifulsoup4 pandas rich tabulate
"""

import random
import re
import time
from urllib.parse import urljoin
import json
import os
from datetime import datetime, timedelta, timezone
import pandas as pd
import requests
from requests.exceptions import RequestException
from bs4 import BeautifulSoup
from rich.console import Console
from rich.table import Table
from rich import box
from rich.progress import (
	BarColumn,
	Progress,
	SpinnerColumn,
	TextColumn,
	TimeElapsedColumn,
	TimeRemainingColumn,
)

console = Console()
# Cache configuration
CACHE_FILE = "rankings_cache.json"
CACHE_DURATION_DAYS = 30

# Set to True to ignore existing cache and refresh everything.
FORCE_REFRESH = False

SCIMAGO_BASE = "https://www.scimagojr.com/"
SCIMAGO_SEARCH = urljoin(SCIMAGO_BASE, "journalsearch.php")
CORE_SEARCH = "https://portal.core.edu.au/conf-ranks/"

COMMON_HEADERS = {
	"User-Agent": (
		"Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:158.0) "
		"Gecko/20100101 Firefox/158.0"
	),
	"Accept": (
		"text/html,application/xhtml+xml,application/xml;"
		"q=0.9,*/*;q=0.8"
	),
	"Accept-Language": "en-GB,en;q=0.9,fr;q=0.8",
	"Upgrade-Insecure-Requests": "1",
	"Connection": "keep-alive",
}

HEADERS_SCIMAGO = {
	**COMMON_HEADERS,
	"Referer": (
		"https://www.scimagojr.com/journalsearch.php"
	),
}

HEADERS_PORTAL_CORE = {
	**COMMON_HEADERS,
	"Referer": (
		"https://portal.core.edu.au/conf-ranks/"
	),
}

def missing(value):
	"""True for empty or placeholder values."""
	return value is None or str(value).strip().lower() in {
		"", "not found", "n/a", "none", "-"
	}


def normalise(value):
	"""Normalise names for matching search results."""
	return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def is_journal_name(name):
	name_lower = name.lower()
	return (
		"journal" in name_lower
		and "proceedings" not in name_lower
		and "conference" not in name_lower
	)
def print_rich_table(results):
	if not results:
		console.print("[yellow]No results found.[/yellow]")
		return

	table = Table(
		title="Journal and Conference Rankings",
		box=box.ROUNDED,
		show_lines=True,
		header_style="bold cyan"
	)

	table.add_column("Name", overflow="fold")
	table.add_column("Acronym")
	table.add_column("Source")
	table.add_column("SJR", justify="center")
	table.add_column("Quartile / Rank", justify="center")
	table.add_column("H-index", justify="center")

	for item in results:
		table.add_row(
			str(item.get("Name", "N/A")),
			str(item.get("Acronym", "N/A")),
			str(item.get("Source", "N/A")),
			str(item.get("SJR", "N/A")),
			str(item.get("Quartile / Rank", "N/A")),
			str(item.get("H-index", "N/A"))
		, style="on red" if (item.get("Quartile / Rank") not in ["Q1", "A*", "A"]) else "")

	console.print(table)

def load_cache():
	"""Load cached results from JSON."""

	if not os.path.exists(CACHE_FILE):
		console.print("[yellow]No existing cache found.[/yellow]")
		return {}

	try:
		with open(CACHE_FILE, "r", encoding="utf-8") as file:
			cache = json.load(file)

		console.print(
			f"[green]Loaded {len(cache)} cached entries.[/green]"
		)

		return cache

	except (json.JSONDecodeError, OSError) as exc:
		console.print(f"[red]Could not load cache: {exc}[/red]")
		return {}


def save_cache(cache):
	"""Save cache to JSON."""

	try:
		with open(CACHE_FILE, "w", encoding="utf-8") as file:
			json.dump(
				cache,
				file,
				indent=4,
				ensure_ascii=False
			)

	except OSError as exc:
		console.print(f"[red]Could not save cache: {exc}[/red]")


def get_cache_key(name):
	"""Generate a normalised cache key."""

	return normalise(name)


def get_cached_result(name, cache):
	"""Return a valid cached result, or None if unavailable/expired."""

	key = get_cache_key(name)

	if FORCE_REFRESH or key not in cache:
		return None

	entry = cache[key]

	try:
		cached_at = datetime.fromisoformat(entry["timestamp"])

		if cached_at.tzinfo is None:
			cached_at = cached_at.replace(tzinfo=timezone.utc)

		expiration = cached_at + timedelta(
			days=CACHE_DURATION_DAYS
		)

		if datetime.now(timezone.utc) > expiration:
			console.print(
				f"[yellow]Cache expired:[/yellow] {name}"
			)
			return None

		console.print(
			f"[green]CACHE HIT:[/green] {name}"
		)

		return entry["result"]

	except (KeyError, ValueError, TypeError):
		return None


def update_cache(name, result, cache):
	"""Store a successful result in the cache."""

	# Do not cache failed or incomplete lookups.
	if result.get("Status") != "OK":
		return

	key = get_cache_key(name)

	cache[key] = {
		"timestamp": datetime.now(timezone.utc).isoformat(),
		"result": result
	}

	save_cache(cache)

	console.print(
		f"[cyan]Cached:[/cyan] {name}"
	)
	
def fetch_url(session, url, headers=None, params=None):
	"""Fetch a URL and report HTTP or connection errors."""

	try:
		response = session.get(
			url,
			headers=headers,
			params=params,
			timeout=30
		)

		# console.print(
		# 	f"[cyan]HTTP {response.status_code}[/cyan] "
		# 	f"[dim]{response.url}[/dim]"
		# )

		response.raise_for_status()
		return response

	except RequestException as exc:
		console.print(f"\n[bold red]REQUEST FAILED[/bold red]")
		console.print(f"[red]URL:[/red] {url}")
		console.print(f"[red]Error:[/red] {exc}")

		if exc.response is not None:
			console.print(
				f"[red]HTTP status:[/red] {exc.response.status_code}"
			)
			console.print(
				f"[red]Response body:[/red]\n"
				f"{exc.response.text[:1500]}"
			)

		return None

def extract_scimago_metrics(soup):
	"""Extract SJR, quartile and H-index from a SCImago profile page."""

	metrics = {
		"SJR": "N/A",
		"Quartile / Rank": "N/A",
		"H-index": "N/A"
	}

	# SJR
	sjr_element = soup.select_one(".hsjr")
	if sjr_element:
		metrics["SJR"] = sjr_element.get_text(strip=True)

	# Quartile
	quartile_element = soup.select_one(
		".content-hindex .hindexnumber span[class^='Q']"
	)
	if quartile_element:
		metrics["Quartile / Rank"] = quartile_element.get_text(strip=True)

	# H-index: find the square containing the H-Index heading
	for square in soup.select(".content-cuadrado .cuadrado"):
		heading = square.find("h2")

		if heading and "H-Index" in heading.get_text(strip=True):
			value = square.select_one(".hindexnumber")

			if value:
				metrics["H-index"] = value.get_text(strip=True)

			break

	return metrics

def extract_scimago_quartiles(soup):
	"""Return all quartiles found in SCImago's quartiles section."""
	found = []
	container = soup.find("div", class_="quartiles")

	if container:
		for item in container.find_all("div", class_="quartile"):
			year = item.find("span", class_="year")
			quartile = item.find("span", class_="quartile")
			if year and quartile:
				q = quartile.get_text(" ", strip=True)
				y = year.get_text(" ", strip=True)
				found.append(f"{q} ({y})")

	# Keep order but remove duplicates.
	found = list(dict.fromkeys(found))
	return "; ".join(found) if found else "Not Found"


def extract_scimago_categories(soup):
	areas, categories = [], []
	container = soup.find("div", class_="table_area")

	if container:
		for row in container.find_all("tr"):
			cells = row.find_all("td")
			if len(cells) >= 2:
				areas.append(cells[0].get_text(" ", strip=True))
				categories.append(cells[1].get_text(" ", strip=True))

	return (
		"; ".join(dict.fromkeys(areas)) if areas else "Not Found",
		"; ".join(dict.fromkeys(categories)) if categories else "Not Found",
	)


def search_scimago(name, session):
	"""Search SCImago and return journal metrics, quartile and profile URL."""
	data = {
		"Found Title": "Not Found",
		"SJR": "Not Found",
		"Quartile / Rank": "Not Found",
		"H-index": "Not Found",
		"Subject Area": "Not Found",
		"Subject Category": "Not Found",
		"SCImago Link": "Not Found",
		"SCImago Status": "Not Found",
	}

	try:
		response = fetch_url(
			session,
			SCIMAGO_SEARCH,
			headers=HEADERS_SCIMAGO,
			params={"q": name, "tip": "jou", "clean": "0"}
		)
		if response is None:
			return None
		response.raise_for_status()
		soup = BeautifulSoup(response.content, "html.parser")
		# console.print("\n[bold yellow]SCIMAGO PAGE DEBUG[/bold yellow]")

		# console.print(f"Page title: {soup.title.get_text(strip=True) if soup.title else 'No title'}")

		# console.print("\n[bold]First 3000 characters of page text:[/bold]")
		# console.print(soup.get_text(" ", strip=True)[:3000])

		results = soup.find("div", class_="search_results")
		first = results.find("a", href=True) if results else None
		if not first:
			data["SCImago Status"] = "No search result"
			return data

		profile_url = urljoin(SCIMAGO_BASE, first["href"])
		data["Found Title"] = first.get_text(" ", strip=True)
		data["SCImago Link"] = profile_url

		time.sleep(random.uniform(0.8, 1.5))
		profile_response = fetch_url(
			session,
			profile_url,
			headers=HEADERS_SCIMAGO
		)
		if profile_response is None:
			return None
		profile_response.raise_for_status()
		profile = BeautifulSoup(profile_response.content, "html.parser")
		

		data |= extract_scimago_metrics(profile)
		data["Subject Area"], data["Subject Category"] = (
			extract_scimago_categories(profile)
		)
		data["SCImago Status"] = "OK"

	except requests.RequestException as exc:
		data["SCImago Status"] = f"Request error: {exc}"
	except Exception as exc:
		data["SCImago Status"] = f"Parsing error: {exc}"

	return data

def extract_core_results(soup):
	"""Extract conference records from Portal CORE."""

	results = []

	for row in soup.find_all("tr"):

		cells = row.find_all("td", recursive=False)

		if len(cells) < 4:
			continue

		name = cells[0].get_text(" ", strip=True)
		acronym = cells[1].get_text(" ", strip=True)
		source = cells[2].get_text(" ", strip=True)
		rank = cells[3].get_text(" ", strip=True)

		if not name or not rank:
			continue

		onclick = row.get("onclick", "")

		match = re.search(
			r"""navigate\(\s*['"]([^'"]+)['"]\s*\)""",
			onclick
		)

		profile_url = (
			urljoin("https://portal.core.edu.au", match.group(1))
			if match else "N/A"
		)

		results.append({
			"Name": name,
			"Acronym": acronym or "N/A",
			"Source": source or "N/A",
			"Rank": rank,
			"Profile URL": profile_url
		})

	return results
def search_core(name, session):
	"""Search Portal CORE using original and cleaned search terms."""

	data = {
		"CORE Found Title": "N/A",
		"Conference Acronym": "N/A",
		"CORE Source": "N/A",
		"Quartile / Rank": "N/A",
		"CORE Link": "N/A",
		"CORE Status": "Not Found"
	}

	stop_words = {
		"the", "of", "and", "on", "for", "in",
		"a", "an", "proceedings", "conference"
	}

	def match_score(query, result):
		query_norm = normalise(query)
		title_norm = normalise(result["Name"])

		if query_norm == title_norm:
			return 100

		query_words = set(query_norm.split()) - stop_words
		title_words = set(title_norm.split()) - stop_words

		if not query_words or not title_words:
			return 0

		common = query_words.intersection(title_words)

		recall = len(common) / len(query_words)
		precision = len(common) / len(title_words)

		score = 100 * (0.75 * recall + 0.25 * precision)

		acronym = normalise(result["Acronym"])

		if acronym and acronym in query_words:
			score = max(score, 100)

		return score

	candidates = []

	try:
		for query in core_query_variants(name):

			console.print(
				f"\n[cyan]CORE search:[/cyan] {query}"
			)

			response = fetch_url(
				session,
				CORE_SEARCH,
				headers=HEADERS_PORTAL_CORE,
				params={
					"search": query,
					"by": "all",
					"source": "all",
					"sort": "arank",
					"page": 1
				}
			)

			if response is None:
				continue

			soup = BeautifulSoup(
				response.content,
				"html.parser"
			)

			rows = extract_core_results(soup)

			# console.print(
			# 	f"[dim]CORE rows extracted: {len(rows)}[/dim]"
			# )

			for item in rows:
				score = match_score(query, item)

				if score >= 60:
					candidates.append((score, item))

		if not candidates:
			data["CORE Status"] = "No matching conference"

			console.print(
				f"[yellow]No matching CORE result for {name}[/yellow]"
			)

			return data

		candidates.sort(
			key=lambda x: x[0],
			reverse=True
		)

		score, best = candidates[0]

		data.update({
			"CORE Found Title": best["Name"],
			"Conference Acronym": best["Acronym"],
			"CORE Source": best["Source"],
			"Quartile / Rank": best["Rank"],
			"CORE Link": best["Profile URL"],
			"CORE Status": "OK"
		})

		# console.print(
		# 	f"\n[bold green]CORE MATCH FOUND[/bold green]\n"
		# 	f"Name: {best['Name']}\n"
		# 	f"Acronym: {best['Acronym']}\n"
		# 	f"Source: {best['Source']}\n"
		# 	f"Rank: {best['Rank']}\n"
		# 	f"Match score: {score:.1f}%"
		# )

	except requests.RequestException as exc:
		data["CORE Status"] = f"Request error: {exc}"

	except Exception as exc:
		data["CORE Status"] = f"Parsing error: {exc}"
		console.print(f"[red]CORE error: {exc}[/red]")

	return data

def core_query_variants(name):
	"""Generate cleaner search terms for Portal CORE."""

	variants = [name]

	cleaned = re.sub(
		r"^\s*proceedings\s+(?:(?:of|-)\s+)?(?:the\s+)?(?:\d+(?:st|nd|rd|th)?\s+)?(?:international\s+)?",
		"",
		name,
		flags=re.IGNORECASE
	)

	cleaned = re.sub(
		r"\bcomputer\s+society\b",
		"",
		cleaned,
		flags=re.IGNORECASE
	)

	cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.-")

	if cleaned and cleaned.lower() not in {
		v.lower() for v in variants
	}:
		variants.append(cleaned)

	return variants

def process_venue(name, session):
	"""Apply source priority and return a consistent result dictionary."""

	result = {
		"Input Name": name,
		"Name": name,
		"Acronym": "N/A",
		"Source": "N/A",
		"SJR": "N/A",
		"Quartile / Rank": "N/A",
		"H-index": "N/A",
		"Ranking Source": "N/A",
		"SCImago Link": "N/A",
		"CORE Link": "N/A",
		"SCImago Status": "Not searched",
		"CORE Status": "Not searched",
		"Status": "Ranking not found"
	}

	def apply_core(core):
		if not core:
			return

		if not missing(core.get("CORE Found Title")):
			result["Name"] = core["CORE Found Title"]

		if not missing(core.get("Conference Acronym")):
			result["Acronym"] = core["Conference Acronym"]

		if not missing(core.get("CORE Source")):
			result["Source"] = core["CORE Source"]

		if not missing(core.get("Quartile / Rank")):
			result["Quartile / Rank"] = core["Quartile / Rank"]

		if not missing(core.get("CORE Link")):
			result["CORE Link"] = core["CORE Link"]

		result["CORE Status"] = core.get("CORE Status", "Unknown")

	def apply_scimago(sjr, replace_name=True):
		if not sjr:
			return

		if replace_name and not missing(sjr.get("Found Title")):
			result["Name"] = sjr["Found Title"]

		if not missing(sjr.get("SJR")):
			result["SJR"] = sjr["SJR"]

		if not missing(sjr.get("H-index")):
			result["H-index"] = sjr["H-index"]

		if not missing(sjr.get("Quartile / Rank")):
			result["Quartile / Rank"] = sjr["Quartile / Rank"]

		if not missing(sjr.get("SCImago Link")):
			result["SCImago Link"] = sjr["SCImago Link"]

		result["SCImago Status"] = sjr.get(
			"SCImago Status", "Unknown"
		)

		if result["Source"] == "N/A":
			result["Source"] = "SCImago"

	journal_first = is_journal_name(name)

	if journal_first:
		# Primary source: SCImago.
		sjr = search_scimago(name, session)
		apply_scimago(sjr)

		if not missing(result["Quartile / Rank"]):
			result["Ranking Source"] = "SCImago"

		else:
			# Fallback: Portal CORE.
			core = search_core(name, session)
			apply_core(core)

			if not missing(result["Quartile / Rank"]):
				result["Ranking Source"] = "CORE (fallback)"

	else:
		# Primary source: Portal CORE.
		core = search_core(name, session)
		apply_core(core)

		if not missing(result["Quartile / Rank"]):
			result["Ranking Source"] = "CORE"

		else:
			# Fallback: SCImago, but preserve CORE conference information.
			sjr = search_scimago(name, session)

			if sjr:
				result["SJR"] = sjr.get("SJR", "N/A")
				result["H-index"] = sjr.get("H-index", "N/A")
				result["SCImago Link"] = sjr.get("SCImago Link", "N/A")

				if missing(result["Quartile / Rank"]):
					quartile = sjr.get("Quartile / Rank", "N/A")

					if not missing(quartile):
						result["Quartile / Rank"] = quartile
						result["Ranking Source"] = "SCImago (fallback)"

			# Do not replace the conference name with SCImago search-card text.
			if not missing(result["Quartile / Rank"]):
				result["Status"] = "OK"

	# A record is successful if a ranking or journal metric was retrieved.
	if (
		not missing(result["Quartile / Rank"])
		or not missing(result["SJR"])
		or not missing(result["H-index"])
	):
		result["Status"] = "OK"

	return result

def main():
	input_file = "venues.txt"
	output_file = "rankings_results.md"

	try:
		with open(input_file, "r", encoding="utf-8") as file:
			venues = [line.strip() for line in file if line.strip()]

		# Remove duplicates (case-insensitive), preserving original order
		unique_venues = []
		seen = set()

		for venue in venues:
			key = venue.casefold()
			if key not in seen:
				seen.add(key)
				unique_venues.append(venue)

		duplicates_removed = len(venues) - len(unique_venues)
		venues = unique_venues

	except FileNotFoundError:
		console.print(f"[red]Input file not found: {input_file}[/red]")
		return

	if not venues:
		console.print("[red]The input file is empty.[/red]")
		return

	results = []

	cache = load_cache()

	cache_hits = 0
	cache_misses = 0
	console.print(
		f"\n[bold cyan]Journal & Conference Ranking Lookup[/bold cyan]\n"
		f"Venues to process: [bold]{len(venues)}[/bold]\n"
	)

	with requests.Session() as session:
		with Progress(
			SpinnerColumn(),
			TextColumn("[progress.description]{task.description}"),
			BarColumn(),
			TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
			TextColumn("({task.completed}/{task.total})"),
			TimeElapsedColumn(),
			TimeRemainingColumn(),
			console=console,
		) as progress:
			task = progress.add_task(
				"[cyan]Looking up venues...", total=len(venues)
			)
			for venue in venues:
				progress.update(
					task,
					description=f"[cyan]{venue[:55]}"
				)
				# Check cache first.
				cached_result = get_cached_result(venue, cache)

				if cached_result is not None:
					result = cached_result
					cache_hits += 1
				else:
					# Cache miss: perform actual scraping.
					result = process_venue(venue, session)
					# Save successful results.
					update_cache(venue, result, cache)
					cache_misses += 1
					time.sleep(random.uniform(1.5, 3.0))
				results.append(result)
				progress.advance(task)
				
	print_rich_table(results)
	df = pd.DataFrame(results)

	# Use Markdown pipe-table output.
	with open(output_file, "w", encoding="utf-8") as file:
		file.write("# Journal and Conference Rankings\n\n")
		file.write(
			"> Journal names are searched in SCImago first. "
			"Other names are searched in CORE first. "
			"The other source is queried when the primary ranking is missing.\n\n"
		)
		file.write(df.to_markdown(index=False))
		file.write("\n")

	successful = sum(row["Status"] == "OK" for row in results)
	console.print("\n[bold green]Finished.[/bold green]")
	console.print(f"Rankings found: [green]{successful}[/green]/{len(venues)}")
	console.print(f"Markdown table saved to: [bold]{output_file}[/bold]")
 
	console.print("\n[bold cyan]Cache statistics[/bold cyan]")
	console.print(f"Cache hits: [green]{cache_hits}[/green]")
	console.print(f"Cache misses: [yellow]{cache_misses}[/yellow]")
	console.print(
		f"Cache file: [bold]{CACHE_FILE}[/bold]"
	)

if __name__ == "__main__":
	main()
