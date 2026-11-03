"""Pack a folder of compiled Source 2 files into a single-file VPK (version 2).

Run:  python pack_vpk.py <folder> <out pakNN_dir.vpk>

Every file goes into the _dir.vpk itself (archive index 0x7fff), which is how Deadlock
addon VPKs in game/citadel/addons are laid out.
"""
import os
import struct
import sys
import zlib


def collect(root):
    tree = {}
    for dirpath, _, files in os.walk(root):
        for f in files:
            full = os.path.join(dirpath, f)
            rel = os.path.relpath(full, root).replace("\\", "/")
            path, name = rel.rsplit("/", 1) if "/" in rel else (" ", rel)
            name, ext = name.rsplit(".", 1) if "." in name else (name, " ")
            tree.setdefault(ext, {}).setdefault(path, []).append((name, full))
    return tree


def pack(root, out):
    tree = collect(root)
    entries, data, offset = bytearray(), bytearray(), 0
    for ext in sorted(tree):
        entries += ext.encode() + b"\0"
        for path in sorted(tree[ext]):
            entries += path.encode() + b"\0"
            for name, full in sorted(tree[ext][path]):
                blob = open(full, "rb").read()
                entries += name.encode() + b"\0"
                entries += struct.pack("<IHHII", zlib.crc32(blob) & 0xFFFFFFFF, 0, 0x7FFF, offset, len(blob))
                entries += struct.pack("<H", 0xFFFF)
                data += blob
                offset += len(blob)
            entries += b"\0"
        entries += b"\0"
    entries += b"\0"
    with open(out, "wb") as f:
        f.write(struct.pack("<IIIIIII", 0x55AA1234, 2, len(entries), len(data), 0, 0, 0))
        f.write(entries)
        f.write(data)
    return sum(len(n) for p in tree.values() for n in p.values())


if __name__ == "__main__":
    print(f"packed {pack(sys.argv[1], sys.argv[2])} files into {sys.argv[2]}")
