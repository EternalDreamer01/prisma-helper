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

	# Index existing rows by normalized title and DOI
	existing_by_title = {}
	existing_by_doi = {}
	for row in existing_rows:
		t = normalize_text(row.get(existing_title, ""))
		if t:
			existing_by_title.setdefault(t, row)
		if existing_doi:
			d = normalize_doi(row.get(existing_doi, ""))
			if d:
				existing_by_doi.setdefault(d, row)

	existing_titles_list = list(existing_by_title)
	references_col = find_column(existing_headers, "References")
	year_col = find_column(existing_headers, "Year")
	source_col = find_column(existing_headers, "Source title")

	missing_rows = []
	matching_rows = []

	def record_match(saved_row, existing_row):
		matching_rows.append({
			"Year": existing_row.get(year_col, "") if year_col else "",
			"Title": saved_row.get(saved_title, ""),
			# "Matched Title": existing_row.get(existing_title, ""),
			"Source title": existing_row.get(source_col, "") if source_col else "",
			"DOI": existing_row.get(existing_doi, "") if existing_doi else "",
			"References": existing_row.get(references_col, "") if references_col else "",
		})

	from rich.progress import track
	for row in track(saved_rows, description="Checking papers..."):
		title = normalize_text(row.get(saved_title, ""))

		doi = normalize_doi(
			row.get(saved_doi, "") if saved_doi else ""
		)

		# DOI match takes priority
		if doi and doi in existing_by_doi:
			record_match(row, existing_by_doi[doi])
			continue

		# Exact normalized title match
		if title and title in existing_by_title:
			record_match(row, existing_by_title[title])
			continue

		# Fuzzy title matching
		if title:
			best = max(
				existing_titles_list,
				key=lambda e: similarity(title, e),
				default=None
			)
			if best is not None and similarity(title, best) >= THRESHOLD:
				record_match(row, existing_by_title[best])
				continue

		missing_rows.append(row)

	with open("matched.csv", "w", encoding="utf-8", newline="") as f:
		writer = csv.DictWriter(
			f,
			fieldnames=["Year", "Title", "Source title", "DOI", "References"],
			lineterminator="\n"
		)
		writer.writeheader()
		writer.writerows(matching_rows)

	# Print results
	print(f"Missing papers: {len(missing_rows)}", file=sys.stderr)

	# output_headers = [
	# 	h for h in headers
	# 	if h.strip().casefold() in ("year", "title", "publication venue", "source title", "doi", "link")
	# 	or "doi" in h.casefold()
	# ]

	# writer = csv.DictWriter(
	# 	sys.stdout,
	# 	fieldnames=output_headers,
	# 	lineterminator="\n",
	# 	extrasaction="ignore"
	# )

	# writer.writeheader()
	# writer.writerows(missing_rows)

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