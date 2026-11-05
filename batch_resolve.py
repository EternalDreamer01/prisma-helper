#!/usr/bin/env python3
################################################################################
# @file      batch_resolve.py
# @brief     
# @date      Th Nov 2026
# @author    Dimitri Simon
# 
# PROJECT:   prisma-helper
# 
# MODIFIED:  Thu Nov 05 2026
# BY:        Dimitri Simon
# 
# Copyright (c) 2026 Dimitri Simon
# 
################################################################################

import sys
import re
import os
from typing import Callable
from urllib.parse import urlsplit, unquote
import requests

# Helpers ported from bash functions

USER_AGENT = "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:138.0) Gecko/20100101 Firefox/138.0"
HEADERS = {
	"User-Agent": USER_AGENT,
	"Accept": "application/x-bibtex,text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
}
    
def good_name(s: str) -> bool:
    if s is None:
        return False
    s_l = s.lower()
    # matches numbers of length >=3, source, src, http in string, or string made only of punctuation/spaces
    if re.search(r"[0-9]{3,}", s_l): return False
    if "source" in s_l or "src" in s_l: return False
    if re.search(r"http.+", s_l): return False
    if re.fullmatch(r"[-&/!?\$\*\~\s]+", s_l): return False
    return True

def title_format(s: str) -> str:
    # cut -d: -f1 | cut '-d(' -f1 | sed -E 's/( |_)-( |_).+//' | xargs
    if s is None:
        return ""
    s1 = s.split(":", 1)[0]
    s1 = s1.split("(", 1)[0]
    s1 = re.sub(r"( |_)-( |_).+", "", s1)
    return s1.strip()

def title_escape(s: str) -> str:
    # if empty, read from stdin in bash; here just handle given string
    if s is None:
        s = ""
    esc = re.sub(r'([.\[\*\^\$])', r'\\\1', s)
    esc = esc.replace(" ", "_").replace(",", "_")
    return esc

def doi(doi_str: str) -> str:
    # curl -L "https://dx.doi.org/$1" -H 'Accept: application/x-bibtex'
    doi_str = re.sub(r"^https?://(dx\.)?doi\.org/", "", str(doi_str or "").strip(), flags=re.IGNORECASE)
    url = f"https://dx.doi.org/{doi_str}"
    headers = {"Accept": "application/x-bibtex", "User-Agent": "bibfetch/1.0"}
    r = requests.get(url, headers=headers, allow_redirects=True, timeout=30)
    r.encoding = "utf-8"
    return r.text if r.ok else ""

def arxiv(url: str) -> str:
    # original used arxiv-doi-link from arXiv HTML; emulate by fetching page and extracting citation when possible
    r = requests.get(url.replace("/pdf/", "/abs/"), timeout=30)
    if not r.ok:
        return ""
    # print(r.text)
    
    m = re.search(r'arxiv-doi-link">https://doi.org/([^<]+)', r.text)
    # print(m.group(1))
    # print(m.group(1) if m else "No DOI link found in arXiv page")
    if m:
        return doi_org(m.group(1))
    return ""

def ieee(url: str) -> str:
	m = re.search(r'arnumber=(\d+)', url) or re.search(r'/document/(\d+)', url)
	if not m:
		return ""
	article_id = m.group(1)

	# replicate POST to IEEE endpoint to get bibtex text chunk (original returned JSON.data then cut -d'"' -f2)
	url = "https://ieeexplore.ieee.org/rest/search/citation/format"
	headers = {
		"User-Agent": USER_AGENT,
		"Accept": "application/json",
		"Content-Type": "application/json",
		"Origin": "https://ieeexplore.ieee.org",
	}
	payload = {"recordIds":[str(article_id)], "download-format":"download-bibtex", "lite": True}
	r = requests.post(url, json=payload, headers=headers, timeout=30)
	# print(r)
	if not r.ok:
		return ""
	try:
		data = r.json()
		# original used jq .data then cut -d'"' -f2; try robust extraction
		if "data" in data:
			return data["data"]
	except Exception:
		return ""
	return ""

def cvf(url: str) -> str:
    # transform pdf link to html and grab bibref area
    url_html = re.sub(r'(/)papers(/)|(\.)pdf', r'\1\3html\2', url)
    r = requests.get(url_html, headers=HEADERS, timeout=30)
    # print(r, "'"+url_html+"'")
    if not r.ok:
        return ""
    # print(r.text)
    # crude extraction of lines near "bibref"
    m = re.search(r'<div class="bibref[a-zA-Z0-9 -]*">\s*(@.+?)\s*</div>', r.text, re.MULTILINE|re.DOTALL)
    # text = "\n".join(parts)
    # m = re.search(r'@.*\}', text, re.S)
    # print(m.group(1))
    return m.group(1).replace("<br>", "") if m else ""

def mdpi(url: str) -> str:
    r = requests.get(url, timeout=30)
    if not r.ok:
        return ""
    # find articles_ids in page
    m = re.search(r'articles_ids.*?["\']([^"\']+)["\']', r.text)
    if not m:
        return ""
    article_id = m.group(1)
    post_url = "https://www.mdpi.com/export"
    payload = { "articles_ids[]": article_id, "export_format_top":"bibtex", "export_submit_top":""}
    r2 = requests.post(post_url, data=payload, timeout=30)
    return r2.text if r2.ok else ""

def sciencedirect(path: str) -> str:
    # original extracts doi with complex regex; attempt to find a DOI in the URL path tail and call doi()
    # fallback: try to fetch page and look for citation download link
    r = requests.get(path, headers=HEADERS, timeout=30)
    if r.ok:
        m = re.search(r'data-doi=["\']([^"\']+)["\']', r.text)
        if m:
            return doi(m.group(1))
        m = re.search(r'doi.org/([^"\']+)["\']', r.text)
        if m:
            return doi(m.group(1))
        
    # fallback heuristic: try extract 10.1016/... from path
    m2 = re.search(r'\/(S)?([0-9]{4})([0-9]{4})([0-9]{2})([0-9]{5})([0-9])$', path)
    if m2:
        doi_str = f"10.1016/{m2.group(0).lstrip('/')}"
        return doi(doi_str)
    return ""

def neurips(url: str) -> str:
    r = requests.get(re.sub(r'-Paper(?:-Conference)?.pdf$', '-Bibtex.bib', url, flags=re.IGNORECASE), timeout=30)
    return r.text if r.ok else ""

def mlr(url: str) -> str:
    r = requests.get(re.sub(r'(/v[0-9]+)\/([a-z0-9]+)(\/\2)?\.pdf$', r'\1/\2.html', url), timeout=30)
    if not r.ok:
        return ""
    # print(r.text)
    m = re.search(r'<code class="citecode" id="bibtex">\s*(@.+?)\s*</code>', r.text, re.MULTILINE|re.DOTALL)
    return m.group(1) if m else ""

def openreview(url: str) -> str:
    r = requests.get(url.replace("/pdf?id=", "/forum?id="), headers=HEADERS, timeout=30)
    if not r.ok:
        return ""
    # extract meta tags citation_*
    metas = re.findall(r'<meta name="([^"]+)" content="([^"]*)"', r.text)
    meta_map = {k:v for k,v in metas if k.startswith("citation_")}
    author = meta_map.get("citation_author", "")
    # authors may be multiple; in original they joined with ", " and replace final newline with " and "
    if isinstance(author, list):
        author = ", ".join(author)
    date = meta_map.get("citation_online_date", "")
    year = date.split("/",1)[0] if date else ""
    month = date.split("/",2)[1] if date and "/" in date else ""
    title = meta_map.get("citation_title", "")
    if not title:
        # OpenReview serves a browser challenge to scripts: no metadata available
        return ""
    titleid = title_format(title)
    titleid = title_escape(titleid) or unquote(url.split("id=")[-1])
    # abstract = meta_map.get("citation_abstract", "")
    booktitle = meta_map.get("citation_conference_title", "")
    bookid = re.sub(r'.+/(pdf|forum)\?id=', '', url)
    bib = f"""@article{{{titleid if titleid else bookid},
url       = {{{url}}},
author    = {{{author}}},
title     = {{{title}}},
year      = {{{year}}},
month     = {{{month}}},
booktitle = {{{booktitle}}}
}}"""
    return bib

def aaai(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=30)
    # print(r)
    if not r.ok:
        return ""
    m = re.search(r'https://doi.org/([^"]+)"', r.text)
    # print(m.group(1))
    if m:
        return doi(m.group(1))
    # fallback: try to find DOI link
    m2 = re.search(r'https?://dx.doi.org/([^"]+)"', r.text)
    if m2:
        return doi(m2.group(1))
    return ""

def acm(url: str) -> str:
    # transform /pdf/ to /abs/ then call doi() with path after domain as in bash
    abs_url = url.replace("/pdf/", "/abs/")
    parts = abs_url.split("/")[5:]
    doi_path = "/".join(parts)
    # print(f"ACM extracted DOI path: {doi_path}")
    return doi(doi_path)

def usenix(url: str) -> str:
    m = re.search(r'bibtex-text-entry bibtex-accordion-text-entry">(.*?)</div>', requests.get(url, headers=HEADERS, timeout=30).text, re.MULTILINE|re.DOTALL)
    if m:
        return re.sub(r'<br\s*/?>', '', m.group(1))
    return ""

def doi_org(url: str) -> str:
    # take path after doi.org/ and call doi()
    parts = urlsplit(url).path.lstrip("/").split("/")
    doi_str = "/".join(parts[:2]) if len(parts) >= 2 else "/".join(parts)
    return doi(doi_str)

def eprint(*args, **kwargs) -> None:
	print("\x1b[31m[-] " + " ".join(str(arg) for arg in args) + "\x1b[0m", file=sys.stderr, **kwargs)

ORGANISMS: dict[str, Callable[[str], str]] = {
	"https://arxiv.org/": arxiv,
	"https://www.usenix.org/": usenix,
	"https://ieeexplore.ieee.org/": ieee,
	"https://openaccess.thecvf.com/": cvf,
	"https://www.mdpi.com/": mdpi,
	"https://www.sciencedirect.com/": sciencedirect,
	"https://proceedings.neurips.cc/": neurips,
	"https://proceedings.mlr.press/": mlr,
	"https://openreview.net/": openreview,
	"https://ojs.aaai.org/": aaai,
	"https://dl.acm.org/": acm,
	"https://doi.org/": doi_org,
}

def get_domain(u: str) -> str:
	return "/".join(u.split('/', 3)[:3])+"/"

def bibtex_fields(bibtex: str) -> dict[str, str]:
	"""Extract BibTeX fields (single- or multi-line; braced, quoted or bare values)."""
	fields = {}
	start = bibtex.find(",")  # skip "@type{key"
	pos = start + 1 if start >= 0 else len(bibtex)
	name_re = re.compile(r"\s*,?\s*([A-Za-z][\w-]*)\s*=\s*")
	while True:
		m = name_re.match(bibtex, pos)
		if not m:
			break
		name, pos = m.group(1).lower(), m.end()
		if pos >= len(bibtex):
			break
		if bibtex[pos] == "{":
			depth, end = 0, pos
			while end < len(bibtex):
				depth += {"{": 1, "}": -1}.get(bibtex[end], 0)
				end += 1
				if depth == 0:
					break
			value = bibtex[pos + 1:end - 1]
		elif bibtex[pos] == '"':
			end = bibtex.find('"', pos + 1)
			end = len(bibtex) if end < 0 else end
			value = bibtex[pos + 1:end]
			end += 1
		else:
			m2 = re.compile(r"[^,}]*").match(bibtex, pos)
			value, end = m2.group(0), m2.end()
		fields[name] = re.sub(r"\s+", " ", value).strip()
		pos = end
	return fields

def is_doi_url(url: str) -> bool:
	return re.match(r"\d{2}\.\d{4,9}/[-._;A-Z0-9]+", url, re.IGNORECASE) is not None

def resolve_url(url: str) -> str:
	"""Resolve a supported publication URL to a BibTeX entry."""
	org = get_domain(url)
	if org not in ORGANISMS:
		if is_doi_url(url):
			return doi(url)
		raise ValueError(f"Unknown organization {org}")
	return ORGANISMS[org](url)

def clean_entry(out: str) -> str:
	"""Return the BibTeX entry, or "" if the response is not BibTeX (HTML error page, null...)."""
	low = out.lower()
	if not out or "<html" in low or "<div " in low or "\nbr\n" in low or out.strip().lower() == "null":
		return ""
	# abstracts and keywords are long and not needed
	return re.sub(r'(abstract|keywords)\s*=\s*[\{\"](.+?)[\}\"]\s*,?', '', out, flags=re.I | re.S)

ARTICLES = {"a", "an", "the", "on", "of"}

def make_key(bibtex: str, title: str = "") -> str:
	"""Build a citation key: <first author><year><first title word>."""
	fields = bibtex_fields(bibtex)
	title = title or fields.get("title", "").strip('{"} ')
	words = [re.sub(r'[^a-z0-9]+', '', w.lower()) for w in re.sub(r'[{},"\'`]', '', title).split()]
	words = [w for w in words if w and w not in ARTICLES]
	first_word = words[0] if words else ""
	if first_word == "https" or first_word.isdigit():
		first_word = ""
	author = re.sub(r'[{}]', '', fields.get("author", "").split(",")[0]).strip().split()
	author_key = re.sub(r'[^a-z]+', '', author[0].lower()) if author else ""
	year_key = re.sub(r'[^0-9]+', '', fields.get("year", ""))
	return f"{author_key or 'anon'}{year_key or '0000'}{first_word}"

def resolve_entry(url: str, title: str = "") -> str:
	"""Resolve a URL/DOI to a BibTeX entry with a generated citation key ("" if unresolved)."""
	out = clean_entry(resolve_url(url))
	if not out:
		return ""
	key = make_key(out, title_format(title))
	return re.sub(r'@(\w+)\{[^,]+,', lambda m: f"@{m.group(1)}{{{key},", out, count=1)

def read_list(path: str) -> list[tuple[str, str]]:
	"""Read a text list: one "<url-or-doi> [title]" per line; blank lines and # comments are ignored."""
	entries, seen = [], set()
	with open(path, encoding="utf-8") as f:
		for line in f:
			line = line.split("#", 1)[0].strip()
			if not line:
				continue
			url, _, title = line.partition(" ")
			if url not in seen:
				seen.add(url)
				entries.append((url, title.strip()))
	return entries

def resolve_list(entries: list[tuple[str, str]], output_path: str) -> int:
	"""Resolve every entry, print each BibTeX entry to stdout and write them all to output_path."""
	from rich.console import Console
	from rich.progress import Progress

	console = Console(stderr=True)  # keep stdout clean for the BibTeX
	count = 0
	with open(output_path, "w", encoding="utf-8") as bib_file, \
			Progress(console=console, redirect_stdout=False) as progress:
		task = progress.add_task("Processing...", total=len(entries))
		for url, title in entries:
			progress.advance(task)
			try:
				bib = resolve_entry(url, title)
			except Exception as e:
				progress.console.print(f"[red][-] Error fetching {url}: {e}[/red]", markup=True, highlight=False)
				continue
			if not bib:
				progress.console.print(f"[red][-] {url} bibtex failed![/red]", highlight=False)
				continue
			bib = bib if bib.endswith("\n") else bib + "\n"
			progress.console.print(f"[green][+][/green] {url}", highlight=False)
			# sys.stdout.write(bib + "\n")
			# sys.stdout.flush()
			bib_file.write(bib + "\n")
			bib_file.flush()
			count += 1
	return count


if __name__ == "__main__":
	import argparse
	parser = argparse.ArgumentParser(description="Resolve publication URLs/DOIs to BibTeX.")
	parser.add_argument("input", help="a .txt list (one URL or DOI per line, optionally followed by a title) or a single URL/DOI")
	parser.add_argument("-o", "--output", default="out.bib", help="BibTeX output file (default: out.bib)")
	args = parser.parse_args()

	entries = read_list(args.input) if os.path.isfile(args.input) else [(args.input, "")]
	try:
		count = resolve_list(entries, args.output)
	except KeyboardInterrupt:
		print("\nInterrupted by user, exiting.", file=sys.stderr)
		sys.exit(130)
	print(f"{count}/{len(entries)} entries written to {args.output}", file=sys.stderr)
	sys.exit(0 if count else 1)
