#!/usr/bin/env node
/**
 * Regression check for src/utils/maskSensitive.ts against the corpus in
 * src/utils/maskSensitive.cases.ts.
 *
 *     npm run check:masking
 *
 * Deliberately dependency-free: it shells out to the TypeScript compiler
 * that is already a devDependency rather than adding a test runner, because
 * this project has no frontend test framework and one check script is not
 * a reason to introduce one.
 */
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const out = mkdtempSync(join(tmpdir(), 'maskcheck-'));
try {
  execFileSync(
    'npx',
    ['tsc', 'src/utils/maskSensitive.ts', 'src/utils/maskSensitive.cases.ts',
     '--outDir', out, '--target', 'es2020', '--module', 'esnext', '--moduleResolution', 'bundler'],
    { stdio: 'inherit' },
  );

  const { maskText } = await import(pathToFileURL(join(out, 'maskSensitive.js')));
  const { MASK_CASES } = await import(pathToFileURL(join(out, 'maskSensitive.cases.js')));

  let leaks = 0;
  let overMasked = 0;

  for (const c of MASK_CASES) {
    const masked = maskText(c.line);
    const survived = (c.mustNotSurvive ?? []).filter((t) => masked.includes(t));
    const destroyed = (c.mustSurvive ?? []).filter((t) => !masked.includes(t));
    if (!survived.length && !destroyed.length) continue;

    if (survived.length) leaks++;
    if (destroyed.length) overMasked++;
    console.error(`\n${survived.length ? 'LEAK' : 'OVER-MASKED'}  ${c.name}`);
    console.error(`  note   ${c.note}`);
    console.error(`  in     ${c.line}`);
    console.error(`  out    ${masked}`);
    if (survived.length) console.error(`  LEAKED    ${JSON.stringify(survived)}`);
    if (destroyed.length) console.error(`  DESTROYED ${JSON.stringify(destroyed)}`);
  }

  const failed = leaks + overMasked;
  if (failed) {
    console.error(`\n${MASK_CASES.length} cases: ${leaks} leaking, ${overMasked} over-masked\n`);
    process.exit(1);
  }
  console.log(`${MASK_CASES.length} masking cases passed (no leaks, no over-masking)`);
} finally {
  rmSync(out, { recursive: true, force: true });
}
