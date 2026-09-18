"""classify.py's generic Job runner (run_direct/run_batch/collect_batch): the same runner is
shared with doubles.py, so these tests exercise it through a fake Job/client rather than the
real pole schema or the real anthropic client. No network, no anthropic import at module load --
classify.py only imports anthropic lazily inside the functions that need it, and it needs it
whether or not a real request is ever sent, so the package must be installed to run these tests
(it is, via pyproject's `anthropic` dependency) but is never touched at import time."""
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from classify import Job, collect_batch, cost_usd, run_direct  # noqa: E402


class Sentinel:
    """An opaque todo item. __getitem__ raises so any runner code that indexes into an item
    instead of calling job.id_of/job.build_request fails the test immediately."""
    def __init__(self, cid):
        self.cid = cid

    def __getitem__(self, key):
        raise AssertionError(f"runner indexed into an opaque item with key {key!r}")


USAGE = {"input": 100, "output": 20, "cache_read": 0, "cache_write": 0}


class FakeMessages:
    def __init__(self, client):
        self._client = client

    def create(self, **kwargs):
        self._client.calls.append(kwargs)
        r = self._client.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class FakeBatches:
    """Enough of client.messages.batches for collect_batch: one already-ended batch."""
    def __init__(self, client, result_rows):
        self._client = client
        self._result_rows = result_rows

    def retrieve(self, batch_id):
        class Status:
            processing_status = "ended"
            request_counts = {"succeeded": len(self._result_rows)}
        return Status()

    def results(self, batch_id):
        return iter(self._result_rows)


class FakeClient:
    """client.messages.create returns queued `responses` in order; exceptions in the queue are raised."""
    def __init__(self, responses, batch_rows=None):
        self.responses = list(responses)
        self.calls = []
        self.messages = FakeMessages(self)
        self.messages.batches = FakeBatches(self, batch_rows or [])


def make_job(results_dir, id_field="detection_id", parse=None):
    return Job(
        name="test",
        results_dir=results_dir,
        batches_dir=results_dir / "batches",
        schema_version=7,
        id_field=id_field,
        id_of=lambda it: it.cid,
        build_request=lambda it: {"item": it.cid},
        parse=parse or (lambda msg: ({"echo": msg}, dict(USAGE))),
    )


class RunDirectTest(unittest.TestCase):
    def test_writes_result_keyed_by_id_field_and_sums_usage(self):
        with TemporaryDirectory() as d:
            res = Path(d)
            job = make_job(res, id_field="pair_id")
            client = FakeClient(responses=["msg-a", "msg-b"])
            items = [Sentinel("a1"), Sentinel("b2")]

            total = run_direct(client, job, items)

            self.assertEqual(total, {"input": 200, "output": 40, "cache_read": 0, "cache_write": 0})
            got_a = json.loads((res / "a1.json").read_text())
            self.assertEqual(got_a["pair_id"], "a1")
            self.assertEqual(got_a["schema"], 7)
            self.assertEqual(got_a["result"], {"echo": "msg-a"})
            self.assertEqual(got_a["usage"], USAGE)
            got_b = json.loads((res / "b2.json").read_text())
            self.assertEqual(got_b["pair_id"], "b2")
            # only id_of/build_request touched the item -- confirmed by Sentinel not raising
            self.assertEqual(client.calls, [{"item": "a1"}, {"item": "b2"}])

    def test_malformed_response_retries_once_then_drops(self):
        def flaky_parse(msg):
            raise ValueError(f"bad json: {msg}")

        with TemporaryDirectory() as d:
            res = Path(d)
            job = make_job(res, parse=flaky_parse)
            client = FakeClient(responses=["try-1", "try-2"])  # both attempts get a message; both fail to parse

            total = run_direct(client, job, [Sentinel("x1")])

            self.assertEqual(total, {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0})
            self.assertEqual(len(client.calls), 2, "one retry: two create() calls total")
            dropped = json.loads((res / "x1.json").read_text())
            self.assertEqual(dropped["dropped"], "malformed:bad json: try-2")

    def test_item_shape_is_never_assumed(self):
        # a Sentinel's __getitem__ raises AssertionError; reaching the end without one firing
        # proves the runner only ever called job.id_of(item) / job.build_request(item).
        with TemporaryDirectory() as d:
            res = Path(d)
            job = make_job(res)
            client = FakeClient(responses=["ok"])
            run_direct(client, job, [Sentinel("only")])
            self.assertTrue((res / "only.json").exists())


class CollectBatchRetryTest(unittest.TestCase):
    def test_retry_uses_job_build_request_on_the_item_not_a_hardcoded_path(self):
        # Simulate a batch result that comes back errored, forcing the synchronous retry path.
        # The old code rebuilt the request from a hardcoded DATA/"crops"/<id>.jpg path; the retry
        # must now go through job.build_request(item) on the matching todo item instead.
        class ErroredResult:
            custom_id = "z9"

            class result:
                type = "errored"

        with TemporaryDirectory() as d:
            res = Path(d)
            job = make_job(res, id_field="pair_id")
            client = FakeClient(responses=["retry-msg"], batch_rows=[ErroredResult()])

            total = collect_batch(client, job, "batch-1", [Sentinel("z9")])

            self.assertEqual(client.calls, [{"item": "z9"}], "retry built its request from job.build_request(item)")
            got = json.loads((res / "z9.json").read_text())
            self.assertEqual(got["pair_id"], "z9")
            self.assertEqual(got["retried"], "batch:errored")
            self.assertEqual(total, USAGE)


class CostUsdTest(unittest.TestCase):
    def test_batch_is_half_of_direct(self):
        usage = {"input": 1_000_000, "output": 1_000_000, "cache_read": 0, "cache_write": 0}
        direct = cost_usd(usage, batch=False)
        batch = cost_usd(usage, batch=True)
        self.assertAlmostEqual(batch, direct * 0.5)
        self.assertAlmostEqual(direct, 12.0)  # $2/1M in + $10/1M out at these token counts


if __name__ == "__main__":
    unittest.main()
