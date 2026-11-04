#!/usr/bin/python3
################################################################################
# @file      cross-references-find.py
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


import argparse, csv, json, os, re, sys
from rapidfuzz.distance import Levenshtein


_scopus_ready = False
THRESHOLD = 0.95

CACHE_FILE = "snowball_cache.json"
OUTPUT_FILE = "snowball.csv"

def normalize_doi(doi):
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", str(doi or "").strip())
    return doi or None

class QuotaExceeded(Exception):
    """The Scopus API quota/limit is exhausted; stop querying."""

def load_cache(path=CACHE_FILE):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}

def save_cache(cache, path=CACHE_FILE):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

def enrich_cached(ref, threshold, cache):
    """enrich() with a persistent cache; failed lookups (errors) are not cached."""
    key = f"{threshold}|{ref}"
    if key in cache:
        return cache[key]
    try:
        value = _lookup(ref, threshold)
    except Exception as e:
        if "maximum number allowed" in str(e) or type(e).__name__ == "Scopus429Error":
            raise QuotaExceeded(str(e)) from e
        print(f"Error retrieving {ref[:60]}: {e}", file=sys.stderr)
        return ["", "", ""]
    cache[key] = value
    return value

def enrich(ref, threshold=THRESHOLD):
    """Look the reference up in Scopus; return [doi, publication venue, abstract] (empty if unknown)."""
    try:
        return _lookup(ref, threshold)
    except Exception as e:
        print(f"Error retrieving {ref[:60]}: {e}", file=sys.stderr)
        return ["", "", ""]

def _lookup(ref, threshold):
    global _scopus_ready
    from pybliometrics.scopus import AbstractRetrieval, ScopusSearch
    if not _scopus_ready:
        from pybliometrics import init
        init()
        _scopus_ready = True
    key = parse(ref)
    if key is None:
        return ["", "", ""]
    hits = ScopusSearch(f'TITLE("{key[1]}")', view="STANDARD", download=True).results or []
    hits = [h for h in hits
            if Levenshtein.normalized_similarity(clean(h.title or ""), key[1]) >= threshold]
    if not hits:
        return ["", "", ""]
    best = max(hits, key=lambda h: Levenshtein.normalized_similarity(clean(h.title), key[1]))
    doi = normalize_doi(best.doi)
    venue = best.publicationName or ""
    abstract = ""
    if doi:
        article = AbstractRetrieval(doi, id_type="doi", view="META_ABS")
        abstract = article.abstract or article.description or ""
    return [doi or "", venue, abstract]

def split_references(text):
    # References are "; "-separated, each ending with "(year)"; authors also use "; "
    return [r.strip() for r in re.split(r"(?<=\(\d{4}\)); ", text) if r.strip()]

def clean(text):
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()

def parse(ref):
    """Return (first-author surname, normalized title) or None if no usable title."""
    body = re.sub(r"^(?:[^,;]+?\.(?:; )?)+, ", "", ref)
    title = clean(re.split(r", ", body)[0])
    return (clean(ref.split()[0]), title) if len(title) > 8 else None

def same_paper(a, b, threshold):
    return a[0] == b[0] and Levenshtein.normalized_similarity(a[1], b[1]) >= threshold

def find_cross_references(file_path, min_papers=2, exclude_present=False, threshold=THRESHOLD):
    with open(file_path, 'r', encoding='utf-8-sig') as file:
        rows = list(csv.DictReader(file))
    present = [clean(r["Title"]) for r in rows] if exclude_present else []
    # Each cluster: [parsed key, display ref, set of citing papers]
    clusters = []
    for row in rows:
        for ref in split_references(row["References"]):
            key = parse(ref)
            if key is None or any(Levenshtein.normalized_similarity(key[1], t) >= threshold for t in present):
                continue
            for c in clusters:
                if same_paper(c[0], key, threshold):
                    c[2].add(row["Title"])
                    break
            else:
                clusters.append([key, ref, {row["Title"]}])
    result = [(c[1], sorted(c[2])) for c in clusters if len(c[2]) >= min_papers]
    return sorted(result, key=lambda x: (-len(x[1]), x[0]))

if __name__ == "__main__":
	parser = argparse.ArgumentParser()
	parser.add_argument("screen", nargs='?', default="screen.csv", help="CSV file with papers to screen (default: screen.csv)")
	parser.add_argument("-x", "--exclude-present", action="store_true",
						help="skip references that are themselves papers listed in the CSV")
	parser.add_argument("-t", "--threshold", type=float, default=THRESHOLD,
						help="minimum normalized Levenshtein similarity of titles (default {THRESHOLD})".format(THRESHOLD=THRESHOLD))
	parser.add_argument("--no-enrich", action="store_true",
						help="skip the Scopus lookup of DOI, publication venue and abstract")
	args = parser.parse_args()
	from rich.console import Console
	from rich.progress import track
	from rich.table import Table
	from rich import box
	console = Console()

	with console.status("Finding cross references..."):
		results = find_cross_references(args.screen, exclude_present=args.exclude_present,
										threshold=args.threshold)

	header = ["Citing papers", "Reference"]
	if not args.no_enrich:
		header += ["DOI", "Publication venue", "Abstract"]
	cache = {} if args.no_enrich else load_cache()
	rows = []
	try:
		quota_exceeded = False
		for ref, papers in track(results, description="Enriching references...",
								 console=console, disable=args.no_enrich):
			row = [len(papers), ref]
			if not args.no_enrich:
				try:
					row += enrich_cached(ref, args.threshold, cache)
				except QuotaExceeded as e:
					if not quota_exceeded:
						console.print(f"[red]Scopus quota exceeded ({e}). Continuing with the remaining references without enrichment...[/red]")
						quota_exceeded = True
			rows.append(row)
	finally:
		if not args.no_enrich:
			save_cache(cache)

	with open(OUTPUT_FILE, "w", encoding="utf-8", newline="") as f:
		writer = csv.writer(f, lineterminator="\n")
		writer.writerow(header)
		writer.writerows(rows)

	table = Table(title=f"Snowballing candidates ({len(rows)})", box=box.ROUNDED,
				  show_lines=True, header_style="bold cyan")
	for h in header:
		table.add_column(h, overflow="fold")
	for row in rows:
		shown = [str(c) for c in row]
		if not args.no_enrich:
			shown[-1] = shown[-1][:200] + ("…" if len(shown[-1]) > 200 else "")
		table.add_row(*shown)
	console.print(table)
	console.print(f"Saved to {OUTPUT_FILE}")
