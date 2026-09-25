import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    include: ['src/**/*.test.js'],
    // fontSize.test.js uses node:test and runs with `node --test`.
    exclude: ['src/utils/fontSize.test.js', 'node_modules/**'],
  },
});
