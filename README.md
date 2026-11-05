# PRISMA Helper

Small command-line scripts that help researchers with the screening and
identification steps of a [PRISMA](https://www.prisma-statement.org/) systematic
review (Scopus CSV exports in, CSV/Markdown out).

| Script | PRISMA step | Purpose |
|--------|-------------|---------|
| `check_wanted_papers.py` | Search validation | Check that known/expected papers are present in your search results |
| `cross_references_finder.py` | Snowballing | Find papers cited by several screened papers, enriched with DOI, venue and abstract |
| `batch_venue_verifier.py` | Eligibility / quality | Look up [SCImago](https://www.scimagojr.com/) quartiles and [CORE](https://portal.core.edu.au/conf-ranks/) ranks for journals and conferences |



## Installation

```sh
pip install -r requirements.txt
```

`cross_references_finder.py` first resolves references containing a DOI through DOI
content negotiation (using the BibTeX resolver in `batch_resolve.py`), then falls
back to [pybliometrics](https://pybliometrics.readthedocs.io/), which needs a
[Scopus API key](https://www.scopus.com/pages/home)
(you are prompted for it on first use and it is stored in
`~/.config/pybliometrics.cfg`). Use `--no-enrich` to work without one.

## Usage

### 1. Validate your search: `check_wanted_papers.py`

Given the Scopus export and a list of papers you know should be found, print the ones
that are missing (a hint that the search string needs improvement).

```sh
python check_wanted_papers.py scopus_export.csv expected_papers.csv > missing.csv
```

Both CSVs need a `Title` column; a column containing `DOI` is also used. Matching is by
DOI, then exact normalized title, then fuzzy title (Levenshtein similarity ≥ 0.98).
The number of missing papers is printed on stderr; the missing rows (Year, Title,
Authors, DOI) on stdout.

### 2. Snowballing: `cross_references_finder.py`

Reads `screen.csv` (a Scopus export with `Title` and `References` columns) and lists
references cited by at least two of the screened papers, which are good candidates
for inclusion.

```sh
python cross_references_finder.py [-x] [-t 0.9] [--no-enrich]
```

Progress is shown with rich, the results are printed as a table and saved to
`snowball.csv`. Lookups are cached in `snowball_cache.json`, so reruns only query new references.

| Option | Description |
|--------|-------------|
| `-x`, `--exclude-present` | Skip references that are already papers in the CSV |
| `-t`, `--threshold` | Minimum title similarity to treat references as the same paper (default 0.9) |
| `--no-enrich` | Skip DOI and Scopus enrichment |

Output is tab-separated: `Citing papers`, `Reference`, `DOI`, `Publication venue`,
`Abstract`. References with a DOI use DOI metadata first; references without usable
DOI metadata are looked up in Scopus by title. The last three fields are left empty
when no match is found.

### 3. Resolve publication URLs: `batch_resolve.py`

Put one URL or DOI per line in a `.txt` file (optionally followed by a title; blank lines
and `#` comments are ignored), then run:

```sh
python batch_resolve.py urls.txt                  # BibTeX on stdout and in out.bib
python batch_resolve.py urls.txt -o references.bib
python batch_resolve.py https://doi.org/10.1000/xyz   # a single URL/DOI also works
```

Entries are printed to stdout (progress and errors go to stderr) and written to the
file given by `-o`/`--output` (default `out.bib`). Citation keys are generated as
`<first author><year><first title word>`.

### 4. Check venue quality: `batch_venue_verifier.py`

Put one journal or conference name per line in `venues.txt`, then run:

```sh
python batch_venue_verifier.py
```

Journals are searched on SCImago first, other venues on CORE first, with the other
source as fallback. Results are written to `rankings_results.md` and cached for 30 days
in `rankings_cache.json`.

## Suggested workflow

1. Export search results from Scopus; run `check_wanted_papers.py` to validate coverage.
2. Screen titles/abstracts and save the included papers as `screen.csv`.
3. Run `cross_references_finder.py` to find additional candidates via snowballing.
4. Collect the venues of included papers in `venues.txt` and run `batch_venue_verifier.py`
   to assess their quality.

## Tests

```sh
python -m unittest discover -s tests -t .
```

Network and Scopus calls are mocked; no API key or internet access is needed.

## License

See [LICENSE](LICENSE).
