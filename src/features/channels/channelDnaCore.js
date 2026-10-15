const exactKeys = (value, expected, path) => {
  const actual = Object.keys(value).sort();
  const wanted = [...expected].sort();
  if (actual.length !== wanted.length || actual.some((key, index) => key !== wanted[index])) {
    throw new Error(`${path} must contain exactly: ${expected.join(', ')}.`);
  }
};

const objectValue = (value, path) => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`${path} must be an object.`);
  return value;
};

const textValue = (value, path) => {
  if (typeof value !== 'string' || !value.trim()) throw new Error(`${path} must be a non-empty string.`);
  return value.trim();
};

const numberValue = (value, path) => {
  if (typeof value !== 'number' || !Number.isFinite(value)) throw new Error(`${path} must be a finite number.`);
  return value;
};

const stringArray = (value, path) => {
  if (!Array.isArray(value)) throw new Error(`${path} must be an array.`);
  const normalized = value.map((item, index) => textValue(item, `${path}[${index}]`));
  const duplicate = normalized.find((item, index) => normalized.indexOf(item) !== index);
  if (duplicate) throw new Error(`${path} contains duplicate value: ${duplicate}`);
  return normalized;
};

const pacingProfilesValue = (value, path) => {
  const profiles = objectValue(value, path);
  const modes = ['fast', 'medium', 'slow'];
  const densities = ['high', 'medium', 'low'];
  exactKeys(profiles, modes, path);
  return Object.fromEntries(modes.map((mode) => {
    const modeProfile = objectValue(profiles[mode], `${path}.${mode}`);
    exactKeys(modeProfile, densities, `${path}.${mode}`);
    return [mode, Object.fromEntries(densities.map((density) => {
      const densityProfile = objectValue(modeProfile[density], `${path}.${mode}.${density}`);
      exactKeys(densityProfile, ['preferred_hold_seconds'], `${path}.${mode}.${density}`);
      const hold = objectValue(densityProfile.preferred_hold_seconds, `${path}.${mode}.${density}.preferred_hold_seconds`);
      exactKeys(hold, ['min', 'max'], `${path}.${mode}.${density}.preferred_hold_seconds`);
      const min = numberValue(hold.min, `${path}.${mode}.${density}.preferred_hold_seconds.min`);
      const max = numberValue(hold.max, `${path}.${mode}.${density}.preferred_hold_seconds.max`);
      if (min <= 0 || max <= min) throw new Error(`${path}.${mode}.${density}.preferred_hold_seconds must satisfy 0 < min < max.`);
      return [density, { preferred_hold_seconds: { min, max } }];
    }))];
  }));
};

export function normalizeLineArrayInput(value) {
  const values = value
    .split(String.fromCharCode(10))
    .map((item) => item.replace(String.fromCharCode(13), '').trim())
    .filter(Boolean);
  const duplicates = [...new Set(values.filter((item, index) => values.indexOf(item) !== index))];
  return { values: values.filter((item, index) => values.indexOf(item) === index), duplicates };
}

export function parseChannelDnaObject(value) {
  const root = objectValue(value, 'channel_dna');
  exactKeys(root, ['schema_version', 'channel', 'narration_wpm', 'pacing_profiles', 'narration_identity', 'visual_identity', 'entity_styles', 'global_negative_constraints'], 'channel_dna');
  if (root.schema_version !== '3.0') throw new Error('channel_dna.schema_version must be 3.0.');
  const channel = objectValue(root.channel, 'channel_dna.channel');
  exactKeys(channel, ['name', 'language', 'niche', 'audience'], 'channel_dna.channel');
  const narrationWpm = objectValue(root.narration_wpm, 'channel_dna.narration_wpm');
  exactKeys(narrationWpm, ['min', 'max'], 'channel_dna.narration_wpm');
  const narrationWpmMin = numberValue(narrationWpm.min, 'channel_dna.narration_wpm.min');
  const narrationWpmMax = numberValue(narrationWpm.max, 'channel_dna.narration_wpm.max');
  if (!Number.isInteger(narrationWpmMin) || !Number.isInteger(narrationWpmMax) || narrationWpmMin < 80 || narrationWpmMax > 300 || narrationWpmMin >= narrationWpmMax) throw new Error('channel_dna.narration_wpm must satisfy integer 80 <= min < max <= 300.');
  const narrationIdentity = objectValue(root.narration_identity, 'channel_dna.narration_identity');
  const narrationKeys = ['voice', 'delivery', 'explanation_style', 'tone_boundaries', 'avoid'];
  exactKeys(narrationIdentity, narrationKeys, 'channel_dna.narration_identity');
  const visualIdentity = objectValue(root.visual_identity, 'channel_dna.visual_identity');
  const visualKeys = ['medium', 'linework', 'rendering', 'palette', 'shape_language', 'texture', 'editorial_language'];
  exactKeys(visualIdentity, visualKeys, 'channel_dna.visual_identity');
  const parsedVisualIdentity = Object.fromEntries(
    visualKeys.map((key) => [key, stringArray(visualIdentity[key], `channel_dna.visual_identity.${key}`)]),
  );
  if (parsedVisualIdentity.editorial_language.length === 0) {
    throw new Error('channel_dna.visual_identity.editorial_language must contain at least one value.');
  }
  const entityStyles = objectValue(root.entity_styles, 'channel_dna.entity_styles');
  const entityKeys = ['character', 'location', 'prop', 'recurring_visual_subject'];
  exactKeys(entityStyles, entityKeys, 'channel_dna.entity_styles');
  const parsedEntities = {};
  for (const entityKey of entityKeys) {
    const entity = objectValue(entityStyles[entityKey], `channel_dna.entity_styles.${entityKey}`);
    exactKeys(entity, ['appearance', 'avoid'], `channel_dna.entity_styles.${entityKey}`);
    parsedEntities[entityKey] = {
      appearance: stringArray(entity.appearance, `channel_dna.entity_styles.${entityKey}.appearance`),
      avoid: stringArray(entity.avoid, `channel_dna.entity_styles.${entityKey}.avoid`),
    };
  }
  return {
    schema_version: '3.0',
    channel: {
      name: textValue(channel.name, 'channel_dna.channel.name'),
      language: textValue(channel.language, 'channel_dna.channel.language'),
      niche: textValue(channel.niche, 'channel_dna.channel.niche'),
      audience: textValue(channel.audience, 'channel_dna.channel.audience'),
    },
    narration_wpm: { min: narrationWpmMin, max: narrationWpmMax },
    pacing_profiles: pacingProfilesValue(root.pacing_profiles, 'channel_dna.pacing_profiles'),
    narration_identity: Object.fromEntries(narrationKeys.map((key) => [key, stringArray(narrationIdentity[key], `channel_dna.narration_identity.${key}`)])),
    visual_identity: parsedVisualIdentity,
    entity_styles: parsedEntities,
    global_negative_constraints: stringArray(root.global_negative_constraints, 'channel_dna.global_negative_constraints'),
  };
}
