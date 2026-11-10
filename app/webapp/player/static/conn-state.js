// The quiz player's connection label, declared once: checking, online, polling or offline.
// `connState` is the one function that reads the page's facts (did the ping answer, is the poll
// timer running, is the socket open, has the player joined) and says which of the four the dot and
// the label show; `play.js` calls it from `setConn` and never spells the states. It throws on a
// ping answer that is not null, true or false, so a stray value cannot pass as a connection.
// No imports, so Node runs this file as it is (tests/webapp/test_conn_state.py).

export const ConnState = Object.freeze({
  CHECKING: 'checking',
  ONLINE: 'online',
  POLLING: 'polling',
  OFFLINE: 'offline',
});

export const MEANINGS = Object.freeze({
  [ConnState.CHECKING]: 'nothing has answered yet',
  [ConnState.ONLINE]: 'the server answered: the socket is open, or the player has not joined and only the ping ran',
  [ConnState.POLLING]: 'the socket is down or flaky and the page polls the server every second',
  [ConnState.OFFLINE]: 'the last request failed',
});

/**
 * @param {{online: boolean|null, polling: boolean, wsOpen: boolean, joined: boolean}} facts
 *   online: the last request's outcome (null before the first); polling: the poll timer runs;
 *   wsOpen: the socket is open; joined: the player has an identity.
 */
export function connState({ online, polling, wsOpen, joined }) {
  if (online !== null && typeof online !== 'boolean') throw new Error(`the ping answer must be null, true or false, not ${online}`);
  if (online === false) return ConnState.OFFLINE;
  // Not joined yet: the ping answered, that is all there is to say.
  if (!joined && online) return ConnState.ONLINE;
  if (polling) return ConnState.POLLING;
  return wsOpen ? ConnState.ONLINE : ConnState.CHECKING;
}
