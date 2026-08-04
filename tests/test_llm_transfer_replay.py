from __future__ import annotations

import csv
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


class LLMTransferReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("c++") is None:
            raise unittest.SkipTest("a C++20 compiler is required")
        cls._temporary = tempfile.TemporaryDirectory()
        cls.build = Path(cls._temporary.name)
        completed = subprocess.run(
            ["bash", str(REPO / "script" / "build_llm_transfer_replay.sh"), str(cls.build)],
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr)
        cls.binary = cls.build / "llm_transfer_replay"

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    def run_example(self, profile: str | None = None, mode: str = "auto") -> list[dict[str, str]]:
        output = self.build / f"service-{mode}-{len(list(self.build.glob('service-*')))}.csv"
        metadata = output.with_suffix(".metadata.json")
        profile_path = Path(profile) if profile else (
            REPO / "examples" / "llm_transfer_replay" / "ocean-hardware-profile.yaml"
        )
        completed = subprocess.run(
            [
                str(self.binary),
                "--trace",
                str(REPO / "examples" / "llm_transfer_replay" / "transfer-events.csv"),
                "--hardware-profile",
                str(profile_path),
                "--output",
                str(output),
                "--metadata-output",
                str(metadata),
                "--mode",
                mode,
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertFalse(json.loads(metadata.read_text())["qemu_required"])
        with output.open(newline="") as handle:
            return list(csv.DictReader(handle))

    def test_bulk_read_service_time_and_topology(self) -> None:
        rows = self.run_example()
        first = rows[0]
        expected_bandwidth_ns = 1048576 / (32 * 1024**3) * 1e9
        self.assertAlmostEqual(float(first["base_latency_ns"]), 150.0)
        self.assertAlmostEqual(float(first["media_latency_ns"]), 100.0)
        self.assertAlmostEqual(float(first["topology_latency_ns"]), 50.0)
        self.assertAlmostEqual(float(first["bandwidth_delay_ns"]), expected_bandwidth_ns)

    def test_fifo_queueing_and_two_client_contention(self) -> None:
        rows = self.run_example()
        self.assertEqual(rows[0]["queue_delay_ns"], "0")
        self.assertGreater(float(rows[1]["queue_delay_ns"]), 0.0)
        self.assertNotEqual(rows[0]["event_id"], rows[1]["event_id"])

    def test_transfer_dependency_is_enforced_without_fifo_congestion(self) -> None:
        profile = self.build / "no-congestion.yaml"
        original = (
            REPO / "examples" / "llm_transfer_replay" / "ocean-hardware-profile.yaml"
        ).read_text()
        profile.write_text(original.replace("congestion_model: fifo", "congestion_model: none"))
        rows = {row["event_id"]: row for row in self.run_example(str(profile))}
        first = rows["xfer-weight-1-prefill"]
        dependent = rows["xfer-weight-2-prefill"]
        self.assertGreaterEqual(
            float(dependent["service_start_ns"]), float(first["service_end_ns"])
        )

    def test_read_and_write_use_different_bandwidth(self) -> None:
        rows = {row["event_id"]: row for row in self.run_example()}
        read = rows["xfer-weight-1-decode"]
        write = rows["xfer-kv-write-decode"]
        read_seconds_per_byte = float(read["bandwidth_delay_ns"]) / int(read["modeled_bytes"])
        write_seconds_per_byte = float(write["bandwidth_delay_ns"]) / int(write["modeled_bytes"])
        self.assertGreater(write_seconds_per_byte, read_seconds_per_byte)

    def test_aggregate_and_detailed_formula_match(self) -> None:
        aggregate = self.run_example(mode="aggregate")
        detailed = self.run_example(mode="detailed")
        self.assertEqual(
            [row["service_end_ns"] for row in aggregate],
            [row["service_end_ns"] for row in detailed],
        )
        self.assertEqual({row["model_mode"] for row in aggregate}, {"aggregate"})
        self.assertEqual({row["model_mode"] for row in detailed}, {"detailed"})

    def test_staged_copy_is_slower_than_direct(self) -> None:
        direct = self.run_example()
        profile = self.build / "staged.yaml"
        original = (
            REPO / "examples" / "llm_transfer_replay" / "ocean-hardware-profile.yaml"
        ).read_text()
        profile.write_text(original.replace("mode: direct_dma", "mode: staged_copy"))
        staged = self.run_example(str(profile))
        self.assertGreater(float(staged[0]["service_end_ns"]), float(direct[0]["service_end_ns"]))

    def test_latency_and_bandwidth_sensitivity(self) -> None:
        original = (
            REPO / "examples" / "llm_transfer_replay" / "ocean-hardware-profile.yaml"
        ).read_text()
        baseline = self.run_example()
        slower_latency_profile = self.build / "latency-2x.yaml"
        slower_latency_profile.write_text(original.replace("base_ns: 150", "base_ns: 300"))
        slower_bandwidth_profile = self.build / "bandwidth-05x.yaml"
        slower_bandwidth_profile.write_text(original.replace("read_gib_s: 32", "read_gib_s: 16"))
        slower_latency = self.run_example(str(slower_latency_profile))
        slower_bandwidth = self.run_example(str(slower_bandwidth_profile))
        self.assertGreater(
            float(slower_latency[0]["service_end_ns"]),
            float(baseline[0]["service_end_ns"]),
        )
        self.assertGreater(
            float(slower_bandwidth[0]["service_end_ns"]),
            float(baseline[0]["service_end_ns"]),
        )

    def test_capacity_violation(self) -> None:
        profile = self.build / "small.yaml"
        original = (
            REPO / "examples" / "llm_transfer_replay" / "ocean-hardware-profile.yaml"
        ).read_text()
        profile.write_text(original.replace("capacity_gib: 16", "capacity_bytes: 1048576"))
        rows = self.run_example(str(profile))
        self.assertTrue(any(row["capacity_hit"] == "false" for row in rows))

    def test_deterministic_replay(self) -> None:
        first = self.run_example()
        second = self.run_example()
        self.assertEqual(first, second)

    def test_malformed_trace_is_rejected(self) -> None:
        bad_trace = self.build / "bad.csv"
        bad_trace.write_text("schema_version,event_id\n1,x\n")
        completed = subprocess.run(
            [
                str(self.binary),
                "--trace",
                str(bad_trace),
                "--hardware-profile",
                str(REPO / "examples" / "llm_transfer_replay" / "ocean-hardware-profile.yaml"),
                "--output",
                str(self.build / "bad-output.csv"),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("missing column", completed.stderr)


if __name__ == "__main__":
    unittest.main()
