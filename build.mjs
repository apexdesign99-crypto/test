// www/js/app.js を生成するビルドスクリプト（esbuild でバンドル）
import { build, context } from 'esbuild';

const options = {
  entryPoints: ['src/app.js'],
  bundle: true,
  format: 'iife',
  target: ['es2020', 'safari14'],
  outfile: 'www/js/app.js',
  sourcemap: true,
  minify: !process.argv.includes('--dev'),
  logLevel: 'info',
};

if (process.argv.includes('--watch')) {
  const ctx = await context(options);
  await ctx.watch();
  console.log('watching src/ …');
} else {
  await build(options);
}
