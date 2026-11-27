import {staticFile} from 'remotion';

/** The worker stages files locally. The Player uses the authenticated spec media URL. */
export function compositionMedia(source: string) {
  return source.startsWith('/api/backend/v1/video-specs/') ? source : staticFile(source);
}
