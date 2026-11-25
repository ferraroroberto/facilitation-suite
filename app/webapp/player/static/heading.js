// Which activity the phone's header names: the one on stage, read from the phone's view message.
// `quiz` is any quiz view (the default, a quiz view has no `kind`), `poll` a room poll, `form` a
// feedback form, `room` a room member with nothing to answer, `idle` no view at all (before joining,
// no game). No imports, so Node runs this file as it is (tests/webapp/test_player_heading.py).

export const HEADINGS = Object.freeze(['quiz', 'poll', 'form', 'room', 'idle']);

export function headingKind(view) {
  if (!view) return 'idle';
  if (view.kind === 'poll' || view.kind === 'form') return view.kind;
  if (view.kind === 'room') return view.quiz_join ? 'quiz' : 'room';
  return 'quiz';
}
