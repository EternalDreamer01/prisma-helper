import contextlib
import csv
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import check_wanted_papers as cwp


class NormalizationTests(unittest.TestCase):
	def test_normalize_text_strips_accents_case_and_punctuation(self):
		self.assertEqual(cwp.normalize_text("  Étude: Deep-Learning!  "), "etude deep learning")

	def test_normalize_text_collapses_whitespace(self):
		self.assertEqual(cwp.normalize_text("a \t\n b"), "a b")

	def test_normalize_doi_variants(self):
		for raw in ("10.1000/ABC.", "https://doi.org/10.1000/abc", "http://dx.doi.org/10.1000/abc",
					"doi: 10.1000/abc", "  10.1000/abc  "):
			self.assertEqual(cwp.normalize_doi(raw), "10.1000/abc", raw)

	def test_normalize_doi_empty(self):
		self.assertEqual(cwp.normalize_doi(None), "")
		self.assertEqual(cwp.normalize_doi(""), "")


class SimilarityTests(unittest.TestCase):
	def test_levenshtein(self):
		self.assertEqual(cwp.levenshtein("kitten", "sitting"), 3)
		self.assertEqual(cwp.levenshtein("abc", "abc"), 0)
		self.assertEqual(cwp.levenshtein("", "abc"), 3)

	def test_similarity_bounds(self):
		self.assertEqual(cwp.similarity("", ""), 1.0)
		self.assertEqual(cwp.similarity("abc", "abc"), 1.0)
		self.assertEqual(cwp.similarity("abc", "xyz"), 0.0)
		self.assertAlmostEqual(cwp.similarity("abcd", "abce"), 0.75)


class HelperTests(unittest.TestCase):
	def test_find_column_ignores_case_and_spaces(self):
		self.assertEqual(cwp.find_column(["Year", " Title "], "title"), " Title ")
		self.assertIsNone(cwp.find_column(["Year"], "Title"))


class MainTests(unittest.TestCase):
	def setUp(self):
		self.tmp = tempfile.TemporaryDirectory()
		self.addCleanup(self.tmp.cleanup)

	def write(self, name, rows):
		path = os.path.join(self.tmp.name, name)
		with open(path, "w", encoding="utf-8", newline="") as f:
			w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
			w.writeheader()
			w.writerows(rows)
		return path

	def run_main(self, existing, wanted):
		out, err = io.StringIO(), io.StringIO()
		with mock.patch.object(sys, "argv", ["prog", existing, wanted]), \
				contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
			cwp.main()
		return out.getvalue(), err.getvalue()

	def test_reports_only_missing_papers(self):
		existing = self.write("existing.csv", [
			{"Title": "Deep learning for X", "DOI": "10.1/a", "Year": "2020"},
			{"Title": "Another study on Y", "DOI": "", "Year": "2021"},
			{"Title": "Fuzzy matched title here", "DOI": "", "Year": "2022"},
		])
		wanted = self.write("wanted.csv", [
			{"Title": "Totally different name", "DOI": "https://doi.org/10.1/A", "Year": "1999", "Authors": "A"},
			{"Title": "ANOTHER study on Y!", "DOI": "", "Year": "2021", "Authors": "B"},
			{"Title": "Fuzzy matched title here.", "DOI": "", "Year": "2022", "Authors": "C"},
			{"Title": "Missing paper", "DOI": "10.9/z", "Year": "2023", "Authors": "D"},
		])
		out, err = self.run_main(existing, wanted)
		self.assertIn("Missing papers: 1", err)
		rows = list(csv.DictReader(io.StringIO(out)))
		self.assertEqual([r["Title"] for r in rows], ["Missing paper"])
		self.assertEqual(set(rows[0]), {"Title", "DOI", "Year", "Authors"})

	def test_all_found_prints_nothing_on_stdout(self):
		rows = [{"Title": "Same paper", "DOI": "10.1/a"}]
		existing = self.write("e.csv", rows)
		wanted = self.write("w.csv", rows)
		out, err = self.run_main(existing, wanted)
		self.assertEqual(out, "")
		self.assertIn("Missing papers: 0", err)

	def test_requires_title_column(self):
		existing = self.write("e.csv", [{"Name": "x"}])
		wanted = self.write("w.csv", [{"Title": "x"}])
		with self.assertRaises(SystemExit):
			self.run_main(existing, wanted)

	def test_usage_error_without_args(self):
		with mock.patch.object(sys, "argv", ["prog"]), self.assertRaises(SystemExit):
			cwp.main()


if __name__ == "__main__":
	unittest.main()
