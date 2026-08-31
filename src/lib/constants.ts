/** Text pasted or typed past this length is carried as a "Pasted text.txt"
 * attachment instead of raw inline text — shared by the composer (which
 * intercepts long pastes as they happen) and the send flow (a fallback for
 * anything that reaches that length without going through paste, e.g. text
 * typed directly). Both paths produce the same kind of attachment, so a
 * message ends up represented identically either way. */
export const LONG_PASTE_THRESHOLD = 250;
