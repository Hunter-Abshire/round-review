module.exports = {
  root: true,
  parser: '@typescript-eslint/parser',
  parserOptions: { project: './tsconfig.test.json', tsconfigRootDir: __dirname },
  plugins: ['@typescript-eslint', 'jest', 'prettier'],
  extends: [
    'eslint:recommended',
    'plugin:@typescript-eslint/recommended',
    'plugin:jest/recommended',
    'plugin:prettier/recommended',
  ],
  env: { node: true, browser: true, es2022: true, 'jest/globals': true },
  rules: { '@typescript-eslint/no-explicit-any': 'error' },
};
