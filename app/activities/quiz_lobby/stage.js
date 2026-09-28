// A quiz lobby on the stage (#53): the QR code, the join link and PIN, and the
// players popping in as they join. Drawn by the quiz's renderer (quiz/stage.js).

import { draw } from '/activities/quiz/stage.js';

export function render(body, result, ctx) {
  draw(body, ctx, 'quiz_lobby');
}
