// Regenerate parity.json from the rig's OpenUnum (run on the rig):
//   node gen-parity.mjs /home/corpunum/main > parity.json
import { pathToFileURL } from 'node:url';
const root = process.argv[2];
const sf = await import(pathToFileURL(`${root}/src/core/spoken-form.mjs`).href);
const vf = await import(pathToFileURL(`${root}/src/ui/modules/voice-fillers.js`).href);
const vt = await import(pathToFileURL(`${root}/src/ui/modules/voice-thinking.js`).href);
const replies = [
  'Sure. The capital of France is Paris.',
  'Here is what I found:\n\n- The battery is at 80%\n- Wi-Fi is connected to home\n- Bluetooth is off\n\nLet me know if you want more.',
  '## Weather\nIt will be sunny today, with a high of 24 degrees and a light breeze from the north. Tomorrow looks cooler and cloudier, so take a jacket if you go out. The weekend should be dry overall, though rain is possible on Sunday evening.',
  '1. Open the settings\n2. Tap Wi-Fi\n3. Pick your network\n4. Enter the password\n5. Press connect',
  'The result is **42**, see [the docs](https://example.com/docs) or `config.json` in /home/user/project/config.json. It is ~15% faster & uses 3/4 of the memory => good.',
  'Η πρωτεύουσα της Γαλλίας είναι το Παρίσι. Είναι μια πολύ όμορφη πόλη με πολλά μουσεία και πάρκα. Μπορείς να την επισκεφτείς οποιαδήποτε εποχή του χρόνου, αλλά η άνοιξη είναι ιδιαίτερα ευχάριστη.',
  'Βρήκα τα εξής:\n- Η μπαταρία είναι στο 80%\n- Το Wi-Fi είναι συνδεδεμένο\n- Το Bluetooth είναι κλειστό',
  '```js\nconsole.log(1)\n```\nThat snippet prints one. It is the simplest possible program and it does nothing else at all, which is why it is a good first test of anything.',
  'A very long single sentence that keeps going and going because there is a lot to say about this particular subject, and it never really stops until we reach the end of the line which is much too far away.',
  'Done.',
  '',
  'First thought. Second thought is a little longer than the first. Third thought is again fairly long to avoid being blocked. Fourth sentence here is also long enough. Fifth sentence goes on a bit as well, yes. Sixth sentence closes this paragraph off nicely.',
  'Temperature is 21 and humidity is 40 - that is fine — really fine. See #3 for details: it matters. Use and/or both options.'
];
const questions = ['What is the weather in Athens?', 'hello', 'Hi there!', 'Send a text to Maria', 'write me a poem about the sea', 'how much is 15% of 80', 'calculate 12 * 7', 'tell me a joke', 'What time is it', 'γεια σου', 'πες μου για την Αθήνα', 'τι καιρό κάνει;', 'στείλε μήνυμα στη Μαρία', 'γράψε ένα ποίημα', 'πόσο κάνει 12 x 7', 'Καλημέρα', 'what is the battery level', '', 'why does the sky look blue', 'set a timer for five minutes', 'Ποιος είναι ο πρωθυπουργός'];
const thinking = ['Let me check the battery level first. Then I will report.', 'The user wants the weather in Athens so I should call the weather tool and read the result.', '{"tool":"x"}', 'ok', '- first idea about the thing\n- second idea', 'Πρέπει να ελέγξω τη μπαταρία του κινητού και μετά να απαντήσω στον χρήστη.'];
const out = {
  enhance: replies.map((t) => ({ in: t, out: sf.enhanceForVoice(t), outEl: sf.enhanceForVoice(t, { lang: 'el' }), light: sf.enhanceForVoice(t, { style: 'light', seed: 'abc' }) })),
  strip: replies.map((t) => ({ in: t, out: sf.stripForSpeech(t) })),
  classify: questions.map((q) => ({ in: q, category: vf.classifyQuestion(q), lang: vf.fillerLang(q) })),
  thinking: thinking.map((t) => ({ in: t, summary: vt.summarizeThinking(t), spoken0: vt.thinkingToSpoken(t, { index: 0 }), spoken1: vt.thinkingToSpoken(t, { index: 1 }) })),
};
console.log(JSON.stringify(out, null, 1));
