import { useEffect, useState } from 'react';
import { FieldLabel, TextArea } from './forms';
import { normalizeLineArrayInput } from './channelDna';

export function LineArrayEditor({ label, value, onChange }: { label: string; value: string[]; onChange: (value: string[]) => void }) {
  const [text, setText] = useState(value.join('\n'));
  const [error, setError] = useState('');
  useEffect(() => setText(value.join('\n')), [value]);

  return (
    <div className="grid gap-2">
      <FieldLabel>{label}</FieldLabel>
      <TextArea
        rows={3}
        value={text}
        onChange={(event) => {
          const next = event.target.value;
          setText(next);
          const normalized = normalizeLineArrayInput(next);
          if (normalized.duplicates.length) {
            setError(`Duplicate values: ${normalized.duplicates.join(', ')}`);
            return;
          }
          setError('');
          onChange(normalized.values);
        }}
      />
      {error ? <div role="alert" className="text-xs text-rose-300">{error}</div> : null}
    </div>
  );
}
