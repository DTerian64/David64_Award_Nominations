import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';

// Bundle real evidence renderers while isolating browser authentication.
export async function loadEvidenceModule(relativePath) {
  const bundle = await build({
    entryPoints: [fileURLToPath(new URL(relativePath, import.meta.url))],
    bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external',
    define: { 'import.meta.env': '{}' },
    plugins: [{ name: 'isolate-browser-auth', setup(builder) {
      builder.onResolve({ filter: /(?:ImpersonationContext|services\/api)$/ }, () => ({
        path: 'browser-auth', namespace: 'test',
      }));
      builder.onLoad({ filter: /.*/, namespace: 'test' }, () => ({
        contents: 'export function useImpersonation() { throw new Error("Unexpected auth access"); } export function getAccessToken() { throw new Error("Unexpected auth access"); }',
        loader: 'js',
      }));
    } }],
  });
  const componentModule = { exports: {} };
  new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(
    createRequire(import.meta.url), componentModule, componentModule.exports,
  );
  return componentModule.exports;
}
