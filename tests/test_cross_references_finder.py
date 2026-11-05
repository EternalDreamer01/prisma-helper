import contextlib
import io
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cross_references_finder as crf

REF_A = "Smith J.; Doe A., Deep learning for screening, Journal of Tests, 1, pp. 1-10, (2020)"
REF_A_TYPO = "Smith J.; Doe A., Deep learning for screenings, Journal of Tests, 1, pp. 1-10, (2020)"
REF_B = "Brown B., A completely different paper title, Other Journal, 2, (2019)"


def write_csv(directory, rows):
	import csv
	path = os.path.join(directory, "screen.csv")
	with open(path, "w", encoding="utf-8-sig", newline="") as f:
		w = csv.DictWriter(f, fieldnames=["Title", "References"])
		w.writeheader()
		w.writerows(rows)
	return path


class ParsingTests(unittest.TestCase):
	def test_split_references(self):
		text = f"{REF_A}; {REF_B}"
		self.assertEqual(crf.split_references(text), [REF_A, REF_B])

	def test_split_keeps_semicolon_separated_authors(self):
		ref = "Smith J.; Doe A., Some long title here, Venue, (2020)"
		self.assertEqual(crf.split_references(ref), [ref])

	def test_clean(self):
		self.assertEqual(crf.clean("  Hello, World-2!  "), "hello world 2")

	def test_parse_extracts_surname_and_title(self):
		self.assertEqual(crf.parse(REF_A), ("smith", "deep learning for screening"))

	def test_parse_short_title_is_none(self):
		self.assertIsNone(crf.parse("Smith J., Short, Venue, (2020)"))

	def test_same_paper(self):
		a, b = crf.parse(REF_A), crf.parse(REF_A_TYPO)
		self.assertTrue(crf.same_paper(a, b, 0.9))
		self.assertFalse(crf.same_paper(a, ("jones", a[1]), 0.9))
		self.assertFalse(crf.same_paper(a, crf.parse(REF_B), 0.9))

	def test_normalize_doi(self):
		self.assertEqual(crf.normalize_doi("https://doi.org/10.1/x"), "10.1/x")
		self.assertIsNone(crf.normalize_doi(None))
		self.assertIsNone(crf.normalize_doi("  "))


class FindCrossReferencesTests(unittest.TestCase):
	def setUp(self):
		self.tmp = tempfile.TemporaryDirectory()
		self.addCleanup(self.tmp.cleanup)

	def test_groups_fuzzy_duplicates_and_counts_citing_papers(self):
		path = write_csv(self.tmp.name, [
			{"Title": "Paper 1", "References": f"{REF_A}; {REF_B}"},
			{"Title": "Paper 2", "References": REF_A_TYPO},
			{"Title": "Paper 3", "References": REF_B},
		])
		result = crf.find_cross_references(path, min_papers=2)
		self.assertEqual(len(result), 2)
		by_papers = {ref: papers for ref, papers in result}
		self.assertEqual(by_papers[REF_A], ["Paper 1", "Paper 2"])
		self.assertEqual(by_papers[REF_B], ["Paper 1", "Paper 3"])

	def test_min_papers_filters(self):
		path = write_csv(self.tmp.name, [
			{"Title": "Paper 1", "References": REF_A},
			{"Title": "Paper 2", "References": REF_A},
		])
		self.assertEqual(len(crf.find_cross_references(path, min_papers=3)), 0)

	def test_same_paper_cited_twice_by_one_paper_counts_once(self):
		path = write_csv(self.tmp.name, [
			{"Title": "Paper 1", "References": f"{REF_A}; {REF_A_TYPO}"},
		])
		self.assertEqual(crf.find_cross_references(path, min_papers=2), [])

	def test_exclude_present(self):
		path = write_csv(self.tmp.name, [
			{"Title": "Deep learning for screening", "References": REF_B},
			{"Title": "Paper 2", "References": f"{REF_A}; {REF_B}"},
			{"Title": "Paper 3", "References": REF_A},
		])
		kept = [r for r, _ in crf.find_cross_references(path, exclude_present=True)]
		self.assertNotIn(REF_A, kept)
		self.assertIn(REF_B, [r for r, _ in crf.find_cross_references(path)])

	def test_sorted_by_citation_count_desc(self):
		path = write_csv(self.tmp.name, [
			{"Title": "P1", "References": f"{REF_A}; {REF_B}"},
			{"Title": "P2", "References": f"{REF_A}; {REF_B}"},
			{"Title": "P3", "References": REF_B},
		])
		result = crf.find_cross_references(path)
		self.assertEqual([r for r, _ in result], [REF_B, REF_A])


class EnrichTests(unittest.TestCase):
	def fake_scopus(self, hits, abstract="An abstract."):
		scopus = types.ModuleType("pybliometrics.scopus")
		scopus.ScopusSearch = mock.Mock(return_value=mock.Mock(results=hits))
		scopus.AbstractRetrieval = mock.Mock(return_value=mock.Mock(abstract=abstract, description=None))
		pkg = types.ModuleType("pybliometrics")
		pkg.init = mock.Mock()
		pkg.scopus = scopus
		return mock.patch.dict(sys.modules, {"pybliometrics": pkg, "pybliometrics.scopus": scopus}), scopus

	def hit(self, title, doi="https://doi.org/10.1/x", venue="Journal of Tests"):
		return types.SimpleNamespace(title=title, doi=doi, publicationName=venue)

	def test_returns_doi_venue_abstract(self):
		patcher, scopus = self.fake_scopus([self.hit("Deep learning for screening")])
		with patcher:
			self.assertEqual(crf.enrich(REF_A), ["10.1/x", "Journal of Tests", "An abstract."])
			scopus.AbstractRetrieval.assert_called_once_with("10.1/x", id_type="doi", view="META_ABS")

	def test_no_matching_title_returns_empty(self):
		patcher, _ = self.fake_scopus([self.hit("Something unrelated entirely")])
		with patcher:
			self.assertEqual(crf.enrich(REF_A), ["", "", ""])

	def test_unparseable_reference_returns_empty(self):
		patcher, scopus = self.fake_scopus([])
		with patcher:
			self.assertEqual(crf.enrich("Smith J., Short, (2020)"), ["", "", ""])
			scopus.ScopusSearch.assert_not_called()

	def test_errors_are_swallowed(self):
		patcher, scopus = self.fake_scopus([])
		scopus.ScopusSearch.side_effect = RuntimeError("boom")
		with patcher, contextlib.redirect_stderr(io.StringIO()) as err:
			self.assertEqual(crf.enrich(REF_A), ["", "", ""])
		self.assertIn("boom", err.getvalue())

	def test_doi_metadata_is_preferred_to_scopus(self):
		ref = REF_A + ", https://doi.org/10.1234/example"
		bibtex = """@article{key,
  author = {Smith, J.},
  journal = {DOI Journal},
  abstract = {Metadata abstract}
}"""
		with mock.patch.object(crf.batch_resolve, "doi", return_value=bibtex), \
				mock.patch.object(crf.Enricher, "_lookup_scopus") as scopus:
			self.assertEqual(crf.enrich(ref), ["10.1234/example", "DOI Journal", "Metadata abstract"])
			scopus.assert_not_called()

	def test_scopus_is_used_when_doi_lookup_fails(self):
		ref = REF_A + ", https://doi.org/10.1234/example"
		with mock.patch.object(crf.batch_resolve, "doi", return_value=""), \
				mock.patch.object(crf.Enricher, "_lookup_scopus", return_value=["10.1/x", "Scopus Journal", "Abstract"]) as scopus:
			self.assertEqual(crf.enrich(ref), ["10.1/x", "Scopus Journal", "Abstract"])
			scopus.assert_called_once_with(ref)


if __name__ == "__main__":
	unittest.main()
