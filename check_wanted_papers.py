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


THRESHOLD = 0.98

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
	if not a and not b:
		return 1.0

	return 1 - levenshtein(a, b) / max(len(a), len(b))


def read_csv(filename):
	with open(filename, "r", encoding="utf-8-sig", newline="") as f:
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

	for row in saved_rows:
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

	output_headers = [
		h for h in headers
		if h.strip().casefold() in ("year", "title", "authors")
		or "doi" in h.casefold()
	]

	writer = csv.DictWriter(
		sys.stdout,
		fieldnames=output_headers,
		lineterminator="\n",
		extrasaction="ignore"
	)

	if missing_rows:
		writer.writeheader()
		writer.writerows(missing_rows)


if __name__ == "__main__":
	main()