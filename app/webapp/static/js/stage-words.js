// The words the stage says by itself — not the facilitator's own questions and
// titles — in the session's language (session.yaml → language). The app, the
// presenter and the remote stay in English. src/live/plan.py keeps the default
// titles (break, breakout, "who are you with?", the Join slide) in step; a unit test checks.

const WORDS = {
  en: {
    chat_hint: 'Write your answer in the chat',
    quiz_hint: 'Answer on your phone or type A–D in the chat',
    break: 'Break',
    breakout: 'Breakout rooms',
    reveal: 'Who are you with?',
    join: 'Join on your phone',
    join_scan: 'Scan to answer',
    join_link: 'Go to',
    join_off: 'Phones cannot join right now: answer in the chat',
    joined: (n) => `${n} joined`,
    question: 'Your question here',
    rounds: { pairs: 'Pairs', g4a: 'Groups of 4 · A', g4b: 'Groups of 4 · B' },
    rooms: (n) => `${n} room${n === 1 ? '' : 's'}`,
    no_rooms: 'Shuffle the groups in the Groups tab first.',
    more: (n) => `+${n} more`,
  },
  es: {
    chat_hint: 'Escribe tu respuesta en el chat',
    quiz_hint: 'Responde en tu móvil o escribe A–D en el chat',
    break: 'Descanso',
    breakout: 'Salas de grupos',
    reveal: '¿Con quién estás?',
    join: 'Únete desde tu móvil',
    join_scan: 'Escanea para responder',
    join_link: 'Entra en',
    join_off: 'Ahora no se puede entrar desde el móvil: responde en el chat',
    joined: (n) => `${n} dentro`,
    question: 'Tu pregunta aquí',
    rounds: { pairs: 'Parejas', g4a: 'Grupos de 4 · A', g4b: 'Grupos de 4 · B' },
    rooms: (n) => `${n} sala${n === 1 ? '' : 's'}`,
    no_rooms: 'Haz los grupos en la pestaña Groups primero.',
    more: (n) => `+${n} más`,
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

/** "123 456": a 6-digit PIN in two groups, easier to read off the screen. */
export function spacedPin(pin) {
  const p = String(pin || '');
  return p.length === 6 ? `${p.slice(0, 3)} ${p.slice(3)}` : p;
}

/** The join link without its scheme and query: what a person types if they do not scan. */
export function shortUrl(url) {
  return String(url || '').replace(/^https?:\/\//, '').replace(/\?.*$/, '');
}
