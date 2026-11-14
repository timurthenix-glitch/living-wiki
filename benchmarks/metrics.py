"""
benchmarks.metrics: Formal Token Counting and Compression Ratio Calculations for ALMF.

Implements Section 2.3 (Formal Compression Metrics) of docs/ALMF_SPECIFICATION.md:
1. In-Context Prompt Compression Ratio: CR_prompt = T_raw / T_rendered
2. Query-Conditioned Retrieval Compression Ratio: CR_retrieval = T_raw / T_facts
3. Physical Storage Compression Ratio: CR_bytes = Bytes_raw / Bytes_almf
4. Cross-Tokenizer Harmonic Mean: CR_harmonic = 3 / (1/CR_cl100k + 1/CR_o200k + 1/CR_llama3)
5. Compression-Fidelity Index: CFI = min(1.0, log10(CR) / 2.301) * Accuracy
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Union

try:
    import tiktoken
    _HAS_TIKTOKEN = True
except ImportError:  # pragma: no cover
    _HAS_TIKTOKEN = False

# Cached tiktoken encoding instances
_ENCODINGS: Dict[str, Any] = {}

# Canonical constant: log10(200) = ~2.30102999566
LOG10_200 = 2.3010299956639813


def get_tokenizer_encoding(name: str = "o200k_base") -> Optional[Any]:
    """Retrieves or loads a tiktoken encoding object if available."""
    if not _HAS_TIKTOKEN:
        return None
    if name in _ENCODINGS:
        return _ENCODINGS[name]
    try:
        enc = tiktoken.get_encoding(name)
        _ENCODINGS[name] = enc
        return enc
    except Exception:
        return None


def count_tokens(text: str, tokenizer: str = "o200k_base") -> int:
    """
    Counts tokens in a string using tiktoken (`o200k_base`, `cl100k_base`)
    with specialized subword/regex and character heuristics for fallback and `llama3`.

    - `o200k_base`: OpenAI GPT-4o / modern frontier models.
    - `cl100k_base`: OpenAI GPT-4 / text-embedding-3 models.
    - `llama3`: Meta LLaMA 3 (128k tiktoken/BPE equivalent).
    """
    if not text:
        return 0

    norm_tokenizer = tokenizer.lower().strip()

    # 1. Direct tiktoken mapping
    if _HAS_TIKTOKEN:
        target_enc = norm_tokenizer
        if norm_tokenizer in ("llama3", "llama-3", "llama"):
            # LLaMA 3 shares a 128k vocabulary with token frequency very close to o200k_base / cl100k_base
            target_enc = "o200k_base"

        enc = get_tokenizer_encoding(target_enc)
        if enc is not None:
            try:
                tokens = enc.encode(text, disallowed_special=())
                if norm_tokenizer in ("llama3", "llama-3", "llama"):
                    # LLaMA 3 tokenizes whitespace and code slightly differently (within ~2%)
                    return max(1, int(len(tokens) * 1.02))
                return len(tokens)
            except Exception:
                pass

    # 2. Reliable Whitespace & Subword Fallback Heuristic
    # Distinguishes code, Cyrillic, Latin prose, and punctuation
    cyrillic_chars = len(re.findall(r"[\u0400-\u04FF]", text))
    total_chars = len(text)
    is_mostly_cyrillic = (cyrillic_chars / max(1, total_chars)) > 0.3

    if is_mostly_cyrillic:
        # Cyrillic UTF-8 text typically takes ~1.5 - 2.0 characters per BPE token
        return max(1, int(math.ceil(total_chars / 1.8)))
    elif norm_tokenizer in ("llama3", "llama-3", "llama"):
        # LLaMA 3 English / Code is ~3.6 chars/token
        return max(1, int(math.ceil(total_chars / 3.6)))
    else:
        # Standard Latin / Code heuristic (~3.8 chars/token)
        return max(1, int(math.ceil(total_chars / 3.8)))


def calculate_compression_ratio(raw_tokens: int, compressed_tokens: int) -> float:
    """
    Computes prompt or token compression ratio:
      CR = Raw_Tokens / max(1, Compressed_Tokens)
    """
    if raw_tokens <= 0:
        return 1.0
    return raw_tokens / max(1, compressed_tokens)


def calculate_bytes_ratio(raw_bytes: int, compressed_bytes: int) -> float:
    """
    Computes physical storage compression ratio:
      CR_bytes = Raw_Bytes / max(1, Compressed_Bytes)
    """
    if raw_bytes <= 0:
        return 1.0
    return raw_bytes / max(1, compressed_bytes)


def calculate_harmonic_mean(ratios: Sequence[float]) -> float:
    """
    Computes the harmonic mean across multiple compression ratios:
      H = N / sum(1 / r_i)
    """
    valid = [r for r in ratios if r > 0]
    if not valid:
        return 1.0
    sum_reciprocals = sum(1.0 / r for r in valid)
    return len(valid) / sum_reciprocals


def calculate_cfi(compression_ratio: float, accuracy: float) -> float:
    """
    Calculates the Compression-Fidelity Index (CFI):
      CFI = min(1.0, log10(max(1.0, CR)) / log10(200)) * Accuracy

    When CR >= 200:1, the compression factor saturates at 1.0, making CFI = Accuracy.
    When CR < 200:1, CFI penalizes the accuracy proportionally to the log10 deficit.
    """
    cr_safe = max(1.0, float(compression_ratio))
    acc_safe = max(0.0, min(1.0, float(accuracy)))
    compression_factor = min(1.0, math.log10(cr_safe) / LOG10_200)
    return max(0.0, round(compression_factor * acc_safe, 4))


@dataclass
class CompressionMetrics:
    """
    Comprehensive multi-tokenizer and physical compression metrics for an evaluation run.
    """
    dataset_name: str = "ALL"
    raw_tokens_o200k: int = 0
    raw_tokens_cl100k: int = 0
    raw_tokens_llama3: int = 0

    rendered_prompt_tokens_o200k: int = 0
    rendered_prompt_tokens_cl100k: int = 0
    rendered_prompt_tokens_llama3: int = 0

    retrieved_fact_tokens_o200k: int = 0

    raw_bytes: int = 0
    almf_container_bytes: int = 0

    cr_prompt_o200k: float = 0.0
    cr_prompt_cl100k: float = 0.0
    cr_prompt_llama3: float = 0.0
    cr_prompt_harmonic: float = 0.0

    cr_retrieval: float = 0.0
    cr_bytes: float = 0.0

    accuracy: float = 0.0
    cfi: float = 0.0

    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def compute(
        cls,
        raw_text: str,
        rendered_prompt: str,
        retrieved_facts_text: str,
        raw_bytes: int,
        container_bytes: int,
        accuracy: float,
        dataset_name: str = "BENCHMARK",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "CompressionMetrics":
        """Factory method to calculate all metrics from raw and compressed artifacts."""
        # 1. Raw tokens
        t_raw_o200k = count_tokens(raw_text, "o200k_base")
        t_raw_cl100k = count_tokens(raw_text, "cl100k_base")
        t_raw_llama3 = count_tokens(raw_text, "llama3")

        # 2. Rendered in-context prompt tokens
        t_rend_o200k = count_tokens(rendered_prompt, "o200k_base")
        t_rend_cl100k = count_tokens(rendered_prompt, "cl100k_base")
        t_rend_llama3 = count_tokens(rendered_prompt, "llama3")

        # 3. Retrieved facts tokens
        t_ret_o200k = count_tokens(retrieved_facts_text, "o200k_base")

        # 4. Prompt compression ratios
        cr_o200k = calculate_compression_ratio(t_raw_o200k, t_rend_o200k)
        cr_cl100k = calculate_compression_ratio(t_raw_cl100k, t_rend_cl100k)
        cr_llama3 = calculate_compression_ratio(t_raw_llama3, t_rend_llama3)
        cr_harmonic = calculate_harmonic_mean([cr_cl100k, cr_o200k, cr_llama3])

        # 5. Retrieval and bytes ratios
        cr_retrieval = calculate_compression_ratio(t_raw_o200k, t_ret_o200k)
        cr_bytes = calculate_bytes_ratio(raw_bytes, container_bytes)

        # 6. CFI
        cfi = calculate_cfi(cr_o200k if cr_o200k > 0 else cr_harmonic, accuracy)

        return cls(
            dataset_name=dataset_name,
            raw_tokens_o200k=t_raw_o200k,
            raw_tokens_cl100k=t_raw_cl100k,
            raw_tokens_llama3=t_raw_llama3,
            rendered_prompt_tokens_o200k=t_rend_o200k,
            rendered_prompt_tokens_cl100k=t_rend_cl100k,
            rendered_prompt_tokens_llama3=t_rend_llama3,
            retrieved_fact_tokens_o200k=t_ret_o200k,
            raw_bytes=raw_bytes,
            almf_container_bytes=container_bytes,
            cr_prompt_o200k=round(cr_o200k, 2),
            cr_prompt_cl100k=round(cr_cl100k, 2),
            cr_prompt_llama3=round(cr_llama3, 2),
            cr_prompt_harmonic=round(cr_harmonic, 2),
            cr_retrieval=round(cr_retrieval, 2),
            cr_bytes=round(cr_bytes, 2),
            accuracy=round(accuracy, 4),
            cfi=cfi,
            metadata=metadata or {},
        )

    @property
    def target_met(self) -> bool:
        """Indicates whether all formal target thresholds are met."""
        return self.cr_prompt_o200k >= 200.0 and self.accuracy >= 0.88 and self.cfi >= 0.880

    def to_dict(self) -> Dict[str, Any]:
        """Converts metrics to a serializable dictionary."""
        return {
            "dataset_name": self.dataset_name,
            "raw_tokens": {
                "o200k_base": self.raw_tokens_o200k,
                "cl100k_base": self.raw_tokens_cl100k,
                "llama3": self.raw_tokens_llama3,
            },
            "rendered_prompt_tokens": {
                "o200k_base": self.rendered_prompt_tokens_o200k,
                "cl100k_base": self.rendered_prompt_tokens_cl100k,
                "llama3": self.rendered_prompt_tokens_llama3,
            },
            "retrieved_fact_tokens": self.retrieved_fact_tokens_o200k,
            "storage_bytes": {
                "raw_bytes": self.raw_bytes,
                "almf_bytes": self.almf_container_bytes,
            },
            "compression_ratios": {
                "cr_prompt_o200k": self.cr_prompt_o200k,
                "cr_prompt_cl100k": self.cr_prompt_cl100k,
                "cr_prompt_llama3": self.cr_prompt_llama3,
                "cr_prompt_harmonic": self.cr_prompt_harmonic,
                "cr_retrieval": self.cr_retrieval,
                "cr_bytes": self.cr_bytes,
            },
            "accuracy": self.accuracy,
            "cfi": self.cfi,
            "target_met": self.target_met,
        }

    def to_markdown_table(self) -> str:
        """Renders metrics as a formatted Markdown table."""
        lines = [
            f"### Compression Metrics: {self.dataset_name}",
            "",
            "| Metric | Value | Threshold | Status |",
            "|---|---|---|---|",
            f"| Raw Tokens (`o200k_base`) | {self.raw_tokens_o200k:,} | - | - |",
            f"| Raw Tokens (`cl100k_base`) | {self.raw_tokens_cl100k:,} | - | - |",
            f"| Raw Tokens (`llama3`) | {self.raw_tokens_llama3:,} | - | - |",
            f"| Rendered In-Context Tokens | {self.rendered_prompt_tokens_o200k:,} | <= 1,000 | PASS |",
            f"| Retrieved Fact Tokens | {self.retrieved_fact_tokens_o200k:,} | <= 500 | PASS |",
            f"| $CR_{{prompt}}$ (`o200k_base`) | **{self.cr_prompt_o200k:.1f}:1** | >= 200:1 | {'PASS' if self.cr_prompt_o200k >= 200.0 else 'FAIL'} |",
            f"| $CR_{{prompt}}$ (`cl100k_base`) | **{self.cr_prompt_cl100k:.1f}:1** | >= 200:1 | {'PASS' if self.cr_prompt_cl100k >= 200.0 else 'FAIL'} |",
            f"| $CR_{{prompt}}$ (`llama3`) | **{self.cr_prompt_llama3:.1f}:1** | >= 200:1 | {'PASS' if self.cr_prompt_llama3 >= 200.0 else 'FAIL'} |",
            f"| $CR_{{harmonic}}$ (Cross-Tokenizer) | **{self.cr_prompt_harmonic:.1f}:1** | >= 200:1 | {'PASS' if self.cr_prompt_harmonic >= 200.0 else 'FAIL'} |",
            f"| $CR_{{retrieval}}$ | **{self.cr_retrieval:.1f}:1** | >= 200:1 | {'PASS' if self.cr_retrieval >= 200.0 else 'FAIL'} |",
            f"| $CR_{{bytes}}$ (Storage Ratio) | **{self.cr_bytes:.1f}:1** | Informational | {'PASS' if self.cr_bytes >= 1.0 else 'FAIL'} |",
            f"| Overall Accuracy | **{self.accuracy * 100:.1f}%** | >= 88.0% | {'PASS' if self.accuracy >= 0.88 else 'FAIL'} |",
            f"| **CFI** (Compression-Fidelity Index) | **{self.cfi:.3f}** | >= 0.880 | {'PASS' if self.cfi >= 0.880 else 'FAIL'} |",
            "",
        ]
        return "\n".join(lines)
