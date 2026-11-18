// /presenter — the two-note chime a timer that ran out plays on the presenter's own speakers.

let audio = null;

/** Play the chime; a machine with no audio device stays silent. */
export function chime() {
  try {
    audio = audio || new AudioContext();
    [0, 0.18].forEach((delay, i) => {
      const o = audio.createOscillator();
      const g = audio.createGain();
      o.frequency.value = i ? 1320 : 880;
      g.gain.setValueAtTime(0.0001, audio.currentTime + delay);
      g.gain.exponentialRampToValueAtTime(0.3, audio.currentTime + delay + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, audio.currentTime + delay + 0.6);
      o.connect(g).connect(audio.destination);
      o.start(audio.currentTime + delay);
      o.stop(audio.currentTime + delay + 0.65);
    });
  } catch (e) { /* no audio device */ }
}
