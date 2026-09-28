// A quiz podium on the stage (#53): 3rd, then 2nd, then 1st. Drawn by the
// quiz's renderer (quiz/stage.js).

import { draw } from '/activities/quiz/stage.js';

export function render(body, result, ctx) {
  draw(body, ctx, 'quiz_podium');
}
