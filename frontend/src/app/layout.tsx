import type {Metadata} from 'next';
import '@fontsource-variable/inter';
import '@/styles/globals.css';

export const metadata: Metadata = {
  title: 'Adjacent Studio',
  description: 'Your brand, creative ideas and videos in one workspace.',
};

export default function RootLayout({children}: Readonly<{children: React.ReactNode}>) {
  return <html lang="en"><body>{children}</body></html>;
}
