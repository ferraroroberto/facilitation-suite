// The chat source names, shared by the presenter, the phone remote and Settings so they never
// disagree about which chat the reader is on.

export const SOURCE_NAMES = { zoom: 'Zoom', teams: 'Teams', meet: 'Meet' };

/** The reader's source: its last heartbeat says Zoom, Teams or Meet (the simulator counts as Zoom);
 *  a stopped reader shows the source chosen on the switch, the one it starts on. */
export function sourceOf(r) {
  const s = !r || r.state === 'off' || !r.source ? r && r.chosen : r.source;
  return s in SOURCE_NAMES ? s : 'zoom';
}

/** The display name for a live reader block. */
export const sourceName = (r) => SOURCE_NAMES[sourceOf(r)];

/** The display name for a configured source id (Settings has no live heartbeat). */
export const configuredSourceName = (source) => SOURCE_NAMES[source] || SOURCE_NAMES.zoom;
