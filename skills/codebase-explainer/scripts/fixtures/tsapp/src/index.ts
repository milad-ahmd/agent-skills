import React from 'react';
import { useQuery } from '@tanstack/query/react';
import { add } from '@/lib';
// import { gone } from './deleted';
/* import { alsoGone } from './deleted2'; */
export * from './util';

export const total = add(1, 2) + (React ? 0 : 1) + (useQuery ? 0 : 1);
