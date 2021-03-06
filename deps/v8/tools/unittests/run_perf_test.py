#!/usr/bin/env python
# Copyright 2014 the V8 project authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from collections import namedtuple
import coverage
import json
from mock import DEFAULT
from mock import MagicMock
import os
from os import path, sys
import platform
import shutil
import subprocess
import tempfile
import unittest

# Requires python-coverage and python-mock. Native python coverage
# version >= 3.7.1 should be installed to get the best speed.

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN_PERF = os.path.join(BASE_DIR, 'run_perf.py')
TEST_DATA = os.path.join(BASE_DIR, 'unittests', 'testdata')

TEST_WORKSPACE = path.join(tempfile.gettempdir(), "test-v8-run-perf")

V8_JSON = {
  "path": ["."],
  "binary": "d7",
  "flags": ["--flag"],
  "main": "run.js",
  "run_count": 1,
  "results_regexp": "^%s: (.+)$",
  "tests": [
    {"name": "Richards"},
    {"name": "DeltaBlue"},
  ]
}

V8_NESTED_SUITES_JSON = {
  "path": ["."],
  "flags": ["--flag"],
  "run_count": 1,
  "units": "score",
  "tests": [
    {"name": "Richards",
     "path": ["richards"],
     "binary": "d7",
     "main": "run.js",
     "resources": ["file1.js", "file2.js"],
     "run_count": 2,
     "results_regexp": "^Richards: (.+)$"},
    {"name": "Sub",
     "path": ["sub"],
     "tests": [
       {"name": "Leaf",
        "path": ["leaf"],
        "run_count_x64": 3,
        "units": "ms",
        "main": "run.js",
        "results_regexp": "^Simple: (.+) ms.$"},
     ]
    },
    {"name": "DeltaBlue",
     "path": ["delta_blue"],
     "main": "run.js",
     "flags": ["--flag2"],
     "results_regexp": "^DeltaBlue: (.+)$"},
    {"name": "ShouldntRun",
     "path": ["."],
     "archs": ["arm"],
     "main": "run.js"},
  ]
}

V8_GENERIC_JSON = {
  "path": ["."],
  "binary": "cc",
  "flags": ["--flag"],
  "generic": True,
  "run_count": 1,
  "units": "ms",
}

Output = namedtuple("Output", "stdout, stderr, timed_out")

class PerfTest(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls.base = path.dirname(path.dirname(path.abspath(__file__)))
    sys.path.append(cls.base)
    cls._cov = coverage.coverage(
        include=([os.path.join(cls.base, "run_perf.py")]))
    cls._cov.start()
    import run_perf
    from testrunner.local import commands
    global commands
    global run_perf

  @classmethod
  def tearDownClass(cls):
    cls._cov.stop()
    print ""
    print cls._cov.report()

  def setUp(self):
    self.maxDiff = None
    if path.exists(TEST_WORKSPACE):
      shutil.rmtree(TEST_WORKSPACE)
    os.makedirs(TEST_WORKSPACE)

  def tearDown(self):
    if path.exists(TEST_WORKSPACE):
      shutil.rmtree(TEST_WORKSPACE)

  def _WriteTestInput(self, json_content):
    self._test_input = path.join(TEST_WORKSPACE, "test.json")
    with open(self._test_input, "w") as f:
      f.write(json.dumps(json_content))

  def _MockCommand(self, *args, **kwargs):
    # Fake output for each test run.
    test_outputs = [Output(stdout=arg,
                           stderr=None,
                           timed_out=kwargs.get("timed_out", False))
                    for arg in args[1]]
    def execute(*args, **kwargs):
      return test_outputs.pop()
    commands.Execute = MagicMock(side_effect=execute)

    # Check that d8 is called from the correct cwd for each test run.
    dirs = [path.join(TEST_WORKSPACE, arg) for arg in args[0]]
    def chdir(*args, **kwargs):
      self.assertEquals(dirs.pop(), args[0])
    os.chdir = MagicMock(side_effect=chdir)

    subprocess.check_call = MagicMock()
    platform.system = MagicMock(return_value='Linux')

  def _CallMain(self, *args):
    self._test_output = path.join(TEST_WORKSPACE, "results.json")
    all_args=[
      "--json-test-results",
      self._test_output,
      self._test_input,
    ]
    all_args += args
    return run_perf.Main(all_args)

  def _LoadResults(self, file_name=None):
    with open(file_name or self._test_output) as f:
      return json.load(f)

  def _VerifyResults(self, suite, units, traces, file_name=None):
    self.assertEquals([
      {"units": units,
       "graphs": [suite, trace["name"]],
       "results": trace["results"],
       "stddev": trace["stddev"]} for trace in traces],
      self._LoadResults(file_name)["traces"])

  def _VerifyErrors(self, errors):
    self.assertEquals(errors, self._LoadResults()["errors"])

  def _VerifyMock(self, binary, *args, **kwargs):
    arg = [path.join(path.dirname(self.base), binary)]
    arg += args
    commands.Execute.assert_called_with(
        arg, timeout=kwargs.get("timeout", 60))

  def _VerifyMockMultiple(self, *args, **kwargs):
    expected = []
    for arg in args:
      a = [path.join(path.dirname(self.base), arg[0])]
      a += arg[1:]
      expected.append(((a,), {"timeout": kwargs.get("timeout", 60)}))
    self.assertEquals(expected, commands.Execute.call_args_list)

  def testOneRun(self):
    self._WriteTestInput(V8_JSON)
    self._MockCommand(["."], ["x\nRichards: 1.234\nDeltaBlue: 10657567\ny\n"])
    self.assertEquals(0, self._CallMain())
    self._VerifyResults("test", "score", [
      {"name": "Richards", "results": ["1.234"], "stddev": ""},
      {"name": "DeltaBlue", "results": ["10657567.0"], "stddev": ""},
    ])
    self._VerifyErrors([])
    self._VerifyMock(path.join("out", "x64.release", "d7"), "--flag", "run.js")

  def testOneRunWithTestFlags(self):
    test_input = dict(V8_JSON)
    test_input["test_flags"] = ["2", "test_name"]
    self._WriteTestInput(test_input)
    self._MockCommand(["."], ["Richards: 1.234\nDeltaBlue: 10657567"])
    self.assertEquals(0, self._CallMain())
    self._VerifyResults("test", "score", [
      {"name": "Richards", "results": ["1.234"], "stddev": ""},
      {"name": "DeltaBlue", "results": ["10657567.0"], "stddev": ""},
    ])
    self._VerifyErrors([])
    self._VerifyMock(path.join("out", "x64.release", "d7"), "--flag", "run.js",
                     "--", "2", "test_name")

  def testTwoRuns_Units_SuiteName(self):
    test_input = dict(V8_JSON)
    test_input["run_count"] = 2
    test_input["name"] = "v8"
    test_input["units"] = "ms"
    self._WriteTestInput(test_input)
    self._MockCommand([".", "."],
                      ["Richards: 100\nDeltaBlue: 200\n",
                       "Richards: 50\nDeltaBlue: 300\n"])
    self.assertEquals(0, self._CallMain())
    self._VerifyResults("v8", "ms", [
      {"name": "Richards", "results": ["50.0", "100.0"], "stddev": ""},
      {"name": "DeltaBlue", "results": ["300.0", "200.0"], "stddev": ""},
    ])
    self._VerifyErrors([])
    self._VerifyMock(path.join("out", "x64.release", "d7"), "--flag", "run.js")

  def testTwoRuns_SubRegexp(self):
    test_input = dict(V8_JSON)
    test_input["run_count"] = 2
    del test_input["results_regexp"]
    test_input["tests"][0]["results_regexp"] = "^Richards: (.+)$"
    test_input["tests"][1]["results_regexp"] = "^DeltaBlue: (.+)$"
    self._WriteTestInput(test_input)
    self._MockCommand([".", "."],
                      ["Richards: 100\nDeltaBlue: 200\n",
                       "Richards: 50\nDeltaBlue: 300\n"])
    self.assertEquals(0, self._CallMain())
    self._VerifyResults("test", "score", [
      {"name": "Richards", "results": ["50.0", "100.0"], "stddev": ""},
      {"name": "DeltaBlue", "results": ["300.0", "200.0"], "stddev": ""},
    ])
    self._VerifyErrors([])
    self._VerifyMock(path.join("out", "x64.release", "d7"), "--flag", "run.js")

  def testNestedSuite(self):
    self._WriteTestInput(V8_NESTED_SUITES_JSON)
    self._MockCommand(["delta_blue", "sub/leaf", "richards"],
                      ["DeltaBlue: 200\n",
                       "Simple: 1 ms.\n",
                       "Simple: 2 ms.\n",
                       "Simple: 3 ms.\n",
                       "Richards: 100\n",
                       "Richards: 50\n"])
    self.assertEquals(0, self._CallMain())
    self.assertEquals([
      {"units": "score",
       "graphs": ["test", "Richards"],
       "results": ["50.0", "100.0"],
       "stddev": ""},
      {"units": "ms",
       "graphs": ["test", "Sub", "Leaf"],
       "results": ["3.0", "2.0", "1.0"],
       "stddev": ""},
      {"units": "score",
       "graphs": ["test", "DeltaBlue"],
       "results": ["200.0"],
       "stddev": ""},
      ], self._LoadResults()["traces"])
    self._VerifyErrors([])
    self._VerifyMockMultiple(
        (path.join("out", "x64.release", "d7"), "--flag", "run.js"),
        (path.join("out", "x64.release", "d7"), "--flag", "run.js"),
        (path.join("out", "x64.release", "d8"), "--flag", "run.js"),
        (path.join("out", "x64.release", "d8"), "--flag", "run.js"),
        (path.join("out", "x64.release", "d8"), "--flag", "run.js"),
        (path.join("out", "x64.release", "d8"), "--flag", "--flag2", "run.js"))

  def testOneRunStdDevRegExp(self):
    test_input = dict(V8_JSON)
    test_input["stddev_regexp"] = "^%s\-stddev: (.+)$"
    self._WriteTestInput(test_input)
    self._MockCommand(["."], ["Richards: 1.234\nRichards-stddev: 0.23\n"
                              "DeltaBlue: 10657567\nDeltaBlue-stddev: 106\n"])
    self.assertEquals(0, self._CallMain())
    self._VerifyResults("test", "score", [
      {"name": "Richards", "results": ["1.234"], "stddev": "0.23"},
      {"name": "DeltaBlue", "results": ["10657567.0"], "stddev": "106"},
    ])
    self._VerifyErrors([])
    self._VerifyMock(path.join("out", "x64.release", "d7"), "--flag", "run.js")

  def testTwoRunsStdDevRegExp(self):
    test_input = dict(V8_JSON)
    test_input["stddev_regexp"] = "^%s\-stddev: (.+)$"
    test_input["run_count"] = 2
    self._WriteTestInput(test_input)
    self._MockCommand(["."], ["Richards: 3\nRichards-stddev: 0.7\n"
                              "DeltaBlue: 6\nDeltaBlue-boom: 0.9\n",
                              "Richards: 2\nRichards-stddev: 0.5\n"
                              "DeltaBlue: 5\nDeltaBlue-stddev: 0.8\n"])
    self.assertEquals(1, self._CallMain())
    self._VerifyResults("test", "score", [
      {"name": "Richards", "results": ["2.0", "3.0"], "stddev": "0.7"},
      {"name": "DeltaBlue", "results": ["5.0", "6.0"], "stddev": "0.8"},
    ])
    self._VerifyErrors(
        ["Test test/Richards should only run once since a stddev is provided "
         "by the test.",
         "Test test/DeltaBlue should only run once since a stddev is provided "
         "by the test.",
         "Regexp \"^DeltaBlue\-stddev: (.+)$\" didn't match for test "
         "test/DeltaBlue."])
    self._VerifyMock(path.join("out", "x64.release", "d7"), "--flag", "run.js")

  def testBuildbot(self):
    self._WriteTestInput(V8_JSON)
    self._MockCommand(["."], ["Richards: 1.234\nDeltaBlue: 10657567\n"])
    self.assertEquals(0, self._CallMain("--buildbot"))
    self._VerifyResults("test", "score", [
      {"name": "Richards", "results": ["1.234"], "stddev": ""},
      {"name": "DeltaBlue", "results": ["10657567.0"], "stddev": ""},
    ])
    self._VerifyErrors([])
    self._VerifyMock(path.join("out", "Release", "d7"), "--flag", "run.js")

  def testBuildbotWithTotal(self):
    test_input = dict(V8_JSON)
    test_input["total"] = True
    self._WriteTestInput(test_input)
    self._MockCommand(["."], ["Richards: 1.234\nDeltaBlue: 10657567\n"])
    self.assertEquals(0, self._CallMain("--buildbot"))
    self._VerifyResults("test", "score", [
      {"name": "Richards", "results": ["1.234"], "stddev": ""},
      {"name": "DeltaBlue", "results": ["10657567.0"], "stddev": ""},
      {"name": "Total", "results": ["3626.49109719"], "stddev": ""},
    ])
    self._VerifyErrors([])
    self._VerifyMock(path.join("out", "Release", "d7"), "--flag", "run.js")

  def testBuildbotWithTotalAndErrors(self):
    test_input = dict(V8_JSON)
    test_input["total"] = True
    self._WriteTestInput(test_input)
    self._MockCommand(["."], ["x\nRichards: bla\nDeltaBlue: 10657567\ny\n"])
    self.assertEquals(1, self._CallMain("--buildbot"))
    self._VerifyResults("test", "score", [
      {"name": "Richards", "results": [], "stddev": ""},
      {"name": "DeltaBlue", "results": ["10657567.0"], "stddev": ""},
    ])
    self._VerifyErrors(
        ["Regexp \"^Richards: (.+)$\" "
         "returned a non-numeric for test test/Richards.",
         "Not all traces have the same number of results."])
    self._VerifyMock(path.join("out", "Release", "d7"), "--flag", "run.js")

  def testRegexpNoMatch(self):
    self._WriteTestInput(V8_JSON)
    self._MockCommand(["."], ["x\nRichaards: 1.234\nDeltaBlue: 10657567\ny\n"])
    self.assertEquals(1, self._CallMain())
    self._VerifyResults("test", "score", [
      {"name": "Richards", "results": [], "stddev": ""},
      {"name": "DeltaBlue", "results": ["10657567.0"], "stddev": ""},
    ])
    self._VerifyErrors(
        ["Regexp \"^Richards: (.+)$\" didn't match for test test/Richards."])
    self._VerifyMock(path.join("out", "x64.release", "d7"), "--flag", "run.js")

  def testOneRunGeneric(self):
    test_input = dict(V8_GENERIC_JSON)
    self._WriteTestInput(test_input)
    self._MockCommand(["."], [
      "RESULT Infra: Constant1= 11 count\n"
      "RESULT Infra: Constant2= [10,5,10,15] count\n"
      "RESULT Infra: Constant3= {12,1.2} count\n"
      "RESULT Infra: Constant4= [10,5,error,15] count\n"])
    self.assertEquals(1, self._CallMain())
    self.assertEquals([
      {"units": "count",
       "graphs": ["test", "Infra", "Constant1"],
       "results": ["11.0"],
       "stddev": ""},
      {"units": "count",
       "graphs": ["test", "Infra", "Constant2"],
       "results": ["10.0", "5.0", "10.0", "15.0"],
       "stddev": ""},
      {"units": "count",
       "graphs": ["test", "Infra", "Constant3"],
       "results": ["12.0"],
       "stddev": "1.2"},
      {"units": "count",
       "graphs": ["test", "Infra", "Constant4"],
       "results": [],
       "stddev": ""},
      ], self._LoadResults()["traces"])
    self._VerifyErrors(["Found non-numeric in test/Infra/Constant4"])
    self._VerifyMock(path.join("out", "x64.release", "cc"), "--flag", "")

  def testOneRunTimingOut(self):
    test_input = dict(V8_JSON)
    test_input["timeout"] = 70
    self._WriteTestInput(test_input)
    self._MockCommand(["."], [""], timed_out=True)
    self.assertEquals(1, self._CallMain())
    self._VerifyResults("test", "score", [
      {"name": "Richards", "results": [], "stddev": ""},
      {"name": "DeltaBlue", "results": [], "stddev": ""},
    ])
    self._VerifyErrors([
      "Regexp \"^Richards: (.+)$\" didn't match for test test/Richards.",
      "Regexp \"^DeltaBlue: (.+)$\" didn't match for test test/DeltaBlue.",
    ])
    self._VerifyMock(
        path.join("out", "x64.release", "d7"), "--flag", "run.js", timeout=70)

  # Simple test that mocks out the android platform. Testing the platform would
  # require lots of complicated mocks for the android tools.
  def testAndroid(self):
    self._WriteTestInput(V8_JSON)
    # FIXME(machenbach): This is not test-local!
    platform = run_perf.AndroidPlatform
    platform.PreExecution = MagicMock(return_value=None)
    platform.PostExecution = MagicMock(return_value=None)
    platform.PreTests = MagicMock(return_value=None)
    platform.Run = MagicMock(
        return_value=("Richards: 1.234\nDeltaBlue: 10657567\n", None))
    run_perf.AndroidPlatform = MagicMock(return_value=platform)
    self.assertEquals(
        0, self._CallMain("--android-build-tools", "/some/dir",
                          "--arch", "arm"))
    self._VerifyResults("test", "score", [
      {"name": "Richards", "results": ["1.234"], "stddev": ""},
      {"name": "DeltaBlue", "results": ["10657567.0"], "stddev": ""},
    ])

  def testTwoRuns_Trybot(self):
    test_input = dict(V8_JSON)
    test_input["run_count"] = 2
    self._WriteTestInput(test_input)
    self._MockCommand([".", ".", ".", "."],
                      ["Richards: 100\nDeltaBlue: 200\n",
                       "Richards: 200\nDeltaBlue: 20\n",
                       "Richards: 50\nDeltaBlue: 200\n",
                       "Richards: 100\nDeltaBlue: 20\n"])
    test_output_no_patch = path.join(TEST_WORKSPACE, "results_no_patch.json")
    self.assertEquals(0, self._CallMain(
        "--outdir-no-patch", "out-no-patch",
        "--json-test-results-no-patch", test_output_no_patch,
    ))
    self._VerifyResults("test", "score", [
      {"name": "Richards", "results": ["100.0", "200.0"], "stddev": ""},
      {"name": "DeltaBlue", "results": ["20.0", "20.0"], "stddev": ""},
    ])
    self._VerifyResults("test", "score", [
      {"name": "Richards", "results": ["50.0", "100.0"], "stddev": ""},
      {"name": "DeltaBlue", "results": ["200.0", "200.0"], "stddev": ""},
    ], test_output_no_patch)
    self._VerifyErrors([])
    self._VerifyMockMultiple(
        (path.join("out", "x64.release", "d7"), "--flag", "run.js"),
        (path.join("out-no-patch", "x64.release", "d7"), "--flag", "run.js"),
        (path.join("out", "x64.release", "d7"), "--flag", "run.js"),
        (path.join("out-no-patch", "x64.release", "d7"), "--flag", "run.js"),
    )

  def testWrongBinaryWithProf(self):
    test_input = dict(V8_JSON)
    self._WriteTestInput(test_input)
    self._MockCommand(["."], ["x\nRichards: 1.234\nDeltaBlue: 10657567\ny\n"])
    self.assertEquals(0, self._CallMain("--extra-flags=--prof"))
    self._VerifyResults("test", "score", [
      {"name": "Richards", "results": ["1.234"], "stddev": ""},
      {"name": "DeltaBlue", "results": ["10657567.0"], "stddev": ""},
    ])
    self._VerifyErrors([])
    self._VerifyMock(path.join("out", "x64.release", "d7"),
                     "--flag", "--prof", "run.js")

  def testUnzip(self):
    def Gen():
      for i in [1, 2, 3]:
        yield i, i + 1
    l, r = run_perf.Unzip(Gen())
    self.assertEquals([1, 2, 3], list(l()))
    self.assertEquals([2, 3, 4], list(r()))

  #############################################################################
  ### System tests

  def _RunPerf(self, mocked_d8, test_json):
    output_json = path.join(TEST_WORKSPACE, "output.json")
    args = [
      sys.executable, RUN_PERF,
      "--binary-override-path", os.path.join(TEST_DATA, mocked_d8),
      "--json-test-results", output_json,
      os.path.join(TEST_DATA, test_json),
    ]
    subprocess.check_output(args)
    return self._LoadResults(output_json)

  def testNormal(self):
    results = self._RunPerf("d8_mocked1.py", "test1.json")
    self.assertEquals([], results['errors'])
    self.assertEquals([
      {
        'units': 'score',
        'graphs': ['test1', 'Richards'],
        'results': [u'1.2', u'1.2'],
        'stddev': '',
      },
      {
        'units': 'score',
        'graphs': ['test1', 'DeltaBlue'],
        'results': [u'2.1', u'2.1'],
        'stddev': '',
      },
    ], results['traces'])

  def testResultsProcessor(self):
    results = self._RunPerf("d8_mocked2.py", "test2.json")
    self.assertEquals([], results['errors'])
    self.assertEquals([
      {
        'units': 'score',
        'graphs': ['test2', 'Richards'],
        'results': [u'1.2', u'1.2'],
        'stddev': '',
      },
      {
        'units': 'score',
        'graphs': ['test2', 'DeltaBlue'],
        'results': [u'2.1', u'2.1'],
        'stddev': '',
      },
    ], results['traces'])

  def testResultsProcessorNested(self):
    results = self._RunPerf("d8_mocked2.py", "test3.json")
    self.assertEquals([], results['errors'])
    self.assertEquals([
      {
        'units': 'score',
        'graphs': ['test3', 'Octane', 'Richards'],
        'results': [u'1.2'],
        'stddev': '',
      },
      {
        'units': 'score',
        'graphs': ['test3', 'Octane', 'DeltaBlue'],
        'results': [u'2.1'],
        'stddev': '',
      },
    ], results['traces'])
