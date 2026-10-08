"""Pull single files out of large remote zip archives with HTTP range requests.

The USDA Forest Service publishes each Wildfire Risk to Communities raster set as one zip of up
to 36 GB. The pipeline only needs one or two files from each, so this reads the zip directory,
then streams just the wanted member through zlib to disk.
"""
import io
import struct
import sys
import time
import urllib.request
import zipfile
import zlib
from pathlib import Path

CHUNK = 8 * 1024 * 1024


def _open(url, start, end=None, retries=5):
    rng = f"bytes={start}-" if end is None else f"bytes={start}-{end}"
    for attempt in range(retries):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers={"Range": rng}), timeout=120)
        except OSError:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)


class RangeFile(io.RawIOBase):
    """Seekable read-only view of a remote file. Box refuses HEAD, so size comes from a ranged GET."""

    def __init__(self, url):
        r = _open(url, 0, 0)
        self.source, self.url, self.pos = url, r.geturl(), 0
        self.size = int(r.headers["Content-Range"].split("/")[1])

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, off, whence=0):
        self.pos = off if whence == 0 else self.pos + off if whence == 1 else self.size + off
        return self.pos

    def read(self, n=-1):
        if n < 0:
            n = self.size - self.pos
        if n <= 0 or self.pos >= self.size:
            return b""
        data = _open(self.url, self.pos, min(self.size, self.pos + n) - 1).read()
        self.pos += len(data)
        return data

    def readinto(self, b):
        d = self.read(len(b))
        b[:len(d)] = d
        return len(d)


def members(url):
    return zipfile.ZipFile(RangeFile(url)).infolist()


def extract_member(url, name, dest):
    """Stream one member of a remote zip to `dest`, inflating on the fly. Skips if already complete."""
    dest = Path(dest)
    f = RangeFile(url)
    info = zipfile.ZipFile(f).getinfo(name)
    if dest.exists() and dest.stat().st_size == info.file_size:
        return dest
    # The local header has variable-length fields; read it to find where the data starts.
    head = _open(f.url, info.header_offset, info.header_offset + 29).read()
    name_len, extra_len = struct.unpack("<HH", head[26:30])
    start = info.header_offset + 30 + name_len + extra_len
    end = start + info.compress_size - 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    inflate = zlib.decompressobj(-15) if info.compress_type == zipfile.ZIP_DEFLATED else None
    done, t0, pos = 0, time.time(), start
    with open(tmp, "wb") as out:
        while pos <= end:
            # Signed download URLs expire, so re-resolve and resume from the current offset on failure.
            try:
                r = _open(f.url, pos, end)
                while True:
                    chunk = r.read(CHUNK)
                    if not chunk:
                        break
                    pos += len(chunk)
                    out.write(inflate.decompress(chunk) if inflate else chunk)
                    done += len(chunk)
                    if done % (512 * 1024 * 1024) < CHUNK:
                        rate = done / max(1, time.time() - t0) / 1e6
                        print(f"  {name}: {done / 1e9:.1f} of {info.compress_size / 1e9:.1f} GB ({rate:.0f} MB/s)", flush=True)
            except OSError:
                f = RangeFile(url)
        if inflate:
            out.write(inflate.flush())
    if tmp.stat().st_size != info.file_size:
        raise IOError(f"{name}: wrote {tmp.stat().st_size} bytes, expected {info.file_size}")
    tmp.rename(dest)
    return dest


if __name__ == "__main__":
    # python pipeline/remote_zip.py <zip url> [member dest]
    if len(sys.argv) == 2:
        for i in members(sys.argv[1]):
            print(f"{i.filename:60} {i.file_size / 1e9:8.3f} GB")
    else:
        print(extract_member(sys.argv[1], sys.argv[2], sys.argv[3]))
