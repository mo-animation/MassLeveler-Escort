// Minimal read-only PK2 reader. Usage:
//   node --openssl-legacy-provider pk2.js <file.pk2> list [filter]
//   node --openssl-legacy-provider pk2.js <file.pk2> extract <path-inside> <outfile>
const fs = require('fs');
const crypto = require('crypto');

const [, , pk2Path, cmd, arg1, arg2] = process.argv;
const KEY_ASCII = process.env.PK2_KEY || '169841';
const BASE = [0x03, 0xF8, 0xE4, 0x44, 0x88, 0x99, 0x3F, 0x64, 0xFE, 0x35];
const key = Buffer.from([...Buffer.from(KEY_ASCII, 'ascii')].map((b, i) => b ^ BASE[i]));

function swap32(buf) {
  const out = Buffer.alloc(buf.length);
  for (let i = 0; i < buf.length; i += 4) {
    out[i] = buf[i + 3]; out[i + 1] = buf[i + 2]; out[i + 2] = buf[i + 1]; out[i + 3] = buf[i];
  }
  return out;
}
function decrypt(buf) {
  const d = crypto.createDecipheriv('bf-ecb', key, null);
  d.setAutoPadding(false);
  return swap32(Buffer.concat([d.update(swap32(buf)), d.final()]));
}

const fd = fs.openSync(pk2Path, 'r');
function readAt(pos, len) {
  const b = Buffer.alloc(len);
  fs.readSync(fd, b, 0, len, pos);
  return b;
}

function readBlockChain(pos) {
  const entries = [];
  const seen = new Set();
  while (pos && !seen.has(pos)) {
    seen.add(pos);
    const blk = decrypt(readAt(pos, 2560));
    let next = 0;
    for (let i = 0; i < 20; i++) {
      const e = blk.subarray(i * 128, i * 128 + 128);
      const type = e[0];
      let name = e.subarray(1, 82).toString('latin1');
      name = name.substring(0, name.indexOf('\0') >= 0 ? name.indexOf('\0') : name.length);
      const position = Number(e.readBigInt64LE(106));
      const size = e.readUInt32LE(114);
      const nextChain = Number(e.readBigInt64LE(118));
      if (i === 19) next = nextChain;
      if (type === 1 || type === 2) entries.push({ type, name, position, size });
    }
    pos = next;
  }
  return entries;
}

function walk(pos, prefix, cb, depth = 0) {
  if (depth > 30) return;
  for (const e of readBlockChain(pos)) {
    if (e.name === '.' || e.name === '..') continue;
    const full = prefix ? prefix + '\\' + e.name : e.name;
    cb(full, e);
    if (e.type === 1) walk(e.position, full, cb, depth + 1);
  }
}

const ROOT = 256;
const root = readBlockChain(ROOT);
if (!root.length || root[0].name !== '.') {
  console.error('Root block did not decode (wrong key?). First names:', root.slice(0, 3).map(e => e.name));
  process.exit(2);
}

if (cmd === 'list') {
  const re = arg1 ? new RegExp(arg1, 'i') : null;
  walk(ROOT, '', (full, e) => {
    if (e.type === 2 && (!re || re.test(full))) console.log(e.size + '\t' + full);
  });
} else if (cmd === 'extract') {
  const target = arg1.toLowerCase();
  let found = false;
  walk(ROOT, '', (full, e) => {
    if (!found && e.type === 2 && full.toLowerCase() === target) {
      fs.writeFileSync(arg2, readAt(e.position, e.size));
      found = true;
      console.log('extracted', full, e.size);
    }
  });
  if (!found) { console.error('not found'); process.exit(1); }
}
