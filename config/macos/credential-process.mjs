#!/usr/bin/env node
// Stdout is the SDK credential pipe. Never run this helper in a terminal.
import fs from 'node:fs';
import path from 'node:path';

try {
  if (process.argv.length !== 3 || process.stdout.isTTY) throw new Error();
  const file = path.resolve(process.argv[2]);
  for (let dir = path.dirname(file); ; dir = path.dirname(dir)) {
    if (fs.lstatSync(dir).isSymbolicLink()) throw new Error();
    if (dir === path.dirname(dir)) break;
  }
  const fd = fs.openSync(file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK);
  let data;
  try {
    const st = fs.fstatSync(fd);
    if (!st.isFile() || (st.mode & 0o077) || st.uid !== process.geteuid() || st.nlink !== 1 || st.size > 32768) throw new Error();
    data = JSON.parse(fs.readFileSync(fd, 'utf8'));
  } finally { fs.closeSync(fd); }
  if (!data || Array.isArray(data) || Object.keys(data).sort().join(',') !== 'AccessKeyId,Expiration,SecretAccessKey,SessionToken,Version' || data.Version !== 1) throw new Error();
  for (const [name, pattern] of Object.entries({AccessKeyId: /^ASIA[A-Z0-9]{12,124}$/, SecretAccessKey: /^[A-Za-z0-9/+=]{40}$/, SessionToken: /^[A-Za-z0-9/+=_-]{16,16384}$/})) {
    if (typeof data[name] !== 'string' || !pattern.test(data[name])) throw new Error();
  }
  if (typeof data.Expiration !== 'string' || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|\+00:00)$/.test(data.Expiration)) throw new Error();
  const ttl = Date.parse(data.Expiration) - Date.now();
  if (!Number.isFinite(ttl) || ttl <= 120000 || ttl > 86400000) throw new Error();
  process.stdout.write(JSON.stringify(data));
} catch {
  process.stderr.write('credential-process: unavailable\n');
  process.exitCode = 1;
}
