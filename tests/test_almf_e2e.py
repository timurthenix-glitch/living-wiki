"""
tests.test_almf_e2e: Fast Automated End-to-End Integration Test Suite for Milestone 4.

Verifies:
1. Multi-modal procedural generators synthesize valid text and paired ground truths.
2. 4-tier verification battery executes against loaded MemoryGraphs.
3. Cross-tokenizer metrics (o200k_base, cl100k_base, llama3) and CFI score calculations.
4. Binary container serialization and end-to-end roundtrip fidelity.
5. Headless benchmark runner executes in CI with scale factor producing clean report.
"""

from __future__ import annotations

import os
import tempfile
import unittest

from almf.codec import decode, encode, load_from_file, save_to_file
from almf.types import MemoryGraph
from benchmarks.battery import (
    evaluate_battery,
    evaluate_tier1_niah,
    evaluate_tier2_qa,
    evaluate_tier3_fidelity,
    evaluate_tier4_multihop,
)
from benchmarks.datasets import (
    GroundTruthSuite,
    generate_code_dataset,
    generate_dialogue_dataset,
    generate_temporal_dataset,
    generate_trace_dataset,
    ingest_dataset_to_graph,
)
from benchmarks.metrics import (
    CompressionMetrics,
    calculate_cfi,
    calculate_compression_ratio,
    calculate_harmonic_mean,
    count_tokens,
)
from benchmarks.run_benchmark import run_benchmark


class TestALMFDatasetsE2E(unittest.TestCase):
    """E2E verification of procedural dataset generators and ground truth items."""

    def test_trace_dataset_generation(self) -> None:
        raw_text, gt = generate_trace_dataset(target_tokens=5000, seed=42)
        tokens = count_tokens(raw_text, "o200k_base")
        self.assertGreaterEqual(tokens, 5000)
        self.assertEqual(len(gt.needles), 5)
        depths = [n.depth_percent for n in gt.needles]
        self.assertEqual(depths, [0.0, 25.0, 50.0, 75.0, 100.0])
        self.assertGreaterEqual(len(gt.qa_pairs), 1)
        self.assertGreaterEqual(len(gt.triples), 1)
        self.assertGreaterEqual(len(gt.multi_hops), 1)

    def test_dialogue_dataset_generation(self) -> None:
        raw_text, gt = generate_dialogue_dataset(target_tokens=5000, seed=42)
        tokens = count_tokens(raw_text, "o200k_base")
        self.assertGreaterEqual(tokens, 5000)
        self.assertEqual(len(gt.needles), 5)
        self.assertIn("User:", raw_text)
        self.assertIn("Assistant:", raw_text)

    def test_code_dataset_generation(self) -> None:
        raw_text, gt = generate_code_dataset(target_tokens=5000, seed=42)
        tokens = count_tokens(raw_text, "o200k_base")
        self.assertGreaterEqual(tokens, 5000)
        self.assertEqual(len(gt.needles), 5)
        self.assertIn("class ServiceController", raw_text)

    def test_temporal_dataset_generation(self) -> None:
        raw_text, gt = generate_temporal_dataset(target_tokens=5000, seed=42)
        tokens = count_tokens(raw_text, "o200k_base")
        self.assertGreaterEqual(tokens, 5000)
        self.assertEqual(len(gt.needles), 5)
        self.assertIn("### EPOCH", raw_text)


class TestALMFBatteryE2E(unittest.TestCase):
    """E2E verification of the 4-tier verification battery against loaded graphs."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.trace_txt, cls.trace_gt = generate_trace_dataset(target_tokens=5000, seed=42)
        cls.trace_graph = ingest_dataset_to_graph("trace", cls.trace_txt, cls.trace_gt)

    def test_tier1_niah(self) -> None:
        t1 = evaluate_tier1_niah(self.trace_graph, self.trace_gt.needles, token_budget=500)
        self.assertGreaterEqual(t1.actual_accuracy, 0.95)
        self.assertTrue(t1.passed)

    def test_tier2_qa(self) -> None:
        t2 = evaluate_tier2_qa(self.trace_graph, self.trace_gt.qa_pairs, token_budget=500)
        self.assertGreaterEqual(t2.actual_accuracy, 0.90)
        self.assertTrue(t2.passed)

    def test_tier3_fidelity(self) -> None:
        t3 = evaluate_tier3_fidelity(self.trace_graph, self.trace_gt.triples)
        self.assertGreaterEqual(t3.actual_accuracy, 0.88)
        self.assertTrue(t3.passed)

    def test_tier4_multihop(self) -> None:
        t4 = evaluate_tier4_multihop(self.trace_graph, self.trace_gt.multi_hops, token_budget=500)
        self.assertGreaterEqual(t4.actual_accuracy, 0.80)
        self.assertTrue(t4.passed)

    def test_full_battery_composite(self) -> None:
        b_res = evaluate_battery(self.trace_graph, self.trace_gt, dataset_name="E2E_TRACE")
        self.assertGreaterEqual(b_res.overall_accuracy, 0.880)
        self.assertTrue(b_res.target_met)
        md = b_res.to_markdown_table()
        self.assertIn("4-Tier Verification Battery Scorecard", md)


class TestALMFMetricsAndEncodingE2E(unittest.TestCase):
    """E2E verification of container codec, multi-tokenizer metrics, and CFI."""

    def test_container_encode_decode_roundtrip(self) -> None:
        txt, gt = generate_dialogue_dataset(target_tokens=5000, seed=42)
        graph = ingest_dataset_to_graph("dialogue", txt, gt)

        with tempfile.NamedTemporaryFile(suffix=".almf", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            bytes_written = save_to_file(graph, tmp_path)
            self.assertGreater(bytes_written, 0)
            loaded_graph = load_from_file(tmp_path)
            self.assertEqual(len(loaded_graph.entities), len(graph.entities))
            self.assertEqual(len(loaded_graph.hyperedges), len(graph.hyperedges))
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_cross_tokenizer_and_cfi_computation(self) -> None:
        text = "Architecture decision: use PostgreSQL 16 on port 5432 with gRPC services."
        t_o200k = count_tokens(text, "o200k_base")
        t_cl100k = count_tokens(text, "cl100k_base")
        t_llama3 = count_tokens(text, "llama3")

        self.assertGreater(t_o200k, 0)
        self.assertGreater(t_cl100k, 0)
        self.assertGreater(t_llama3, 0)

        # Harmonic mean
        ratios = [250.0, 240.0, 260.0]
        h_mean = calculate_harmonic_mean(ratios)
        self.assertAlmostEqual(h_mean, 249.66, delta=0.5)

        # CFI when CR >= 200:1 saturates to Accuracy
        cfi_full = calculate_cfi(250.0, accuracy=0.95)
        self.assertEqual(cfi_full, 0.95)


class TestALMFCLIRunnerE2E(unittest.TestCase):
    """E2E verification of the headless benchmark runner executable."""

    def test_run_benchmark_mini_scale(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as tmp:
            report_path = tmp.name

        try:
            # Run mini-scale benchmark (~20,000 tokens total across 4 datasets)
            success = run_benchmark(
                dataset_name="all",
                output_path=report_path,
                scale=0.04,
                seed=42,
                token_budget=400,
            )
            self.assertTrue(os.path.exists(report_path))
            with open(report_path, "r", encoding="utf-8") as f:
                content = f.read()

            self.assertIn("Project ALMF — Long-Term Memory Compression & Retrieval Benchmark Report", content)
            self.assertIn("Executive Summary", content)
            self.assertIn("4-Tier Verification Battery Scorecard", content)
        finally:
            if os.path.exists(report_path):
                os.remove(report_path)


if __name__ == "__main__":
    unittest.main()
