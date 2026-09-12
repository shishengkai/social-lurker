/* Local WASM loader; stream-copy everything after the 128 KiB encrypted header.
 * The pinned WASM/glue and MIT attribution are adjacent. No GUI/browser/server.
 */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { pipeline } = require('node:stream/promises');
const SIZE = 131072;

async function keystream(key) {
  const sandbox = {
    WebAssembly, TextDecoder, TextEncoder, setTimeout, clearTimeout,
    performance, console: { log() {}, warn() {}, error() {} },
    location: { href: 'file:///wasm_video_decode.js' },
    document: { title: '', currentScript: { src: 'wasm_video_decode.js' } },
    VTS_WASM_URL: 'wasm_video_decode.wasm',
  };
  sandbox.self = sandbox;
  sandbox.window = sandbox;
  sandbox.Module = { wasmBinary: fs.readFileSync(path.join(__dirname, 'wasm_video_decode.wasm')) };
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('WASM_TIMEOUT')), 60000);
    sandbox.Module.onRuntimeInitialized = () => { clearTimeout(timer); resolve(); };
    sandbox.Module.onAbort = () => { clearTimeout(timer); reject(new Error('WASM_ABORT')); };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, 'wasm_video_decode.js'), 'utf8'), sandbox);
  });
  let result;
  sandbox.wasm_isaac_generate = (pointer, length) => {
    result = Buffer.from(sandbox.Module.HEAPU8.subarray(pointer, pointer + length)).reverse();
  };
  const decoder = new sandbox.Module.WxIsaac64(String(key));
  try { decoder.generate(SIZE); } finally { decoder.delete(); }
  if (!result || result.length !== SIZE) throw new Error('KEYSTREAM_INVALID');
  return result;
}

async function main() {
  const { input, output, decode_key } = JSON.parse(fs.readFileSync(0, 'utf8'));
  if (typeof decode_key !== 'string' || !decode_key) throw new Error('KEY_REQUIRED');
  const source = fs.openSync(input, 'r');
  const head = Buffer.alloc(SIZE);
  let count;
  try { count = fs.readSync(source, head, 0, SIZE, 0); } finally { fs.closeSync(source); }
  const prefix = head.subarray(0, count);
  if (prefix.subarray(4, 8).toString('ascii') !== 'ftyp') {
    const key = await keystream(decode_key);
    for (let i = 0; i < count; i++) prefix[i] ^= key[i];
  }
  if (prefix.subarray(4, 8).toString('ascii') !== 'ftyp') throw new Error('DECRYPT_INVALID');
  const partial = output + '.part';
  try {
    fs.writeFileSync(partial, prefix, { mode: 0o600 });
    await pipeline(fs.createReadStream(input, { start: count }), fs.createWriteStream(partial, { flags: 'a' }));
    fs.renameSync(partial, output);
  } finally { if (fs.existsSync(partial)) fs.unlinkSync(partial); }
  process.stdout.write('{"ok":true}\n');
}
main().catch(() => { process.stderr.write('DECRYPT_FAILED\n'); process.exitCode = 1; });
