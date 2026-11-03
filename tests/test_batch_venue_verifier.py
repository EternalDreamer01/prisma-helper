import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from bs4 import BeautifulSoup
from requests.exceptions import RequestException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import batch_venue_verifier as bvv


def soup(html):
	return BeautifulSoup(html, "html.parser")


class NameHelperTests(unittest.TestCase):
	def test_missing(self):
		for value in (None, "", "  ", "Not Found", "N/A", "none", "-"):
			self.assertTrue(bvv.missing(value), value)
		for value in ("Q1", "A*", 0):
			self.assertFalse(bvv.missing(value), value)

	def test_normalise(self):
		self.assertEqual(bvv.normalise("  IEEE Trans. on X-Y! "), "ieee trans on x y")

	def test_is_journal_name(self):
		self.assertTrue(bvv.is_journal_name("Journal of Software"))
		self.assertFalse(bvv.is_journal_name("Proceedings of the Journal Club"))
		self.assertFalse(bvv.is_journal_name("Journal Conference on X"))
		self.assertFalse(bvv.is_journal_name("ICSE"))

	def test_core_query_variants(self):
		self.assertEqual(bvv.core_query_variants("ICSE"), ["ICSE"])
		variants = bvv.core_query_variants("Proceedings of the 10th International Conference on Testing")
		self.assertEqual(variants[0], "Proceedings of the 10th International Conference on Testing")
		self.assertEqual(variants[1], "Conference on Testing")
		self.assertEqual(
			bvv.core_query_variants("Proceedings IEEE Computer Society Symposium")[1],
			"IEEE Symposium",
		)


class CacheTests(unittest.TestCase):
	def setUp(self):
		self.tmp = tempfile.TemporaryDirectory()
		self.addCleanup(self.tmp.cleanup)
		patchers = [
			mock.patch.object(bvv, "CACHE_FILE", os.path.join(self.tmp.name, "cache.json")),
			mock.patch.object(bvv, "FORCE_REFRESH", False),
			mock.patch.object(bvv, "console"),
		]
		for p in patchers:
			p.start()
			self.addCleanup(p.stop)

	def test_update_and_get_round_trip(self):
		cache = {}
		result = {"Status": "OK", "Name": "X"}
		bvv.update_cache("My Journal", result, cache)
		self.assertEqual(bvv.get_cached_result("my  journal", cache), result)
		self.assertEqual(bvv.load_cache(), cache)

	def test_failed_results_are_not_cached(self):
		cache = {}
		bvv.update_cache("X", {"Status": "Not Found"}, cache)
		self.assertEqual(cache, {})
		self.assertFalse(os.path.exists(bvv.CACHE_FILE))

	def test_expired_entry_ignored(self):
		old = (datetime.now(timezone.utc) - timedelta(days=bvv.CACHE_DURATION_DAYS + 1)).isoformat()
		cache = {"x": {"timestamp": old, "result": {"Status": "OK"}}}
		self.assertIsNone(bvv.get_cached_result("X", cache))

	def test_naive_timestamp_treated_as_utc(self):
		recent = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
		cache = {"x": {"timestamp": recent, "result": {"Status": "OK"}}}
		self.assertEqual(bvv.get_cached_result("X", cache), {"Status": "OK"})

	def test_malformed_entry_ignored(self):
		self.assertIsNone(bvv.get_cached_result("X", {"x": {"result": {}}}))

	def test_force_refresh_bypasses_cache(self):
		cache = {"x": {"timestamp": datetime.now(timezone.utc).isoformat(), "result": {}}}
		with mock.patch.object(bvv, "FORCE_REFRESH", True):
			self.assertIsNone(bvv.get_cached_result("X", cache))

	def test_load_cache_handles_missing_and_corrupt_file(self):
		self.assertEqual(bvv.load_cache(), {})
		with open(bvv.CACHE_FILE, "w") as f:
			f.write("{not json")
		self.assertEqual(bvv.load_cache(), {})


class FetchUrlTests(unittest.TestCase):
	def test_success(self):
		session = mock.Mock()
		response = session.get.return_value
		with mock.patch.object(bvv, "console"):
			self.assertIs(bvv.fetch_url(session, "http://x", params={"a": 1}), response)
		response.raise_for_status.assert_called_once()

	def test_request_error_returns_none(self):
		session = mock.Mock()
		session.get.side_effect = RequestException("down")
		with mock.patch.object(bvv, "console"):
			self.assertIsNone(bvv.fetch_url(session, "http://x"))


class ScimagoParsingTests(unittest.TestCase):
	def test_metrics(self):
		s = soup("""
			<div class="hsjr">1.234</div>
			<div class="content-hindex"><div class="hindexnumber"><span class="Q1">Q1</span></div></div>
			<div class="content-cuadrado">
				<div class="cuadrado"><h2>Other</h2><div class="hindexnumber">9</div></div>
				<div class="cuadrado"><h2>H-Index</h2><div class="hindexnumber">150</div></div>
			</div>""")
		self.assertEqual(bvv.extract_scimago_metrics(s),
						{"SJR": "1.234", "Quartile / Rank": "Q1", "H-index": "150"})

	def test_metrics_defaults(self):
		self.assertEqual(bvv.extract_scimago_metrics(soup("<p></p>")),
						{"SJR": "N/A", "Quartile / Rank": "N/A", "H-index": "N/A"})

	def test_quartiles_deduplicated(self):
		s = soup("""<div class="quartiles">
			<div class="quartile"><span class="year">2020</span><span class="quartile">Q1</span></div>
			<div class="quartile"><span class="year">2020</span><span class="quartile">Q1</span></div>
			<div class="quartile"><span class="year">2021</span><span class="quartile">Q2</span></div>
		</div>""")
		self.assertEqual(bvv.extract_scimago_quartiles(s), "Q1 (2020); Q2 (2021)")
		self.assertEqual(bvv.extract_scimago_quartiles(soup("")), "Not Found")

	def test_categories(self):
		s = soup("""<div class="table_area"><table>
			<tr><th>h</th></tr>
			<tr><td>Computer Science</td><td>Software</td></tr>
			<tr><td>Computer Science</td><td>AI</td></tr>
		</table></div>""")
		self.assertEqual(bvv.extract_scimago_categories(s),
						("Computer Science", "Software; AI"))
		self.assertEqual(bvv.extract_scimago_categories(soup("")), ("Not Found", "Not Found"))


class CoreParsingTests(unittest.TestCase):
	def test_extract_core_results(self):
		s = soup("""<table>
			<tr onclick="navigate('/conf-ranks/123/')"><td>Intl Conf on Software Eng</td><td>ICSE</td><td>CORE2023</td><td>A*</td></tr>
			<tr><td>No rank</td><td>NR</td><td>CORE2023</td><td></td></tr>
			<tr><td>Too</td><td>few</td></tr>
		</table>""")
		self.assertEqual(bvv.extract_core_results(s), [{
			"Name": "Intl Conf on Software Eng",
			"Acronym": "ICSE",
			"Source": "CORE2023",
			"Rank": "A*",
			"Profile URL": "https://portal.core.edu.au/conf-ranks/123/",
		}])

	def test_profile_url_missing(self):
		s = soup("<table><tr><td>N</td><td></td><td>S</td><td>B</td></tr></table>")
		result = bvv.extract_core_results(s)
		self.assertEqual(result[0]["Profile URL"], "N/A")
		self.assertEqual(result[0]["Acronym"], "N/A")


class ProcessVenueTests(unittest.TestCase):
	SJR = {"Found Title": "Journal of X", "Quartile / Rank": "Q1", "SJR": "1.0",
		"H-index": "10", "SCImago Link": "http://s"}
	CORE = {"CORE Found Title": "Conf X", "Conference Acronym": "CX", "CORE Source": "CORE2023",
			"Quartile / Rank": "A", "CORE Link": "http://c", "CORE Status": "Found"}

	def run_process(self, name, sjr, core):
		with mock.patch.object(bvv, "search_scimago", return_value=sjr) as s, \
				mock.patch.object(bvv, "search_core", return_value=core) as c:
			result = bvv.process_venue(name, mock.Mock())
		return result, s, c

	def test_journal_uses_scimago_first(self):
		result, _, core = self.run_process("Journal of X", dict(self.SJR), None)
		self.assertEqual(result["Status"], "OK")
		self.assertEqual(result["Quartile / Rank"], "Q1")
		core.assert_not_called()

	def test_conference_uses_core_first(self):
		result, scimago, _ = self.run_process("Intl Conf X", None, dict(self.CORE))
		self.assertEqual(result["Status"], "OK")
		self.assertEqual(result["Quartile / Rank"], "A")
		self.assertEqual(result["Ranking Source"], "CORE")
		self.assertEqual(result["Acronym"], "CX")
		scimago.assert_not_called()

	def test_conference_falls_back_to_scimago(self):
		core = dict(self.CORE, **{"Quartile / Rank": "N/A"})
		result, _, _ = self.run_process("Intl Conf X", dict(self.SJR), core)
		self.assertEqual(result["Ranking Source"], "SCImago (fallback)")
		self.assertEqual(result["Quartile / Rank"], "Q1")

	def test_journal_falls_back_to_core(self):
		sjr = dict(self.SJR, **{"Quartile / Rank": "Not Found"})
		result, _, _ = self.run_process("Journal of X", sjr, dict(self.CORE))
		self.assertEqual(result["Ranking Source"], "CORE (fallback)")

	def test_nothing_found(self):
		empty_sjr = {"Found Title": "Not Found", "Quartile / Rank": "Not Found", "SJR": "N/A",
					"H-index": "N/A", "SCImago Link": "N/A"}
		result, _, _ = self.run_process("Unknown Venue", empty_sjr, None)
		self.assertNotEqual(result["Status"], "OK")


class MainTests(unittest.TestCase):
	def test_missing_input_file(self):
		with tempfile.TemporaryDirectory() as d:
			cwd = os.getcwd()
			os.chdir(d)
			try:
				with mock.patch.object(bvv, "console") as console:
					bvv.main()
				self.assertTrue(any("not found" in str(c).lower() for c in console.print.call_args_list))
				self.assertFalse(os.path.exists("rankings_results.md"))
			finally:
				os.chdir(cwd)

	def test_deduplicates_and_writes_markdown(self):
		result = {"Input Name": "Journal of X", "Name": "Journal of X", "Acronym": "N/A",
				"Source": "SCImago", "SJR": "1", "Quartile / Rank": "Q1", "H-index": "5", "Status": "OK"}
		with tempfile.TemporaryDirectory() as d:
			cwd = os.getcwd()
			os.chdir(d)
			try:
				with open("venues.txt", "w") as f:
					f.write("Journal of X\njournal of x\n\n")
				with mock.patch.object(bvv, "console"), \
						mock.patch.object(bvv, "CACHE_FILE", os.path.join(d, "c.json")), \
						mock.patch.object(bvv, "process_venue", return_value=result) as proc, \
						mock.patch.object(bvv.time, "sleep"):
					bvv.main()
				proc.assert_called_once()
				with open("rankings_results.md", encoding="utf-8") as f:
					content = f.read()
				self.assertIn("Journal of X", content)
				self.assertIn("Q1", content)
			finally:
				os.chdir(cwd)


if __name__ == "__main__":
	unittest.main()
