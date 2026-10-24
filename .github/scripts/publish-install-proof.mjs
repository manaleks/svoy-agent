// Public CI receives a closed summary, never private source/error/log text.
import {readFileSync, writeFileSync, mkdirSync, existsSync, lstatSync, readdirSync} from 'node:fs';
import {join, resolve, parse, sep} from 'node:path';
import {spawnSync} from 'node:child_process';
import {pathToFileURL} from 'node:url';
import assert from 'node:assert/strict';
const CHECK_NAMES = new Set(["native-agent-service", "native-panel-service", "native-user-bus", "real-gateway", "native-package", "clean-install", "panel-screenshot", "real-conversation", "conversation-screenshot", "permissions-and-personal", "generated-update", "generated-manual-rollback", "generated-auto-rollback", "generated-double-start", "interrupted-recovery", "generated-write-denial", "occupied-foreign-port", "generated-retry", "generated-final-rollback", "generated-rejection-wrong-signature", "generated-rejection-changed-document", "generated-rejection-wrong-archive", "generated-rejection-wrong-platform", "generated-rejection-wrong-commit", "generated-rejection-unknown-key", "generated-rejection-duplicate-inventory"]);
export function publicInstallSummary(input) {
  assert(['passed','failed'].includes(input.status));
  assert(/^[a-f0-9]{40}$/.test(input.commit));
  assert(input.sourceSHA256 === null || /^[a-f0-9]{64}$/.test(input.sourceSHA256));
  assert(['darwin-arm64','darwin-x64','linux-x64','win32-x64'].includes(input.platform));
  assert(Array.isArray(input.checks) && input.checks.length <= CHECK_NAMES.size);
  assert(new Set(input.checks.map(check => check.name)).size === input.checks.length);
  const checks = input.checks.map(check => {
    assert(CHECK_NAMES.has(check.name));
    assert(['passed','failed','blocked'].includes(check.result));
    return {name:check.name,result:check.result};
  });
  return {status:input.status,commit:input.commit,sourceSHA256:input.sourceSHA256,platform:input.platform,workingTree:input.workingTree === true,checks};
}
export function publishInstallProof(source, destination) {
  checkedDirectory(source, false);
  checkedDirectory(destination, true);
  assert.equal(readdirSync(destination).length, 0, 'Proof destination must be empty');
  let found = false;
  for (const os of ['mac','linux','windows']) {
    const file = join(source, `install-cycle-${os}.json`);
    if (!existsSync(file)) continue;
    assert(lstatSync(file).isFile() && !lstatSync(file).isSymbolicLink());
    const summary = publicInstallSummary(JSON.parse(readFileSync(file,'utf8')));
    writeFileSync(join(destination, `install-cycle-${os}.json`), JSON.stringify(summary,null,2), {flag:'wx'});
    found = true;
    for (const screen of ['panel','conversation']) {
      const name = `install-cycle-${os}-${screen}.png`, image = join(source,name);
      if (existsSync(image)) {
        assert(lstatSync(image).isFile() && !lstatSync(image).isSymbolicLink());
        assert(lstatSync(image).size <= 10 * 1024 * 1024);
        // Decode pixels and encode a fresh PNG. Never publish source metadata or trailing bytes.
        const result = spawnSync(process.platform === 'win32' ? 'python' : 'python3', ['-c', `
import sys, warnings
from PIL import Image
Image.MAX_IMAGE_PIXELS = 20000000
warnings.simplefilter('error')
with Image.open(sys.argv[1]) as original:
    if original.format != 'PNG' or not (0 < original.width <= 4096 and 0 < original.height <= 16384):
        raise ValueError('invalid screenshot')
    original.load()
    rgba = original.convert('RGBA')
    clean = Image.frombytes('RGBA', rgba.size, rgba.tobytes())
    with open(sys.argv[2], 'xb') as target:
        clean.save(target, format='PNG')
`, image, join(destination,name)], {encoding:'utf8',timeout:30000});
        assert.equal(result.status, 0, 'Screenshot decoding failed');
      }
    }
  }
  if (!found) writeFileSync(join(destination,'preflight.json'),JSON.stringify({status:'failed',phase:'preflight'}));
}
function checkedDirectory(directory, create) {
  assert.equal(directory, resolve(directory));
  let current = parse(directory).root;
  for (const part of directory.slice(current.length).split(sep).filter(Boolean)) {
    current = join(current, part);
    try {
      const entry = lstatSync(current);
      assert(entry.isDirectory() && !entry.isSymbolicLink(), 'Unsafe proof directory');
    } catch (error) {
      if (!create || error.code !== 'ENOENT') throw error;
      mkdirSync(current);
    }
  }
}
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) publishInstallProof(resolve(process.argv[2]),resolve(process.argv[3]));
