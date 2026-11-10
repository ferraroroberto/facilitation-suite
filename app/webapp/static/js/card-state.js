// A results or session card's load state, declared once: loading, ready, empty or error.
// The Analyze, Playbook and Conversation cards each read their data from the server and show it as
// `data-state` on the card; `setCardState` is the only code that writes that attribute and `advance`
// refuses a move the table does not list. State table and readers: docs/results.md.
// No imports, so Node runs this file as it is (tests/webapp/test_card_state.py).

export const CardState = Object.freeze({
  LOADING: 'loading',
  READY: 'ready',
  EMPTY: 'empty',
  ERROR: 'error',
});

export const MEANINGS = Object.freeze({
  [CardState.LOADING]: 'the first read, or a reload the user asked for, is in flight',
  [CardState.READY]: 'the read answered and the card shows what it found',
  [CardState.EMPTY]: 'the read answered and there is nothing to show yet',
  [CardState.ERROR]: 'the read failed; the card offers a retry',
});

// Each state and the states it may move to. A read settles into ready, empty or error from any
// state (a retry, a source switch, a create all read again); only a ready card goes back to
// loading, when the user reloads it: an empty or failed card shows its own action instead.
export const TRANSITIONS = Object.freeze({
  [CardState.LOADING]: [CardState.READY, CardState.EMPTY, CardState.ERROR],
  [CardState.READY]: [CardState.LOADING, CardState.READY, CardState.EMPTY, CardState.ERROR],
  [CardState.EMPTY]: [CardState.READY, CardState.EMPTY, CardState.ERROR],
  [CardState.ERROR]: [CardState.READY, CardState.EMPTY, CardState.ERROR],
});

/** `to` when `current` may move there (a card nothing was written to yet may start anywhere), otherwise it throws. */
export function advance(current, to) {
  if (!Object.values(CardState).includes(to)) throw new Error(`unknown card state ${to}`);
  if (current && !(TRANSITIONS[current] || []).includes(to)) throw new Error(`a card cannot go from ${current} to ${to}`);
  return to;
}

/** Write `to` on the card's `data-state`, refusing a move the table does not list. */
export function setCardState(card, to) {
  card.dataset.state = advance(card.dataset.state, to);
}

/** The state a finished read leaves: ready when it found something, empty when it found nothing. */
export function settledState(found) {
  return found ? CardState.READY : CardState.EMPTY;
}
