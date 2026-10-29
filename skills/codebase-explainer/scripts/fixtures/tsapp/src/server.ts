import { createServer } from 'node:http';

export async function start() {
  const { page } = await import('./lazy');
  return createServer(() => page());
}
