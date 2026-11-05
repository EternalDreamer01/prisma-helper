import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import batch_resolve as br

BIB = """@article{old,
  author = {Smith, John and Doe, A.},
  title = {Deep learning for screening},
  year = {2020},
  abstract = {Long text}
}"""


class ResolveTests(unittest.TestCase):
	def setUp(self):
		self.tmp = tempfile.TemporaryDirectory()
		self.addCleanup(self.tmp.cleanup)

	def write(self, name, text):
		path = os.path.join(self.tmp.name, name)
		with open(path, "w", encoding="utf-8") as f:
			f.write(text)
		return path

	def test_read_list_skips_comments_blanks_and_duplicates(self):
		path = self.write("l.txt", "# c\\n\\nhttps://doi.org/10.1/x My title # note\\nhttps://doi.org/10.1/x\\n10.2/y\\n".replace("\\n", "\n"))
		self.assertEqual(br.read_list(path), [("https://doi.org/10.1/x", "My title"), ("10.2/y", "")])

	def test_resolve_entry_rekeys_and_strips_abstract(self):
		with mock.patch.object(br, "resolve_url", return_value=BIB):
			out = br.resolve_entry("https://doi.org/10.1/x")
		self.assertTrue(out.startswith("@article{smith2020deep,"))
		self.assertNotIn("abstract", out)

	def test_resolve_entry_rejects_html(self):
		with mock.patch.object(br, "resolve_url", return_value="<html>oops</html>"):
			self.assertEqual(br.resolve_entry("https://doi.org/10.1/x"), "")

	def test_resolve_list_writes_stdout_and_bib(self):
		out_path = os.path.join(self.tmp.name, "o.bib")
		results = {"a": BIB, "b": "", "c": RuntimeError("boom")}

		def fake(url):
			r = results[url]
			if isinstance(r, Exception):
				raise r
			return r

		stdout, stderr = io.StringIO(), io.StringIO()
		with mock.patch.object(br, "resolve_url", side_effect=fake), \
				contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
			count = br.resolve_list([("a", ""), ("b", ""), ("c", "")], out_path)
		self.assertEqual(count, 1)
		with open(out_path, encoding="utf-8") as f:
			written = f.read()
		self.assertIn("@article{smith2020deep,", written)
		self.assertIn("@article{smith2020deep,", stdout.getvalue())
		self.assertIn("boom", stderr.getvalue())


class ParsingTests(unittest.TestCase):
	ONE_LINE = ("@inproceedings{anon0000, title={On Adversarial {X}}, DOI={10.1/x}, "
				"author={Zhang, Qingzhao and Hu, S}, year={2022}, month=June, pages={1\u20132} }")

	def test_fields_single_line_bibtex(self):
		f = br.bibtex_fields(self.ONE_LINE)
		self.assertEqual(f["author"], "Zhang, Qingzhao and Hu, S")
		self.assertEqual(f["title"], "On Adversarial {X}")
		self.assertEqual(f["month"], "June")
		self.assertEqual(f["pages"], "1\u20132")

	def test_fields_multiline_quoted_and_bare(self):
		f = br.bibtex_fields('@article{k,\n author = {Smith, J.},\n title = "T x",\n year = 2020\n}')
		self.assertEqual(f, {"author": "Smith, J.", "title": "T x", "year": "2020"})

	def test_key_from_single_line_skips_articles(self):
		self.assertEqual(br.make_key(self.ONE_LINE), "zhang2022adversarial")

	def test_openreview_without_metadata_is_unresolved(self):
		resp = mock.Mock(ok=True, text="<html><title>Verifying your browser</title></html>")
		with mock.patch.object(br.requests, "get", return_value=resp):
			self.assertEqual(br.openreview("https://openreview.net/forum?id=x"), "")


if __name__ == "__main__":
	unittest.main()
