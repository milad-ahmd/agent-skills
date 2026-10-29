import { add } from './lib/math.js';
import type { Lazy } from '@lib';

export const two = add(1, 1) as unknown as Lazy;
