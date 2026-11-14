"""
almf.parser: Ingestion Parsers for Living Wiki Markdown, Python AST, and Trace Logs.

Implements:
1. Living Wiki & Markdown Parser:
   - Parses bilingual (Latin & Cyrillic) Markdown memory documents (MEMORY.md, memory/*.md, ADRs).
   - Extracts typed entities, facts, rules, decisions, gotchas, and topic pointer references.
2. Python Code AST Parser:
   - Extracts classes, functions, signatures, docstrings, type annotations, and module hierarchy.
   - Discards internal procedural bodies to generate high-density structural AST skeletons.
3. Trace / Log Parser:
   - Extracts invariant triples ("Problem -> Root Cause -> Fix") from verbose logs and stacktraces.
   - Collapses procedural trial-and-error traces by 99.9% into concise actionable invariants.
4. Unified Directory & File Parser:
   - Ingests files or directories into an indexed, compressed MemoryGraph using SmartCompressionRouter.
"""

from __future__ import annotations

import ast
import os
import re
from typing import Any, Dict, List, Optional, Tuple, Union

from almf.cluster import (
    HierarchicalAggregator,
    SimHashClusterer,
    TemplateLogClusterer,
)
from almf.router import SmartCompressionRouter
from almf.types import (
    CompressionModality,
    Entity,
    EntityType,
    EpisodicNode,
    Hyperedge,
    MemoryGraph,
    VaultPointer,
)
from almf.vault import BlobVault


# =============================================================================
# 1. Living Wiki & Markdown Parser (Bilingual Latin & Cyrillic)
# =============================================================================

# Header mappings to EntityType and Semantic Scope
_CYRILLIC_HEADER_MAP = {
    "о пользователе": (EntityType.ACTOR, "user_profile"),
    "проект и стек": (EntityType.ARTIFACT, "project_stack"),
    "решения": (EntityType.DECISION, "architecture_decisions"),
    "грабли": (EntityType.CONSTRAINT, "gotchas_and_pitfalls"),
    "открытые вопросы": (EntityType.CONCEPT, "open_questions"),
    "правила": (EntityType.CONSTRAINT, "rules_and_invariants"),
    "архитектура": (EntityType.DECISION, "architecture"),
    "память": (EntityType.CONCEPT, "memory_root"),
}

_LATIN_HEADER_MAP = {
    "user": (EntityType.ACTOR, "user_profile"),
    "about user": (EntityType.ACTOR, "user_profile"),
    "project": (EntityType.ARTIFACT, "project_stack"),
    "stack": (EntityType.ARTIFACT, "project_stack"),
    "decisions": (EntityType.DECISION, "architecture_decisions"),
    "adr": (EntityType.DECISION, "architecture_decisions"),
    "gotchas": (EntityType.CONSTRAINT, "gotchas_and_pitfalls"),
    "pitfalls": (EntityType.CONSTRAINT, "gotchas_and_pitfalls"),
    "open questions": (EntityType.CONCEPT, "open_questions"),
    "rules": (EntityType.CONSTRAINT, "rules_and_invariants"),
    "constraints": (EntityType.CONSTRAINT, "rules_and_invariants"),
    "architecture": (EntityType.DECISION, "architecture"),
}

_TOPIC_POINTER_RE = re.compile(
    r"^[-*]\s*(.+?)\s*(?:—|--|->|:)\s*(memory/[\w\-./]+\.md)\s*$",
    re.IGNORECASE | re.UNICODE,
)


def _classify_header(header_text: str) -> Tuple[EntityType, str]:
    """Classifies a Markdown header into EntityType and scope."""
    clean = re.sub(r"^#+\s*", "", header_text).strip().lower()
    clean = re.sub(r"[\d\.\-:]+", "", clean).strip()

    for k, v in _CYRILLIC_HEADER_MAP.items():
        if k in clean:
            return v
    for k, v in _LATIN_HEADER_MAP.items():
        if k in clean:
            return v

    return EntityType.CONCEPT, clean.replace(" ", "_") if clean else "general"


def parse_markdown(
    content: str,
    filename: str = "MEMORY.md",
    graph: Optional[MemoryGraph] = None,
    router: Optional[SmartCompressionRouter] = None,
    epoch: int = 0,
    enable_clustering: bool = False,
    aggregate: bool = False,
    file_path: Optional[str] = None,
) -> MemoryGraph:
    """
    Parses a Markdown document (such as Living Wiki MEMORY.md, memory/*.md, or ADRs).
    Extracts user preferences, decisions, rules, gotchas, configurations, and topic pointers.
    """
    if file_path is not None and filename == "MEMORY.md":
        filename = os.path.basename(file_path)

    if graph is None:
        graph = MemoryGraph()
    if not content or not content.strip():
        return graph
    if router is None:
        vault = graph.vault if graph.vault is not None else BlobVault()
        router = SmartCompressionRouter(vault=vault)
    graph.vault = router.vault

    current_type = EntityType.CONCEPT
    current_scope = "root"
    current_section_title = "General"

    doc_entity = graph.add_entity(
        name=os.path.basename(filename),
        entity_type=EntityType.ARTIFACT,
        attributes={"path": filename, "source": "markdown"},
    )

    lines = content.splitlines()
    fact_counter = 0

    for line_num, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue

        # Header detection
        if stripped.startswith("#"):
            current_section_title = stripped.lstrip("#").strip()
            current_type, current_scope = _classify_header(stripped)
            # Add section entity
            sec_entity = graph.add_entity(
                name=f"{doc_entity.name}::{current_scope}",
                entity_type=current_type,
                attributes={"section": current_section_title, "scope": current_scope},
            )
            graph.add_hyperedge(
                subject_id=doc_entity.id,
                predicate="contains_section",
                object_id=sec_entity.id,
                scope=current_scope,
                epoch=epoch,
            )
            continue

        # Topic pointer detection (e.g. `- Архитектура и слои — memory/architecture.md`)
        ptr_match = _TOPIC_POINTER_RE.match(stripped)
        if ptr_match:
            topic_label = ptr_match.group(1).strip()
            pointer_path = ptr_match.group(2).strip()
            topic_ent = graph.add_entity(
                name=topic_label,
                entity_type=EntityType.CONCEPT,
                attributes={"cold_pointer": pointer_path, "scope": current_scope},
            )
            graph.add_hyperedge(
                subject_id=doc_entity.id,
                predicate="points_to_topic",
                object_id=topic_ent.id,
                scope=current_scope,
                epoch=epoch,
            )
            continue

        # Bullet point or content line
        if stripped.startswith(("-", "*", ">", "!")):
            body = re.sub(r"^[-*>!]\s*", "", stripped).strip()
            if not body:
                continue

            fact_counter += 1

            # Check if line contains exact secret/token or regex -> vault candidate
            routed = router.route_and_compress(body, modality=router.classify_modality(body))
            if routed.vault_pointer:
                graph.add_vault_pointer(routed.vault_pointer)
                fact_text = routed.compressed_text
            else:
                fact_text = body

            # Invariant rule extraction: `!RULE: ...` or `!CONSTRAINT: ...`
            if stripped.startswith("!"):
                rule_ent = graph.add_entity(
                    name=f"rule_{doc_entity.name}_{fact_counter}",
                    entity_type=EntityType.CONSTRAINT,
                    attributes={"rule_text": fact_text, "scope": current_scope},
                )
                graph.add_hyperedge(
                    subject_id=doc_entity.id,
                    predicate="enforces_rule",
                    object_id=rule_ent.id,
                    scope=current_scope,
                    epoch=epoch,
                    confidence=1.0,
                )
                continue

            # Standard proposition / fact line
            # Extract possible key-value (e.g. "port: 8080" or "backend = .NET")
            kv_match = re.match(r"^([a-zA-Z0-9_\-\s]{2,30})\s*[:=]\s*(.+)$", fact_text)
            if kv_match and len(kv_match.group(1).split()) <= 4:
                k = kv_match.group(1).strip().replace(" ", "_").lower()
                v = kv_match.group(2).strip()
                # Check if value is a vault candidate
                if router._is_vault_candidate(v) or router.classify_modality(v) == CompressionModality.LOSSLESS_VAULT:
                    symbol, ptr = router.compress_vault(v, name=k)
                    graph.add_vault_pointer(ptr)
                    v = symbol
                    fact_text = f"{k}: {v}"
                fact_ent = graph.add_entity(
                    name=f"{current_scope}::{k}",
                    entity_type=current_type,
                    attributes={k: v, "raw": fact_text},
                )
                graph.add_hyperedge(
                    subject_id=doc_entity.id,
                    predicate="configures",
                    object_id=fact_ent.id,
                    scope=current_scope,
                    epoch=epoch,
                )
            else:
                fact_ent = graph.add_entity(
                    name=f"{current_scope}_fact_{fact_counter}",
                    entity_type=current_type,
                    attributes={"fact_text": fact_text, "line": line_num},
                )
                graph.add_hyperedge(
                    subject_id=doc_entity.id,
                    predicate="records_fact",
                    object_id=fact_ent.id,
                    scope=current_scope,
                    epoch=epoch,
                    confidence=0.95,
                )

    # Add an episodic summary node for the document
    graph.add_episodic_node(
        level=1,
        summary=f"Ingested markdown document {filename} ({fact_counter} facts extracted)",
        turn_range=(epoch, epoch),
        entity_ids=[doc_entity.id],
    )

    if aggregate:
        HierarchicalAggregator().aggregate(graph)

    return graph


# =============================================================================
# 2. Python Source Code AST Parser
# =============================================================================

class _ASTSkeletonExtractor(ast.NodeVisitor):
    """
    Extracts structural definitions (classes, functions, methods, docstrings, type annotations)
    while discarding procedural loops, branches, and function bodies.
    """

    def __init__(self, module_name: str, graph: MemoryGraph, epoch: int = 0) -> None:
        self.module_name = module_name
        self.graph = graph
        self.epoch = epoch
        self.module_entity = graph.add_entity(
            name=module_name,
            entity_type=EntityType.ARTIFACT,
            attributes={"type": "python_module"},
        )
        self.current_class: Optional[Entity] = None

    def _format_args(self, args: ast.arguments) -> str:
        """Formats function argument signatures with type annotations and default values."""
        parts = []
        posonly = getattr(args, "posonlyargs", [])
        all_pos = posonly + args.args
        num_defaults = len(args.defaults)
        first_default_idx = len(all_pos) - num_defaults

        # 1. Positional-only arguments
        for idx, a in enumerate(posonly):
            name = a.arg
            s = f"{name}: {ast.unparse(a.annotation)}" if a.annotation and hasattr(ast, "unparse") else name
            if idx >= first_default_idx:
                def_node = args.defaults[idx - first_default_idx]
                def_str = ast.unparse(def_node) if hasattr(ast, "unparse") else "..."
                s += f" = {def_str}"
            parts.append(s)
        if posonly:
            parts.append("/")

        # 2. Standard arguments
        offset = len(posonly)
        for idx, a in enumerate(args.args):
            global_idx = offset + idx
            name = a.arg
            s = f"{name}: {ast.unparse(a.annotation)}" if a.annotation and hasattr(ast, "unparse") else name
            if global_idx >= first_default_idx:
                def_node = args.defaults[global_idx - first_default_idx]
                def_str = ast.unparse(def_node) if hasattr(ast, "unparse") else "..."
                s += f" = {def_str}"
            parts.append(s)

        # 3. Vararg or bare star for kwonlyargs
        if args.vararg:
            parts.append(f"*{args.vararg.arg}")
        elif getattr(args, "kwonlyargs", None):
            parts.append("*")

        # 4. Keyword-only arguments
        for idx, a in enumerate(getattr(args, "kwonlyargs", [])):
            name = a.arg
            s = f"{name}: {ast.unparse(a.annotation)}" if a.annotation and hasattr(ast, "unparse") else name
            if idx < len(args.kw_defaults) and args.kw_defaults[idx] is not None:
                def_str = ast.unparse(args.kw_defaults[idx]) if hasattr(ast, "unparse") else "..."
                s += f" = {def_str}"
            parts.append(s)

        # 5. Kwarg
        if args.kwarg:
            parts.append(f"**{args.kwarg.arg}")

        return ", ".join(parts)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        doc = ast.get_docstring(node) or ""
        bases = [ast.unparse(b) if hasattr(ast, "unparse") else getattr(b, "id", "object") for b in node.bases]
        bases_str = f"({', '.join(bases)})" if bases else ""

        class_name = f"{self.module_name}.{node.name}"
        class_entity = self.graph.add_entity(
            name=class_name,
            entity_type=EntityType.CONCEPT,
            attributes={
                "kind": "class",
                "bases": bases,
                "docstring": doc.strip()[:150],
                "lineno": node.lineno,
                "signature": f"class {node.name}{bases_str}",
            },
        )
        self.graph.add_hyperedge(
            subject_id=self.module_entity.id,
            predicate="defines_class",
            object_id=class_entity.id,
            scope="ast_skeleton",
            epoch=self.epoch,
        )

        prev_class = self.current_class
        self.current_class = class_entity
        self.generic_visit(node)
        self.current_class = prev_class

    def visit_FunctionDef(self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef]) -> None:
        self._handle_function(node, is_async=isinstance(node, ast.AsyncFunctionDef))

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._handle_function(node, is_async=True)

    def _handle_function(
        self,
        node: Union[ast.FunctionDef, ast.AsyncFunctionDef],
        is_async: bool = False,
    ) -> None:
        doc = ast.get_docstring(node) or ""
        args_str = self._format_args(node.args)
        ret_str = ""
        if node.returns:
            ret_str = f" -> {ast.unparse(node.returns) if hasattr(ast, 'unparse') else 'Any'}"

        prefix = "async def " if is_async else "def "
        sig = f"{prefix}{node.name}({args_str}){ret_str}"

        if self.current_class is not None:
            fn_name = f"{self.current_class.name}.{node.name}"
            parent_id = self.current_class.id
            predicate = "defines_method"
        else:
            fn_name = f"{self.module_name}.{node.name}"
            parent_id = self.module_entity.id
            predicate = "defines_function"

        fn_entity = self.graph.add_entity(
            name=fn_name,
            entity_type=EntityType.CONCEPT,
            attributes={
                "kind": "method" if self.current_class else "function",
                "signature": sig,
                "docstring": doc.strip()[:150],
                "lineno": node.lineno,
                "is_async": is_async,
            },
        )
        self.graph.add_hyperedge(
            subject_id=parent_id,
            predicate=predicate,
            object_id=fn_entity.id,
            scope="ast_skeleton",
            epoch=self.epoch,
        )
        # Note: we do NOT visit internal body nodes (statements, loops, assignments)
        # to enforce pure structural AST skeleton extraction.


def parse_python_ast(
    source_code: str,
    module_name: str = "module",
    graph: Optional[MemoryGraph] = None,
    epoch: int = 0,
    aggregate: bool = False,
    file_path: Optional[str] = None,
) -> MemoryGraph:
    """
    Parses Python source code into structural AST skeletons.
    Extracts module docstring, classes, method/function signatures, and type contracts.
    """
    if file_path is not None and module_name == "module":
        module_name = os.path.splitext(os.path.basename(file_path))[0]

    if graph is None:
        graph = MemoryGraph()

    try:
        tree = ast.parse(source_code)
    except (SyntaxError, ValueError) as e:
        # Gracefully handle syntax errors by recording an ERROR entity
        err_ent = graph.add_entity(
            name=f"{module_name}::SyntaxError",
            entity_type=EntityType.ERROR,
            attributes={"error": str(e), "lineno": getattr(e, "lineno", 0) or 0},
        )
        mod_ent = graph.add_entity(name=module_name, entity_type=EntityType.ARTIFACT)
        graph.add_hyperedge(
            subject_id=mod_ent.id,
            predicate="has_syntax_error",
            object_id=err_ent.id,
            scope="ast_skeleton",
            epoch=epoch,
        )
        return graph

    extractor = _ASTSkeletonExtractor(module_name=module_name, graph=graph, epoch=epoch)
    mod_doc = ast.get_docstring(tree)
    if mod_doc:
        extractor.module_entity.attributes["docstring"] = mod_doc.strip()[:200]

    extractor.visit(tree)

    # Episodic summary of parsed code
    cls_count = sum(1 for e in graph.entities.values() if e.attributes.get("kind") == "class")
    fn_count = sum(1 for e in graph.entities.values() if e.attributes.get("kind") in ("function", "method"))
    graph.add_episodic_node(
        level=1,
        summary=f"AST skeleton for {module_name}: {cls_count} classes, {fn_count} functions/methods extracted",
        turn_range=(epoch, epoch),
        entity_ids=[extractor.module_entity.id],
    )

    if aggregate:
        HierarchicalAggregator().aggregate(graph)

    return graph


# Interface alias
parse_ast = parse_python_ast


# =============================================================================
# 3. Trace & Log Parser (Problem -> Root Cause -> Fix Invariants)
# =============================================================================

_ERROR_SIGNATURES = [
    r"Traceback \(most recent call last\):.*?(?:([a-zA-Z0-9_\.]+(?:Error|Exception)): ([^\r\n]+))",
    r"(?:npm ERR!|fatal:|ERROR:|Error:)\s*([^\r\n]+)",
    r"FAILED\s*\((?:failures|errors)=\d+\)",
    r"(?:AssertionError|KeyError|TypeError|ValueError|RuntimeError|IndexError):\s*([^\r\n]+)",
    r"BUILD FAILED:\s*([^\r\n]+)",
]


def parse_trace_log(
    log_text: str,
    trace_id: str = "trace",
    graph: Optional[MemoryGraph] = None,
    router: Optional[SmartCompressionRouter] = None,
    epoch: int = 0,
    enable_clustering: bool = True,
    aggregate: bool = True,
) -> MemoryGraph:
    """
    Parses verbose debugging traces, multi-turn tool logs, and compiler errors.
    Strips procedural output and extracts the final invariant:
      Problem -> Root Cause -> Fix
    """
    if graph is None:
        graph = MemoryGraph()
    if not log_text or not log_text.strip():
        return graph
    if router is None:
        vault = graph.vault if graph.vault is not None else BlobVault()
        router = SmartCompressionRouter(vault=vault)
    graph.vault = router.vault

    trace_entity = graph.add_entity(
        name=trace_id,
        entity_type=EntityType.ARTIFACT,
        attributes={"type": "execution_trace", "original_chars": len(log_text)},
    )

    # 1. Compress trace using SmartCompressionRouter
    invariant_stmt = router.compress_trace(log_text, epoch=epoch)

    # 2. Extract Problem / Trigger
    error_match = None
    for pattern in _ERROR_SIGNATURES:
        m = re.search(pattern, log_text, re.DOTALL | re.MULTILINE)
        if m:
            error_match = m
            break

    if error_match:
        err_msg = error_match.group(0).splitlines()[-1].strip()[:120]
    else:
        err_msg = "Command failure or execution anomaly"

    problem_ent = graph.add_entity(
        name=f"{trace_id}_error",
        entity_type=EntityType.ERROR,
        attributes={"error_message": err_msg, "invariant": invariant_stmt},
    )

    # 3. Extract Command or Action Attempted
    cmd = "action"
    for line in log_text.splitlines():
        clean_l = line.strip()
        if clean_l.startswith(("$", ">", "Running", "Command:")):
            cmd = clean_l.lstrip("$> ").replace("Command:", "").strip()[:80]
            break

    # 4. Extract Fix or Outcome
    outcome = (
        "RESOLVED_SUCCESS"
        if any(w in log_text for w in ["PASSED", "SUCCESS", "Fixed"]) or bool(re.search(r"\bOK\b", log_text))
        else "UNRESOLVED_ERROR"
    )

    fix_ent = graph.add_entity(
        name=f"{trace_id}_resolution",
        entity_type=EntityType.DECISION,
        attributes={"command": cmd, "outcome": outcome},
    )

    # Extract explicit root cause and fix details if present
    root_cause_m = re.search(r"Root cause:\s*([^\n\r]+)", log_text, re.IGNORECASE)
    if root_cause_m:
        problem_ent.attributes["root_cause"] = root_cause_m.group(1).strip()
    fix_m = re.search(r"Fix (?:applied|deployed|resolution):\s*([^\n\r]+)", log_text, re.IGNORECASE)
    if fix_m:
        fix_ent.attributes["fix"] = fix_m.group(1).strip()

    # Extract explicit bulleted invariant items or needles
    for line in log_text.splitlines():
        s = line.strip()
        if s.startswith("- ") and ":" in s and len(s) < 300:
            parts = s[2:].split(":", 1)
            k = parts[0].strip().replace(" ", "_")
            v = parts[1].strip()
            ent = graph.add_entity(
                name=f"{trace_id}::{k}",
                entity_type=EntityType.DECISION,
                attributes={k: v, "raw": s},
            )
            graph.add_hyperedge(
                subject_id=trace_entity.id,
                predicate="records_invariant",
                object_id=ent.id,
                scope="trace_invariant",
                epoch=epoch,
            )

    # Connect Problem and Resolution
    graph.add_hyperedge(
        subject_id=trace_entity.id,
        predicate="encountered_error",
        object_id=problem_ent.id,
        scope="trace_invariant",
        epoch=epoch,
    )
    graph.add_hyperedge(
        subject_id=problem_ent.id,
        predicate="resolves_with",
        object_id=fix_ent.id,
        scope="trace_invariant",
        epoch=epoch,
    )

    # Add episodic summary
    graph.add_episodic_node(
        level=0,
        summary=f"Trace {trace_id}: {cmd} -> {outcome} ({err_msg})",
        turn_range=(epoch, epoch),
        entity_ids=[trace_entity.id, problem_ent.id, fix_ent.id],
    )

    # 5. Log Line Template Clustering (Drain-style O(N))
    lines = [line.strip() for line in log_text.splitlines() if line.strip()]
    if enable_clustering and len(lines) > 2:
        clusterer = TemplateLogClusterer()
        cluster_records = clusterer.cluster_lines(lines)
        for rec in cluster_records:
            c_key = f"{trace_id}_template_{rec.cluster_id}"
            graph.clusters[c_key] = rec.member_ids
            if rec.count >= 2:
                cluster_ent = graph.add_entity(
                    name=f"{trace_id}::cluster_{rec.cluster_id}",
                    entity_type=EntityType.ERROR if "error" in rec.template.lower() else EntityType.CONCEPT,
                    attributes={
                        "template": rec.template,
                        "count": rec.count,
                        "sample_params": rec.parameters[:3] if rec.parameters else [],
                    },
                )
                graph.add_hyperedge(
                    subject_id=trace_entity.id,
                    predicate="has_log_cluster",
                    object_id=cluster_ent.id,
                    scope="trace_cluster",
                    epoch=epoch,
                )

    # 6. Hierarchical Aggregation (Synthesize L0/L1 MacroPropositions)
    if aggregate:
        HierarchicalAggregator().aggregate(graph)

    return graph


# =============================================================================
# 4. Unified File & Directory Ingestion Orchestrator
# =============================================================================

def parse_file(
    filepath: str,
    modality: str = "auto",
    graph: Optional[MemoryGraph] = None,
    router: Optional[SmartCompressionRouter] = None,
    epoch: int = 0,
) -> MemoryGraph:
    """
    Parses a single file into MemoryGraph based on extension and modality hint.
    """
    if graph is None:
        graph = MemoryGraph()

    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    fname = os.path.basename(filepath)
    ext = os.path.splitext(fname)[1].lower()

    if modality == "code" or ext in (".py", ".pyw"):
        mod_name = os.path.splitext(fname)[0]
        return parse_python_ast(content, module_name=mod_name, graph=graph, epoch=epoch)
    elif modality == "trace" or ext in (".log", ".trace", ".txt") and ("Traceback" in content or "ERROR" in content):
        trace_id = os.path.splitext(fname)[0]
        return parse_trace_log(content, trace_id=trace_id, graph=graph, router=router, epoch=epoch)
    else:
        # Default Markdown / living wiki parser
        return parse_markdown(content, filename=fname, graph=graph, router=router, epoch=epoch)


def parse_directory(
    dirpath: str,
    modality: str = "auto",
    graph: Optional[MemoryGraph] = None,
    router: Optional[SmartCompressionRouter] = None,
    aggregate: bool = True,
) -> MemoryGraph:
    """
    Recursively scans and parses a directory of Markdown documents, source code, and logs.
    """
    if graph is None:
        graph = MemoryGraph()
    if router is None:
        vault = graph.vault if graph.vault is not None else BlobVault()
        router = SmartCompressionRouter(vault=vault)
    graph.vault = router.vault

    epoch = 0
    for root, _, files in os.walk(dirpath):
        for f in files:
            if f.endswith((".md", ".py", ".log", ".txt", ".json", ".yaml", ".yml")):
                fpath = os.path.join(root, f)
                epoch += 1
                try:
                    parse_file(fpath, modality=modality, graph=graph, router=router, epoch=epoch)
                except Exception as err:
                    # Log error in graph rather than crashing
                    err_ent = graph.add_entity(
                        name=f"parse_error_{f}",
                        entity_type=EntityType.ERROR,
                        attributes={"file": fpath, "error": str(err)},
                    )
                    graph.add_hyperedge(
                        subject_id=1,
                        predicate="failed_to_parse",
                        object_id=err_ent.id,
                        epoch=epoch,
                    )

    if aggregate:
        HierarchicalAggregator().aggregate(graph)

    return graph
