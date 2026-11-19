import { useEffect, useRef, useState } from "react";
import { Avatar, CHARACTERS, type CharacterId } from "../characters";
import { applyTheme, FONTS, PRESETS, type FontId, type Mode, type Theme } from "../theme";
import type { Profile } from "../types";

interface Props {
  open: boolean;
  profile: Profile;
  onClose(): void;
  onSave(p: Pick<Profile, "character" | "agent_name" | "theme">): Promise<void>;
}

const MODES: { id: Mode; label: string }[] = [
  { id: "system", label: "시스템 설정" }, { id: "light", label: "밝게" }, { id: "dark", label: "어둡게" },
];

/** Customize: character (하댕이/하냥이), agent name, accent color, light/dark, font. Preview is live. */
export function Settings({ open, profile, onClose, onSave }: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  const [character, setCharacter] = useState<CharacterId>(profile.character);
  const [name, setName] = useState(profile.agent_name);
  const [theme, setTheme] = useState<Theme>(profile.theme);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) {
      setCharacter(profile.character);
      setName(profile.agent_name);
      setTheme(profile.theme);
      setError(null);
      d.showModal();
    } else if (!open && d.open) d.close();
  }, [open, profile]);

  useEffect(() => {
    if (open) applyTheme(theme); // live preview
  }, [open, theme]);

  const cancel = () => {
    applyTheme(profile.theme);
    onClose();
  };

  const pickCharacter = (c: CharacterId) => {
    if (name.trim() === CHARACTERS[character].name || !name.trim()) setName(CHARACTERS[c].name); // default name follows
    setCharacter(c);
  };

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    const n = name.trim();
    if (!n) {
      setError("에이전트 이름을 입력하십시오.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await onSave({ character, agent_name: n, theme });
      onClose();
    } catch {
      setError("저장하지 못했습니다. 다시 시도하십시오.");
    } finally {
      setSaving(false);
    }
  };

  const custom = !PRESETS.some((p) => p.color === theme.accent);
  return (
    <dialog ref={ref} className="settings" aria-labelledby="settings-h" onCancel={(e) => { e.preventDefault(); cancel(); }}>
      <form onSubmit={save}>
        <h2 id="settings-h">에이전트와 화면 꾸미기</h2>

        <fieldset>
          <legend>캐릭터</legend>
          <div className="char-options">
            {(Object.keys(CHARACTERS) as CharacterId[]).map((c) => (
              <label key={c} className={"char-option" + (character === c ? " char-option--on" : "")}>
                <input type="radio" name="character" value={c} checked={character === c} onChange={() => pickCharacter(c)} />
                <Avatar character={c} size={72} />
                <span className="char-name">{CHARACTERS[c].name}</span>
                <span className="char-intro">{CHARACTERS[c].intro}</span>
              </label>
            ))}
          </div>
        </fieldset>

        <div className="field">
          <label htmlFor="agent-name">에이전트 이름</label>
          <div className="row">
            <input id="agent-name" type="text" maxLength={20} value={name} autoComplete="off"
                   aria-describedby="agent-name-help" onChange={(e) => setName(e.target.value)} />
            <button type="button" className="btn btn--small" onClick={() => setName(CHARACTERS[character].name)}>기본 이름</button>
          </div>
          <p id="agent-name-help" className="help">1~20자. 다른 사람의 에이전트와 대화할 때도 이 이름을 사용합니다.</p>
        </div>

        <fieldset>
          <legend>테마 색</legend>
          <div className="swatches">
            {PRESETS.map((p) => (
              <label key={p.color} className="swatch" title={p.name}>
                <input type="radio" name="accent" value={p.color} checked={theme.accent === p.color}
                       onChange={() => setTheme({ ...theme, accent: p.color })} />
                <span className="swatch-color" style={{ background: p.color }} aria-hidden="true" />
                <span className="sr-only">{p.name}</span>
              </label>
            ))}
            <label className={"swatch swatch--custom" + (custom ? " swatch--on" : "")}>
              <input type="color" value={theme.accent} onChange={(e) => setTheme({ ...theme, accent: e.target.value })} />
              <span>직접 고르기</span>
            </label>
          </div>
          <p className="help">어떤 색을 골라도 글자와 버튼은 읽기 쉬운 대비(4.5:1 이상)로 자동 조정됩니다.</p>
        </fieldset>

        <fieldset>
          <legend>화면 모드</legend>
          <div className="seg">
            {MODES.map((m) => (
              <label key={m.id} className="seg-item">
                <input type="radio" name="mode" value={m.id} checked={theme.mode === m.id} onChange={() => setTheme({ ...theme, mode: m.id })} />
                <span>{m.label}</span>
              </label>
            ))}
          </div>
        </fieldset>

        <fieldset>
          <legend>글꼴</legend>
          <div className="seg seg--fonts">
            {(Object.keys(FONTS) as FontId[]).map((f) => (
              <label key={f} className="seg-item">
                <input type="radio" name="font" value={f} checked={theme.font === f} onChange={() => setTheme({ ...theme, font: f })} />
                <span className={`font-sample font-sample--${f}`}>{FONTS[f].label}</span>
              </label>
            ))}
          </div>
        </fieldset>

        <div className="preview" aria-hidden="true">
          <Avatar character={character} size={44} />
          <div>
            <p className="preview-name">{name.trim() || CHARACTERS[character].name}</p>
            <p className="preview-text">안녕하세요! 오늘은 어떤 업무를 도와 드릴까요?</p>
          </div>
          <span className="btn btn--primary btn--small">미리보기 버튼</span>
        </div>

        {error && <p className="error" role="alert">{error}</p>}
        <div className="dialog-actions">
          <button type="button" className="btn" onClick={cancel}>취소</button>
          <button type="submit" className="btn btn--primary" disabled={saving} aria-busy={saving}>저장</button>
        </div>
      </form>
    </dialog>
  );
}
