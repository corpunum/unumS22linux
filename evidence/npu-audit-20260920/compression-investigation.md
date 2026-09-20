# F2FS compression investigation

The initial zero payloads were an extractor limitation, not proof that the
source blocks are zero. The inode entries have `i_flags=4` and
`i_log_cluster_size=2`; `fsck.f2fs -M` shows compressed block maps with zero
placeholders. A source data block begins with a 24-byte F2FS compression header;
its little-endian length is followed by an LZ4 block. For example physical
block 143620 has length 3228 and LZ4-decompresses to 16,384 bytes beginning
with ELF magic. The stock Ubuntu `dump.f2fs` does not decompress these blocks.

An isolated current f2fs-tools build was attempted and its dump path has the
same limitation. A partial LZ4 recovery was explored, but multi-block clusters
need the exact F2FS compressed-cluster mapping (some length-bearing blocks span
continuations); outputs from that attempt are not declared valid runtime
artifacts. Treat the current extracted copies as failed/provisional and do not
load them. `blockmap.txt` is retained only in `/tmp`; source image remains
unchanged. Existing staged hashes/indexes must be regenerated after a complete
cluster-aware decompression.
