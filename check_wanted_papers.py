#!/usr/bin/python3
################################################################################
# @file      check-wanted-papers.py
# @brief     
# @date      Tu Nov 2026
# @author    Dimitri Simon
# 
# PROJECT:   prisma-helper
# 
# MODIFIED:  Tue Nov 03 2026
# BY:        Dimitri Simon
# 
# Copyright (c) 2026 Dimitri Simon
# 
################################################################################

import csv
import sys
import re
import unicodedata
from rich.console import Console


console = Console()
THRESHOLD = 0.95

def normalize_text(text):
	"""Normalize text for comparison."""
	text = unicodedata.normalize("NFKD", str(text))
	text = "".join(
		c for c in text if not unicodedata.combining(c)
	)
	text = text.casefold()
	text = re.sub(r"[^\w\s]", " ", text)
	text = re.sub(r"\s+", " ", text)
	return text.strip()


def normalize_doi(doi):
	"""Normalize DOI."""
	doi = str(doi or "").strip().casefold()
	doi = re.sub(
		r"^(https?://)?(dx\.)?doi\.org/",
		"",
		doi
	)
	doi = re.sub(r"^doi:\s*", "", doi)
	return doi.strip().rstrip(".")


def levenshtein(a, b):
	"""Calculate Levenshtein edit distance."""
	if len(a) < len(b):
		a, b = b, a

	previous = list(range(len(b) + 1))

	for i, ca in enumerate(a, 1):
		current = [i]
		for j, cb in enumerate(b, 1):
			current.append(min(
				current[j - 1] + 1,
				previous[j] + 1,
				previous[j - 1] + (ca != cb)
			))
		previous = current

	return previous[-1]

def similarity(a, b):
	"""Return normalized Levenshtein similarity (0 to 1)."""
	if not a or not b:
		if not a and not b:
			return 1.0
		return 0.0
	return 1 - levenshtein(a, b) / max(len(a), len(b))

def read_csv(filename):
	with open(filename, "r", encoding="utf-8-sig", newline="") as f:
		first = f.readline().strip()
		# Single-column list (e.g. papers.txt): titles may contain commas,
		# so read one value per line instead of splitting as CSV.
		if first.casefold() == "title":
			rows = [
				{"Title": line.strip()}
				for line in f if line.strip()
			]
			return ["Title"], rows
		f.seek(0)
		reader = csv.DictReader(f)
		if not reader.fieldnames:
			sys.exit(f"Error: Empty CSV file: {filename}")
		return reader.fieldnames, list(reader)

def find_column(headers, name):
	"""Find a column regardless of capitalization or surrounding spaces."""
	for header in headers:
		if header.strip().casefold() == name.casefold():
			return header
	return None

def main():
	if len(sys.argv) != 3:
		sys.exit(f"Usage: {sys.argv[0]} <scopus_export.csv> <expected_papers.csv>")

	existing_file = sys.argv[1]
	saved_file = sys.argv[2]

	headers, saved_rows = read_csv(saved_file)
	existing_headers, existing_rows = read_csv(existing_file)

	# Identify columns
	saved_title = find_column(headers, "Title")
	existing_title = find_column(existing_headers, "Title")

	saved_doi = next(
		(h for h in headers if "doi" in h.casefold()),
		None
	)

	existing_doi = next(
		(h for h in existing_headers if "doi" in h.casefold()),
		None
	)

	if not saved_title or not existing_title:
		sys.exit("Error: Both CSV files must contain a Title column.")

	# Extract existing titles and DOIs
	existing_titles = {
		normalize_text(row[existing_title])
		for row in existing_rows
		if row.get(existing_title, "").strip()
	}

	existing_dois = {
		normalize_doi(row[existing_doi])
		for row in existing_rows
		if existing_doi and row.get(existing_doi, "").strip()
	}

	# Prepare titles for fuzzy matching
	existing_titles_list = list(existing_titles)

	missing_rows = []

	from rich.progress import track
	for row in track(saved_rows, description="Checking papers..."):
		title = normalize_text(row.get(saved_title, ""))

		doi = normalize_doi(
			row.get(saved_doi, "") if saved_doi else ""
		)

		# DOI match takes priority
		if doi and doi in existing_dois:
			continue

		# Exact normalized title match
		if title and title in existing_titles:
			continue

		# Fuzzy title matching
		if title and any(
			similarity(title, existing) >= THRESHOLD
			for existing in existing_titles_list
		):
			continue

		missing_rows.append(row)

	# Print results
	print(f"Missing papers: {len(missing_rows)}", file=sys.stderr)

	# output_headers = [
	# 	h for h in headers
	# 	if h.strip().casefold() in ("year", "title", "publication venue", "source title", "doi", "link")
	# 	or "doi" in h.casefold()
	# ]

	if missing_rows:
		from rich.table import Table
		from rich import box
		table = Table(
			title="Missing Papers",
			box=box.ROUNDED,
			show_lines=True,
			header_style="bold cyan"
		)

		table.add_column("Year")
		table.add_column("Title", overflow="fold")
		table.add_column("Publication Venue", overflow="fold")
		table.add_column("DOI / Source", overflow="fold")

		for item in missing_rows:
			table.add_row(
				str(item.get("Year", "")),
				str(item.get("Title", "")),
				str(item.get("Publication venue", "")),
				str(item.get("DOI / Source", ""))
			)

		console.print(table)


if __name__ == "__main__":
	main()