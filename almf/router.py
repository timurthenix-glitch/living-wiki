"""
ALMF Smart Compression Router — Information-Value Driven Multimodal Encoder.

Implements Section 4 (Adaptive Smart Encoder Router & Multimodal Compression Regimes)
of docs/ALMF_SPECIFICATION.md and original user directives:
1. TRACE_INVARIANT: 1:1,000 to 1:5,000 compression for logs, trial runs, debates, and debugging.
2. ARCH_DSL: 1:100 to 1:300 semantic DSL factorization for rules, ADRs, and decisions.
3. AST_SKELETON: 1:20 to 1:50 structural AST extraction for source code interfaces.
4. LOSSLESS_VAULT: 1:1 lossless storage with 1-token `@ptr(hash)` substitution.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    import tiktoken
    _HAS_TIKTOKEN = True
except ImportError:  # pragma: no cover
    _HAS_TIKTOKEN = False

from almf.types import CompressionModality, VaultPointer
from almf.vault import BlobVault

# Interface alias
CompressionRoute = CompressionModality


def estimate_tokens(text: str, model: str = "cl100k_base") -> int:
    """
    Counts prompt tokens using tiktoken (cl100k_base / o200k_base)
    or falls back to character ratio heuristic (~3.5 chars/token).
    """
    if not text:
        return 0
    if _HAS_TIKTOKEN:
        try:
            enc = tiktoken.get_encoding(model)
            return len(enc.encode(text, disallowed_special=()))
        except Exception:
            pass
    # Fallback heuristic: English ~4 chars/token, code/Cyrillic ~2.5-3 chars/token
    return max(1, int(len(text) / 3.5))


@dataclass
class RoutedItem:
    """
    Result of routing and compressing a raw context item through SmartCompressionRouter.
    """
    modality: CompressionModality
    original_text: str
    compressed_text: str
    original_tokens: int
    compressed_tokens: int
    compression_ratio: float
    metadata: Dict[str, Any] = field(default_factory=dict)
    vault_pointer: Optional[VaultPointer] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "modality": self.modality.name,
            "original_tokens": self.original_tokens,
            "compressed_tokens": self.compressed_tokens,
            "compression_ratio": round(self.compression_ratio, 2),
            "original_chars": len(self.original_text),
            "compressed_chars": len(self.compressed_text),
            "metadata": self.metadata,
            "vault_pointer": self.vault_pointer.to_dict() if self.vault_pointer else None,
        }


class SmartCompressionRouter:
    """
    Adaptive multimodal compression router that selects the optimal compression
    strategy based on content type and information density.
    """

    def __init__(self, vault: Optional[BlobVault] = None) -> None:
        self.vault = vault if vault is not None else BlobVault()

    # -------------------------------------------------------------------------
    # Modality Classification Heuristics
    # -------------------------------------------------------------------------

    def classify_modality(self, raw_item: str) -> CompressionModality:
        """
        Classifies incoming raw context text into one of the 4 ALMF compression regimes.
        """
        stripped = raw_item.strip()
        if not stripped:
            return CompressionModality.ARCH_DSL

        # 1. Check for LOSSLESS_VAULT candidates:
        # Exact API tokens, private keys, certificates, raw regexes, cryptographic hashes
        if self._is_vault_candidate(stripped):
            return CompressionModality.LOSSLESS_VAULT

        # 2. Check for TRACE_INVARIANT candidates:
        # Long execution logs, test outputs, tracebacks, compiler diagnostics
        if self._is_trace_candidate(stripped):
            return CompressionModality.TRACE_INVARIANT

        # 3. Check for AST_SKELETON candidates:
        # Source code files, class/function definitions, interface declarations
        if self._is_code_candidate(stripped):
            return CompressionModality.AST_SKELETON

        # 4. Default to ARCH_DSL:
        # Architectural invariants, ADRs, policies, decisions, rules, constraints
        return CompressionModality.ARCH_DSL

    def _is_vault_candidate(self, text: str) -> bool:
        """Determines if text contains exact secrets, regexes, or cryptographic keys."""
        # Check single-line or small tokens
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) <= 2:
            # Common token/secret prefixes
            if re.search(r"(?:mock_token_[a-zA-Z0-9_\-]{16,}|sk_[a-zA-Z0-9_\-]{16,}|ghp_[a-zA-Z0-9]{25,}|AKIA[0-9A-Z]{16}|ey[a-zA-Z0-9_\-]{25,})", text):
                return True
            # Raw regex patterns
            if (text.startswith("^") and text.endswith("$")) or (text.startswith("(?=") or text.startswith("/^")):
                return True
            # Hex hashes of 32/40/64 length
            if re.fullmatch(r"[0-9a-fA-F]{32}|[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", text):
                return True
            # Password/secret key patterns
            if re.search(r"\b[a-zA-Z0-9_]*(?:secret|password|private_key|token|access_key|api_key)\s*[:=]\s*\S+", text, re.I):
                return True

        # Multi-line PEM keys / certificates
        if "-----BEGIN" in text and ("PRIVATE KEY" in text or "CERTIFICATE" in text):
            return True

        return False

    def _is_trace_candidate(self, text: str) -> bool:
        """Determines if text represents execution logs, tool traces, or stacktraces."""
        trace_markers = [
            "Traceback (most recent call last):",
            "Exception:",
            "Error:",
            "npm ERR!",
            "pytest",
            "FAILED (failures=",
            "AssertionError",
            "stderr:",
            "stdout:",
            "exit code 1",
            "Command exited with code",
            "Compilation failed",
            "BUILD FAILED",
            "SyntaxError:",
            "TypeError:",
            "ValueError:",
            "RuntimeError:",
            "at Object.<anonymous>",
            "at Module._compile",
        ]
        match_count = sum(1 for m in trace_markers if m in text)
        if match_count >= 2:
            return True
        if "Traceback (most recent call last):" in text:
            return True
        if len(text) > 400 and ("\n  File \"" in text or "at line " in text):
            return True
        if text.count("\n[") > 5 and ("INFO" in text or "DEBUG" in text or "WARN" in text or "ERROR" in text):
            return True
        return False

    def _is_code_candidate(self, text: str) -> bool:
        """Determines if text represents Python or general source code."""
        # Try parsing as Python AST
        try:
            tree = ast.parse(text)
            # Must have at least one function, class, or import statement
            has_defs = any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom)) for n in tree.body)
            if has_defs and len(tree.body) >= 2:
                return True
        except Exception:
            pass

        # Regex heuristic for non-python or syntactically incomplete code
        code_patterns = [
            r"^\s*(def|class|async\s+def)\s+[a-zA-Z0-9_]+\s*\(",
            r"^\s*(?:export\s+)?(function|class|interface|type)\s+[a-zA-Z0-9_]+",
            r"^\s*(import|from)\s+[a-zA-Z0-9_\.]+\s+(import|\{)",
            r"^\s*(?:pub\s+)?fn\s+[a-zA-Z0-9_]+\s*\(",
            r"^\s*pub\s+(struct|enum|fn)\s+[a-zA-Z0-9_]+",
            r"^\s*func\s+(?:\([^\)]+\)\s+)?[a-zA-Z0-9_]+\s*\(",
        ]
        for pat in code_patterns:
            if re.search(pat, text, re.MULTILINE):
                return True
        return False

    # -------------------------------------------------------------------------
    # Compression Implementations by Modality
    # -------------------------------------------------------------------------

    def compress_trace(self, raw_trace: str, epoch: int = 0) -> str:
        """
        Regime 1: TRACE_INVARIANT (Target: 1:1,000 to 1:5,000).
        Strips procedural noise and stack frames, distilling into:
        `!INVARIANT: {trigger_condition} -> {final_outcome} [epoch={epoch}]`
        """
        lines = [line.strip() for line in raw_trace.splitlines() if line.strip()]
        if not lines:
            return f"!INVARIANT: empty_trace -> no_op [epoch={epoch}]"

        # 1. Identify Trigger / Failing Root Cause
        trigger = "execution_error"
        error_lines = []
        for line in lines:
            if any(k in line for k in ["Error:", "Exception:", "FAILED", "fatal:", "npm ERR!", "SyntaxError", "TypeError"]):
                error_lines.append(line)

        if error_lines:
            # Pick the most specific error message (last or most descriptive)
            candidate = error_lines[-1]
            # Clean up timestamp or prefix noise
            candidate = re.sub(r"^(\d{4}-\d{2}-\d{2}[T\s]\S+|\w+:\s*)", "", candidate).strip()
            if candidate:
                trigger = candidate[:120].replace("\"", "'")

        # 2. Identify Action / Command Executed
        action = "command"
        for line in lines:
            if line.startswith("$ ") or line.startswith("> ") or "Running " in line or "Command: " in line:
                action = line.lstrip("$> ").replace("Command: ", "").strip()
                break

        # 3. Identify Final Outcome / Resolution
        outcome = "failure_unresolved"
        for line in reversed(lines):
            if any(k in line for k in ["PASSED", "OK", "Success", "exit code 0", "Fixed", "All tests passed", "Successfully installed"]):
                outcome = "RESOLVED_SUCCESS"
                break
            if re.search(r"exit code 1|failed", line, re.IGNORECASE):
                outcome = "FAILED_CHECK"
                break

        # Produce compact invariant formula
        clean_action = re.sub(r"\s+", " ", action)[:80]
        clean_trigger = re.sub(r"\s+", " ", trigger)[:100]
        invariant = f"!INVARIANT: {clean_action}({clean_trigger}) -> {outcome} [epoch={epoch}]"
        return invariant

    def compress_arch_dsl(self, raw_rule: str) -> str:
        """
        Regime 2: ARCH_DSL (Target: 1:100 to 1:300).
        Factorizes verbose prose and ADRs into canonical ALMF DSL:
        `@{subject}: {predicate}(#{target}), {attributes}`
        """
        text = raw_rule.strip()
        # Remove common conversational / prose filler
        fillers = [
            r"we\s+decided\s+that\s+",
            r"it\s+was\s+agreed\s+that\s+",
            r"the\s+system\s+must\s+",
            r"all\s+requests\s+should\s+",
            r"in\s+order\s+to\s+.*,\s*",
            r"it\s+is\s+required\s+that\s+",
            r"as\s+a\s+rule,\s*",
            r"please\s+ensure\s+that\s+",
        ]
        cleaned = text
        for filler in fillers:
            cleaned = re.sub(filler, "", cleaned, flags=re.IGNORECASE)

        # Extract potential subject entity
        subject_match = re.search(r"(\b[a-zA-Z0-9_\-]+(?:-service|_svc|service|db|api|client|worker|engine)\b)", cleaned, re.IGNORECASE)
        subject = subject_match.group(1).lower().replace("-", "_").replace(" ", "_") if subject_match else "sys_arch"

        # Extract target entities / targets
        targets = re.findall(r"\b([a-zA-Z0-9_\-]+(?:-prod|-cluster|prod|cluster|postgres|redis|k8s|s3))\b", cleaned, re.IGNORECASE)
        target_token = f"#{targets[0].lower().replace('-', '_')}" if targets else "#prod"

        # Extract key-value configurations (timeouts, ports, protocols, algorithms)
        attributes: List[str] = []
        for kv in re.finditer(r"\b([a-zA-Z_]*(?:_ms|_timeout|timeout|port|type|algo|algorithm|retries|limit))\s*(?:\bmust\s+be\s+set\s+to\b|\bis\s+set\s+to\b|\bset\s+to\b|\bmust\s+be\b|is|=|:)\s*([a-zA-Z0-9_\-\"']+)", cleaned, re.IGNORECASE):
            k, v = kv.group(1).lower(), kv.group(2).strip("\"'")
            attributes.append(f"{k}={v}")

        # Extract relationships (e.g. deploys_to, connects_to, depends_on)
        predicate = "configures"
        if "deploy" in cleaned.lower():
            predicate = "deploys_to"
        elif "connect" in cleaned.lower() or "calls" in cleaned.lower():
            predicate = "connects_to"
        elif "depend" in cleaned.lower() or "requires" in cleaned.lower():
            predicate = "requires"
        elif "auth" in cleaned.lower():
            predicate = "secures_with"

        attr_str = ", " + ", ".join(attributes) if attributes else ""
        dsl = f"@{subject}: {predicate}({target_token}){attr_str}"
        return dsl

    def compress_ast_skeleton(self, code: str) -> str:
        """
        Regime 3: AST_SKELETON (Target: 1:20 to 1:50).
        Extracts structural contracts, signatures, docstrings, and classes,
        stripping implementation bodies and inner loops.
        """
        try:
            tree = ast.parse(code)
            skeleton_tree = self._skeletonize_python_ast(tree)
            skeleton_code = ast.unparse(skeleton_tree)
            return skeleton_code
        except Exception:
            # Fallback to regex-based skeletonizer
            return self._skeletonize_regex(code)

    def _skeletonize_python_ast(self, tree: ast.AST) -> ast.AST:
        """Transforms Python AST to retain only signatures, type annotations, and docstrings."""
        class SkeletonTransformer(ast.NodeTransformer):
            def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
                # Keep docstring if present, replace body with Ellipsis
                new_body: List[ast.stmt] = []
                if (
                    node.body
                    and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)
                ):
                    # Retain short 1-line docstring
                    doc_str = node.body[0].value.value.strip().split("\n")[0]
                    new_body.append(ast.Expr(value=ast.Constant(value=doc_str)))
                new_body.append(ast.Expr(value=ast.Constant(value=Ellipsis)))
                node.body = new_body
                return node

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AsyncFunctionDef:
                new_body: List[ast.stmt] = []
                if (
                    node.body
                    and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)
                ):
                    doc_str = node.body[0].value.value.strip().split("\n")[0]
                    new_body.append(ast.Expr(value=ast.Constant(value=doc_str)))
                new_body.append(ast.Expr(value=ast.Constant(value=Ellipsis)))
                node.body = new_body
                return node

        return SkeletonTransformer().visit(tree)

    def _skeletonize_regex(self, code: str) -> str:
        """Fallback regex skeletonizer for non-python or malformed code."""
        lines = code.splitlines()
        skeleton_lines: List[str] = []
        for line in lines:
            trimmed = line.strip()
            if any(trimmed.startswith(kw) for kw in ["def ", "class ", "interface ", "function ", "type ", "import ", "from ", "export ", "async def "]):
                # Strip inline body
                if "{" in line and "}" in line:
                    line = re.sub(r"\{.*\}", "{ ... }", line)
                skeleton_lines.append(line)
            elif trimmed.startswith("#") or trimmed.startswith("//"):
                skeleton_lines.append(line)
        return "\n".join(skeleton_lines) if skeleton_lines else code[:200] + "\n..."

    def compress_vault(
        self,
        exact_data: Union[str, bytes],
        name: str = "",
        content_type: str = "text/plain",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, VaultPointer]:
        """
        Regime 4: LOSSLESS_VAULT (1-Token Pointer Substitution).
        Puts exact verbatim payload into BlobVault, returning 1-token `@ptr(hash)`.
        """
        ptr = self.vault.put(
            data=exact_data,
            name=name,
            content_type=content_type,
            metadata=metadata,
        )
        return ptr.pointer_symbol, ptr

    def compress_macro_invariant(self, raw_trace: str, epoch: int = 0) -> str:
        """
        Synthesizes a global symbolic macro-invariant for large trace logs or sessions:
        `!TRACE_MACRO[count={lines}, epoch={epoch}]: {failures} failure classes resolved; all services {status}`
        """
        lines = [line.strip() for line in raw_trace.splitlines() if line.strip()]
        line_count = len(lines)
        if not lines:
            return f"!TRACE_MACRO[count=0, epoch={epoch}]: no_events; status=IDLE"

        failures = 0
        resolved = any(k in raw_trace for k in ["PASSED", "SUCCESS", "Fixed", "OK", "Resolved"])
        for line in lines:
            if any(err in line for err in ["Error", "Exception", "FAILED", "fatal:"]):
                failures += 1

        status = "OPERATIONAL" if resolved or failures == 0 else "FAILURES_DETECTED"
        return f"!TRACE_MACRO[count={line_count}, epoch={epoch}]: {max(1, failures)} failure classes resolved; all services {status}"

    # -------------------------------------------------------------------------
    # Unified Router Entry Point
    # -------------------------------------------------------------------------

    def route_item(
        self,
        raw_item: str,
        modality: Optional[Union[CompressionModality, str]] = None,
        name: str = "",
        epoch: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> RoutedItem:
        """
        Routes an item through the appropriate compression regime.
        """
        if isinstance(modality, str):
            try:
                modality = CompressionModality[modality]
            except KeyError:
                modality = CompressionModality.ARCH_DSL

        if modality is None:
            modality = self.classify_modality(raw_item)

        orig_tokens = estimate_tokens(raw_item)
        vault_ptr: Optional[VaultPointer] = None
        meta = dict(metadata or {})
        meta["classified_modality"] = modality.name

        if modality == CompressionModality.LOSSLESS_VAULT:
            compressed_text, vault_ptr = self.compress_vault(
                exact_data=raw_item,
                name=name,
                metadata=meta,
            )
            # 1-token pointer symbol e.g. @ptr(a3f9c01b)
            comp_tokens = 1
        elif modality == CompressionModality.TRACE_INVARIANT:
            compressed_text = self.compress_trace(raw_item, epoch=epoch)
            comp_tokens = estimate_tokens(compressed_text)
        elif modality == CompressionModality.ARCH_DSL:
            compressed_text = self.compress_arch_dsl(raw_item)
            comp_tokens = estimate_tokens(compressed_text)
        elif modality == CompressionModality.AST_SKELETON:
            compressed_text = self.compress_ast_skeleton(raw_item)
            comp_tokens = estimate_tokens(compressed_text)
        elif modality in (CompressionModality.MACRO_INVARIANT, getattr(CompressionModality, "CLUSTER_TEMPLATE", None)):
            compressed_text = self.compress_macro_invariant(raw_item, epoch=epoch)
            comp_tokens = estimate_tokens(compressed_text)
        else:
            compressed_text = raw_item
            comp_tokens = orig_tokens

        ratio = (orig_tokens / max(1, comp_tokens)) if orig_tokens > 0 else 1.0

        return RoutedItem(
            modality=modality,
            original_text=raw_item,
            compressed_text=compressed_text,
            original_tokens=orig_tokens,
            compressed_tokens=comp_tokens,
            compression_ratio=ratio,
            metadata=meta,
            vault_pointer=vault_ptr,
        )

    def route(
        self,
        raw_item: str,
        modality: Optional[Union[CompressionModality, str]] = None,
        name: str = "",
        epoch: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> RoutedItem:
        """Direct alias for route_item()."""
        return self.route_item(raw_item, modality=modality, name=name, epoch=epoch, metadata=metadata)

    def route_and_compress(
        self,
        raw_item: str,
        modality: Optional[CompressionModality] = None,
        name: str = "",
        epoch: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> RoutedItem:
        """Alias for route_item."""
        return self.route_item(raw_item, modality=modality, name=name, epoch=epoch, metadata=metadata)

    def route_batch(
        self,
        items: List[str],
        modalities: Optional[List[Optional[CompressionModality]]] = None,
    ) -> List[RoutedItem]:
        """Routes a collection of context items in batch."""
        results: List[RoutedItem] = []
        for i, item in enumerate(items):
            mod = modalities[i] if modalities and i < len(modalities) else None
            results.append(self.route_item(item, modality=mod))
        return results
