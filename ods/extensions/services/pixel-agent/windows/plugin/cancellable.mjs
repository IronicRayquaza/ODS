// The Pixel plugin's Windows cancellable exec form (tool-loop-guard.mjs on win32):
//   ods-cancellable <sha256 of the run id> <base64 command>
// Direct and Tool Search exec both pass the guard, so one command can arrive
// wrapped twice; both layers carry the same marker id.
import path from 'node:path';

const CANCELLABLE = /^ods-cancellable ([a-f0-9]{64}) ([A-Za-z0-9+/=]+)$/;

export function unwrapCancellable(command, controlRoot) {
  const first = CANCELLABLE.exec(command);
  let text = command;
  for (let layer = first; layer; layer = CANCELLABLE.exec(text)) text = Buffer.from(layer[2], 'base64').toString('utf8');
  return { text, marker: first ? path.win32.join(controlRoot, `${first[1]}.cancel`) : '' };
}
