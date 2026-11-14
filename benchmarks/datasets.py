"""
benchmarks.datasets: Procedural Multi-Modal Benchmark Dataset Synthesizers for ALMF.

Generates 4 realistic multi-modal datasets totaling >= 550,000 raw prompt tokens:
1. Trace Invariant (200k tokens): Verbose traces with invariant ratio >= 1000:1.
2. Bilingual Dialogue (150k tokens): Multi-turn RU/EN discussions.
3. Code & Living Wiki (100k tokens): Python AST skeletons & MEMORY.md markdown.
4. Temporal State Evolution (100k tokens): Multi-epoch configuration updates.
Deterministic (seed=42) paired with NIAH, QA, Triples, and Multi-Hop test suites.
"""

from __future__ import annotations

import os
import random
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from almf.cluster import HierarchicalAggregator
from almf.index import SimHashIndex
from almf.parser import parse_markdown, parse_python_ast, parse_trace_log
from almf.router import SmartCompressionRouter
from almf.types import CompressionModality, EntityType, MemoryGraph
from benchmarks.metrics import count_tokens


@dataclass
class NeedleItem:
    needle_id: str
    key: str
    value: str
    query: str
    depth_percent: float
    modality: str


@dataclass
class QAItem:
    question: str
    expected_answer: str
    keywords: List[str]
    modality: str


@dataclass
class TripleItem:
    subject: str
    predicate: str
    object_val: str
    modality: str


@dataclass
class MultiHopItem:
    question: str
    expected_answer: str
    hops: List[str]
    keywords: List[str]
    modality: str


@dataclass
class GroundTruthSuite:
    needles: List[NeedleItem] = field(default_factory=list)
    qa_pairs: List[QAItem] = field(default_factory=list)
    triples: List[TripleItem] = field(default_factory=list)
    multi_hops: List[MultiHopItem] = field(default_factory=list)

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "needles": [asdict(n) for n in self.needles],
            "qa_pairs": [asdict(q) for q in self.qa_pairs],
            "triples": [asdict(t) for t in self.triples],
            "multi_hops": [asdict(m) for m in self.multi_hops],
        }


# =============================================================================
# 1. Trace Dataset Generator (200k tokens, >= 1000:1 Invariant Ratio)
# =============================================================================

_TRACE_SERVICES = ["auth_service", "billing_gateway", "payment_worker", "stream_processor", "order_dispatcher", "cache_cluster", "index_daemon", "metrics_collector"]
_TRACE_ERRORS = [
    ("ConnectionRefusedError", "PostgreSQL database port 5432 closed by remote host", "Reconnection backoff exceeded max attempts in connection pool", "alembic upgrade head && systemctl restart postgresql"),
    ("DeadlockDetected", "Deadlock on table order_invoices during concurrent settlement", "Lock acquisition order mismatch between worker threads", "set transaction isolation level read committed"),
    ("MemoryQuotaExceeded", "Pod exceeded cgroup memory limit of 1024Mi during serialization", "Unbounded buffer allocation in parquet export pipeline", "export PARQUET_BATCH_SIZE=2048"),
    ("TimeoutException", "Upstream payment provider gateway response timeout after 30000ms", "Missing keep-alive probe in reverse proxy TLS session", "nginx reload -s with keepalive_timeout 65"),
    ("IndexCorrupted", "B-tree page checksum verification mismatch in shard-04", "Unclean shutdown during WAL write cycle", "pg_waldump -p /wal shard-04 && vacuum full"),
]

def generate_trace_dataset(target_tokens: int = 200_000, seed: int = 42) -> Tuple[str, GroundTruthSuite]:
    """Generates verbose execution logs, compiler outputs, and stack traces with invariants."""
    rng = random.Random(seed)
    gt = GroundTruthSuite()
    sessions: List[str] = []
    depth_targets = [0.0, 25.0, 50.0, 75.0, 100.0]
    needle_vals = ["TRACE_VAL_Z0", "TRACE_VAL_Q25", "TRACE_VAL_M50", "TRACE_VAL_T75", "TRACE_VAL_E100"]
    num_sessions = max(5, int(target_tokens / 2200))
    needle_idx = {int(round((d / 100.0) * (num_sessions - 1))): (d, needle_vals[i]) for i, d in enumerate(depth_targets)}

    svc0 = _TRACE_SERVICES[0]
    _, _, root_cause0, fix_cmd0 = _TRACE_ERRORS[0]
    gt.qa_pairs.append(QAItem(f"What was the root cause of the ConnectionRefusedError on PostgreSQL port 5432 in session_0 ({svc0})?", root_cause0, [svc0, "reconnection", "pool"], "trace"))
    gt.triples.append(TripleItem("session_0", "encountered_error", "session_0_error", "trace"))
    gt.triples.append(TripleItem("session_0_error", "resolves_with", "session_0_resolution", "trace"))
    gt.multi_hops.append(MultiHopItem(
        f"What fix command resolved the error in session_0 ({svc0})?",
        fix_cmd0,
        [f"session_0 {svc0} encountered ConnectionRefusedError", f"root cause: {root_cause0}", f"fix: {fix_cmd0}"],
        ["alembic", "restart", "postgresql"],
        "trace",
    ))

    session_id, total_tokens = 0, 0
    while total_tokens < target_tokens or session_id < num_sessions:
        svc = _TRACE_SERVICES[session_id % len(_TRACE_SERVICES)]
        err_type, err_msg, root_cause, fix_cmd = _TRACE_ERRORS[session_id % len(_TRACE_ERRORS)]
        sess_lines = [f"=== SESSION {session_id} ===", f"$ pytest tests/test_{svc}.py -v --log-level=DEBUG"]

        for step in range(rng.randint(70, 110)):
            ts = f"2026-11-14T0{rng.randint(1, 9)}:{rng.randint(10, 59)}:{rng.randint(10, 59)}.{rng.randint(100, 999)}Z"
            mem = f"0x{rng.randint(0x10000000, 0x7FFFFFFF):08x}"
            sess_lines.append(f"[{ts}] [DEBUG] {svc}.worker_{step % 8}: processing frame {step} at {mem} status=IN_PROGRESS buffer_len={rng.randint(1024, 65536)}")
            if step == 40:
                sess_lines.append(f"Traceback (most recent call last):\n  File \"/app/services/{svc}.py\", line {rng.randint(50, 400)}, in execute_job\n    raise {err_type}(\"{err_msg}\")\n{err_type}: {err_msg}")

        if session_id in needle_idx:
            depth_pct, n_val = needle_idx[session_id]
            n_key = f"needle_trace_{int(depth_pct)}"
            sess_lines.append(f"- {n_key}: {n_val}")
            gt.needles.append(NeedleItem(f"needle_trace_{int(depth_pct)}", n_key, n_val, f"What is the value of {n_key}?", depth_pct, "trace"))

        sess_lines.extend([f"Root cause: {root_cause}", f"Fix applied: {fix_cmd}", "Outcome: RESOLVED_SUCCESS", f"- invariant_summary: {svc}_issue_resolved", f"- {svc}_status: operational"])
        session_str = "\n".join(sess_lines) + "\n\n"
        sessions.append(session_str)
        total_tokens += count_tokens(session_str, "o200k_base")
        session_id += 1

    return "".join(sessions), gt


# =============================================================================
# 2. Bilingual Dialogue Dataset Generator (150k tokens)
# =============================================================================

_DLG_TOPICS = [
    ("архитектуре микросервисов биллинга", "billing microservices architecture", "Go 1.22", "PostgreSQL 16", "8443", "gRPC"),
    ("хранению кэша и очередей событий", "caching and event queues", "Redis 7.2", "Kafka 3.7", "6379", "Protobuf"),
    ("безопасности и ротации JWT ключей", "security and JWT key rotation", "Vault 1.15", "Ed25519", "443", "HTTPS"),
    ("поисковому индексу и векторной памяти", "search indexing and vector memory", "SimHash-64", "SQLite FTS5", "9200", "Brotli"),
]

def generate_dialogue_dataset(target_tokens: int = 150_000, seed: int = 42) -> Tuple[str, GroundTruthSuite]:
    """Generates bilingual (English/Russian) multi-turn conversations with architecture decisions."""
    rng = random.Random(seed)
    gt = GroundTruthSuite()
    dialogues: List[str] = []
    depth_targets = [0.0, 25.0, 50.0, 75.0, 100.0]
    needle_vals = ["DLG_VAL_Z0", "DLG_VAL_Q25", "DLG_VAL_M50", "DLG_VAL_T75", "DLG_VAL_E100"]
    num_sessions = max(5, int(target_tokens / 1800))
    needle_idx = {int(round((d / 100.0) * (num_sessions - 1))): (d, needle_vals[i]) for i, d in enumerate(depth_targets)}

    gt.qa_pairs.append(QAItem("What is the listen port for the billing service discussed in session 0?", "8443", ["8443", "service_listen_port"], "dialogue"))
    gt.qa_pairs.append(QAItem("What is the primary database chosen for the architecture in session 0?", "PostgreSQL 16", ["PostgreSQL", "primary_database"], "dialogue"))
    gt.triples.append(TripleItem("dialogues.md", "configures", "architecture::primary_database", "dialogue"))
    gt.triples.append(TripleItem("dialogues.md", "configures", "architecture::service_listen_port", "dialogue"))
    gt.multi_hops.append(MultiHopItem(
        "What database and protocol were chosen for the billing architecture in session 0?",
        "PostgreSQL 16 and gRPC",
        ["primary_database: PostgreSQL 16", "interservice_protocol: gRPC"],
        ["PostgreSQL", "grpc"],
        "dialogue",
    ))

    session_id, total_tokens = 0, 0
    while total_tokens < target_tokens or session_id < num_sessions:
        ru_topic, en_topic, lang_stack, db_stack, port_val, proto = _DLG_TOPICS[session_id % len(_DLG_TOPICS)]
        sess_lines = [
            f"# Session {session_id}: Discussion on {en_topic} / {ru_topic}",
            f"User: Привет! Нам необходимо зафиксировать решения по {ru_topic}.",
            f"Assistant: Здравствуйте! Готов зафиксировать. Какой основной стек и база данных будут использоваться?",
            f"User: Запомни: основной стек — {lang_stack}, база данных — {db_stack}, а порт сервиса равен {port_val}.",
            f"Assistant: Отлично. Фиксирую в долговременной памяти: {lang_stack}, {db_stack}, порт {port_val}.",
            f"User: Also note for the international team: the protocol must strictly be {proto}.",
            f"Assistant: Understood. Registered protocol={proto} for cross-service communication.",
            "- Пользователь предпочитает: строгий контроль типов и лаконичные логи.",
            f"- stack_technology: {lang_stack}", f"- primary_database: {db_stack}",
            f"- service_listen_port: {port_val}", f"- interservice_protocol: {proto}",
        ]

        for turn in range(rng.randint(25, 40)):
            if turn % 2 == 0:
                sess_lines.append(f"User: Уточни требования к отказоустойчивости узла {turn} в кластере.")
                sess_lines.append(f"Assistant: Узел {turn} реплицируется в 3 зонах с failover за {rng.randint(5, 30)} сек.")
            else:
                sess_lines.append(f"User: What is the maximum throughput allowed for client {turn}?")
                sess_lines.append(f"Assistant: Throughput for client {turn} is rate-limited to {rng.randint(100, 5000)} RPS.")

        if session_id in needle_idx:
            depth_pct, n_val = needle_idx[session_id]
            n_key = f"needle_dialogue_{int(depth_pct)}"
            sess_lines.append(f"- {n_key}: {n_val}")
            gt.needles.append(NeedleItem(f"needle_dialogue_{int(depth_pct)}", n_key, n_val, f"What is the value of {n_key}?", depth_pct, "dialogue"))

        dialogue_str = "\n".join(sess_lines) + "\n\n"
        dialogues.append(dialogue_str)
        total_tokens += count_tokens(dialogue_str, "o200k_base")
        session_id += 1

    return "".join(dialogues), gt


# =============================================================================
# 3. Code & Living Wiki Dataset Generator (100k tokens)
# =============================================================================

def generate_code_dataset(target_tokens: int = 100_000, seed: int = 42) -> Tuple[str, GroundTruthSuite]:
    """Generates Python source modules and Living Wiki Markdown memory documents."""
    rng = random.Random(seed)
    gt = GroundTruthSuite()
    docs: List[str] = []
    depth_targets = [0.0, 25.0, 50.0, 75.0, 100.0]
    needle_vals = ["CODE_VAL_Z0", "CODE_VAL_Q25", "CODE_VAL_M50", "CODE_VAL_T75", "CODE_VAL_E100"]
    num_modules = max(5, int(target_tokens / 1600))
    needle_idx = {int(round((d / 100.0) * (num_modules - 1))): (d, needle_vals[i]) for i, d in enumerate(depth_targets)}

    gt.qa_pairs.append(QAItem("What is the default timeout_seconds in ServiceController_0?", "30", ["30", "timeout_seconds", "ServiceController_0"], "code"))
    gt.triples.append(TripleItem("controller_0.ServiceController_0", "defines_method", "controller_0.ServiceController_0.dispatch_job", "code"))
    gt.multi_hops.append(MultiHopItem(
        "Which class defines dispatch_job, and what is its default timeout_seconds?",
        "ServiceController_0 with timeout_seconds 30",
        ["controller_0.ServiceController_0 defines_method dispatch_job", "timeout_seconds: 30"],
        ["ServiceController_0", "30", "dispatch_job"],
        "code",
    ))

    mod_id, total_tokens = 0, 0
    while total_tokens < target_tokens or mod_id < num_modules:
        if mod_id % 2 == 0:
            class_name = f"ServiceController_{mod_id}"
            needle_doc = ""
            if mod_id in needle_idx:
                depth_pct, n_val = needle_idx[mod_id]
                n_key = f"needle_code_{int(depth_pct)}"
                needle_doc = f" {n_key}: {n_val}"
                gt.needles.append(NeedleItem(f"needle_code_{int(depth_pct)}", n_key, n_val, f"What is the value of {n_key}?", depth_pct, "code"))

            py_lines = [
                f"### FILE: services/controller_{mod_id}.py",
                f'"""Controller module for distributed service tier {mod_id}.{needle_doc}"""',
                "from typing import Any, Dict, List, Optional", "",
                f"class {class_name}:",
                f'    """Handles routing, state management, and request dispatch for tier {mod_id}.{needle_doc}"""', "",
                f"    def __init__(self, endpoint: str, timeout_seconds: int = 30) -> None:",
                f'        """Initializes connection to endpoint with timeout."""',
                f"        self.endpoint = endpoint",
                f"        self.timeout = timeout_seconds", "",
                f"    async def dispatch_job(self, job_id: str, payload: Dict[str, Any]) -> bool:",
                f'        """Dispatches an asynchronous job to downstream workers."""',
                f"        return len(payload) > 0",
            ]
            for fn_idx in range(rng.randint(6, 12)):
                py_lines.extend([
                    f"    def execute_subtask_{fn_idx}(self, task_token: str, retries: int = 3) -> int:",
                    f'        """Executes subtask {fn_idx} with retry threshold."""',
                    f"        return len(task_token) * retries",
                ])
            content_str = "\n".join(py_lines) + "\n\n"
        else:
            wiki_lines = [
                f"### FILE: memory/module_spec_{mod_id}.md",
                f"# Память: Архитектурные правила и стек модуля {mod_id}",
                "## Проект и стек",
                "- Framework: FastAPI on Python 3.12",
                f"- Service tier: {mod_id}",
                f"- Primary cache: Redis 7.2 on port {6300 + (mod_id % 100)}",
                "## Решения",
                f"- Архитектура и слои — memory/architecture_{mod_id}.md",
                f"- Контракт безопасности — memory/security_{mod_id}.md",
                "## Грабли",
                f"- В модуле {mod_id} запрещено синхронное чтение из сокетов во избежание блокировки event loop.",
                "## Правила",
                f"!RULE: All database migrations for tier {mod_id} must have backward compatibility locks.",
            ]
            if mod_id in needle_idx:
                depth_pct, n_val = needle_idx[mod_id]
                n_key = f"needle_code_{int(depth_pct)}"
                wiki_lines.append(f"- {n_key}: {n_val}")
                gt.needles.append(NeedleItem(f"needle_code_{int(depth_pct)}", n_key, n_val, f"What is the value of {n_key}?", depth_pct, "code"))

            content_str = "\n".join(wiki_lines) + "\n\n"

        docs.append(content_str)
        total_tokens += count_tokens(content_str, "o200k_base")
        mod_id += 1

    return "".join(docs), gt


# =============================================================================
# 4. Temporal State Evolution Dataset Generator (100k tokens)
# =============================================================================

def generate_temporal_dataset(target_tokens: int = 100_000, seed: int = 42) -> Tuple[str, GroundTruthSuite]:
    """Generates multi-session configuration logs where properties are overridden across epochs."""
    rng = random.Random(seed)
    gt = GroundTruthSuite()
    epochs_data: List[str] = []
    depth_targets = [0.0, 25.0, 50.0, 75.0, 100.0]
    needle_vals = ["TEMP_VAL_Z0", "TEMP_VAL_Q25", "TEMP_VAL_M50", "TEMP_VAL_T75", "TEMP_VAL_E100"]
    total_epochs = max(5, int(target_tokens / 3000))
    needle_idx = {int(round((d / 100.0) * (total_epochs - 1))): (d, needle_vals[i]) for i, d in enumerate(depth_targets)}

    db_hosts = ["db-v1.internal", "db-v2.internal", "db-cluster-v3.internal", "db-cluster-v4.internal"]

    total_tokens, epoch = 0, 0
    while total_tokens < target_tokens or epoch < total_epochs:
        current_db = db_hosts[min(epoch // 4, len(db_hosts) - 1)]
        current_timeout = 30 + (epoch * 6)
        epoch_lines = [
            f"### EPOCH {epoch}: State Snapshot and Configuration Audit",
            f"epoch_id: {epoch}",
            f"- service_timeout: {current_timeout}",
            f"- database_host: {current_db}",
            f"- active_workers: {4 + epoch * 2}",
            f"- memory_reservation_mb: {512 + epoch * 64}",
            f"- deployment_region: {'us-east-1' if epoch < 8 else 'us-west-2'}",
        ]

        for log_idx in range(rng.randint(60, 90)):
            epoch_lines.append(f"[AuditLog-{epoch}:{log_idx}] node_{log_idx % 16} reported state=HEALTHY latency_ms={rng.randint(2, 45)} load_avg={round(rng.uniform(0.1, 2.5), 2)} active_epoch={epoch} verified_host={current_db}")

        if epoch in needle_idx:
            depth_pct, n_val = needle_idx[epoch]
            n_key = f"needle_temporal_{int(depth_pct)}"
            epoch_lines.append(f"- {n_key}: {n_val}")
            gt.needles.append(NeedleItem(f"needle_temporal_{int(depth_pct)}", n_key, n_val, f"What is the value of {n_key}?", depth_pct, "temporal"))

        epoch_str = "\n".join(epoch_lines) + "\n\n"
        epochs_data.append(epoch_str)
        total_tokens += count_tokens(epoch_str, "o200k_base")
        epoch += 1

    final_epoch = epoch - 1
    final_timeout = str(30 + final_epoch * 6)
    final_db = db_hosts[min(final_epoch // 4, len(db_hosts) - 1)]
    final_epoch_file = f"epoch_{final_epoch}.md"

    gt.qa_pairs.append(QAItem("What is the current active service_timeout in the latest configuration?", final_timeout, [final_timeout, "service_timeout"], "temporal"))
    gt.qa_pairs.append(QAItem("What is the active database_host in the latest configuration?", final_db, [final_db, "database_host"], "temporal"))
    gt.triples.append(TripleItem(final_epoch_file, "configures", "root::service_timeout", "temporal"))
    gt.triples.append(TripleItem(final_epoch_file, "configures", "root::database_host", "temporal"))
    gt.multi_hops.append(MultiHopItem(
        "What was the initial timeout in Epoch 0 and what is the latest timeout in the final epoch?",
        f"Initial timeout 30 evolved to {final_timeout}",
        ["Epoch 0 timeout: 30", f"Final epoch timeout: {final_timeout}"],
        ["30", final_timeout, "timeout"],
        "temporal",
    ))

    return "".join(epochs_data), gt


# =============================================================================
# 5. Ingestion Engine: Raw Text -> MemoryGraph
# =============================================================================

def ingest_dataset_to_graph(
    dataset_name: str,
    raw_text: str,
    ground_truth: GroundTruthSuite,
    graph: Optional[MemoryGraph] = None,
) -> MemoryGraph:
    """Ingests procedural raw dataset text into an indexed ALMF MemoryGraph."""
    if graph is None:
        graph = MemoryGraph()

    router = SmartCompressionRouter(vault=graph.vault)
    norm_name = dataset_name.lower().strip()

    if "trace" in norm_name:
        sessions = re.split(r"===\s*SESSION\s+(\d+)\s*===", raw_text)
        if len(sessions) > 1:
            for i in range(1, len(sessions), 2):
                sess_id = int(sessions[i])
                parse_trace_log(sessions[i + 1], trace_id=f"session_{sess_id}", graph=graph, router=router, epoch=sess_id, aggregate=False)
            HierarchicalAggregator().aggregate(graph)
        else:
            parse_trace_log(raw_text, trace_id="trace_session", graph=graph, router=router, epoch=0)

    elif "dialogue" in norm_name:
        parse_markdown(raw_text, filename="dialogues.md", graph=graph, router=router, epoch=1)
        HierarchicalAggregator().aggregate(graph)

    elif "code" in norm_name:
        file_chunks = re.split(r"###\s*FILE:\s*([^\n\r]+)", raw_text)
        if len(file_chunks) > 1:
            for i in range(1, len(file_chunks), 2):
                fname, fcontent = file_chunks[i].strip(), file_chunks[i + 1]
                if fname.endswith(".py"):
                    parse_python_ast(fcontent, module_name=os.path.splitext(os.path.basename(fname))[0], graph=graph, epoch=i)
                else:
                    parse_markdown(fcontent, filename=fname, graph=graph, router=router, epoch=i)
        else:
            parse_markdown(raw_text, filename="code_spec.md", graph=graph, router=router, epoch=1)
        HierarchicalAggregator().aggregate(graph)

    elif "temporal" in norm_name:
        epoch_chunks = re.split(r"###\s*EPOCH\s+(\d+)[^\n\r]*", raw_text)
        if len(epoch_chunks) > 1:
            for i in range(1, len(epoch_chunks), 2):
                ep_id, ep_content = int(epoch_chunks[i]), epoch_chunks[i + 1]
                parse_markdown(ep_content, filename=f"epoch_{ep_id}.md", graph=graph, router=router, epoch=ep_id)
        else:
            parse_markdown(raw_text, filename="temporal.md", graph=graph, router=router, epoch=1)
    else:
        parse_markdown(raw_text, filename=f"{dataset_name}.md", graph=graph, router=router, epoch=1)
        HierarchicalAggregator().aggregate(graph)

    SimHashIndex().index_graph(graph)
    return graph
