# Vendor LZ4 plus BOOT gzip boundary reproducer

Host-only deterministic audit; no vendor_boot or phone writes.

Inputs are the exact phone backup vendor ramdisk fragments:

- fragment 00: 217,438 bytes, SHA256
  `2ee3688c2c98274af35b9c9cc4ffa0bc9ca1878370049502f73af14eab3d41d5`
- fragment 01 (`dlkm`): 15,315,914 bytes, SHA256
  `3faacc0eec09b38d0ad0532028dcf472363d2551daa58d9afd2b2e2ccc55d083`
- BOOT v2 generic gzip ramdisk: 28,761,647 bytes, SHA256
  `0d82710ce5a6e089610a63a5da22573686f58b5de60a18f3ecbfa3932ab95925`

The pinned kernel `lib/decompress_unlz4.c` reads a 32-bit little-endian
chunk size. It recognizes only the LZ4 archive magic (`0x184c2102`) and a
zero chunk terminator; it does not break on generic gzip magic. The exact
concatenation therefore sees the gzip bytes `1f 8b 08 00` as little-endian
chunk size `559903`, then passes gzip bytes to the LZ4 decoder. The pure parser
result is recorded by
`reproduce_parser.py`:

```text
vendor00+vendor01+gzip: result=chunk_overrun
gzip transition offset=15533352, bytes=1f8b080000000000, le32=559903
```

An additional ctypes harness calls host `liblz4` `LZ4_decompress_safe` on the
exact blocks. It successfully decodes vendor00 to 1,133,824 bytes (SHA256
`edb8618917f6c32ea5c9eabc7600607d65d62a6f6928920af4eaf4e9fc0bc0b2`) and all
seven vendor01 blocks to 58,110,208 bytes (SHA256
`aca27665a163acf4e7781bb0ddee1da2f905ba424377f59acdac1da98637d9a5`). The
first fake gzip block returns `LZ4_decompress_safe = -7`, confirming decoder
failure rather than merely a bounds-model overrun.

The proposed four-NUL boundary was also tested with the exact same parser:

```text
vendor00+vendor01+4nul+gzip: result=stop_zero, stop=15533352
gzip begins immediately after the four zero bytes at the initramfs scanner
```

This follows the pinned `init/initramfs.c` logic: after the LZ4 decompressor
returns at the zero block, its scanner skips NUL bytes and reruns generic
compression detection, where `1f 8b` selects gzip. `gzip.decompress` of the
post-prefix stream yields 94,278,940 bytes with SHA256
`661340d6e98c1fa4c377aac32ede21916c4f40885bbcda87a382531040aae7a2`, exactly
matching the BOOT v2 CPIO. The prefixed and raw concatenation artifacts and
hashes are in this directory. This proves the host decoder/parser boundary,
not physical boot acceptance; the candidate image must use the exact boundary
format and still requires hardware validation.
