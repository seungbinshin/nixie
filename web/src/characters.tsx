// 하댕이 (puppy) and 하냥이 (kitten): original round mascots for the agent. The collar uses the theme accent.
export type CharacterId = "hadaeng" | "hanyang";
export const CHARACTERS: Record<CharacterId, { name: string; kind: string; intro: string }> = {
  hadaeng: { name: "하댕이", kind: "강아지", intro: "부지런하고 사람을 잘 따르는 강아지 에이전트" },
  hanyang: { name: "하냥이", kind: "고양이", intro: "꼼꼼하고 침착한 고양이 에이전트" },
};

const LINE = "#5c3a24";
const EYE = "#2b1d14";

function Puppy() {
  return (
    <>
      <path d="M24 36C9 42 7 74 20 86c8 7 17-1 17-14l2-30z" fill="#b97a4e" stroke={LINE} strokeWidth="3" strokeLinejoin="round" />
      <path d="M96 36c15 6 17 38 4 50-8 7-17-1-17-14l-2-30z" fill="#b97a4e" stroke={LINE} strokeWidth="3" strokeLinejoin="round" />
      <ellipse cx="60" cy="62" rx="40" ry="37" fill="#f7ddb5" stroke={LINE} strokeWidth="3" />
      <path d="M69 38c15-5 26 8 22 21-4 9-17 8-21-1-3-6-6-15-1-20z" fill="#e3b382" />
      <ellipse cx="60" cy="81" rx="20" ry="14" fill="#fff6e9" />
      <ellipse cx="44" cy="61" rx="5.4" ry="6.2" fill={EYE} />
      <ellipse cx="76" cy="61" rx="5.4" ry="6.2" fill={EYE} />
      <circle cx="45.9" cy="58.6" r="1.9" fill="#fff" />
      <circle cx="77.9" cy="58.6" r="1.9" fill="#fff" />
      <path d="M53.5 73.5q6.5-5 13 0-1.8 6-6.5 6t-6.5-6z" fill={EYE} />
      <path d="M57 86q3 8 6 0z" fill="#ff8fa3" />
      <path d="M60 79.5v4M52 84.5q4 5 8 0 4 5 8 0" stroke={EYE} strokeWidth="2.4" fill="none" strokeLinecap="round" />
      <ellipse cx="33" cy="74" rx="6" ry="3.6" fill="#ff9dae" opacity=".6" />
      <ellipse cx="87" cy="74" rx="6" ry="3.6" fill="#ff9dae" opacity=".6" />
      <path className="char-collar" d="M30 95q30 14 60 0l-1.5 8q-28.5 13-57 0z" stroke={LINE} strokeWidth="2" strokeLinejoin="round" />
      <circle cx="60" cy="108" r="5.2" fill="#ffd25a" stroke={LINE} strokeWidth="2" />
    </>
  );
}

function Kitten() {
  const L = "#6b4423";
  return (
    <>
      <path d="M23 50 27 13l27 21z" fill="#ffd7a3" stroke={L} strokeWidth="3" strokeLinejoin="round" />
      <path d="M97 50 93 13 66 34z" fill="#ffd7a3" stroke={L} strokeWidth="3" strokeLinejoin="round" />
      <path d="m31 40 2-16 13 11z" fill="#ffb3c1" />
      <path d="m89 40-2-16-13 11z" fill="#ffb3c1" />
      <ellipse cx="60" cy="63" rx="42" ry="36" fill="#ffd7a3" stroke={L} strokeWidth="3" />
      <path d="M60 29v10M49 31l2.5 8M71 31l-2.5 8" stroke="#f0a55a" strokeWidth="4" strokeLinecap="round" />
      <ellipse cx="44" cy="63" rx="5.6" ry="6.8" fill={EYE} />
      <ellipse cx="76" cy="63" rx="5.6" ry="6.8" fill={EYE} />
      <circle cx="46" cy="60.4" r="2" fill="#fff" />
      <circle cx="78" cy="60.4" r="2" fill="#fff" />
      <path d="M56 73h8l-4 4.5z" fill="#ff7f96" stroke="#ff7f96" strokeWidth="1.5" strokeLinejoin="round" />
      <path d="M60 77.5q-2.5 5.5-7.5 3.5M60 77.5q2.5 5.5 7.5 3.5" stroke={EYE} strokeWidth="2.4" fill="none" strokeLinecap="round" />
      <path d="M20 67l13 2M20 75l13-1.5M100 67l-13 2M100 75l-13-1.5" stroke={L} strokeWidth="2" strokeLinecap="round" />
      <ellipse cx="34" cy="76" rx="6" ry="3.6" fill="#ff9dae" opacity=".6" />
      <ellipse cx="86" cy="76" rx="6" ry="3.6" fill="#ff9dae" opacity=".6" />
      <path className="char-collar" d="M30 95q30 14 60 0l-1.5 8q-28.5 13-57 0z" stroke={L} strokeWidth="2" strokeLinejoin="round" />
      <circle cx="60" cy="108.5" r="5.4" fill="#ffd25a" stroke={L} strokeWidth="2" />
      <path d="M55.5 109.5h9" stroke={L} strokeWidth="1.6" strokeLinecap="round" />
    </>
  );
}

interface AvatarProps {
  character?: string | null;
  size?: number;
  label?: string; // accessible name; without it the avatar is decorative
  fallback?: string; // letter for an agent without a known character
}

export function Avatar({ character, size = 40, label, fallback }: AvatarProps) {
  const a11y = label ? { role: "img", "aria-label": label } : { "aria-hidden": true as const };
  if (character !== "hadaeng" && character !== "hanyang") {
    return (
      <span className="avatar avatar--letter" style={{ width: size, height: size, fontSize: size * 0.45 }} {...a11y}>
        {(fallback || "?").slice(0, 1)}
      </span>
    );
  }
  return (
    <span className="avatar" style={{ width: size, height: size }} {...a11y}>
      <svg viewBox="0 0 120 120" width={size} height={size} focusable="false" aria-hidden="true">
        {character === "hadaeng" ? <Puppy /> : <Kitten />}
      </svg>
    </span>
  );
}
