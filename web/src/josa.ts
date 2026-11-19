// Korean particles that depend on the last syllable of a name (콩이 → "콩이가", 콩 → "콩이"...).
const PAIRS: Record<string, [string, string]> = { "이/가": ["이", "가"], "와/과": ["과", "와"], "을/를": ["을", "를"], "은/는": ["은", "는"] };

export function hasBatchim(word: string): boolean {
  const c = word.trim().slice(-1).charCodeAt(0);
  if (c >= 0xac00 && c <= 0xd7a3) return (c - 0xac00) % 28 !== 0;
  return /[0-9lmnLMN]$/.test(word.trim()); // rough rule for letters and digits read aloud
}

/** josa("콩", "이/가") -> "콩이", josa("하댕이", "이/가") -> "하댕이가" */
export function josa(word: string, pair: keyof typeof PAIRS): string {
  const [withB, withoutB] = PAIRS[pair];
  return word + (hasBatchim(word) ? withB : withoutB);
}
