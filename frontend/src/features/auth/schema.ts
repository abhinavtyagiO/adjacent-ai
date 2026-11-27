import {z} from 'zod';

export const loginSchema = z.object({
  passphrase: z.string().min(1, 'Enter your workspace passphrase.').max(512),
}).strict();

export type LoginValues = z.infer<typeof loginSchema>;
