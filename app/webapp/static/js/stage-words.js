// The words the stage says by itself — not the facilitator's own questions and
// titles — in the session's language (session.yaml → language). The app, the
// presenter and the remote stay in English. src/live/plan.py keeps the default
// titles (break, breakout, "who are you with?") in step; a unit test checks.

const WORDS = {
  en: {
    chat_hint: 'Write your answer in the chat',
    break: 'Break',
    breakout: 'Breakout rooms',
    reveal: 'Who are you with?',
    question: 'Your question here',
    rounds: { pairs: 'Pairs', g4a: 'Groups of 4 · A', g4b: 'Groups of 4 · B' },
    rooms: (n) => `${n} room${n === 1 ? '' : 's'}`,
    no_rooms: 'Shuffle the groups in the Groups tab first.',
  },
  es: {
    chat_hint: 'Escribe tu respuesta en el chat',
    break: 'Descanso',
    breakout: 'Salas de grupos',
    reveal: '¿Con quién estás?',
    question: 'Tu pregunta aquí',
    rounds: { pairs: 'Parejas', g4a: 'Grupos de 4 · A', g4b: 'Grupos de 4 · B' },
    rooms: (n) => `${n} sala${n === 1 ? '' : 's'}`,
    no_rooms: 'Haz los grupos en la pestaña Groups primero.',
  },
};

export const LANGUAGES = [['en', 'English'], ['es', 'Español']];

/** The stage's words in `lang` (English when unknown). */
export function words(lang) {
  return WORDS[lang] || WORDS.en;
}

/** "Pairs · 9 rooms": a breakout round and its room count, under a reveal's or a breakout's title. */
export function roundLine(lang, round, rooms) {
  const w = words(lang);
  if (!round) return '';
  const n = (rooms || []).length;
  return `${w.rounds[round] || round}${n ? ` · ${w.rooms(n)}` : ''}`;
}
