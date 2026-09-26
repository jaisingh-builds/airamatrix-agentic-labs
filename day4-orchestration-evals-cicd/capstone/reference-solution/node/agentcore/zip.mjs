// A minimal zip writer (deflate, no ZIP64) with node:zlib, so packaging works the same on macOS, Linux and
// Windows without a `zip` binary. AgentCore Runtime reads POSIX permissions from the archive: files 0644, dirs 0755.
import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";

export function zip(entries, outFile) {         // entries: [{name, data: Buffer}]
  const locals = [], central = [];
  let offset = 0;
  const dos = (() => {
    const d = new Date();
    return { time: (d.getHours() << 11) | (d.getMinutes() << 5) | (d.getSeconds() >> 1),
      date: ((d.getFullYear() - 1980) << 9) | ((d.getMonth() + 1) << 5) | d.getDate() };
  })();
  for (const { name, data } of entries) {
    const n = Buffer.from(name.replace(/\\/g, "/"), "utf8");
    const comp = zlib.deflateRawSync(data, { level: 6 });
    const crc = zlib.crc32(data) >>> 0;
    const h = Buffer.alloc(30);
    h.writeUInt32LE(0x04034b50, 0); h.writeUInt16LE(20, 4); h.writeUInt16LE(0x0800, 6); h.writeUInt16LE(8, 8);
    h.writeUInt16LE(dos.time, 10); h.writeUInt16LE(dos.date, 12); h.writeUInt32LE(crc, 14);
    h.writeUInt32LE(comp.length, 18); h.writeUInt32LE(data.length, 22); h.writeUInt16LE(n.length, 26); h.writeUInt16LE(0, 28);
    locals.push(h, n, comp);
    const c = Buffer.alloc(46);
    c.writeUInt32LE(0x02014b50, 0); c.writeUInt16LE((3 << 8) | 20, 4); c.writeUInt16LE(20, 6); c.writeUInt16LE(0x0800, 8);
    c.writeUInt16LE(8, 10); c.writeUInt16LE(dos.time, 12); c.writeUInt16LE(dos.date, 14); c.writeUInt32LE(crc, 16);
    c.writeUInt32LE(comp.length, 20); c.writeUInt32LE(data.length, 24); c.writeUInt16LE(n.length, 28);
    c.writeUInt32LE(((0o100644 << 16) >>> 0), 38); c.writeUInt32LE(offset, 42);
    central.push(c, n);
    offset += 30 + n.length + comp.length;
  }
  const cd = Buffer.concat(central);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0); end.writeUInt16LE(entries.length, 8); end.writeUInt16LE(entries.length, 10);
  end.writeUInt32LE(cd.length, 12); end.writeUInt32LE(offset, 16);
  if (entries.length > 0xffff || offset > 0xffffffff) throw new Error("package too large for a plain zip");
  fs.writeFileSync(outFile, Buffer.concat([...locals, cd, end]));
  return fs.statSync(outFile).size;
}

/** Every regular file under dir (no symlinks), as zip entries rooted at prefix. skip(rel) drops files. */
export function tree(dir, prefix, skip = () => false) {
  const out = [];
  const walk = (d) => {
    for (const e of fs.readdirSync(d, { withFileTypes: true })) {
      const full = path.join(d, e.name);
      const rel = path.relative(dir, full).split(path.sep).join("/");
      if (e.isSymbolicLink() || skip(rel)) continue;
      if (e.isDirectory()) walk(full);
      else if (e.isFile()) out.push({ name: `${prefix}${rel}`, data: fs.readFileSync(full) });
    }
  };
  walk(dir);
  return out;
}
