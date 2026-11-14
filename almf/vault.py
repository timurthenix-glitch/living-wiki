"""
ALMF Blob Vault — Lossless Storage Engine for Critical Constants, Secrets, and Code.

Implements Section 4.2 (Regime 4: LOSSLESS_VAULT) and Section 5.4 (Block 6)
of docs/ALMF_SPECIFICATION.md.
"""

from __future__ import annotations

import hashlib
import struct
import zlib
from typing import Any, Dict, List, Optional, Union

try:
    import brotli
    _HAS_BROTLI = True
except ImportError:  # pragma: no cover
    _HAS_BROTLI = False

from almf.types import VaultPointer


VAULT_MAGIC = b"VLT1"
CODEC_RAW = 0
CODEC_BROTLI = 1
CODEC_DEFLATE = 2

CODEC_NAMES = {
    CODEC_RAW: "raw",
    CODEC_BROTLI: "brotli",
    CODEC_DEFLATE: "deflate",
}

CODEC_IDS = {
    "raw": CODEC_RAW,
    "brotli": CODEC_BROTLI,
    "deflate": CODEC_DEFLATE,
}


def _clean_hash(ptr_or_hash: Union[VaultPointer, str]) -> str:
    """Extracts raw hexadecimal hash string from VaultPointer or prompt symbol."""
    if isinstance(ptr_or_hash, VaultPointer):
        return ptr_or_hash.hash
    s = str(ptr_or_hash).strip()
    if s.startswith("@ptr(") and s.endswith(")"):
        return s[5:-1].strip()
    return s


class BlobVault:
    """
    Lossless storage engine for exact strings and raw byte payloads.

    Generates 1-token `@ptr(hash)` reference pointers, indexing data into
    compact, compressed chunks within Section 6 (Cold Archival Storage).
    """

    def __init__(self) -> None:
        # Maps hash -> raw uncompressed bytes
        self._blobs: Dict[str, bytes] = {}
        # Maps hash -> VaultPointer descriptor
        self._pointers: Dict[str, VaultPointer] = {}

    def __len__(self) -> int:
        return len(self._blobs)

    def __contains__(self, ptr_or_hash: Union[VaultPointer, str]) -> bool:
        return self.has(ptr_or_hash)

    def has(self, ptr_or_hash: Union[VaultPointer, str]) -> bool:
        """Checks if a blob exists in the vault."""
        h = _clean_hash(ptr_or_hash)
        return h in self._blobs

    def put(
        self,
        data: Union[str, bytes],
        name: str = "",
        content_type: str = "text/plain",
        metadata: Optional[Dict[str, Any]] = None,
        hash_length: int = 8,
    ) -> VaultPointer:
        """
        Stores an exact string or byte payload losslessly in the vault.

        Returns a 1-token VaultPointer (e.g. `@ptr(a3f9c01b)`).
        """
        if isinstance(data, str):
            raw_bytes = data.encode("utf-8")
        elif isinstance(data, (bytes, bytearray)):
            raw_bytes = bytes(data)
        else:
            raise TypeError(f"Blob data must be str or bytes, got {type(data).__name__}")

        full_hash = hashlib.sha256(raw_bytes).hexdigest()
        short_hash = full_hash[:hash_length]

        # Handle hash collision within this vault instance
        if short_hash in self._blobs and self._blobs[short_hash] != raw_bytes:
            # Fall back to longer hash slice
            short_hash = full_hash[:16]
            if short_hash in self._blobs and self._blobs[short_hash] != raw_bytes:
                short_hash = full_hash

        meta: Dict[str, Any] = {
            "name": name,
            "content_type": content_type,
            "sha256": full_hash,
        }
        if metadata:
            meta.update(metadata)

        # Determine best codec and compressed length
        compressed_bytes, codec_name = self._compress_blob(raw_bytes)

        pointer = VaultPointer(
            hash=short_hash,
            offset=0,  # Updated during pack()
            length=len(compressed_bytes),
            codec=codec_name,
            original_size=len(raw_bytes),
            metadata=meta,
        )

        self._blobs[short_hash] = raw_bytes
        self._pointers[short_hash] = pointer
        return pointer

    def store(
        self,
        data: Union[str, bytes],
        mime_type: str = "text/plain",
        name: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        hash_length: int = 8,
    ) -> VaultPointer:
        """Alias for put() supporting mime_type parameter."""
        return self.put(
            data=data,
            name=name,
            content_type=mime_type,
            metadata=metadata,
            hash_length=hash_length,
        )

    def get(self, ptr_or_hash: Union[VaultPointer, str]) -> bytes:
        """
        Retrieves uncompressed verbatim bytes for a pointer or hash string.
        """
        h = _clean_hash(ptr_or_hash)
        if h not in self._blobs:
            raise KeyError(f"Vault blob not found for pointer/hash: {ptr_or_hash!r} (resolved: {h})")
        return self._blobs[h]

    def get_str(
        self,
        ptr_or_hash: Union[VaultPointer, str],
        encoding: str = "utf-8",
        errors: str = "replace",
    ) -> str:
        """Retrieves and decodes verbatim string payload."""
        return self.get(ptr_or_hash).decode(encoding, errors=errors)

    def delete(self, ptr_or_hash: Union[VaultPointer, str]) -> bool:
        """Removes a blob from the vault."""
        h = _clean_hash(ptr_or_hash)
        if h in self._blobs:
            del self._blobs[h]
            self._pointers.pop(h, None)
            return True
        return False

    def list_pointers(self) -> List[VaultPointer]:
        """Returns all VaultPointer descriptors currently stored."""
        return list(self._pointers.values())

    def get_pointer(self, ptr_or_hash: Union[VaultPointer, str]) -> Optional[VaultPointer]:
        """Resolves VaultPointer descriptor by hash or symbol."""
        h = _clean_hash(ptr_or_hash)
        return self._pointers.get(h)

    # -------------------------------------------------------------------------
    # Binary Serialization (Section 6 / Block 6 Container)
    # -------------------------------------------------------------------------

    @staticmethod
    def _compress_blob(raw_bytes: bytes) -> tuple[bytes, str]:
        """Compresses raw bytes using Brotli or Deflate, falling back to raw if smaller."""
        if len(raw_bytes) == 0:
            return b"", "raw"

        # Try Brotli first if available
        if _HAS_BROTLI:
            try:
                brotli_c = brotli.compress(raw_bytes, quality=11)
                if len(brotli_c) < len(raw_bytes):
                    return brotli_c, "brotli"
            except Exception:
                pass

        # Try Deflate / zlib level 9
        deflate_c = zlib.compress(raw_bytes, level=9)
        if len(deflate_c) < len(raw_bytes):
            return deflate_c, "deflate"

        # If compression did not reduce size (e.g. tiny payload)
        return raw_bytes, "raw"

    @staticmethod
    def _decompress_blob(data: bytes, codec: str, original_size: int) -> bytes:
        """Decompresses blob payload according to codec."""
        if codec == "raw" or len(data) == 0:
            return data
        if codec == "brotli":
            if _HAS_BROTLI:
                return brotli.decompress(data)
            raise RuntimeError("Cannot decompress Brotli blob: brotli package not available")
        if codec == "deflate":
            return zlib.decompress(data)
        raise ValueError(f"Unknown vault blob codec: {codec}")

    def pack(self) -> bytes:
        """
        Packs all vault blobs and metadata into a single contiguous byte block
        matching Section 6 (Cold Archival Storage) specification:

        Header:
          Magic: 4 bytes (b"VLT1")
          ChunkCount: uint32
        Per chunk:
          HashLen: uint8
          HashBytes: bytes[HashLen]
          OriginalSize: uint32
          CompressedSize: uint32
          Codec: uint8 (0=raw, 1=brotli, 2=deflate)
          Payload: bytes[CompressedSize]
        """
        out = bytearray()
        chunk_count = len(self._blobs)
        out.extend(struct.pack("<4sI", VAULT_MAGIC, chunk_count))

        for h, raw_bytes in sorted(self._blobs.items()):
            compressed_bytes, codec_name = self._compress_blob(raw_bytes)
            codec_id = CODEC_IDS.get(codec_name, CODEC_RAW)
            hash_bytes = h.encode("utf-8")
            hash_len = len(hash_bytes)

            chunk_offset = len(out)
            chunk_header = struct.pack(
                f"<B{hash_len}sIIB",
                hash_len,
                hash_bytes,
                len(raw_bytes),
                len(compressed_bytes),
                codec_id,
            )
            out.extend(chunk_header)
            out.extend(compressed_bytes)

            # Update pointer offset in-memory
            if h in self._pointers:
                self._pointers[h].offset = chunk_offset
                self._pointers[h].length = len(compressed_bytes)
                self._pointers[h].codec = codec_name
                self._pointers[h].original_size = len(raw_bytes)

        return bytes(out)

    def unpack(self, data: bytes) -> "BlobVault":
        """
        Unpacks binary Section 6 payload into this BlobVault instance.
        """
        if len(data) == 0:
            self._blobs.clear()
            self._pointers.clear()
            return self

        if len(data) < 8:
            raise ValueError(f"Invalid vault data length: {len(data)} (minimum 8 bytes required)")

        magic, chunk_count = struct.unpack_from("<4sI", data, 0)
        if magic != VAULT_MAGIC:
            raise ValueError(f"Invalid vault magic: expected {VAULT_MAGIC!r}, got {magic!r}")

        offset = 8
        self._blobs.clear()
        self._pointers.clear()

        for _ in range(chunk_count):
            if offset + 1 > len(data):
                raise ValueError(f"Truncated vault data at chunk offset {offset}")

            (hash_len,) = struct.unpack_from("<B", data, offset)
            offset += 1

            chunk_meta_fmt = f"<{hash_len}sIIB"
            chunk_meta_size = struct.calcsize(chunk_meta_fmt)
            if offset + chunk_meta_size > len(data):
                raise ValueError(f"Truncated vault chunk metadata at offset {offset}")

            hash_bytes, orig_size, comp_size, codec_id = struct.unpack_from(chunk_meta_fmt, data, offset)
            offset += chunk_meta_size

            h = hash_bytes.decode("utf-8")
            if offset + comp_size > len(data):
                raise ValueError(f"Truncated vault chunk payload for hash {h} at offset {offset}")

            payload = data[offset : offset + comp_size]
            offset += comp_size

            codec_name = CODEC_NAMES.get(codec_id, "raw")
            raw_decompressed = self._decompress_blob(payload, codec_name, orig_size)
            if len(raw_decompressed) != orig_size:
                raise ValueError(
                    f"Decompressed size mismatch for vault blob {h}: expected {orig_size}, got {len(raw_decompressed)}"
                )

            pointer = VaultPointer(
                hash=h,
                offset=offset - comp_size - chunk_meta_size - 1,
                length=comp_size,
                codec=codec_name,
                original_size=orig_size,
                metadata={"sha256": hashlib.sha256(raw_decompressed).hexdigest()},
            )

            self._blobs[h] = raw_decompressed
            self._pointers[h] = pointer

        return self

    @classmethod
    def from_bytes(cls, data: bytes) -> "BlobVault":
        """Instantiates and unpacks a BlobVault from binary bytes."""
        vault = cls()
        vault.unpack(data)
        return vault

    def to_dict(self) -> Dict[str, Any]:
        """Serializes vault contents to dictionary for JSON export."""
        return {
            "version": 1,
            "pointers": [p.to_dict() for p in self._pointers.values()],
            "blobs": {
                h: data.hex() for h, data in self._blobs.items()
            },
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BlobVault":
        """Reconstructs a BlobVault from dictionary."""
        vault = cls()
        for p_data in data.get("pointers", []):
            ptr = VaultPointer.from_dict(p_data)
            vault._pointers[ptr.hash] = ptr
        for h, hex_str in data.get("blobs", {}).items():
            vault._blobs[h] = bytes.fromhex(hex_str)
        return vault
